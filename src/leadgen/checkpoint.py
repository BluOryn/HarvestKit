"""SQLite run state so an interrupted run resumes instead of restarting.

A full run is hours long. Losing it to a dropped connection at hour three is not
acceptable.

It is also the cross-run ledger a daily operation needs. Two tables carry that:
`companies` records who has already been crawled, so tomorrow only spends
requests on employers that appeared since; `delivered` records which leads have
already been handed over, so tomorrow's file contains people the buyer has not
seen. Without the second one a daily run re-exports its whole history every
morning, and "100 fresh leads" is the same 100 leads with a new date on them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import LEAD_FIELDS, Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    domain  TEXT PRIMARY KEY,
    seen_at REAL DEFAULT (julianday('now'))
);
CREATE TABLE IF NOT EXISTS leads (
    id      TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivered (
    id           TEXT PRIMARY KEY,
    delivered_at REAL DEFAULT (julianday('now')),
    batch        TEXT DEFAULT ''
);
"""


class Checkpoint:
    """Thread-safe: the pipeline writes from a worker pool."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._lock = threading.Lock()

    def seen_company(self, domain: str, max_age_days: float | None = None) -> bool:
        """Has this company been crawled, and recently enough to skip again?

        `max_age_days` lets a long-running daily operation revisit employers
        eventually. Staff change; a company crawled in January may name three
        new directors by June, and treating "crawled once" as "crawled forever"
        means never finding them.
        """
        query = "SELECT julianday('now') - seen_at FROM companies WHERE domain = ?"
        with self._lock:
            row = self.conn.execute(query, (domain,)).fetchone()
        if row is None:
            return False
        if max_age_days is None:
            return True
        return float(row[0] or 0.0) < max_age_days

    def record_company(self, domain: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO companies (domain) VALUES (?) "
                "ON CONFLICT(domain) DO UPDATE SET seen_at = julianday('now')",
                (domain,),
            )
            self.conn.commit()

    def company_count(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0])

    def save_leads(self, leads: list[Lead]) -> None:
        if not leads:
            return
        rows = [
            (
                lead.fingerprint(),
                json.dumps(
                    {name: getattr(lead, name) for name in LEAD_FIELDS} | {"evidence": lead.evidence},
                    ensure_ascii=False,
                ),
            )
            for lead in leads
        ]
        with self._lock:
            self.conn.executemany("INSERT OR REPLACE INTO leads (id, payload) VALUES (?, ?)", rows)
            self.conn.commit()

    def all_leads(self) -> list[Lead]:
        with self._lock:
            payloads = [row[0] for row in self.conn.execute("SELECT payload FROM leads")]
        leads: list[Lead] = []
        for payload in payloads:
            data = json.loads(payload)
            evidence = data.pop("evidence", {})
            lead = Lead(**{name: data.get(name, "") for name in LEAD_FIELDS})
            lead.evidence = evidence
            leads.append(lead)
        return leads

    def delivered_ids(self) -> set[str]:
        """Fingerprints of every lead already handed over in an earlier run."""
        with self._lock:
            return {row[0] for row in self.conn.execute("SELECT id FROM delivered")}

    def mark_delivered(self, leads: list[Lead], batch: str = "") -> None:
        """Record that these leads went out, so tomorrow does not resend them.

        Written after the CSV exists, never before: a run that dies during the
        write must be repeatable, and a lead marked delivered but never
        delivered is one the buyer never receives at all.
        """
        if not leads:
            return
        rows = [(lead.fingerprint(), batch) for lead in leads]
        with self._lock:
            self.conn.executemany("INSERT OR IGNORE INTO delivered (id, batch) VALUES (?, ?)", rows)
            self.conn.commit()

    def delivered_count(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM delivered").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self.conn.close()
