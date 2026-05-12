import re
from typing import Dict
from urllib.parse import urlparse

from .arbeitsagentur import ArbeitsagenturAdapter
from .ashby import AshbyAdapter
from .base import BaseAdapter
from .finn import FinnNoAdapter
from .generic import GenericAdapter
from .greenhouse import GreenhouseAdapter
from .jobsch import JobsChAdapter
from .lever import LeverAdapter
from .nav import NavNoAdapter
from .personio import PersonioAdapter
from .recruitee import RecruiteeAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workable import WorkableAdapter
from .workday import WorkdayAdapter
from ..config import TargetConfig


ADAPTERS: Dict[str, BaseAdapter] = {
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
    "generic": GenericAdapter(),
}


def get_adapter(target: TargetConfig) -> BaseAdapter:
    adapter_name = (target.adapter or "auto").lower()
    if adapter_name == "auto":
        adapter_name = _detect_adapter(target.url)
    return ADAPTERS.get(adapter_name, GenericAdapter())


def _detect_adapter(url: str) -> str:
    host = urlparse(url).netloc.lower()
    lower = url.lower()
    if "boards.greenhouse.io" in host or "boards-api.greenhouse.io" in host:
        return "greenhouse"
    if host == "jobs.lever.co" or host.endswith(".lever.co"):
        return "lever"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if host.endswith(".jobs.personio.com") or host.endswith(".jobs.personio.de"):
        return "personio"
    if "jobs.ashbyhq.com" in host or host.endswith(".ashbyhq.com"):
        return "ashby"
    if host.endswith(".recruitee.com"):
        return "recruitee"
    if host.endswith(".workable.com") or "apply.workable.com" in host:
        return "workable"
    if re.search(r"\.wd\d+\.myworkdayjobs\.com$", host) or "myworkdayjobs.com" in host:
        return "workday"
    if "arbeitsagentur.de" in host or "rest.arbeitsagentur" in host:
        return "arbeitsagentur"
    if host == "www.jobs.ch" or host == "jobs.ch" or host.endswith(".jobs.ch"):
        return "jobs.ch"
    if host == "www.finn.no" or host == "finn.no" or host.endswith(".finn.no"):
        return "finn.no"
    if "arbeidsplassen.nav.no" in host or host == "arbeidsplassen.nav.no":
        return "nav.no"
    return "generic"
