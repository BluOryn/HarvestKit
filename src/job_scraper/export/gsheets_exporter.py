import logging

from ..config import GSheetsExportConfig
from ..models import CSV_COLUMNS, JobListing

# gspread's append_rows caps out well before this, but batching also keeps each
# request under the Sheets API's 10 MB payload limit.
BATCH_SIZE = 500


def export_gsheets(jobs: list[JobListing], cfg: GSheetsExportConfig) -> None:
    if not cfg.service_account_json or not cfg.spreadsheet_id:
        logging.warning("gsheets: export enabled but service_account_json/spreadsheet_id missing — skipping")
        return

    try:
        import gspread
        from gspread.exceptions import WorksheetNotFound
    except ImportError:
        logging.warning("gsheets: requires `pip install 'harvestkit[exports]'` — skipping")
        return

    try:
        client = gspread.service_account(filename=cfg.service_account_json)
        spreadsheet = client.open_by_key(cfg.spreadsheet_id)
    except Exception as exc:
        logging.warning("gsheets: could not open spreadsheet %s (%s)", cfg.spreadsheet_id, exc)
        return

    try:
        worksheet = spreadsheet.worksheet(cfg.worksheet)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=cfg.worksheet, rows=max(100, len(jobs) + 10), cols=len(CSV_COLUMNS)
        )

    try:
        # Only the header row is needed to decide whether to write one; pulling
        # every existing value just to test emptiness was O(sheet) per run.
        header = worksheet.row_values(1)
        if header != CSV_COLUMNS:
            if not header:
                worksheet.append_row(CSV_COLUMNS, value_input_option="RAW")
            else:
                logging.warning(
                    "gsheets: worksheet %r header does not match the current schema; "
                    "appending anyway — columns may not line up",
                    cfg.worksheet,
                )

        rows = [job.to_row() for job in jobs]
        for start in range(0, len(rows), BATCH_SIZE):
            worksheet.append_rows(rows[start : start + BATCH_SIZE], value_input_option="RAW")
    except Exception as exc:
        logging.warning("gsheets: write failed (%s)", exc)
        return

    logging.info("gsheets: appended %d rows to %s", len(jobs), cfg.worksheet)
