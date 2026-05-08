# JobHarvester Extension

Universal in-browser job scraper. Manifest V3. Detects any job posting on any website (JSON-LD, microdata, ATS-specific, heuristics) and extracts a comprehensive structured record including recruiter contact info.

## Architecture

```
manifest.json                    MV3 manifest, content scripts on <all_urls>
src/
  content/
    inject.js                    entry point (bundled to dist/content.bundle.js)
    detector.js                  is-this-a-job-page? confidence score
    extractor.js                 runs all strategies, merges output
    strategies/
      jsonld.js                  schema.org JobPosting from JSON-LD
      microdata.js               schema.org JobPosting via itemtype/itemprop
      opengraph.js               OG + meta + canonical
      heuristics.js              regex (salary, email, phone, tech stack, sections)
      recruiter.js               recruiter/contact block extractor
    adapters/                    ATS-specific DOM selectors
      greenhouse.js
      lever.js
      ashby.js
      workday.js
      personio.js
      smartrecruiters.js
      linkedin.js
  background/
    service-worker.js            message router
    store.js                     IndexedDB store (jobs)
    bulk.js                      hidden-tab crawl with concurrency
  popup/
    popup.html / popup.css / popup.js
  lib/
    schema.js                    canonical job fields + merge
    utils.js                     helpers
public/icons/                    16/48/128
dist/content.bundle.js           generated
```

## Build

```bash
cd extension
npm install
npm run build      # or: npm run watch
```

## Install (Chrome / Edge / Brave)

1. Open `chrome://extensions`
2. Toggle **Developer mode** (top right).
3. **Load unpacked** → select the `extension` directory.
4. Visit any job page. A small banner offers to save it. Or click the toolbar icon → **Scrape this page**.

## Features

- **Auto-detect** on every page (JSON-LD JobPosting, microdata, ATS embeds, URL hints, text signals).
- **Extracts**: title, company, location (city/region/country/postal_code), remote_type, employment_type, seniority, salary (min/max/currency/period), equity, posted_date, valid_through, description, responsibilities, requirements, benefits, tech_stack, skills, education, experience_years, visa_sponsorship, relocation, recruiter_name/title/email/phone/linkedin, hiring_manager, application_email/phone, apply_url, job_url, requisition_id, source_ats, …
- **Bulk crawl mode**: paste a list of URLs in the popup; the background opens hidden tabs, extracts, closes — concurrency-limited.
- **Storage**: IndexedDB. Survives browser restarts.
- **Export**: CSV or JSON via popup.

## Add a new ATS

1. Create `src/content/adapters/<name>.js` with `isMatch()` + `extract(root)`.
2. Add it to `adapters/index.js`.
3. Run `npm run build` again.
