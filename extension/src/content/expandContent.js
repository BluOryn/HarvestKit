/**
 * expandContent.js — Auto-expand collapsed job descriptions.
 *
 * Many job pages hide the full description behind "Show more", "Read more",
 * "See full description" buttons. This module finds and clicks them all
 * before extraction runs, ensuring we capture everything.
 */

const EXPAND_BUTTON_RX = /\b(show\s+more|read\s+more|see\s+(more|full|all|complete)|expand|mehr\s+(anzeigen|lesen)|vollständig|view\s+(full|more|all)|full\s+description|see\s+description|continue\s+reading|और पढ़ें|पूरा विवरण)\b/i;

const COLLAPSE_TEXT_RX = /\b(show\s+less|read\s+less|see\s+less|collapse|weniger|hide|close)\b/i;

/**
 * Find and click all "expand" buttons on the page.
 * Returns the number of buttons clicked.
 */
export async function expandAllContent(root = document) {
  let clicked = 0;
  const maxAttempts = 5;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const expandButtons = findExpandButtons(root);
    if (expandButtons.length === 0) break;

    for (const btn of expandButtons) {
      try {
        btn.scrollIntoView({ behavior: "instant", block: "center" });
        btn.click();
        clicked++;
        await sleep(400);
      } catch (e) {
        // Button may have been removed after a previous click
      }
    }

    // Wait for content to expand
    await sleep(600);
  }

  // Also try to expand any truncated text containers
  expandTruncatedElements(root);

  return clicked;
}

function findExpandButtons(root) {
  const buttons = [];
  const candidates = root.querySelectorAll("button, a, [role='button'], span[class*='more'], div[class*='more']");

  for (const el of candidates) {
    const text = (el.textContent || "").trim();
    if (text.length > 60) continue; // Too much text to be a button
    if (COLLAPSE_TEXT_RX.test(text)) continue; // Already expanded

    if (EXPAND_BUTTON_RX.test(text)) {
      // Make sure it's visible
      if (el.offsetParent !== null || el.offsetHeight > 0) {
        buttons.push(el);
      }
      continue;
    }

    // Check for ellipsis-style expand triggers
    if (/\.{3}$|…$/.test(text) && text.length < 30) {
      const cls = (el.className || "").toString().toLowerCase();
      if (/more|expand|toggle|trigger/i.test(cls)) {
        buttons.push(el);
      }
    }

    // Check aria-label/title for expand hints
    const ariaLabel = el.getAttribute("aria-label") || "";
    const title = el.getAttribute("title") || "";
    if (EXPAND_BUTTON_RX.test(ariaLabel) || EXPAND_BUTTON_RX.test(title)) {
      buttons.push(el);
    }
  }

  return buttons;
}

/**
 * Some sites use CSS to truncate text (max-height, overflow:hidden, -webkit-line-clamp).
 * Try to remove these constraints.
 */
function expandTruncatedElements(root) {
  const truncated = root.querySelectorAll(
    "[class*='truncat'], [class*='clamp'], [class*='ellipsis'], [class*='collapsed'], [class*='overflow']"
  );
  for (const el of truncated) {
    el.style.maxHeight = "none";
    el.style.overflow = "visible";
    el.style.webkitLineClamp = "unset";
    el.style.display = "block";
    el.classList.remove("truncated", "clamped", "collapsed");
  }
}

/**
 * Dismiss common overlays that block content: cookie banners, signup modals, etc.
 */
export async function dismissOverlays(root = document) {
  let dismissed = 0;

  // Cookie/consent banners
  const consentSelectors = [
    "[class*='cookie'] button",
    "[class*='consent'] button",
    "[class*='gdpr'] button",
    "[id*='cookie'] button",
    "#onetrust-accept-btn-handler",
    ".cc-dismiss",
    "[class*='cookie-banner'] [class*='close']",
    "[class*='cookie-banner'] [class*='accept']",
    "[data-testid*='cookie'] button",
  ];

  for (const sel of consentSelectors) {
    const btns = root.querySelectorAll(sel);
    for (const btn of btns) {
      const text = (btn.textContent || "").trim().toLowerCase();
      if (/accept|agree|ok|got\s+it|close|dismiss|×|✕/i.test(text) || text.length <= 3) {
        try { btn.click(); dismissed++; await sleep(300); } catch {}
      }
    }
  }

  // Generic modal close buttons
  const closeSelectors = [
    "[class*='modal'] [class*='close']",
    "[class*='overlay'] [class*='close']",
    "[class*='popup'] [class*='close']",
    "[class*='dialog'] [class*='close']",
    "[aria-label='Close']",
    "[aria-label='Dismiss']",
    "button[class*='close-btn']",
  ];

  for (const sel of closeSelectors) {
    const btns = root.querySelectorAll(sel);
    for (const btn of btns) {
      // Only close if it looks like an overlay (fixed/absolute positioned, high z-index)
      const style = window.getComputedStyle(btn.closest("[class*='modal'], [class*='overlay'], [class*='popup'], [class*='dialog']") || btn);
      if (style.position === "fixed" || style.position === "absolute") {
        try { btn.click(); dismissed++; await sleep(300); } catch {}
      }
    }
  }

  return dismissed;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
