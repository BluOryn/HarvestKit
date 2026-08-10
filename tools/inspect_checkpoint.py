"""What is actually banked in a checkpoint, right now.

Reads without locking the writer out, so it can be run against a live harvest to
see whether the run is on course before it finishes. `--eu` applies the same
geography filter the cut will apply, so the number it prints is the number that
can actually be delivered — not a total that quietly includes out-of-scope rows.

    python tools/inspect_checkpoint.py .cache/final.sqlite --eu
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from leadgen.checkpoint import Checkpoint  # noqa: E402
from leadgen.geo import EU_COUNTRIES  # noqa: E402
from leadgen.person.name import looks_like_person_name  # noqa: E402
from leadgen.person.roles import TARGET_FAMILIES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a leadgen checkpoint.")
    parser.add_argument("path")
    parser.add_argument("--eu", action="store_true", help="count only EU-27 rows")
    args = parser.parse_args(argv)

    checkpoint = Checkpoint(args.path)
    try:
        leads = checkpoint.all_leads()
    finally:
        checkpoint.close()

    if args.eu:
        leads = [lead for lead in leads if (lead.company_country or "").upper() in EU_COUNTRIES]

    # Mirrors the gates the cut applies, so this number is the number that can
    # actually be delivered rather than an optimistic total.
    deliverable = [
        lead
        for lead in leads
        if lead.person_email
        and lead.person_role_family in TARGET_FAMILIES
        and looks_like_person_name(lead.person_name)
    ]

    print(f"leads banked:      {len(leads)}")
    print(f"with an email:     {sum(1 for lead in leads if lead.person_email)}")
    print(f"DELIVERABLE:       {len(deliverable)}  (named, emailed, in a target role)")
    print(f"companies crawled: {len(checkpoint_domains(leads))}")

    def show(label: str, counts: Counter, limit: int = 14) -> None:
        total = sum(counts.values()) or 1
        print(f"\n{label}")
        for value, count in counts.most_common(limit):
            print(f"  {value or '—':<26} {count:>5}  {100 * count / total:5.1f}%")

    show("role family (deliverable)", Counter(lead.person_role_family for lead in deliverable))
    show("email status (deliverable)", Counter(lead.email_status for lead in deliverable))
    show("country (deliverable)", Counter(lead.company_country for lead in deliverable))

    # Contacts named in a job ad rather than found on the company's own site.
    # They are the only reliable HR source, so their share is worth watching.
    from_ad = sum(1 for lead in deliverable if "Recruiting Contact" in (lead.person_role or ""))
    print(f"\nad-named recruiting contacts: {from_ad}")
    return 0


def checkpoint_domains(leads) -> set[str]:
    return {lead.company_domain for lead in leads if lead.company_domain}


if __name__ == "__main__":
    sys.exit(main())
