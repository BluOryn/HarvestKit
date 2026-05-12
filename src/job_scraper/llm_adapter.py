"""LLM-fallback adapter — when heuristic extractor fills < N fields, ask
Anthropic Haiku to infer a CSS-selector → field map for this host.

Result cached per host in SQLite. On future hits, replay selectors without
calling LLM. Budget capped per month.

Required: pip install anthropic. Set ANTHROPIC_API_KEY env var, or pass
llm_api_key in YAML config under `run`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .models import JobListing


FIELDS = [
    "title", "company", "location", "city", "country",
    "employment_type", "seniority", "department", "company_industry",
    "salary_min", "salary_max", "salary_currency", "salary_period",
    "posted_date", "valid_through", "start_date",
    "application_email", "application_phone", "apply_url",
    "recruiter_name", "recruiter_email", "recruiter_phone", "recruiter_title",
    "responsibilities", "requirements", "qualifications", "benefits",
    "tech_stack", "skills", "language", "remote_type",
]

# Per-million-token Haiku 4.5 pricing (input/output). Used for budget tracking.
HAIKU_PRICE_IN_PER_M = 1.0
HAIKU_PRICE_OUT_PER_M = 5.0


_lock = threading.Lock()


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS selectors (
            host TEXT PRIMARY KEY,
            field_map_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            hit_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budget (
            month TEXT PRIMARY KEY,
            cost_usd REAL NOT NULL DEFAULT 0,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            calls INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _load_map(conn: sqlite3.Connection, host: str) -> Optional[Dict[str, str]]:
    cur = conn.execute("SELECT field_map_json FROM selectors WHERE host = ?", (host,))
    row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _save_map(conn: sqlite3.Connection, host: str, fmap: Dict[str, str]) -> None:
    now = dt.datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO selectors (host, field_map_json, created_at, updated_at, hit_count)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(host) DO UPDATE SET
            field_map_json = excluded.field_map_json,
            updated_at = excluded.updated_at,
            hit_count = selectors.hit_count + 1
    """, (host, json.dumps(fmap), now, now))
    conn.commit()


def _current_month_cost(conn: sqlite3.Connection) -> float:
    month = dt.datetime.utcnow().strftime("%Y-%m")
    cur = conn.execute("SELECT cost_usd FROM budget WHERE month = ?", (month,))
    row = cur.fetchone()
    return row[0] if row else 0.0


def _record_usage(conn: sqlite3.Connection, tokens_in: int, tokens_out: int) -> None:
    month = dt.datetime.utcnow().strftime("%Y-%m")
    cost = (tokens_in / 1_000_000) * HAIKU_PRICE_IN_PER_M + \
           (tokens_out / 1_000_000) * HAIKU_PRICE_OUT_PER_M
    conn.execute("""
        INSERT INTO budget (month, cost_usd, tokens_in, tokens_out, calls)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(month) DO UPDATE SET
            cost_usd = budget.cost_usd + excluded.cost_usd,
            tokens_in = budget.tokens_in + excluded.tokens_in,
            tokens_out = budget.tokens_out + excluded.tokens_out,
            calls = budget.calls + 1
    """, (month, cost, tokens_in, tokens_out))
    conn.commit()


def _condense_html(html: str, max_chars: int) -> str:
    """Strip script/style/noscript, collapse whitespace, truncate."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    text = str(soup)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "...[truncated]"
    return text


def _count_filled(j: JobListing) -> int:
    """Count non-empty primary fields on a JobListing."""
    return sum(1 for f in FIELDS if getattr(j, f, "") and str(getattr(j, f)).strip())


def _build_prompt(html: str) -> str:
    return f"""You are a web scraper. Given the HTML below, return CSS selectors that locate each schema field. Output ONLY JSON, no prose.

Schema fields (use these exact keys):
{json.dumps(FIELDS, indent=2)}

Rules:
- Each field maps to ONE CSS selector that returns the text node containing that field's value.
- For multi-value fields (responsibilities, requirements, benefits, tech_stack, skills), the selector should locate the container — caller will extract all child text.
- Omit fields you cannot locate. Empty values are worse than missing.
- Prefer stable selectors (semantic classes, itemprop, data-*) over positional (nth-child).
- If a field is inline in body text without a stable selector (e.g. "Søknadsfrist: 31.05.2026"), set value to "TEXT:<regex>" where the regex captures the value as group 1.

