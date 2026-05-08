"""Generic page → GeneralRecord extractor.

Three strategies (merged longer-string-wins):
  1. JSON-LD: LocalBusiness / Restaurant / Hotel / Store / Place / Organization / Product.
  2. Microdata (itemtype=schema.org/{LocalBusiness,...}).
  3. OpenGraph + meta tags.
  4. Heuristics (regex over body text + repeated structural cards).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .models import GENERAL_FIELDS, GeneralRecord


BUSINESS_TYPES = {
    "localbusiness", "restaurant", "store", "shop", "hotel", "lodging", "place",
    "medicalbusiness", "medicalclinic", "physician", "dentist", "hospital",
    "automotivebusiness", "foodestablishment", "barorpub", "cafeorcoffeeshop",
    "professionalservice", "homeandconstructionbusiness", "financialservice",
    "legalservice", "healthandbeautybusiness", "sportsclub", "travelagency",
    "library", "museum", "park", "stadiumorarena", "civicstructure",
}


def extract_record_from_page(html: str, page_url: str) -> Optional[GeneralRecord]:
    soup = BeautifulSoup(html, "lxml")
    rec = GeneralRecord()
    rec.source_url = page_url
    parsed = urlparse(page_url)
    rec.source_domain = parsed.netloc

    blocks = list(_iter_json_ld(soup))
    biz_blocks = [b for b in blocks if _is_business(b)]
    if biz_blocks:
        for b in biz_blocks:
            _apply_jsonld(rec, b)

    _apply_microdata(rec, soup)
    _apply_opengraph(rec, soup)
    _apply_heuristics(rec, soup)

    if not rec.name:
        h1 = soup.find("h1")
        if h1:
            rec.name = h1.get_text(" ", strip=True)
    if not rec.name:
        return None
    return rec


def extract_listing_cards(html: str, page_url: str, selectors: Optional[Dict[str, str]] = None) -> List[GeneralRecord]:
    """Extract a list of records from a search/listing page.

    If `selectors` is provided (from config), uses CSS selectors. Otherwise
    falls back to JSON-LD ItemList + repeated-card heuristics.
    """
    soup = BeautifulSoup(html, "lxml")
    out: List[GeneralRecord] = []
    if selectors and selectors.get("card"):
        cards = soup.select(selectors["card"])
        for c in cards:
            r = _record_from_card(c, page_url, selectors)
            if r and r.name:
                out.append(r)
        return out

    # JSON-LD ItemList path
    for block in _iter_json_ld(soup):
        if str(block.get("@type", "")).lower() == "itemlist":
            elements = block.get("itemListElement") or []
            for el in elements:
                item = el.get("item") if isinstance(el, dict) else None
                if isinstance(item, dict) and _is_business(item):
                    r = GeneralRecord()
                    _apply_jsonld(r, item)
                    if not r.source_url and isinstance(item.get("url"), str):
                        r.source_url = item["url"]
                    r.source_listing_url = page_url
                    if r.name:
                        out.append(r)
    if out:
        return out

    # Heuristic: find repeating sibling structures with title + address/phone hints.
    out = _heuristic_card_extract(soup, page_url)
    return out


def _record_from_card(card: Tag, page_url: str, selectors: Dict[str, str]) -> Optional[GeneralRecord]:
    rec = GeneralRecord(source_listing_url=page_url, source_domain=urlparse(page_url).netloc)
    pick = lambda key: _pick(card, selectors.get(key))
    rec.name = pick("title") or pick("name") or ""
    rec.address = pick("address") or ""
    rec.city = pick("city") or ""
    rec.region = pick("region") or ""
    rec.phone = pick("phone") or ""
    rec.rating = pick("rating") or ""
    rec.review_count = pick("review_count") or ""
    rec.price_range = pick("price_range") or ""
    rec.category = pick("category") or ""
    rec.image = pick("image") or ""
    url_sel = selectors.get("url")
    if url_sel:
        a = card.select_one(url_sel)
        if a:
            href = a.get("href") if a.has_attr("href") else None
            if href:
                rec.source_url = urljoin(page_url, href)
    return rec if rec.name else None


def _pick(card: Tag, selector: Optional[str]) -> str:
    if not selector:
        return ""
    el = card.select_one(selector)
    if not el:
        return ""
    if el.has_attr("content"):
        return el["content"].strip()
    if el.name == "img" and el.has_attr("src"):
        return el["src"].strip()
    return el.get_text(" ", strip=True)


def _iter_json_ld(soup: BeautifulSoup) -> Iterable[Dict[str, Any]]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text(strip=False)
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            try:
                data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
            except Exception:
                continue
        for item in _flatten(data):
            if isinstance(item, dict):
                yield item


def _flatten(data: Any) -> Iterable[Any]:
    if isinstance(data, list):
        for item in data:
            yield from _flatten(item)
        return
    if isinstance(data, dict):
        graph = data.get("@graph")
        if graph:
            yield from _flatten(graph)
            return
        yield data


def _is_business(item: Dict[str, Any]) -> bool:
    t = item.get("@type")
    types: List[str] = []
    if isinstance(t, list):
        types = [str(x).lower() for x in t]
    elif t is not None:
        types = [str(t).lower()]
    return any(typ in BUSINESS_TYPES or "business" in typ for typ in types)


def _apply_jsonld(rec: GeneralRecord, item: Dict[str, Any]) -> None:
    rec.name = rec.name or _str(item.get("name") or item.get("legalName"))
    rec.description = rec.description or _str(item.get("description"))
    rec.image = rec.image or _str(_first_url(item.get("image")))
    rec.website = rec.website or _str(item.get("url") or item.get("sameAs"))
    rec.price_range = rec.price_range or _str(item.get("priceRange"))
    rec.menu_url = rec.menu_url or _str(item.get("hasMenu") or item.get("menu"))
    rec.reservation_url = rec.reservation_url or _str(_first_url(item.get("acceptsReservations")))

    cat = item.get("@type")
    if isinstance(cat, list):
        rec.category = rec.category or ", ".join(str(x) for x in cat if x)
    elif cat:
        rec.category = rec.category or _str(cat)
    sub = item.get("servesCuisine") or item.get("category")
    if isinstance(sub, list):
        rec.subcategories = rec.subcategories or ", ".join(str(x) for x in sub if x)
    elif sub:
        rec.subcategories = rec.subcategories or _str(sub)

    # Address
    addr = item.get("address")
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if isinstance(addr, dict):
        rec.street_address = rec.street_address or _str(addr.get("streetAddress"))
        rec.city = rec.city or _str(addr.get("addressLocality"))
        rec.region = rec.region or _str(addr.get("addressRegion"))
        rec.country = rec.country or _str(_country(addr.get("addressCountry")))
        rec.postal_code = rec.postal_code or _str(addr.get("postalCode"))
        if not rec.address:
            parts = [rec.street_address, rec.city, rec.region, rec.postal_code, rec.country]
            rec.address = ", ".join([p for p in parts if p])
    elif isinstance(addr, str):
        rec.address = rec.address or addr

    # Geo
    geo = item.get("geo")
    if isinstance(geo, dict):
        rec.latitude = rec.latitude or _str(geo.get("latitude"))
        rec.longitude = rec.longitude or _str(geo.get("longitude"))

    # Phone / email
    rec.phone = rec.phone or _str(item.get("telephone") or item.get("phone"))
    rec.email = rec.email or _str(item.get("email"))

    # Rating
    agg = item.get("aggregateRating")
    if isinstance(agg, dict):
        rec.rating = rec.rating or _str(agg.get("ratingValue"))
        rec.review_count = rec.review_count or _str(agg.get("reviewCount") or agg.get("ratingCount"))

    # Hours
    oh = item.get("openingHoursSpecification") or item.get("openingHours")
    if oh:
        if isinstance(oh, list):
            parts = []
            for spec in oh:
                if isinstance(spec, str):
                    parts.append(spec)
                elif isinstance(spec, dict):
                    days = spec.get("dayOfWeek") or ""
                    if isinstance(days, list):
                        days = "/".join(d.replace("https://schema.org/", "") for d in days)
                    parts.append(f"{days}: {spec.get('opens', '')}-{spec.get('closes', '')}")
            rec.hours = rec.hours or "; ".join(parts)
        elif isinstance(oh, str):
            rec.hours = rec.hours or oh

    # Social links
    same_as = item.get("sameAs")
    if isinstance(same_as, list):
        rec.social_links = rec.social_links or ", ".join(str(x) for x in same_as if x)
    elif isinstance(same_as, str):
        rec.social_links = rec.social_links or same_as

    if not rec.raw_jsonld:
        try:
            rec.raw_jsonld = json.dumps(item, ensure_ascii=False)[:8000]
        except Exception:
            pass


def _apply_microdata(rec: GeneralRecord, soup: BeautifulSoup) -> None:
    root = soup.find(attrs={"itemtype": re.compile(r"schema\.org/(LocalBusiness|Restaurant|Place|Store|Organization)", re.I)})
    if not root or not isinstance(root, Tag):
        return
    def get_prop(name: str) -> str:
        el = root.find(attrs={"itemprop": name})
        if not el:
            return ""
        if el.has_attr("content"):
            return el["content"]
        return el.get_text(" ", strip=True)
    rec.name = rec.name or get_prop("name")
    rec.phone = rec.phone or get_prop("telephone")
    rec.description = rec.description or get_prop("description")


def _apply_opengraph(rec: GeneralRecord, soup: BeautifulSoup) -> None:
    def og(name: str) -> str:
        el = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if el and el.get("content"):
            return el["content"].strip()
        return ""
    rec.name = rec.name or og("og:title")
    rec.description = rec.description or og("og:description") or og("description")
    rec.image = rec.image or og("og:image")
    rec.website = rec.website or og("og:url")


PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}")
EMAIL_RX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _apply_heuristics(rec: GeneralRecord, soup: BeautifulSoup) -> None:
    text = (soup.find("body") or soup).get_text(" ", strip=True)[:50000]
    if not rec.phone:
        m = PHONE_RX.search(text)
        if m and len(re.sub(r"\D", "", m.group(0))) >= 7:
            rec.phone = m.group(0)
    if not rec.email:
        m = EMAIL_RX.search(text)
        if m and not re.search(r"noreply|no-reply|example\.com", m.group(0), re.I):
            rec.email = m.group(0)


def _heuristic_card_extract(soup: BeautifulSoup, page_url: str) -> List[GeneralRecord]:
    """Find repeating siblings that look like business cards (have name + address/phone)."""
    out: List[GeneralRecord] = []
    candidates = soup.select("li, article, div[class*='result'], div[class*='card'], div[class*='listing'], div[class*='business']")
    for c in candidates:
        # Must have a heading and either an address-like line or a phone.
        h = c.select_one("h1, h2, h3, h4, [class*='title'], [class*='name']")
        if not h:
            continue
        name = h.get_text(" ", strip=True)
        if not name or len(name) < 2:
            continue
        addr = c.select_one("address, [class*='address'], [class*='location']")
        phone = c.select_one("[class*='phone'], [class*='tel']")
        if not addr and not phone:
            continue
        a = c.select_one("a[href]")
        url = ""
        if a:
            url = urljoin(page_url, a.get("href") or "")
        rec = GeneralRecord(
            name=name,
            address=addr.get_text(" ", strip=True) if addr else "",
            phone=phone.get_text(" ", strip=True) if phone else "",
            source_url=url,
            source_listing_url=page_url,
            source_domain=urlparse(page_url).netloc,
        )
        out.append(rec)
    # Dedupe by name
    seen = set()
    dedup = []
    for r in out:
        k = r.name.strip().lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x).strip() for x in v if x)
    return str(v).strip()


def _first_url(v: Any) -> Any:
    if isinstance(v, list) and v:
        return v[0]
    return v


def _country(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("name") or v.get("identifier") or ""
    return _str(v)
