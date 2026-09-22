"""CSV output. Atomic, so a crash never destroys a previous deliverable."""

from __future__ import annotations

import csv
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from job_scraper.csv_safe import safe_row

from .models import LEAD_CSV_COLUMNS, Lead


def write_csv(leads: list[Lead], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=destination.parent,
        prefix=".leadgen-",
        suffix=".csv",
        newline="",
        # BOM so Excel and Google Sheets read the UTF-8 accents correctly on
        # import, which matters for a list full of European names.
        encoding="utf-8-sig",
    )
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=LEAD_CSV_COLUMNS)
            writer.writeheader()
            for lead in leads:
                # Scraped text must not arrive in Sheets as a live formula.
                writer.writerow(safe_row(lead.to_dict()))
        os.replace(handle.name, destination)
    except BaseException:
        with suppress(OSError):
            os.unlink(handle.name)
        raise
