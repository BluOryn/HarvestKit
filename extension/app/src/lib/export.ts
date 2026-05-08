import { JOB_FIELDS, type Job } from "./schema";
import { GENERAL_FIELDS, type GeneralRecord } from "./generalSchema";

const esc = (v: any) => {
  if (v == null) return "";
  if (Array.isArray(v)) v = v.join(", ");
  return `"${String(v).replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
};

export function toCSV(jobs: Job[]): string {
  const cols = ["id", ...JOB_FIELDS, "starred", "tags", "notes", "saved_at"];
  const lines = [cols.join(",")];
  for (const j of jobs) lines.push(cols.map((c) => esc((j as any)[c])).join(","));
  return "\uFEFF" + lines.join("\r\n");
}

export function toGeneralCSV(records: GeneralRecord[]): string {
  const cols = ["id", ...GENERAL_FIELDS, "saved_at"];
  const lines = [cols.join(",")];
  for (const r of records) lines.push(cols.map((c) => esc((r as any)[c])).join(","));
  return "\uFEFF" + lines.join("\r\n");
}

export function toNDJSON(items: any[]): string {
  return items.map((j) => JSON.stringify(j)).join("\n");
}

export async function downloadBlob(filename: string, blob: Blob) {
  // Convert blob to data URL — blob URLs produce UUID filenames in side panels
  const reader = new FileReader();
  const dataUrl = await new Promise<string>((resolve) => {
    reader.onloadend = () => resolve(reader.result as string);
    reader.readAsDataURL(blob);
  });
  await chrome.downloads.download({ url: dataUrl, filename });
}
