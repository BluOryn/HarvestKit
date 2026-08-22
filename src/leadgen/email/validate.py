"""Validation ladder, cheapest check first, stopping at the first failure.

Order matters for cost and for politeness: a role account is rejected from a
frozenset before any DNS query, and a domain with no MX is dropped before any
SMTP connection. Catch-all detection costs one connection per *domain*, not per
address, and its result is cached.
"""

from __future__ import annotations

import logging
import re
import smtplib
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

# Local parts that are a function, not a person. Emitting one of these as "the
# CTO's email" would be wrong in a way the buyer notices immediately.
ROLE_LOCALPARTS: frozenset[str] = frozenset(
    {
        "info",
        "information",
        "office",
        "buero",
        "bureau",
        "kontakt",
        "contact",
        "contacto",
        "contatto",
        "contato",
        "kontakty",
        "hello",
        "hallo",
        "hi",
        "mail",
        "email",
        "post",
        "postmaster",
        "mailbox",
        "jobs",
        "job",
        "karriere",
        "kariera",
        "career",
        "careers",
        "cariere",
        "bewerbung",
        "bewerbungen",
        "recruiting",
        "recruitment",
        "hiring",
        "empleo",
        "trabajo",
        "lavoro",
        "emploi",
        "praca",
        "vacatures",
        "vacancy",
        "hr",
        "people",
        "talent",
        "personal",
        "personel",
        "personeel",
        "sales",
        "vertrieb",
        "ventas",
        "vendite",
        "verkoop",
        "commercial",
        "support",
        "help",
        "helpdesk",
        "service",
        "kundenservice",
        "customerservice",
        "admin",
        "administration",
        "webmaster",
        "hostmaster",
        "abuse",
        "root",
        "team",
        "welcome",
        "willkommen",
        "presse",
        "press",
        "media",
        "pr",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "bounce",
        "marketing",
        "newsletter",
        "invoice",
        "rechnung",
        "billing",
        "buchhaltung",
        "datenschutz",
        "privacy",
        "legal",
        "impressum",
        "compliance",
        "dpo",
        "anfrage",
        "enquiry",
        "enquiries",
        "inquiry",
        "general",
        "allgemein",
    }
)

# Deliberately not full RFC 5322 — that grammar accepts addresses no mail system
# in this dataset will ever use. This is the practical subset.
_SYNTAX_RX = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}$"
)

SMTP_TIMEOUT = 8.0
_PROBE_LOCALPART = "zz-harvestkit-probe-9f3a1c"


@dataclass
class EmailVerdict:
    status: str  # "ok" | "catch_all" | "unknown" | "rejected"
    reason: str = ""

    # "ok" is a statement about the *domain*, not the address: the server
    # rejected a random probe local-part, so it validates recipients rather than
    # accepting everything. The address under test is never itself RCPT'd —
    # doing so per address would be one SMTP session per lead.


def is_role_account(email: str) -> bool:
    local = (email or "").strip().lower().partition("@")[0]
    if not local:
        return False
    if local in ROLE_LOCALPARTS:
        return True
    # "no-reply", "info-de", "jobs2024" — a role word plus a suffix.
    stem = re.split(r"[-_.+0-9]", local)[0]
    return stem in ROLE_LOCALPARTS


def valid_syntax(email: str) -> bool:
    value = (email or "").strip()
    return bool(value) and len(value) <= 254 and bool(_SYNTAX_RX.match(value))


_warned_missing_resolver = False


def _warn_missing_resolver() -> None:
    global _warned_missing_resolver
    if _warned_missing_resolver:
        return
    _warned_missing_resolver = True
    log.error(
        "dnspython is not installed, so no MX lookup can succeed and every "
        "address will be rejected. Run: pip install -r requirements.txt"
    )


def _resolve(domain: str, record_type: str) -> list:
    """Thin seam over dnspython so tests can substitute it."""
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 5.0
    return list(resolver.resolve(domain, record_type))


@lru_cache(maxsize=20000)
def has_mx(domain: str) -> bool:
    """True when the domain can receive mail. Cached — one lookup per domain."""
    if not domain:
        return False
    try:
        return bool(_resolve(domain, "MX"))
    except ImportError:
        # dnspython missing means *every* address is rejected for "no MX" and the
        # run produces an empty list with no error. That is indistinguishable
        # from a genuinely dead domain, so say it out loud, once.
        _warn_missing_resolver()
        return False
    except Exception:
        # No MX is common for parked or dead domains. Some domains accept mail on
        # their A record, but for prospecting a missing MX is a strong enough
        # signal to drop the lead.
        return False


def _mx_host(domain: str) -> str:
    try:
        records = _resolve(domain, "MX")
    except Exception:
        return ""
    hosts = sorted(
        (getattr(r, "preference", 0), str(getattr(r, "exchange", "")).rstrip(".")) for r in records
    )
    return hosts[0][1] if hosts else ""


@lru_cache(maxsize=20000)
def is_catch_all(domain: str) -> bool | None:
    """One probe per domain. None means the server refused to tell us."""
    host = _mx_host(domain)
    if not host:
        return None
    try:
        with smtplib.SMTP(host, 25, timeout=SMTP_TIMEOUT) as server:
            server.ehlo_or_helo_if_needed()
            server.mail("verify@example.com")
            code, _ = server.rcpt(f"{_PROBE_LOCALPART}@{domain}")
        return code in (250, 251)
    except (smtplib.SMTPException, OSError) as exc:
        log.debug("catch-all probe failed for %s: %s", domain, exc)
        return None


def validate(email: str, *, smtp: bool = True) -> EmailVerdict:
    """Run the ladder. Never raises."""
    value = (email or "").strip().lower()
    if not valid_syntax(value):
        return EmailVerdict("rejected", "invalid syntax")
    if is_role_account(value):
        return EmailVerdict("rejected", "role account, not a person")
    domain = value.partition("@")[2]
    if not has_mx(domain):
        return EmailVerdict("rejected", "domain has no MX record")
    if not smtp:
        return EmailVerdict("unknown", "smtp probing disabled")
    catch_all = is_catch_all(domain)
    if catch_all is True:
        return EmailVerdict("catch_all", "domain accepts all recipients")
    if catch_all is None:
        return EmailVerdict("unknown", "server would not answer the probe")
    return EmailVerdict("ok", "")
