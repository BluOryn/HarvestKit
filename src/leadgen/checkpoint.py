"""SQLite run state so an interrupted run resumes instead of restarting.

A full run is hours long. Losing it to a dropped connection at hour three is not
acceptable, and this is also the foundation the scheduled version will need for
its cross-run ledger.
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

    def seen_company(self, domain: str) -> bool:
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM companies WHERE domain = ?", (domain,)).fetchone()
        return row is not None

    def record_company(self, domain: str) -> None:
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO companies (domain) VALUES (?)", (domain,))
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

    def close(self) -> None:
        with self._lock:
            self.conn.close()
