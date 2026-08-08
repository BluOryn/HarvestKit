import logging

from ..config import ExportsConfig
from ..models import JobListing
from .csv_exporter import export_csv
from .gsheets_exporter import export_gsheets
from .notion_exporter import export_notion
from .slack_exporter import export_slack

__all__ = ["export_csv", "export_gsheets", "export_notion", "export_slack", "run_exports"]


def run_exports(jobs: list[JobListing], exports: ExportsConfig) -> None:
    """Run every enabled exporter.

    One failing destination must not lose the others: a Notion token that
    expired should still leave you with the CSV. CSV runs first for that reason.
    """
    targets = (
        ("csv", exports.csv, export_csv),
        ("gsheets", exports.gsheets, export_gsheets),
        ("notion", exports.notion, export_notion),
        ("slack", exports.slack, export_slack),
    )
    for name, cfg, run in targets:
        if not cfg.enabled:
            continue
        try:
            run(jobs, cfg)
        except Exception as exc:
            logging.error(
                "export %s failed: %s", name, exc, exc_info=logging.getLogger().isEnabledFor(logging.DEBUG)
            )
