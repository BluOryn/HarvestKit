"""Refuse to fetch URLs that point inside the network we are running on.

Every URL this pipeline fetches comes from scraped data — a job listing's
apply_url, a <loc> in somebody's sitemap. That is attacker-influenced input, and
the runbook says to run this in a container on a VPS, where
http://169.254.169.254/ hands out cloud credentials to anything that asks.

So a URL is fetched only if its host resolves to a public address. A host that
does not resolve is allowed through: the request cannot reach anything anyway,
and refusing it would break offline fixtures for no security gain.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud metadata services. These resolve to link-local addresses that the
# ip_address checks below already reject, but naming them makes the intent
# explicit and covers a provider that moves one to a routable address.
BLOCKED_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@lru_cache(maxsize=20000)
def _resolves_to_public(host: str) -> bool | None:
    """True/False when the host resolves, None when it does not resolve at all."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return None
    # Every resolved address must be public. A host with one public and one
    # private A record is a classic rebinding trick.
    return all(_is_public(address) for address in addresses)


def is_safe_url(url: str) -> bool:
    """False when the URL is malformed, non-HTTP, or aimed at internal space."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in BLOCKED_HOSTS:
        return False
    # A literal IP needs no DNS and must be checked directly.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_public(host)
    verdict = _resolves_to_public(host)
    if verdict is None:
        # Unresolvable: the fetch will fail on its own. Allowing it keeps
        # offline fixtures working without weakening anything real.
        return True
    return verdict


def guard(url: str) -> bool:
    """is_safe_url with a log line, for use at the fetch sites."""
    if is_safe_url(url):
        return True
    log.warning("net_guard: refusing %s — resolves into internal address space", url)
    return False
