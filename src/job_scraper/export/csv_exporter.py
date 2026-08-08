import csv
import logging
import os
import tempfile
from contextlib import suppress

from ..config import CsvExportConfig
from ..models import CSV_COLUMNS, JobListing


def export_csv(jobs: list[JobListing], cfg: CsvExportConfig) -> None:
    """Write the canonical CSV.

    Writes to a temp file in the destination directory and renames on success,
    so a crash mid-write cannot leave a truncated CSV where a previous good run's
    output used to be.
    """
    directory = os.path.dirname(cfg.path) or "."
    os.makedirs(directory, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        dir=directory,
        prefix=".harvestkit-",
        suffix=".csv.tmp",
        delete=False,
    )
    tmp_path = handle.name
    try:
        with handle:
            # extrasaction="ignore" keeps an unexpected key from aborting the run.
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for job in jobs:
                writer.writerow(job.to_dict())
        os.replace(tmp_path, cfg.path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise

    logging.info("csv: wrote %d rows → %s", len(jobs), cfg.path)
