# Onboarding — running the daily lead harvest

You will run one command a day and get one CSV a day. Everyone in it is a real,
named person at a real Swiss company, and nobody in today's file was in
yesterday's.

This document is the whole job. If something in it is wrong, the document is
wrong — say so rather than working around it.

---

## Day one: setup (about 10 minutes)

### 1. Install Python 3.10 or newer

- **Windows** — [python.org/downloads](https://www.python.org/downloads/), and
  **tick "Add python.exe to PATH"** on the first screen. It is easy to miss and
  nothing works without it.
- **macOS** — `brew install python@3.12`
- **Linux** — `sudo apt install python3.12 python3.12-venv`

### 2. Clone and set up

```bash
git clone https://github.com/BluOryn/HarvestKit.git
cd HarvestKit
```

**Windows (PowerShell):**

```powershell
.\scripts\setup.ps1
```

**macOS / Linux:**

```bash
./scripts/setup.sh
```

That builds the virtualenv, installs everything, checks whether this machine can
verify email addresses, and then **harvests ten real leads to prove it works**.
It takes about five minutes and ends with either `Setup complete.` or a specific
error. There is no ambiguous middle.

If PowerShell refuses to run the script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Note what setup told you about port 25

Setup prints one of two lines:

| It said | What you do |
|---|---|
| `open` | Nothing. Addresses get properly verified. |
| `blocked` | Always pass `-NoSmtp` (Windows) / leave `NO_SMTP=1` (default elsewhere). |

Port 25 is blocked on virtually every home network, most offices, and all of
AWS/GCP/Azure. Blocked is the normal answer. It does not stop the run; it means
addresses are inferred from the company's observed format rather than confirmed
against the mail server, and the CSV says which is which in `email_status`.

---

## Every day after: one command

**Windows:**

```powershell
.\scripts\daily.ps1                    # Switzerland
.\scripts\daily.ps1 -Region europe     # the rest of Europe
```

**macOS / Linux:**

```bash
NO_SMTP=0 ./scripts/daily.sh
NO_SMTP=0 REGION=europe ./scripts/daily.sh
```

The two regions keep separate checkpoints (`.cache/daily-ch.sqlite` and
`.cache/daily-europe.sqlite`), so running both is fine and neither can mark the
other's employers as already crawled.

Add `-NoSmtp` (Windows) or drop `NO_SMTP=0` (elsewhere) **only if setup told you
port 25 was blocked.** It costs you the `verified` status on every row.

That is the entire daily job. It writes:

```
output/leads-ch-2026-08-23.csv     <- today's leads, ready to send
logs/run-ch-2026-08-23.log         <- the full log if you need to look
```

The script checks its own output before it finishes. If the file breaks a hard
guarantee — a row with no name, no address, no company, or no country — it says
**DO NOT SEND THIS FILE** and exits non-zero. If it does not say that, the file
is good.

### The first run is the long one

Day one crawls every employer on the board. After that the checkpoint remembers
them, so each day only spends time on companies that appeared since. That is why
day two is fast, and why it is honest: those really are new companies.

**Start day one in the morning and leave it.** If it dies, run the same command
again — it resumes where it stopped and nothing is double-sent.

### How many leads to actually expect

Measured throughput, at the default concurrency of 8:

| | Companies/min | Target-role rows per company | All roles per company |
|---|---|---|---|
| `ch` | ~4 | 1.65 | 19.4 |
| `europe` | ~4 | 1.22 | 6.9 |

Swiss companies yield far more people each, because Swiss and German law puts an
Impressum on every site. That works out to roughly **400 target-role rows an
hour** in `ch` and **290 an hour** in `europe`.

Day one, `-Region ch`, full 65-page sweep: **~1,000 target-role rows** over 3–5
hours. That figure is extrapolated from 188 rows measured across 12 pages, not
from a full sweep.

**Day two onward is where expectations usually go wrong.** The checkpoint is
doing its job, so supply is capped by what is genuinely new:

```
jobs.ch publishes ~25 new IT postings a day
  -> ~11 new companies a day
  -> ~18 target-role leads a day
```

Eighteen. Not a hundred. If you need 100+ a day, one of these has to change:

| Do this | Steady state per day | Costs |
|---|---|---|
| `-Roles any` | **~210** | Nothing. Same crawl, wider cut. |
| `-Region europe` | Much higher — the EU pool is roughly thirty times Switzerland | 1–2 hours of run time |
| `-RecrawlAfter 14` | Recycles employers twice as often, catching new hires | More requests |

The usual answer is to run both regions every morning. They keep separate
checkpoints and separate files, so nothing overlaps:

```powershell
.\scripts\daily.ps1 -Region ch -Roles any -Target 200
.\scripts\daily.ps1 -Region europe -Target 150
```

---

## The one rule

**Never delete `.cache/daily.sqlite`.**

It is the memory of the whole operation. Two things live in it:

- **which companies have been crawled** — delete it and tomorrow spends five
  hours re-crawling employers it already did
- **which people have already been sent** — delete it and tomorrow's file is a
  reheat of every lead you have ever delivered

Everything else in the repo can be thrown away and rebuilt. Not this. Back it up
if you back up anything.

---

## Reading the CSV

26 columns. These are the ones that decide whether a row is worth contacting.

| Column | What it means |
|---|---|
| `person_name`, `person_role` | Who, and their title in their own words |
| `person_role_family` | `executive`, `hr`, `tech_leadership` — the bucket the title was sorted into |
| `person_email` | The address |
| `email_status` | **How much to trust that address.** See below |
| `company_name`, `company_domain`, `company_city` | Where they work |
| `source_person_url` | The page the name was read off. Open it if a row looks wrong |
| `evidence` | Why the pipeline believed each field |

### `email_status`, best to worst

| Status | Meaning |
|---|---|
| `published` | The address was printed on the company's own site. Send with confidence. |
| `verified` | The domain has a working mail server and is **not** a catch-all, so a wrong address there would bounce rather than be silently accepted. It does not prove this particular mailbox exists — nothing short of sending does. |
| `inferred_high` / `inferred_medium` | Built from a pattern seen on that company's *own* published addresses. |
| `inferred_low` | Built from the common `first.last@` shape with no confirmation. Expect bounces. |
| `catch_all` | The domain accepts everything, so delivery proves nothing. |
| `unknown` | Nothing could be established. |

Running with `-NoSmtp` means you will never see `verified` or `catch_all`, and
most rows land at `inferred_low`. Measured on the same handful of leads: with
port 25 open every row came back `verified`; with `-NoSmtp` the same rows were
`inferred_low`. Use `-NoSmtp` only when you have to.

Whatever the status, send a first batch small and watch the bounce rate before
scaling up.

---

## Getting more out of it

### More rows today

```powershell
.\scripts\daily.ps1 -Target 600
```

If it prints `SHORTFALL`, that is not a bug — the board genuinely did not have
that many new target-role people today. It never pads the file to hit a number.

### Every named person, not just decision makers

The default keeps HR, C-suite and tech leadership. That throws away roughly
**ten times** as many people as it keeps. To take everyone:

```powershell
.\scripts\daily.ps1 -Roles any -Target 3000
```

Measured: 12 pages of the board banked 2,266 people, of whom 188 were in a
target family. If your outreach can use a Head of Sales or a project lead, this
is where the volume is.

### Companies that publish no staff at all

About one company in five names nobody anywhere on its own website — Victorinox,
FREITAG, Kistler. Consumer brands do not publish staff. Their boards are on the
public record instead, and `-Register` reads it:

```powershell
$env:ZEFIX_USER = "your-account"
$env:ZEFIX_PASSWORD = "your-password"
.\scripts\daily.ps1 -Register
```

Register free at
[zefix.admin.ch](https://www.zefix.admin.ch/en/search/entity/welcome). Without
both variables the flag warns and carries on — it never silently falls back to
scraping a site that disallows it. Register rows carry no email addresses of
their own, and their `source_person_url` is the attribution the data licence
requires, so keep that column.

### Every parameter

`.\scripts\daily.ps1 -?` prints these too. Bash uses the same names as
environment variables: `TARGET=600 DAYS=7 ./scripts/daily.sh`.

| Parameter | Bash env | Default | What it does |
|---|---|---|---|
| `-Region` | `REGION` | `ch` | `ch` = jobs.ch. `europe` = cross-platform search across the EU-27 + UK/CH/NO/IS. |
| `-Target` | `TARGET` | `300` | Rows wanted. Stops short and says `SHORTFALL` rather than padding. |
| `-Days` | `DAYS` | `0` (auto) | **`-Region ch` only. How old a posting may be.** `0` picks 30 on a fresh checkpoint and 3 afterwards. |
| `-Pages` | `PAGES` | `65` | jobs.ch result pages to walk, 22 postings each. 65 covers a full 30-day window. |
| `-Term` | `TERM_QUERY` | *(empty)* | Free-text keyword. Empty means the whole sector. |
| `-Categories` | `CATEGORIES` | `106,146,156,167` | jobs.ch sector ids — see below. |
| `-Roles` | `ROLES` | `hr,tech_leadership,executive` | Which people to keep. `any` keeps everyone. |
| `-Countries` | `COUNTRIES` | region default | ISO codes. `eu` = EU-27, `europe` adds UK/CH/NO/IS. |
| `-SearchPages` | `SEARCH_PAGES` | `3` | **`-Region europe` only.** Pages per keyword × location pair. |
| `-Cities` | `CITIES=1` | off | **`-Region europe` only.** 96 cities instead of ~30 countries. |
| `-RecrawlAfter` | `RECRAWL_AFTER` | `30` | Days before an already-crawled employer is visited again. |
| `-JobschUrl` | `JOBSCH_URL` | *(empty)* | A complete search URL. **Overrides `-Days`, `-Term` and `-Categories` entirely.** |
| `-NoSmtp` | `NO_SMTP=1` | off | Skip mailbox checks. Only if setup said port 25 is blocked. |
| `-Register` | `REGISTER=1` | off | Read the commercial register for companies naming nobody. |

### How old the jobs are — `-Days`

This is the parameter that decides how long the run takes. Measured live against
the shipped sector filter:

| `-Days` | Postings | Pages needed |
|---|---|---|
| 1 | 25 | 1 |
| 3 | 185 | 9 |
| 7 | 478 | 22 |
| 14 | 787 | 36 |
| 30 | 1,396 | 64 |
| 60 | 1,542 | 70 |

Leave it at `0`. The script sweeps 30 days the first time and 3 days every
morning after, and prints which it chose:

```
window     : last 3 days (auto)
```

Asking for 30 days every morning is not wrong, just wasteful — it re-fetches
thirteen hundred postings to rediscover employers crawled yesterday. Override it
if the run was skipped for a week: `-Days 14`.

### The sectors — `-Categories`

| Id | Sector |
|---|---|
| `106` | Information technology / Telecom |
| `146` | Engineering / Technical |
| `156` | Management / Consulting |
| `167` | Electronics / Electrotechnics |

`-Categories 106` narrows to pure IT. A typo falls back to the shipped four
rather than silently widening to the whole board.

Employment types are fixed at permanent and fixed-term staff contracts.
Apprenticeships and internships are deliberately excluded: no budget, no hiring
authority.

### Europe instead of Switzerland — `-Region europe`

Two genuinely different machines, not one with a wider filter:

| | `-Region ch` | `-Region europe` |
|---|---|---|
| Source | jobs.ch, one board | Cross-platform job search + Arbeitnow |
| Targeting | The board's own sector filter | Keyword × location, decided before crawling |
| Employer website in the posting | 83% | Workable supplies it; SmartRecruiters does not |
| Countries | CH | EU-27 plus UK, CH, NO, IS |
| Per-lead cost | Lower | Higher — more companies need domain resolution |

Measured on a deliberately small Europe sweep — 3 countries × 3 keywords, one
page each:

```
201 postings -> 93 companies -> 81 with a resolved own-domain
             -> 53 naming a person -> 568 people, 99 in a target role
```

The delivered sample came out 55% `verified`, 20% `published`, spread across
NL/DE/SE, with all three role families present. Widening the keyword and
location lists scales that close to linearly.

Volume dials, in order of effect:

| | What it does |
|---|---|
| `-SearchPages` (default 3) | Result pages per keyword × location pair. Multiplies out across both lists. |
| `-Cities` | Searches 96 named cities instead of ~30 country names. Each query is capped server-side, so "Berlin" reaches employers a "Germany" query never returns — at roughly three times the requests. |
| `-Countries` | Narrow it: `-Countries "DE,NL,SE"` is far faster than all of Europe and often enough. |

**The first Europe run is long.** It pairs every keyword with every country
before it crawls anything. Start narrow — `-Countries "DE,NL"` — confirm the
file looks right, then widen.

### One thing that does not work, and why

`configs/leads/eu-it.yaml` used to declare five Bundesagentur für Arbeit
searches — the largest job board in Germany. **All five returned zero
listings, silently.** The API host answers `robots.txt` with HTTP 403, which
RFC 9309 defines as the whole host being off-limits, so the client correctly
refuses every request.

That is a permission boundary, not an outage: the Bundesagentur publishes the
API to registered users. The dead targets have been removed rather than left to
look like they were doing something, and the adapter now says which of the two
it hit:

```
arbeitsagentur: robots.txt on this host disallows the API, so no listings will
be returned. This is a permission boundary, not an outage.
```

If you get API access, re-add the targets and set `obey_robots: false` **for
that run only**.

### A different sector or country

The whole targeting lives in one URL — the jobs.ch filter. Build the search you
want on jobs.ch in a browser, copy the address bar, and pass it:

```powershell
.\scripts\daily.ps1 -JobschUrl "https://www.jobs.ch/en/vacancies/?category=..."
```

```bash
JOBSCH_URL="https://www.jobs.ch/en/vacancies/?category=..." ./scripts/daily.sh
```

The shipped default is four IT categories, four employment types, last 30 days.
See [RUNBOOK-leads.md](RUNBOOK-leads.md) for the category ids.

---

## When something looks wrong

| Symptom | What it is |
|---|---|
| `WARNING Retrying ... No connection could be made` | A dead or firewalled company site. One company skipped, run unaffected. Ignore. |
| `SHORTFALL: N rows short` | Supply, not failure. Normal on day two onward. |
| `DO NOT SEND THIS FILE` | Real. Do not send it. Send the log. |
| Day two produced 0 rows | Nothing new on the board since yesterday. Check the `already_done` count in the log. |
| Every address is `inferred_low` | Port 25 is blocked, or you passed `-NoSmtp` when you did not need to. |
| A name looks like a job title or a menu item | A real bug. Send the row and its `source_person_url`. |

Inspect the memory at any time:

```bash
python tools/inspect_checkpoint.py .cache/daily.sqlite
```

Re-check any delivered file:

```bash
python tools/verify_leads.py output/leads-ch-2026-08-23.csv
```

---

## What this does not do

- **It does not send anything.** It produces a CSV. Sending is your side.
- **It does not confirm a person still works there.** It reports what the
  company's own site or the public register says today.
- **It is not a LinkedIn scraper.** Every name comes from a page the company
  published or a legally-required public filing.
- **`inferred_*` addresses are educated guesses.** They are labelled as such.
  Treat a batch of them as a test, not a campaign.

Whoever receives the list should read the compliance note at the end of
[RUNBOOK-leads.md](RUNBOOK-leads.md) before the first send.
