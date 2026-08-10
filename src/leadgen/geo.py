"""Resolve a free-text location string to an ISO 3166-1 alpha-2 country code.

Job boards hand back whatever the employer typed: "Munich, Germany", "Berlin",
"Remote - EU", "Paris, France; London, UK". Without a country code the
per-country ceiling cannot bite and a claim that the list covers the EU cannot
be checked, so this is the difference between a spread guarantee and a hope.

Country names are matched first because they are unambiguous. Cities are a
fallback and deliberately limited to ones that are not shared across countries —
there is a Frankfurt in Germany and none worth confusing it with, but "Cambridge"
and "Birmingham" exist on two continents and are therefore absent.
"""

from __future__ import annotations

import re

EU_COUNTRIES: frozenset[str] = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)
# Not EU members, but routinely wanted alongside them for a "European" list.
EFTA_AND_UK: frozenset[str] = frozenset({"GB", "CH", "NO", "IS", "LI"})

COUNTRY_NAMES: dict[str, str] = {
    "germany": "DE",
    "deutschland": "DE",
    "allemagne": "DE",
    "france": "FR",
    "frankreich": "FR",
    "spain": "ES",
    "españa": "ES",
    "espana": "ES",
    "spanien": "ES",
    "italy": "IT",
    "italia": "IT",
    "italien": "IT",
    "netherlands": "NL",
    "nederland": "NL",
    "holland": "NL",
    "niederlande": "NL",
    "belgium": "BE",
    "belgië": "BE",
    "belgique": "BE",
    "austria": "AT",
    "österreich": "AT",
    "oesterreich": "AT",
    "poland": "PL",
    "polska": "PL",
    "polen": "PL",
    "portugal": "PT",
    "sweden": "SE",
    "sverige": "SE",
    "schweden": "SE",
    "denmark": "DK",
    "danmark": "DK",
    "finland": "FI",
    "suomi": "FI",
    "ireland": "IE",
    "éire": "IE",
    "czechia": "CZ",
    "czech republic": "CZ",
    "romania": "RO",
    "hungary": "HU",
    "magyarország": "HU",
    "greece": "GR",
    "bulgaria": "BG",
    "croatia": "HR",
    "hrvatska": "HR",
    "slovakia": "SK",
    "slovenia": "SI",
    "estonia": "EE",
    "eesti": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "luxembourg": "LU",
    "malta": "MT",
    "cyprus": "CY",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "great britain": "GB",
    "switzerland": "CH",
    "schweiz": "CH",
    "suisse": "CH",
    "svizzera": "CH",
    "norway": "NO",
    "norge": "NO",
    "iceland": "IS",
    "liechtenstein": "LI",
    "united states": "US",
    "usa": "US",
    "u.s.": "US",
    "america": "US",
    "canada": "CA",
    "india": "IN",
    "australia": "AU",
    "singapore": "SG",
    "israel": "IL",
    "japan": "JP",
    "brazil": "BR",
    "mexico": "MX",
    "united arab emirates": "AE",
    "uae": "AE",
}