HTML:
{html}

Output JSON only:"""


def _call_anthropic(api_key: str, model: str, prompt: str) -> Tuple[str, int, int]:
    """Call Anthropic API. Returns (text, tokens_in, tokens_out)."""
    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError("LLM fallback requires `pip install anthropic`")

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text if resp.content else ""
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def _apply_map(html: str, fmap: Dict[str, str], j: JobListing) -> int:
    """Apply selector map to HTML. Fill missing fields on `j`. Returns # filled."""
    soup = BeautifulSoup(html, "lxml")
    filled = 0
    for field, sel in fmap.items():
        if field not in FIELDS:
            continue
        if getattr(j, field, ""):
            continue
        value = ""
        if sel.startswith("TEXT:"):
            pat = sel[5:]
            try:
                m = re.search(pat, html, re.I)
                if m:
                    value = (m.group(1) if m.groups() else m.group(0)).strip()
            except re.error:
                continue
        else:
            try:
                el = soup.select_one(sel)
                if el:
                    if field in ("responsibilities", "requirements", "qualifications",
                                 "benefits", "tech_stack", "skills"):
                        items = [li.get_text(" ", strip=True) for li in el.find_all(["li", "p"])]
                        items = [i for i in items if i and len(i) > 2]
                        value = "; ".join(items) if items else el.get_text(" ", strip=True)
                    else:
                        value = el.get_text(" ", strip=True)
            except Exception:
                continue
        if value and len(value) < 5000:
            setattr(j, field, value)
            filled += 1
    return filled


def llm_enrich(
    html: str,
    page_url: str,
    j: JobListing,
    *,
    api_key: str = "",
    model: str = "claude-haiku-4-5-20251001",
    min_fields: int = 5,
    max_html_chars: int = 60000,
    cache_path: str = ".cache/llm_selectors.sqlite",
    monthly_budget_usd: float = 5.0,
) -> int:
    """Try cached selectors first; if none or score still low, call LLM.

    Returns number of fields the LLM added. 0 means no-op / no improvement.
    """
    if _count_filled(j) >= min_fields:
        return 0  # heuristics already good enough

    host = urlparse(page_url).netloc.lower()
    if not host:
        return 0

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logging.warning("llm_adapter: no API key, skipping LLM fallback for %s", host)
        return 0

    with _lock:
        conn = _connect(cache_path)
        try:
            # Try cached map first
            cached = _load_map(conn, host)
            if cached:
                added = _apply_map(html, cached, j)
                if _count_filled(j) >= min_fields:
                    logging.info("llm_adapter: cache hit for %s, +%d fields", host, added)
                    return added

            # Budget check
            spent = _current_month_cost(conn)
            if spent >= monthly_budget_usd:
                logging.warning("llm_adapter: budget exceeded ($%.2f >= $%.2f), skipping", spent, monthly_budget_usd)
                return 0

            # Call LLM
            condensed = _condense_html(html, max_html_chars)
            prompt = _build_prompt(condensed)
            try:
                resp_text, tok_in, tok_out = _call_anthropic(api_key, model, prompt)
            except Exception as exc:
                logging.error("llm_adapter: API call failed for %s: %s", host, exc)
                return 0

            _record_usage(conn, tok_in, tok_out)

            # Parse JSON
            try:
                m = re.search(r"\{.*\}", resp_text, re.S)
                fmap = json.loads(m.group(0)) if m else {}
            except Exception:
                logging.error("llm_adapter: bad JSON response from LLM for %s", host)
                return 0

            if not isinstance(fmap, dict) or not fmap:
                return 0

            # Apply + cache
            added = _apply_map(html, fmap, j)
            if added > 0:
                _save_map(conn, host, fmap)
                logging.info("llm_adapter: learned selectors for %s, +%d fields (cost $%.4f)",
                             host, added, (tok_in / 1_000_000) * HAIKU_PRICE_IN_PER_M +
                             (tok_out / 1_000_000) * HAIKU_PRICE_OUT_PER_M)
            return added
        finally:
            conn.close()
