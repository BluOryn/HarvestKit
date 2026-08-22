"""Country -> the paths on a company site that tend to name people.

Data, not code: adding a market is editing a list. English paths are always
appended because a great many EU companies run an English site regardless of
where they are registered.
"""

from __future__ import annotations

UNIVERSAL: list[str] = [
    "/team",
    "/about",
    "/about-us",
    "/company",
    "/people",
    "/leadership",
    "/management",
    "/contact",
    "/imprint",
    "/legal-notice",
]

BY_COUNTRY: dict[str, list[str]] = {
    "DE": ["/impressum", "/ueber-uns", "/unternehmen", "/kontakt", "/karriere", "/das-team", "/team/"],
    "AT": ["/impressum", "/ueber-uns", "/unternehmen", "/kontakt"],
    # Switzerland is trilingual and its companies name the executive board on a
    # page of its own far more often than they use a German-style Impressum:
    # "Geschäftsleitung" (executive management) and "Verwaltungsrat" (board) are
    # the two pages that actually carry the C-suite.
    "CH": [
        "/impressum",
        "/ueber-uns",
        "/unternehmen",
        "/kontakt",
        "/geschaeftsleitung",
        "/verwaltungsrat",
        "/management",
        "/team",
        "/ueber-uns/team",
        "/ueber-uns/management",
        "/das-team",
        "/mitarbeiter",
        "/a-propos",
        "/equipe",
        "/notre-equipe",
        "/direction",
        "/chi-siamo",
        "/il-team",
    ],
    "FR": ["/mentions-legales", "/equipe", "/a-propos", "/notre-equipe", "/contact", "/qui-sommes-nous"],
    "IT": ["/note-legali", "/chi-siamo", "/il-team", "/contatti", "/azienda"],
    "ES": ["/aviso-legal", "/quienes-somos", "/equipo", "/nosotros", "/contacto"],
    "PT": ["/aviso-legal", "/quem-somos", "/equipa", "/contactos"],
    "NL": ["/colofon", "/over-ons", "/ons-team", "/contact", "/team"],
    "BE": ["/over-ons", "/equipe", "/contact"],
    "PL": ["/o-nas", "/zespol", "/nasz-zespol", "/kontakt"],
    "SE": ["/om-oss", "/vart-team", "/kontakt"],
    "NO": ["/om-oss", "/vart-team", "/kontakt"],
    "DK": ["/om-os", "/vores-team", "/kontakt"],
    "FI": ["/tietoa-meista", "/tiimi", "/yhteystiedot"],
    "CZ": ["/o-nas", "/tym", "/kontakt"],
    "SK": ["/o-nas", "/tim", "/kontakt"],
    "RO": ["/despre-noi", "/echipa", "/contact"],
    "HU": ["/rolunk", "/csapat", "/kapcsolat"],
    "GR": ["/scheti-ka-me-emas", "/omada", "/epikoinonia"],
    "IE": ["/team", "/about-us", "/our-team", "/contact"],
    "BG": ["/za-nas", "/ekip", "/kontakti"],
    "HR": ["/o-nama", "/tim", "/kontakt"],
    "SI": ["/o-nas", "/ekipa", "/kontakt"],
    "LT": ["/apie-mus", "/komanda", "/kontaktai"],
    "LV": ["/par-mums", "/komanda", "/kontakti"],
    "EE": ["/meist", "/meeskond", "/kontakt"],
    "LU": ["/impressum", "/mentions-legales", "/equipe", "/kontakt"],
}


def candidate_paths(country: str) -> list[str]:
    """Localised paths first — they are the higher-yield pages — then English."""
    ordered = list(BY_COUNTRY.get((country or "").upper(), [])) + UNIVERSAL
    seen: set[str] = set()
    return [path for path in ordered if not (path in seen or seen.add(path))]