# Only cities whose name does not collide across countries.
CITY_NAMES: dict[str, str] = {
    "berlin": "DE",
    "munich": "DE",
    "münchen": "DE",
    "muenchen": "DE",
    "hamburg": "DE",
    "cologne": "DE",
    "köln": "DE",
    "frankfurt": "DE",
    "stuttgart": "DE",
    "düsseldorf": "DE",
    "dusseldorf": "DE",
    "leipzig": "DE",
    "dortmund": "DE",
    "karlsruhe": "DE",
    "nuremberg": "DE",
    "nürnberg": "DE",
    "paris": "FR",
    "lyon": "FR",
    "marseille": "FR",
    "toulouse": "FR",
    "bordeaux": "FR",
    "nantes": "FR",
    "lille": "FR",
    "montpellier": "FR",
    "madrid": "ES",
    "barcelona": "ES",
    "valencia": "ES",
    "seville": "ES",
    "bilbao": "ES",
    "málaga": "ES",
    "malaga": "ES",
    "rome": "IT",
    "roma": "IT",
    "milan": "IT",
    "milano": "IT",
    "turin": "IT",
    "torino": "IT",
    "bologna": "IT",
    "naples": "IT",
    "napoli": "IT",
    "amsterdam": "NL",
    "rotterdam": "NL",
    "utrecht": "NL",
    "eindhoven": "NL",
    "the hague": "NL",
    "den haag": "NL",
    "brussels": "BE",
    "bruxelles": "BE",
    "antwerp": "BE",
    "ghent": "BE",
    "vienna": "AT",
    "wien": "AT",
    "graz": "AT",
    "linz": "AT",
    "salzburg": "AT",
    "warsaw": "PL",
    "warszawa": "PL",
    "krakow": "PL",
    "kraków": "PL",
    "wroclaw": "PL",
    "wrocław": "PL",
    "poznan": "PL",
    "poznań": "PL",
    "gdansk": "PL",
    "lisbon": "PT",
    "lisboa": "PT",
    "porto": "PT",
    "stockholm": "SE",
    "gothenburg": "SE",
    "göteborg": "SE",
    "malmö": "SE",
    "malmo": "SE",
    "copenhagen": "DK",
    "københavn": "DK",
    "kobenhavn": "DK",
    "aarhus": "DK",
    "helsinki": "FI",
    "espoo": "FI",
    "tampere": "FI",
    "dublin": "IE",
    "cork": "IE",
    "galway": "IE",
    "prague": "CZ",
    "praha": "CZ",
    "brno": "CZ",
    "bucharest": "RO",
    "bucuresti": "RO",
    "cluj": "RO",
    "cluj-napoca": "RO",
    "timisoara": "RO",
    "budapest": "HU",
    "athens": "GR",
    "thessaloniki": "GR",
    "sofia": "BG",
    "zagreb": "HR",
    "bratislava": "SK",
    "ljubljana": "SI",
    "tallinn": "EE",
    "tartu": "EE",
    "riga": "LV",
    "vilnius": "LT",
    "kaunas": "LT",
    "london": "GB",
    "manchester": "GB",
    "edinburgh": "GB",
    "glasgow": "GB",
    "bristol": "GB",
    "leeds": "GB",
    "belfast": "GB",
    "zurich": "CH",
    "zürich": "CH",
    "geneva": "CH",
    "genève": "CH",
    "basel": "CH",
    "bern": "CH",
    "lausanne": "CH",
    "lugano": "CH",
    "zug": "CH",
    "oslo": "NO",
    "bergen": "NO",
    "trondheim": "NO",
    "reykjavik": "IS",
    "luxembourg city": "LU",
    "new york": "US",
    "san francisco": "US",
    "seattle": "US",
    "boston": "US",
    "austin": "US",
    "chicago": "US",
    "denver": "US",
    "los angeles": "US",
    "toronto": "CA",
    "vancouver": "CA",
    "montreal": "CA",
    "bangalore": "IN",
    "bengaluru": "IN",
    "mumbai": "IN",
    "delhi": "IN",
    "hyderabad": "IN",
    "pune": "IN",
    "chennai": "IN",
    "gurgaon": "IN",
    "noida": "IN",
    "tel aviv": "IL",
    "sydney": "AU",
    "melbourne": "AU",
    "tokyo": "JP",
    "são paulo": "BR",
    "sao paulo": "BR",
    "dubai": "AE",
}


def _tokens(text: str) -> list[str]:
    """Split on the separators job boards actually use, longest phrase first."""
    lowered = (text or "").lower()
    parts = re.split(r"[,;/|()\[\]]|\s+[-–—]\s+|\bor\b|\band\b", lowered)
    return [part.strip(" .\t") for part in parts if part.strip(" .\t")]


def country_from_location(text: str) -> str:
    """Best-effort ISO alpha-2 for a free-text location. "" when unknown."""
    if not text:
        return ""
    parts = _tokens(text)
    # Country names win: "Cambridge, United Kingdom" must not resolve on the city.
    for part in parts:
        if part in COUNTRY_NAMES:
            return COUNTRY_NAMES[part]
    for part in parts:
        for name, code in COUNTRY_NAMES.items():
            if re.search(rf"\b{re.escape(name)}\b", part):
                return code
    for part in parts:
        if part in CITY_NAMES:
            return CITY_NAMES[part]
    for part in parts:
        for name, code in CITY_NAMES.items():
            if re.search(rf"\b{re.escape(name)}\b", part):
                return code
    return ""


def search_name(code: str) -> str:
    """The English name for a country code, for search APIs that take a place
    name rather than a code.

    Derived by inverting COUNTRY_NAMES rather than kept as a second list: the
    English name is the first spelling recorded for every code, and one table
    that cannot drift out of step with itself beats two that can.
    """
    return _SEARCH_NAMES.get((code or "").strip().upper(), "")


def search_names(codes) -> list[str]:
    """Names for a set of codes, skipping any this module cannot name."""
    return sorted(name for code in codes if (name := search_name(code)))


_SEARCH_NAMES: dict[str, str] = {}
for _name, _code in COUNTRY_NAMES.items():
    _SEARCH_NAMES.setdefault(_code, _name.title())


def is_european(code: str) -> bool:
    upper = (code or "").upper()
    return upper in EU_COUNTRIES or upper in EFTA_AND_UK
