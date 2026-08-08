"""One extracted person, before it is merged into a Lead."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersonHit:
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    source_url: str = ""
    strategy: str = ""
