/**
 * expandContent.ts — Auto-expand collapsed job descriptions and dismiss overlays.
 */

const EXPAND_RX = /\b(show\s+more|read\s+more|see\s+(more|full|all|complete)|expand|mehr\s+(anzeigen|lesen)|view\s+(full|more|all)|full\s+description|और पढ़ें)\b/i;
const COLLAPSE_RX = /\b(show\s+less|read\s+less|collapse|weniger|hide|close)\b/i;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export async function expandAllContent(root: Document = document): Promise<number> {
  let clicked = 0;
  for (let attempt = 0; attempt < 5; attempt++) {
    const buttons = findExpandButtons(root);
    if (buttons.length === 0) break;
    for (const btn of buttons) {
      try { btn.scrollIntoView({ behavior: "instant", block: "center" }); btn.click(); clicked++; await sleep(400); } catch {}
    }
    await sleep(600);
  }
  expandTruncated(root);
  return clicked;
}

function findExpandButtons(root: Document): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (const el of root.querySelectorAll("button, a, [role='button'], span[class*='more'], div[class*='more']")) {
    const text = (el.textContent || "").trim();
    if (text.length > 60 || COLLAPSE_RX.test(text)) continue;
    if (EXPAND_RX.test(text) || EXPAND_RX.test(el.getAttribute("aria-label") || "") || EXPAND_RX.test(el.getAttribute("title") || "")) {
      if ((el as HTMLElement).offsetParent !== null) out.push(el as HTMLElement);
    }
  }
  return out;
}

function expandTruncated(root: Document) {
  for (const el of root.querySelectorAll("[class*='truncat'], [class*='clamp'], [class*='collapsed']") as NodeListOf<HTMLElement>) {
    el.style.maxHeight = "none";
    el.style.overflow = "visible";
    el.style.webkitLineClamp = "unset";
    el.style.display = "block";
  }
}

export async function dismissOverlays(root: Document = document): Promise<number> {
  let dismissed = 0;
  const consentSel = [
    "[class*='cookie'] button", "[class*='consent'] button", "[class*='gdpr'] button",
    "#onetrust-accept-btn-handler", ".cc-dismiss", "[class*='cookie-banner'] [class*='close']",
    "[class*='cookie-banner'] [class*='accept']",
  ];
  for (const sel of consentSel) {
    for (const btn of root.querySelectorAll(sel)) {
      const text = (btn.textContent || "").trim().toLowerCase();
      if (/accept|agree|ok|got\s+it|close|dismiss|×|✕/i.test(text) || text.length <= 3) {
        try { (btn as HTMLElement).click(); dismissed++; await sleep(300); } catch {}
      }
    }
  }
  const closeSel = [
    "[class*='modal'] [class*='close']", "[class*='overlay'] [class*='close']",
    "[class*='popup'] [class*='close']", "[aria-label='Close']",
  ];
  for (const sel of closeSel) {
    for (const btn of root.querySelectorAll(sel)) {
      const parent = (btn as HTMLElement).closest("[class*='modal'], [class*='overlay'], [class*='popup']");
      if (parent) {
        const style = window.getComputedStyle(parent);
        if (style.position === "fixed" || style.position === "absolute") {
          try { (btn as HTMLElement).click(); dismissed++; await sleep(300); } catch {}
        }
      }
    }
  }
  return dismissed;
}
