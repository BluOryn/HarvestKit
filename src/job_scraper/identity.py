"""A coherent browsing identity: one UA, the client hints that match it, a
plausible Accept-Language for the target country, and a TLS profile to match.

The previous code drew a fresh User-Agent at random on *every* request and then
attached `Sec-CH-UA: "Chromium";v="120"` regardless of which UA it had drawn. A
single host therefore saw a Safari UA arrive claiming to be Chromium 120, then a
Firefox UA claiming the same, all from one connection. Each of those is a
stronger bot signal than sending no hints at all, because no real browser can
produce them.

Three rules follow from that, and this module exists to enforce them:

1. **An identity is stable for a host.** A browser does not change what it is
   between two page loads. The identity is derived from the hostname, so every
   request to that host in this run presents the same browser.
2. **The parts must agree.** The UA, the client hints, the platform and the TLS
   fingerprint all describe one browser or the identity is self-refuting.
3. **The language should suit the destination.** A Norwegian careers page
   fetched with `Accept-Language: en-US` is not wrong, but it is unusual, and
   unusual is what fingerprinters score. Where the country is known, ask in the
   language the site is written in.

`curl_cffi` already emits a matched UA and hint set for each impersonation
target, so for that transport we deliberately send *neither* — overriding them
is what breaks the match. See `headers_for()`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

#: Accept-Language by ISO-3166 country, most specific first and always with an
#: English tail — European professionals' browsers overwhelmingly carry one, and
#: a header with no fallback at all is itself unusual.
ACCEPT_LANGUAGE_BY_COUNTRY: dict[str, str] = {
    "DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "AT": "de-AT,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "CH": "de-CH,de;q=0.9,fr-CH;q=0.8,en-US;q=0.7,en;q=0.6",
    "FR": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "BE": "nl-BE,nl;q=0.9,fr-BE;q=0.8,en-US;q=0.7,en;q=0.6",
    "NL": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "LU": "fr-LU,fr;q=0.9,de-LU;q=0.8,en;q=0.7",
    "IT": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "ES": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "PT": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "NO": "nb-NO,nb;q=0.9,no;q=0.8,en-US;q=0.7,en;q=0.6",
    "SE": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
    "DK": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
    "FI": "fi-FI,fi;q=0.9,sv-FI;q=0.8,en-US;q=0.7,en;q=0.6",
    "IS": "is-IS,is;q=0.9,en-US;q=0.8,en;q=0.7",
    "PL": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "CZ": "cs-CZ,cs;q=0.9,en-US;q=0.8,en;q=0.7",
    "SK": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7",
    "HU": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "RO": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    "BG": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "GR": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
    "HR": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "SI": "sl-SI,sl;q=0.9,en-US;q=0.8,en;q=0.7",
    "EE": "et-EE,et;q=0.9,en-US;q=0.8,en;q=0.7",
    "LV": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7",
    "LT": "lt-LT,lt;q=0.9,en-US;q=0.8,en;q=0.7",
    "IE": "en-IE,en-GB;q=0.9,en;q=0.8",
    "GB": "en-GB,en;q=0.9",
    "UK": "en-GB,en;q=0.9",
    "MT": "en-MT,en-GB;q=0.9,mt;q=0.8,en;q=0.7",
    "CY": "el-CY,el;q=0.9,en-GB;q=0.8,en;q=0.7",
    "US": "en-US,en;q=0.9",
}

#: ccTLD → country, so a domain alone implies a language even when the caller
#: never resolved a country. `.eu` and the gTLDs deliberately stay absent: they
#: carry no geography and guessing one would be worse than the default.
TLD_COUNTRY: dict[str, str] = {
    "de": "DE",
    "at": "AT",
    "ch": "CH",
    "fr": "FR",
    "be": "BE",
    "nl": "NL",
    "lu": "LU",
    "it": "IT",
    "es": "ES",
    "pt": "PT",
    "no": "NO",
    "se": "SE",
    "dk": "DK",
    "fi": "FI",
    "is": "IS",
    "pl": "PL",
    "cz": "CZ",
    "sk": "SK",
    "hu": "HU",
    "ro": "RO",
    "bg": "BG",
    "gr": "GR",
    "hr": "HR",
    "si": "SI",
    "ee": "EE",
    "lv": "LV",
    "lt": "LT",
    "ie": "IE",
    "uk": "GB",
    "mt": "MT",
    "cy": "CY",
}

DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"


@dataclass(frozen=True)
class BrowserIdentity:
    """One self-consistent browser. `impersonate` names a curl_cffi target."""

    impersonate: str
    user_agent: str
    sec_ch_ua: str
    platform: str
    is_chromium: bool

    @property
    def sec_ch_ua_platform(self) -> str:
        return f'"{self.platform}"'


#: The pool. Every entry pairs a curl_cffi impersonation target with the exact
#: UA and hint string that target emits, so the plain-`requests` transport can
#: present the same identity the impersonating transport would — the two rungs
#: of the ladder then look like the same visitor rather than two.
#:
#: Only current browser versions appear here. A fingerprinter scores an outdated
#: Chrome as suspicious, and the pool this replaced was pinned to Chrome 120 —
#: released in December 2023, and by now old enough to be a signal in itself.
IDENTITY_POOL: tuple[BrowserIdentity, ...] = (
    BrowserIdentity(
        impersonate="chrome146",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        platform="Windows",
        is_chromium=True,
    ),
    BrowserIdentity(
        impersonate="chrome145",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="145", "Not-A.Brand";v="24", "Google Chrome";v="145"',
        platform="macOS",
        is_chromium=True,
    ),
    BrowserIdentity(
        impersonate="chrome142",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="142", "Not-A.Brand";v="24", "Google Chrome";v="142"',
        platform="Windows",
        is_chromium=True,
    ),
    BrowserIdentity(
        impersonate="firefox147",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
        sec_ch_ua="",  # Firefox sends no client hints at all.
        platform="Windows",
        is_chromium=False,
    ),
    BrowserIdentity(
        impersonate="safari260",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/26.0 Safari/605.1.15"
        ),
        sec_ch_ua="",  # Nor does Safari.
        platform="macOS",
        is_chromium=False,
    ),
)


def country_for_host(host: str) -> str:
    """Country implied by the ccTLD, or "" when the domain implies none."""
    tld = (host or "").rsplit(".", 1)[-1].lower()
    return TLD_COUNTRY.get(tld, "")


def accept_language_for(host: str, country: Optional[str] = None) -> str:
    """Country wins when the caller knows it; otherwise infer from the ccTLD."""
    code = (country or "").strip().upper()
    if code in ACCEPT_LANGUAGE_BY_COUNTRY:
        return ACCEPT_LANGUAGE_BY_COUNTRY[code]
    implied = country_for_host(host)
    return ACCEPT_LANGUAGE_BY_COUNTRY.get(implied, DEFAULT_ACCEPT_LANGUAGE)


def identity_for(host: str, *, salt: str = "") -> BrowserIdentity:
    """The identity this host sees, stable for the life of the process.

    Derived by hashing rather than drawn at random so that two requests to the
    same host always present the same browser. `salt` lets a caller deliberately
    take a *different* identity for a host — which is what rotating onto a new
    proxy should do, since a new egress IP presenting the previous IP's browser
    is a correlation a fingerprinter is happy to make.
    """
    digest = hashlib.sha256(f"{(host or '').lower()}|{salt}".encode()).digest()
    return IDENTITY_POOL[digest[0] % len(IDENTITY_POOL)]


def headers_for(
    url: str,
    identity: BrowserIdentity,
    *,
    country: Optional[str] = None,
    referer: Optional[str] = None,
    for_impersonation: bool = False,
) -> dict[str, str]:
    """The header set a real browser sends for a top-level navigation.

    `for_impersonation=True` omits User-Agent and the Sec-CH-UA family, because
    curl_cffi emits its own matched to the TLS fingerprint it is presenting.
    Overriding them there is precisely how a request ends up claiming one
    browser in its headers and another in its ClientHello.
    """
    parsed = urlparse(url)
    host = parsed.netloc

    headers: dict[str, str] = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": accept_language_for(host, country),
        "Upgrade-Insecure-Requests": "1",
        # A browser navigating to a page it was not linked to sends `none`. The
        # code this replaces always sent `same-origin` — including on the very
        # first request to a host, where it is impossible: same-origin means a
        # same-site page linked here, and that page would have set a Referer.
        # Claiming it with no Referer is a contradiction a WAF can test for.
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Priority": "u=0, i",
    }
    if referer:
        headers["Referer"] = referer

    if not for_impersonation:
        # Plain `requests` sets none of this, so the identity must supply it.
        headers["User-Agent"] = identity.user_agent
        # Let urllib3 negotiate the content encodings it can actually decode;
        # advertising `br` without brotli installed yields an unreadable body.
        headers["Accept-Encoding"] = "gzip, deflate"
        if identity.is_chromium and identity.sec_ch_ua:
            headers["Sec-CH-UA"] = identity.sec_ch_ua
            headers["Sec-CH-UA-Mobile"] = "?0"
            headers["Sec-CH-UA-Platform"] = identity.sec_ch_ua_platform

    return headers
