/**
 * The extension and the Python CLI must canonicalise a URL identically.
 *
 * Both halves write into the same `id` column, so a disagreement does not
 * raise — it silently produces two rows for one posting. This file and
 * tests/test_canonical_url_parity.py assert the *same* fixture, which is the
 * only arrangement in which the two implementations cannot drift apart.
 *
 * Seven of these vectors used to diverge, all on European URLs: this side ran
 * the string through `new URL().toString()`, which punycodes IDN hosts and
 * percent-encodes accented path bytes, and Python's urlparse did neither.
 */
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, "canonical-vectors.json"), "utf8"));

const outDir = mkdtempSync(join(tmpdir(), "jh-canon-"));
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

describe("canonicalizeUrl matches the shared canonical form", async () => {
  const { canonicalizeUrl } = await load("lib/canonicalUrl.ts");

  for (const vector of fixture.vectors) {
    it(vector.why, () => {
      assert.equal(canonicalizeUrl(vector.in), vector.out);
    });
  }

  it("is idempotent, so an id cannot depend on how often a URL was processed", () => {
    for (const vector of fixture.vectors) {
      const once = canonicalizeUrl(vector.in);
      assert.equal(canonicalizeUrl(once), once, vector.why);
    }
  });

  it("does not reach for the WHATWG serialiser, which is what caused the drift", () => {
    const source = readFileSync(join(here, "..", "app", "src", "lib", "canonicalUrl.ts"), "utf8");
    // Comments are stripped first: the file explains at length *why* it must
    // not call `new URL()`, and naming the thing is not using it.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    assert.ok(
      !/new URL\(/.test(code),
      "new URL().toString() punycodes hosts and percent-encodes paths; Python does neither",
    );
    assert.ok(
      !/URLSearchParams/.test(code),
      "URLSearchParams re-encodes what it keeps: + becomes %2B, %20 becomes +",
    );
  });
});
