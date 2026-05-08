import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";

const html = `
<!doctype html>
<html lang="en">
<head>
<title>Senior Backend Engineer (Python) — Acme GmbH</title>
<meta property="og:title" content="Senior Backend Engineer (Python)">
<meta property="og:description" content="Build distributed systems at Acme.">
<meta property="og:site_name" content="Acme GmbH">
<script type="application/ld+json">
{
  "@context":"https://schema.org/",
  "@type":"JobPosting",
  "title":"Senior Backend Engineer (Python)",
  "description":"<p>Lead the platform team. Mentor engineers. Ship K8s services on AWS.</p>",
  "datePosted":"2026-04-01",
  "validThrough":"2026-06-01",
  "employmentType":"FULL_TIME",
  "hiringOrganization":{"@type":"Organization","name":"Acme GmbH","sameAs":"https://acme.example"},
  "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Berlin","addressCountry":"DE","postalCode":"10115"}},
  "baseSalary":{"@type":"MonetaryAmount","currency":"EUR","value":{"@type":"QuantitativeValue","minValue":85000,"maxValue":110000,"unitText":"YEAR"}},
  "identifier":{"@type":"PropertyValue","name":"req-id","value":"REQ-12345"}
}
</script>
</head>
<body>
<h1>Senior Backend Engineer (Python)</h1>
<p>Build distributed systems at Acme. Equity included. Visa sponsorship available.</p>
<h2>Responsibilities</h2><ul><li>Design APIs in Python and Go.</li><li>Operate Kubernetes clusters on AWS.</li></ul>
<h2>Requirements</h2><ul><li>5+ years Python.</li><li>Experience with PostgreSQL, Kafka, React.</li></ul>
<h2>Benefits</h2><ul><li>Relocation support to Berlin.</li><li>Stock options.</li></ul>
<aside>
  <h3>Your Talent Partner</h3>
  <img alt="Lena Schmidt" />
  <p>Lena Schmidt — Senior Talent Acquisition Partner</p>
  <a href="mailto:lena.schmidt@acme.example">lena.schmidt@acme.example</a>
  <a href="tel:+49301234567">+49 30 1234567</a>
  <a href="https://www.linkedin.com/in/lena-schmidt">LinkedIn</a>
</aside>
<a id="apply" href="https://acme.example/apply/REQ-12345">Apply now</a>
</body>
</html>`;

const dom = new JSDOM(html, { url: "https://acme.example/jobs/senior-backend-engineer" });
global.window = dom.window;
global.document = dom.window.document;
global.location = dom.window.location;
global.HTMLElement = dom.window.HTMLElement;

// Eval the bundle. It's IIFE that runs `run()` and accesses chrome.runtime — stub it.
global.chrome = {
  runtime: {
    sendMessage: () => Promise.resolve({}),
    onMessage: { addListener: () => {} },
  },
  storage: { local: { get: () => Promise.resolve({}) } },
};

import { extract } from "../src/content/extractor.js";
const r = extract();
console.log(JSON.stringify(r, null, 2));
