# Changelog

Notable changes to HarvestKit. No tagged releases yet — everything below lives on `main`.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Will adopt [SemVer](https://semver.org/spec/v2.0.0.html) starting at the first tag.

## [Unreleased]

### Added
- NAV `__next_f` adData parser. Pulls `expires`, `employer.{name,sector,homepage}`, `locationList[]`, `workLanguages`, `reference`, `engagementType` from NAV's Next.js streaming payload — covers fields the HTML `<dl>` doesn't expose.
- jobbsafari.no dedicated adapter parsing `__NEXT_DATA__` `props.pageProps.jobEntry`.
- thehub.io detail-page handling: title-prefix stripping ("The Hub | <Title> | <Company>") and "Remote" location synthesis when `jobLocationType: TELECOMMUTE`.
- LLM-fallback adapter (Anthropic Haiku) with per-host SQLite selector cache and monthly USD budget cap.
- Playwright wiring at adapter + deep-scrape layers. Per-target opt-in via `use_playwright: true`. karrierestart detail pages auto re-fetched via Playwright to surface JS-rendered contact blocks.
- Top-5 Norway config (`config.norway-top5.yaml`) with URL filter codes verified by live DOM probe.
- finn.no, NAV (arbeidsplassen.nav.no), karrierestart.no dedicated adapters.
- karrierestart deadline extraction via `.jobad-deadline-date` span.
- Norwegian seniority regex (seniorrådgiver, teamleder, direktør, praktikant) with title-based fallback.
- Norwegian-aware HR contact miner and name/role splitter.
- Per-host token bucket throttle, proxy rotation pool, per-proxy cookie jars, WAF/captcha detector.
- Universal smart-DOM extractor for sites without JSON-LD.
- General mode for Yelp-style business directories (LocalBusiness / Restaurant / Place schema).
- 10 ATS adapters: Greenhouse, Lever, Ashby, Workday, Personio, Recruitee, Workable, SmartRecruiters, Arbeitsagentur, jobs.ch.
- Deep-scrape pipeline: JSON-LD → universal smart-DOM → contact miner → optional LLM fallback.
- EN/DE/FR/IT/NO JD section parser.
- 60-field shared schema between Python CLI and Chrome MV3 extension.
- Chrome MV3 extension with side-panel UI, IndexedDB persistence, and bulk-crawl orchestrator.
- Playwright fallback for JS-heavy sites.
- `pyproject.toml` build config, GitHub Actions CI (lint + test + extension build + CodeQL), Dependabot, issue + PR templates, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, Dockerfile, Makefile.

### Changed
- `deep_scrape` pipeline reordered: always run JSON-LD → universal smart-DOM → contact miner → LLM fallback. Previously short-circuited after JSON-LD, which lost karrierestart's DOM-only fields.
- karrierestart company extraction prefers `.jobad_company_logo img[alt]` and title-prefix over `.company-desc <a>` (which often pointed to social links).
- `bad_label_rx` extended to reject site-nav junk words (Partnere, Annonsere, Nyheter, Profil, Studier, etc.) so the contact miner doesn't capture them as recruiter names.

### Fixed
- finn.no JSON-LD wrapper key (`script:ld+json`) unwrap for JobPosting blocks.
- jobbsafari pagination uses `?page=N`, not `?side=N`.
- Phone regex rejects 9-12 raw digit blobs (finnkode IDs) and date-like strings.

[Unreleased]: https://github.com/BluOryn/HarvestKit/commits/main
