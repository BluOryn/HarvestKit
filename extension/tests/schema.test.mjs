/**
 * Tests the *shipped* code (app/src), not the removed v1 tree.
 *
 * The TS sources are bundled to a temporary ESM module with esbuild so these
 * run under `node --test` with no extra toolchain.
 */
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import { pathToFileURL } from "node:url";

import { build } from "esbuild";

const outDir = mkdtempSync(join(tmpdir(), "jh-test-"));
after(() => rmSync(outDir, { recursive: true, force: true }));

/** Point the content script's globals at `dom`. Both DOM suites below call
 *  this inside their own tests: `describe` bodies all run before any `it`, so
 *  installing a DOM at describe time let the last suite loaded win and the
 *  other suite then extracted from the wrong document. */
function installDom(dom) {
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.Element = dom.window.Element;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.Node = dom.window.Node;
  globalThis.URL = dom.window.URL;
}

async function load(entry) {
  const outfile = join(outDir, `${entry.replace(/\W/g, "_")}.mjs`);
  await build({
    entryPoints: [`app/src/${entry}`],
    outfile,
    bundle: true,
    format: "esm",
    platform: "neutral",
    target: ["node20"],
    logLevel: "silent",
  });
  return import(pathToFileURL(outfile).href);
}

describe("sha1", async () => {
  const { sha1Hex } = await load("lib/sha1.ts");

  it("matches known digests", () => {
    assert.equal(sha1Hex(""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
    assert.equal(sha1Hex("abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
    assert.equal(
      sha1Hex("The quick brown fox jumps over the lazy dog"),
      "2fd4e1c67a2d28fced849ee1bb76e7391b93eb12",
    );
  });

  it("hashes UTF-8 exactly like Python's .encode('utf-8')", () => {
    // hashlib.sha1("søk stillingen — Zürich".encode()).hexdigest()
    assert.equal(sha1Hex("søk stillingen — Zürich"), "f64c74248ff57cb9dd9b990f29ee29e431534ed0");
  });

  it("spans multiple 64-byte blocks correctly", () => {
    assert.equal(sha1Hex("a".repeat(1000)), "291e9a6c66994949b57ba5e650361e98fc36b1ba");
  });
});

describe("canonicalizeUrl", async () => {
  const { canonicalizeUrl } = await load("lib/canonicalUrl.ts");

  it("strips tracking params, fragments and trailing slashes", () => {
    assert.equal(canonicalizeUrl("https://x.test/job/1?utm_source=a&fbclid=b"), "https://x.test/job/1");
    assert.equal(canonicalizeUrl("https://x.test/job/1/#apply"), "https://x.test/job/1");
    assert.equal(canonicalizeUrl("https://x.test/s?q=dev&utm_medium=cpc"), "https://x.test/s?q=dev");
  });

  it("passes through empty and unparseable input", () => {
    assert.equal(canonicalizeUrl(""), "");
    assert.equal(canonicalizeUrl("not a url"), "not a url");
  });
});

describe("fingerprint parity with the Python engine", async () => {
  const { fingerprint } = await load("lib/schema.ts");
  const { fingerprintRecord } = await load("lib/generalSchema.ts");

  it("is a 40-char sha1 hex digest, not a raw joined string", () => {
    const id = fingerprint({ title: "Eng", company: "Acme", job_url: "https://x.test/1" });
    assert.match(id, /^[0-9a-f]{40}$/);
  });

  // Golden values produced by JobListing.fingerprint() in the Python engine.
  // If either side changes its algorithm these break — which is the point:
  // both write into the same `id` column.
  it("reproduces the Python engine's digests byte for byte", () => {
    assert.equal(
      fingerprint({
        title: "Senior Engineer",
        company: "Acme AS",
        location: "Oslo",
        job_url: "https://x.test/job/1",
      }),
      "ff4dee3f44dc634dfb08d0e5f3bb2af28279ab4c",
    );
    assert.equal(
      fingerprint({
        title: "Ingénieur",
        company: "Zürich AG",
        location: "Zürich",
        apply_url: "https://ats.test/a?utm_source=x",
      }),
      "3a3c7e0be16e54b79245cc277aae0a98a65bd525",
    );
    assert.equal(fingerprint({}), "d872125b37ff4934a597ebb96796f8cea644edc3");
  });

  it("collapses tracking-param variants to one id", () => {
    const a = fingerprint({ title: "Eng", company: "Acme", job_url: "https://x.test/job/1" });
    const b = fingerprint({
      title: "Eng",
      company: "Acme",
      job_url: "https://x.test/job/1?utm_source=news&fbclid=abc",
    });
    assert.equal(a, b);
  });

  it("prefers apply_url over job_url, matching Python", () => {
    const withApply = fingerprint({ apply_url: "https://ats.test/a", job_url: "https://x.test/1" });
    const applyOnly = fingerprint({ apply_url: "https://ats.test/a" });
    assert.equal(withApply, applyOnly);
  });

  it("produces distinct ids for distinct postings", () => {
    const a = fingerprint({ title: "Eng", company: "Acme", job_url: "https://x.test/1" });
    const b = fingerprint({ title: "Eng", company: "Acme", job_url: "https://x.test/2" });
    assert.notEqual(a, b);
  });

  it("hashes general records the same way", () => {
    const id = fingerprintRecord({ name: "Clinic", source_url: "https://x.test/biz/1" });
    assert.match(id, /^[0-9a-f]{40}$/);
  });
});

describe("csv export", async () => {
  const { toCSV, JOB_CSV_COLUMNS, toNDJSON } = await load("lib/export.ts");
  const { emptyJob } = await load("lib/schema.ts");

  it("emits the shared column layout", () => {
    const header = toCSV([]).replace(/^﻿/, "").split("\r\n")[0];
    assert.equal(header, JOB_CSV_COLUMNS.join(","));
    // Shared block must lead so CLI and extension CSVs concatenate.
    assert.equal(JOB_CSV_COLUMNS[0], "id");
    assert.ok(JOB_CSV_COLUMNS.includes("extras_json"));
  });

  it("escapes quotes and newlines", () => {
    const job = { ...emptyJob(), title: 'He said "hi"', description: "line1\nline2" };
    const row = toCSV([job]).split("\r\n")[1];
    assert.ok(row.includes('"He said ""hi"""'));
    assert.ok(!row.includes("\n"));
  });

  it("serialises NDJSON one object per line", () => {
    assert.equal(toNDJSON([{ a: 1 }, { b: 2 }]), '{"a":1}\n{"b":2}');
  });
});


describe("job extraction", async () => {
  const { JSDOM } = await import("jsdom");
  const { extract } = await load("content/extractor.ts");
  const dom = () =>
    new JSDOM(
      `<!doctype html><html><head>
     <title>Senior Backend Engineer — Acme GmbH</title>
     <link rel="canonical" href="https://acme.test/jobs/senior-backend"/>
     <script type="application/ld+json">
     {"@context":"https://schema.org","@type":"JobPosting",
      "title":"Senior Backend Engineer",
      "description":"<p>Build distributed systems at Acme, owning services end to end.</p>",
      "hiringOrganization":{"@type":"Organization","name":"Acme GmbH"},
      "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
        "addressLocality":"Berlin","addressCountry":"DE"}},
      "employmentType":"FULL_TIME","datePosted":"2026-05-01"}
     </script></head>
     <body><main><h1>Senior Backend Engineer</h1>
     <p>We use Python and Kubernetes. Apply now.</p>
     <a href="https://boards.greenhouse.io/acme/jobs/7">Apply now</a>
     </main></body></html>`,
      { url: "https://acme.test/jobs/senior-backend" },
    );

  it("pulls the core fields out of JSON-LD", () => {
    installDom(dom());
    const { job, detection } = extract();
    assert.ok(detection.isJob, "page should be detected as a job");
    assert.equal(job.title, "Senior Backend Engineer");
    assert.equal(job.company, "Acme GmbH");
    assert.equal(job.employment_type, "FULL_TIME");
    assert.equal(job.posted_date, "2026-05-01");
    assert.match(job.id, /^[0-9a-f]{40}$/);
  });

  it("prefers the external ATS as the apply URL", () => {
    installDom(dom());
    const { job } = extract();
    assert.match(job.apply_url, /greenhouse\.io/);
  });
});

describe("jobs.ch parity with the Python engine", async () => {
  const { JSDOM } = await import("jsdom");
  const { extract } = await load("content/extractor.ts");

  // Shaped like a live jobs.ch detail page: a full JSON-LD body, a nav bar
  // whose links include the word "Recruiter", and a "similar jobs" rail
  // advertising a *different* employer's role.
  const posting = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: "Senior Model-Based System Engineer",
    description:
      "<p>" +
      "At Belimo we build actuators and sensors for heating and ventilation. ".repeat(8) +
      "You will own the model-based systems engineering toolchain end to end.</p>",
    hiringOrganization: { "@type": "Organization", name: "BELIMO Automation AG" },
    applicantLocationRequirements: { "@type": "Country", name: "Switzerland" },
    jobLocation: {
      "@type": "Place",
      address: { "@type": "PostalAddress", addressRegion: "Hinwil", postalCode: "8340", addressCountry: "CH" },
    },
    occupationalCategory: { "@type": "CategoryCode", codeValue: "98", name: "Technical / Electronics" },
    industry: "Industry various",
    employmentType: "Permanent position",
  };
  const dom = () =>
    new JSDOM(
      `<!doctype html><html><head>
     <script type="application/ld+json">${JSON.stringify(posting)}</script></head>
     <body>
       <nav><a href="/1">Find a job</a><a href="/2">Explore companies</a><a href="/3">Salary estimator</a>
         <a href="/4">Recruiter</a><a href="/5">Area</a><a href="/6">Deutsch</a><a href="/7">Français</a>
         <a href="/8">English</a><a href="/9">Login</a><a href="/10">a</a><a href="/11">b</a>
         <a href="/12">c</a><a href="/13">d</a><a href="/14">e</a></nav>
       <main><h1>Senior Model-Based System Engineer</h1></main>
       <aside><h2>Similar jobs</h2>
         <p>DevSecOps Engineer (alle) — LEGIC Identsystems AG — Wallisellen — Homeoffice</p></aside>
     </body></html>`,
      { url: "https://www.jobs.ch/en/vacancies/detail/5b141452-d08d-4edc-964b-da7bfdb1df66/" },
    );

  it("does not call a job remote just because applicants must live in Switzerland", () => {
    installDom(dom());
    assert.equal(extract().job.remote_type, "");
  });

  it("reads a CategoryCode department as its name, not as [object Object]", () => {
    installDom(dom());
    const { job } = extract();
    assert.equal(job.department, "Technical / Electronics");
    assert.equal(job.company_industry, "Industry various");
  });

  it("does not mine a recruiter out of the site navigation", () => {
    installDom(dom());
    const { job } = extract();
    assert.equal(job.recruiter_name, "");
    assert.equal(job.recruiter_title, "");
  });

  it("does not attribute a neighbouring employer's tech stack to this posting", () => {
    installDom(dom());
    const { job } = extract();
    assert.ok(!/devsecops/i.test(job.tech_stack), `tech_stack leaked: ${job.tech_stack}`);
  });
});
