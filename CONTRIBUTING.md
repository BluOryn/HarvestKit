# Contributing to HarvestKit

Glad you're here. This guide covers everything you need to land a PR.

## TL;DR
1. Fork → branch → code → `pytest` + `ruff check` → push → PR
2. New site? Open a "[adapter]" issue first so we can agree on the approach
3. Don't bypass paywalls / login walls / robots when sites prohibit it

---

## Local setup

```bash
git clone https://github.com/<you>/HarvestKit.git
cd HarvestKit
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev,llm,exports]"
python -m playwright install chromium
```

Extension:
```bash
cd extension
npm install
node build.mjs --watch
```

## Code style

| Tool | Purpose | Command |
| --- | --- | --- |
| `ruff` | Lint + import sort | `ruff check src/` |
| `black` | Formatter | `black src/` |
| `mypy` | Optional typing (advisory) | `mypy src/job_scraper/` |
| `pytest` | Tests | `pytest -q` |

Run all checks: `make check` (see Makefile).

## Adding a new adapter

1. Open an issue using the **New site adapter** template
2. Probe the site live and confirm one of:
   - JSON-LD JobPosting → use `generic` adapter, no code needed
   - Public API → write a dedicated adapter (see `src/job_scraper/adapters/greenhouse.py` as a model)
   - HTML labels → use the universal extractor's site-specific hook (see `_nav_no_specific` / `_karrierestart_specific` / `_jobbsafari_specific` in `universal.py`)
   - SPA → enable Playwright and add the host to the re-fetch list in `deep_scrape.py`
3. Add a config snippet to `config.example.yaml` or a dedicated file
4. Smoke run + paste field-fill stats in the PR

## Schema changes

The 60-field `JobListing` schema is shared with the extension. If you add a field:
1. Update `src/job_scraper/models.py` (JobListing + CSV_COLUMNS)
2. Update `extension/app/src/content/extractor.ts` mirror
3. Bump version + add CHANGELOG entry
4. Note in PR that this is a schema-breaking change

For anything that doesn't fit the schema, use `JobListing.set_extra(key, value)` — it serializes to the `extras_json` column.

## Testing

- `pytest -q` runs the test suite (target: <2 min)
- Mark slow / external tests: `@pytest.mark.integration`
- Mark browser tests: `@pytest.mark.playwright`
- Coverage threshold: aim for 60%+ on touched modules
- Don't commit cached HTTP responses (`.cache/`) or output CSVs

## Commit messages

Single line, lowercase, action-first:
```
karrierestart deadline extraction
NAV adData parser (expires/employer/locationList)
fix: jobbsafari pagination param (page= not side=)
```

Body optional — use it for why, not what. The diff shows what.

## Security

See [SECURITY.md](SECURITY.md). Don't open public issues for security bugs.

## Compliance

Read [README.md → Compliance](README.md#compliance) before scraping anything not yours.
We will not accept PRs that:
- Bypass authentication / captcha / WAF
- Scrape sites with explicit Terms-of-Service prohibitions
- Add bot-detection-evasion techniques (fingerprint spoofing beyond UA rotation)
- Use Selenium / Playwright to defeat rate limits

## License

By contributing you agree your code is released under the [MIT License](LICENSE).
