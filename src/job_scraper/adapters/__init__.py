from urllib.parse import urlparse

from ..config import TargetConfig
from .arbeitsagentur import ArbeitsagenturAdapter
from .ashby import AshbyAdapter
from .base import BaseAdapter
from .finn import FinnNoAdapter
from .generic import GenericAdapter
from .greenhouse import GreenhouseAdapter
from .jobbsafari import JobbsafariAdapter
from .jobsch import JobsChAdapter
from .karrierestart import KarrierestartAdapter
from .lever import LeverAdapter
from .nav import NavNoAdapter
from .personio import PersonioAdapter
from .recruitee import RecruiteeAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workable import WorkableAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, BaseAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "smartrecruiters": SmartRecruitersAdapter(),
    "personio": PersonioAdapter(),
    "ashby": AshbyAdapter(),
    "recruitee": RecruiteeAdapter(),
    "workable": WorkableAdapter(),
    "workday": WorkdayAdapter(),
    "arbeitsagentur": ArbeitsagenturAdapter(),
    "jobs.ch": JobsChAdapter(),
    "finn.no": FinnNoAdapter(),
    "nav.no": NavNoAdapter(),
    "karrierestart.no": KarrierestartAdapter(),
    "jobbsafari.no": JobbsafariAdapter(),
    "generic": GenericAdapter(),
}


def get_adapter(target: TargetConfig) -> BaseAdapter:
    """Resolve a target to its adapter singleton.

    Always returns an instance from ADAPTERS — never a fresh GenericAdapter().
    GenericAdapter guards against delegating to itself with `adapter is self`,
    which only holds if both sides are the same registry object.
    """
    adapter_name = (target.adapter or "auto").lower()
    if adapter_name == "auto":
        adapter_name = _detect_adapter(target.url)
    return ADAPTERS.get(adapter_name) or ADAPTERS["generic"]


#: (adapter name, exact hosts, suffixes). A host matches when it *is* one of
#: the exact hosts or ends in ".<suffix>" — never on a substring.
#:
#: Substring matching is the bug this replaces. `"smartrecruiters.com" in host`
#: is true for `smartrecruiters.com.evil.example`, and
#: `"arbeitsagentur.de" in host` is true for `arbeitsagentur.de.phish.tld`. A
#: scraped apply_url is attacker-influenced, so a lookalike host could steer the
#: run into an adapter that then posts the operator's headers and cookies to it
#: as though it were a trusted ATS API.
_ADAPTER_HOSTS: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    (
        "greenhouse",
        frozenset(
            {
                "boards.greenhouse.io",
                "boards-api.greenhouse.io",
                "job-boards.greenhouse.io",
                "api.greenhouse.io",
            }
        ),
        ("greenhouse.io",),
    ),
    ("lever", frozenset({"jobs.lever.co", "api.lever.co"}), ("lever.co",)),
    (
        "smartrecruiters",
        frozenset({"jobs.smartrecruiters.com", "api.smartrecruiters.com", "careers.smartrecruiters.com"}),
        ("smartrecruiters.com",),
    ),
    ("personio", frozenset(), ("jobs.personio.com", "jobs.personio.de")),
    ("ashby", frozenset({"jobs.ashbyhq.com", "api.ashbyhq.com"}), ("ashbyhq.com",)),
    ("recruitee", frozenset(), ("recruitee.com",)),
    ("workable", frozenset({"apply.workable.com", "www.workable.com"}), ("workable.com",)),
    ("workday", frozenset(), ("myworkdayjobs.com", "myworkdaysite.com")),
    (
        "arbeitsagentur",
        frozenset({"arbeitsagentur.de", "www.arbeitsagentur.de", "rest.arbeitsagentur.de"}),
        ("arbeitsagentur.de",),
    ),
    ("jobs.ch", frozenset({"jobs.ch", "www.jobs.ch"}), ("jobs.ch",)),
    ("finn.no", frozenset({"finn.no", "www.finn.no"}), ("finn.no",)),
    ("nav.no", frozenset({"arbeidsplassen.nav.no", "nav.no", "www.nav.no"}), ("nav.no",)),
    ("karrierestart.no", frozenset({"karrierestart.no", "www.karrierestart.no"}), ("karrierestart.no",)),
    ("jobbsafari.no", frozenset({"jobbsafari.no", "www.jobbsafari.no"}), ("jobbsafari.no",)),
)


def _detect_adapter(url: str) -> str:
    """The adapter for a URL's host, matched on labels rather than substrings."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "generic"
    if not host:
        return "generic"
    for name, exact, suffixes in _ADAPTER_HOSTS:
        if host in exact:
            return name
        if any(host.endswith("." + suffix) for suffix in suffixes):
            return name
    return "generic"
