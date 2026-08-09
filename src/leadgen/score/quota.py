"""Turn a pile of candidate leads into exactly N, or say why it could not.

The engine over-fetches deliberately, so this is where the run's promise is
kept. Padding a short list with leads that fail the criteria would poison the
buyer's trust in the rows that *are* good, so a shortfall is reported and the
smaller file written.

Selection is round-robin across countries rather than a hard percentage cap.
A cap of "25% of target" is unreachable whenever fewer than four countries have
leads, and the obvious fallback — fill the remainder from the highest scores —
hands the whole list straight back to the country the cap was meant to limit.
Round-robin spreads as evenly as the supply allows at every list size, and the
cap then only has to stop one country running away when the supply is lopsided.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import Lead
from .completeness import score_lead

_WS_RX = re.compile(r"\s+")


@dataclass
class QuotaReport:
    requested: int = 0
    candidates: int = 0
    selected: int = 0
    shortfall: int = 0
    dropped: Counter = field(default_factory=Counter)
    by_country: Counter = field(default_factory=Counter)
    by_status: Counter = field(default_factory=Counter)
    by_role_family: Counter = field(default_factory=Counter)

    def summary(self) -> str:
        lines = [f"requested {self.requested} · candidates {self.candidates} · selected {self.selected}"]
        if self.shortfall:
            lines.append(f"SHORTFALL {self.shortfall} — not enough qualifying leads were found")
        if self.dropped:
            lines.append("dropped: " + ", ".join(f"{k}={v}" for k, v in self.dropped.most_common()))
        if self.by_status:
            lines.append("email status: " + ", ".join(f"{k}={v}" for k, v in self.by_status.most_common()))
        if self.by_role_family:
            lines.append("roles: " + ", ".join(f"{k}={v}" for k, v in self.by_role_family.most_common()))
        if self.by_country:
            lines.append("countries: " + ", ".join(f"{k}={v}" for k, v in self.by_country.most_common()))
        return "\n".join(lines)


def _person_key(lead: Lead) -> str:
    """Secondary dedupe key: the same human found by two strategies."""
    name = _WS_RX.sub(" ", (lead.person_name or "").strip().lower())
    return f"{name}@@{(lead.company_domain or '').strip().lower()}"


def _country_of(lead: Lead) -> str:
    return (lead.company_country or "??").upper()


def select(
    leads: list[Lead],
    *,
    target: int,
    country_ceiling: float = 0.25,
    role_families: frozenset[str] | None = None,
) -> tuple[list[Lead], QuotaReport]:
    """Cut to exactly `target`, or report how far short the supply fell.

    `role_families` restricts the list to the roles the brief actually asked
    for. Without it a run happily delivers a thousand marketing managers, which
    satisfies the row count and nothing else.
    """
    report = QuotaReport(requested=target, candidates=len(leads))

    qualified: list[Lead] = []
    seen_ids: set[str] = set()
    seen_people: set[str] = set()
    for lead in leads:
        if role_families is not None and lead.person_role_family not in role_families:
            report.dropped["wrong_role"] += 1
            continue
        if not (lead.person_email or "").strip():
            report.dropped["no_email"] += 1
            continue
        if not (lead.person_name or "").strip():
            report.dropped["no_name"] += 1
            continue
        fingerprint, person = lead.fingerprint(), _person_key(lead)
        if fingerprint in seen_ids or person in seen_people:
            report.dropped["duplicate"] += 1
            continue
        seen_ids.add(fingerprint)
        seen_people.add(person)
        qualified.append(lead)

    # Bucket by country, best first inside each bucket.
    buckets: dict[str, list[tuple[float, Lead]]] = {}
    for lead in qualified:
        buckets.setdefault(_country_of(lead), []).append((score_lead(lead), lead))
    for bucket in buckets.values():
        bucket.sort(key=lambda pair: pair[0], reverse=True)

    cap = max(1, int(target * country_ceiling))
    cursors = dict.fromkeys(buckets, 0)
    per_country: Counter = Counter()
    selected: list[Lead] = []

    while len(selected) < target:
        available = [country for country in buckets if cursors[country] < len(buckets[country])]
        if not available:
            break
        under_cap = [country for country in available if per_country[country] < cap]
        # The cap is a spread preference, not a reason to under-deliver: when
        # every country with supply left is already at the cap, it relaxes.
        pool = under_cap or available
        pick = min(
            pool,
            key=lambda country: (per_country[country], -buckets[country][cursors[country]][0]),
        )
        selected.append(buckets[pick][cursors[pick]][1])
        cursors[pick] += 1
        per_country[pick] += 1

    report.selected = len(selected)
    report.shortfall = max(0, target - len(selected))
    report.by_country = per_country
    report.by_status = Counter(lead.email_status or "none" for lead in selected)
    report.by_role_family = Counter(lead.person_role_family or "none" for lead in selected)
    return selected, report
