from typing import List

from ..config import ExportsConfig
from ..models import JobListing
from .csv_exporter import export_csv
from .gsheets_exporter import export_gsheets
from .notion_exporter import export_notion
from .slack_exporter import export_slack


def run_exports(jobs: List[JobListing], exports: ExportsConfig) -> None:
    if exports.csv.enabled:
        export_csv(jobs, exports.csv)
    if exports.gsheets.enabled:
        export_gsheets(jobs, exports.gsheets)
    if exports.notion.enabled:
        export_notion(jobs, exports.notion)
    if exports.slack.enabled:
        export_slack(jobs, exports.slack)
