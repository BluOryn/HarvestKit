from typing import List

from ..config import GSheetsExportConfig
from ..models import CSV_COLUMNS, JobListing


def export_gsheets(jobs: List[JobListing], cfg: GSheetsExportConfig) -> None:
    if not cfg.service_account_json or not cfg.spreadsheet_id:
        return
    import gspread
    from gspread.exceptions import WorksheetNotFound
    client = gspread.service_account(filename=cfg.service_account_json)
    spreadsheet = client.open_by_key(cfg.spreadsheet_id)
    try:
        worksheet = spreadsheet.worksheet(cfg.worksheet)
    except WorksheetNotFound:  # noqa: F821 — imported lazily above
        worksheet = spreadsheet.add_worksheet(title=cfg.worksheet, rows="100", cols="20")

    existing = worksheet.get_all_values()
    if not existing:
        worksheet.append_row(CSV_COLUMNS, value_input_option="RAW")

    rows = [job.to_row() for job in jobs]
    if rows:
        worksheet.append_rows(rows, value_input_option="RAW")
