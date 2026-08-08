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

function csvCell(value: unknown): string {
  if (value == null) return '""';
  const flat = Array.isArray(value) ? value.filter(Boolean).join(", ") : String(value);
  // Escape quotes and flatten newlines so a description can't break the row.
  return `"${flat.replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
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
