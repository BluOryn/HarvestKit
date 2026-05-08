import csv
import os
from typing import List

from ..config import CsvExportConfig
from ..models import CSV_COLUMNS, JobListing


def export_csv(jobs: List[JobListing], cfg: CsvExportConfig) -> None:
    os.makedirs(os.path.dirname(cfg.path) or ".", exist_ok=True)
    with open(cfg.path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for job in jobs:
            writer.writerow(job.to_dict())
