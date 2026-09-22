"""Inspect a delivered lead CSV before it goes to anyone.

Usage:
    python tools/verify_leads.py output/leads.csv

Exits non-zero when a hard guarantee is broken, so it can gate a delivery.
The soft numbers (status mix, country spread, role split) are printed for a
human to judge — they are quality signals, not pass/fail.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys

HARD_REQUIREMENTS = ("person_name", "person_email", "company_name", "company_country")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a lead CSV.")
    parser.add_argument("path")
    parser.add_argument("--expect", type=int, default=0, help="row count the run promised")
    parser.add_argument(
        "--min-rows",
        type=int,
        default=1,
        help="fail below this many rows. Defaults to 1: a zero-row delivery passes every "
        "per-row check there is, and is the exact file a bot-walled machine produces",
    )
    args = parser.parse_args(argv)

    with open(args.path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    failures: list[str] = []

    print(f"rows: {len(rows)}")
    # Every other check here is a per-row aggregation, so an empty file passes
    # all of them: 0 missing fields, 0 duplicates, 0 role accounts. The daily
    # scripts treat exit 0 as "the file is good" and hand it over. A zero-row
    # delivery is the one output that must never pass a gate whose entire
    # purpose is to stop bad files reaching a buyer.
    if len(rows) < max(0, args.min_rows):
        print()
        print("FAILED:")
        print(f"  - {len(rows)} rows, fewer than the {args.min_rows} required")
        print(
            "    A run that harvested nothing writes this. Check the `reachability:` line "
            "in the run log and try `python tools/check_egress.py`."
        )
        return 1
    if args.expect and len(rows) != args.expect:
        failures.append(f"expected {args.expect} rows, found {len(rows)}")

    for field in HARD_REQUIREMENTS:
        missing = sum(1 for row in rows if not (row.get(field) or "").strip())
        print(f"missing {field}: {missing}")
        if missing:
            failures.append(f"{missing} rows missing {field}")

    emails = [(row.get("person_email") or "").strip().lower() for row in rows]
    duplicates = len(emails) - len(set(emails))
    print(f"duplicate emails: {duplicates}")
    if duplicates:
        failures.append(f"{duplicates} duplicate email addresses")

    # A role account in a person's email column is the single most visible
    # quality failure to whoever works the list.
    role_stems = {"info", "jobs", "hr", "karriere", "contact", "kontakt", "office", "sales", "support"}
    role_hits = [e for e in emails if e.partition("@")[0].split(".")[0] in role_stems]
    print(f"role accounts in person_email: {len(role_hits)}")
    if role_hits:
        failures.append(f"{len(role_hits)} role accounts, e.g. {role_hits[:3]}")

    untraceable = sum(1 for row in rows if not (row.get("evidence_json") or "").strip())
    print(f"rows with no evidence: {untraceable}")
    if untraceable:
        failures.append(f"{untraceable} rows have no source URL")

    bad_evidence = 0
    for row in rows:
        raw = (row.get("evidence_json") or "").strip()
        if not raw:
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            bad_evidence += 1
    if bad_evidence:
        failures.append(f"{bad_evidence} rows have unparseable evidence_json")

    def show(label: str, field: str, limit: int = 12) -> None:
        counts = collections.Counter((row.get(field) or "—") for row in rows)
        total = sum(counts.values()) or 1
        print(f"\n{label}:")
        for value, count in counts.most_common(limit):
            print(f"  {value:<28} {count:>5}  {100 * count / total:5.1f}%")

    show("email status", "email_status")
    show("role family", "person_role_family")
    show("country", "company_country")

    top_country = collections.Counter(row.get("company_country") or "—" for row in rows).most_common(1)
    if rows and top_country and top_country[0][1] / len(rows) > 0.60:
        print(
            f"\nNOTE: {top_country[0][0]} is {100 * top_country[0][1] / len(rows):.0f}% of the list. "
            "Not a failure, but worth knowing before you hand it over."
        )

    print()
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All hard guarantees hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
