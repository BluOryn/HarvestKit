#!/usr/bin/env bash
# One day's lead harvest. Run this, get a dated CSV of people nobody has been sent
# before. See scripts/daily.ps1 for the same thing on Windows.
#
# Everything about a daily operation lives in two decisions, and both are made here
# so the operator never has to remember them:
#
#   * ONE checkpoint, forever. It is the memory. `companies` stops today spending
#     hours re-crawling employers yesterday already did; `delivered` stops today's
#     file being yesterday's file with a new date on it. Deleting it means starting
#     over and re-sending everyone.
#
#   * ONE dated output per day. Nothing is overwritten, so a bad morning can be
#     inspected instead of guessed at.
#
# Safe to re-run. If it dies at hour two, run it again -- the checkpoint resumes,
# and nothing is marked delivered until the CSV actually exists on disk.
#
# Usage:
#   ./scripts/daily.sh
#   TARGET=400 PAGES=65 ./scripts/daily.sh
#   ROLES=any TARGET=3000 ./scripts/daily.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Which market to harvest.
#   ch     - jobs.ch. One board, one country, and the richest of the two: its
#            postings carry the employer's own website 83% of the time, which
#            skips the slowest and most lossy step in the pipeline.
#   europe - cross-platform job search plus the German federal board. Wider
#            reach, thinner per-posting data, and slower per lead.
REGION="${REGION:-ch}"
case "$REGION" in ch|europe) ;; *) echo "REGION must be ch or europe, got: $REGION"; exit 1 ;; esac
# Rows wanted today. The run stops short and says so rather than padding.
TARGET="${TARGET:-300}"
# jobs.ch pages to walk. The shipped filter currently matches ~1,400 jobs = ~65 pages.
PAGES="${PAGES:-65}"
# "hr,tech_leadership,executive" for decision makers, "any" for every named person.
ROLES="${ROLES:-hr,tech_leadership,executive}"
# ISO codes, "eu" for the EU-27, or "europe" to add the UK, Switzerland, Norway
# and Iceland. Empty means whatever the region defaults to.
if [ "$REGION" = "europe" ]; then COUNTRIES="${COUNTRIES:-europe}"; else COUNTRIES="${COUNTRIES:-CH}"; fi
# REGION=europe only: result pages per (country, keyword) pair. Every extra page
# multiplies out across both lists, so this is the volume dial there.
SEARCH_PAGES="${SEARCH_PAGES:-3}"
# REGION=europe only: CITIES=1 searches 96 named cities instead of ~30 country
# names. Each query is capped server-side, so "Berlin" reaches employers a
# "Germany" query never returns -- at roughly three times the requests.
CITIES="${CITIES:-0}"
# Revisit an employer this many days after it was last crawled. Staff change.
RECRAWL_AFTER="${RECRAWL_AFTER:-30}"
# Skip mailbox probing. Required wherever outbound port 25 is blocked, which is
# most home and office networks and every major cloud provider. Set NO_SMTP=0 on
# a host with outbound 25 to get real verified/catch_all statuses instead.
NO_SMTP="${NO_SMTP:-1}"
# Read the commercial register for companies that name nobody. Needs ZEFIX_USER
# and ZEFIX_PASSWORD; without them the run warns and carries on.
REGISTER="${REGISTER:-0}"
# The jobs.ch search whose filter defines the sector and recency window. Build the
# search you want in a browser and paste the address bar here. Empty = shipped IT filter.
JOBSCH_URL="${JOBSCH_URL:-}"
# How old a posting may be, in days. 0 = decide from the checkpoint: 30 on the first
# run to sweep the whole board, 3 afterwards. Measured against the live board:
# 1 day = 25 postings, 3 = 185, 7 = 478, 14 = 787, 30 = 1396.
DAYS="${DAYS:-0}"
TERM_QUERY="${TERM_QUERY:-}"
# 106 IT/Telecom, 146 Engineering/Technical, 156 Management/Consulting, 167 Electronics.
CATEGORIES="${CATEGORIES:-106,146,156,167}"

TODAY="$(date +%F)"
mkdir -p output logs .cache

# One checkpoint per region. Sharing one would let a Swiss run mark a German
# employer as already crawled, and the two seeds reach different companies.
CHECKPOINT="$ROOT/.cache/daily-$REGION.sqlite"
# The region belongs in the name. Both regions used to write "leads-<date>.csv",
# so running europe after ch silently replaced the morning's Swiss file.
OUTPUT="$ROOT/output/leads-$REGION-$TODAY.csv"
LOGFILE="$ROOT/logs/run-$REGION-$TODAY.log"

PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

# A fresh checkpoint has never seen an employer, so the first run should sweep the
# whole board. Every run after that only needs what appeared since.
# A fresh checkpoint has never seen an employer, so the first run sweeps the whole
# board; every run after that only needs what appeared since. The recency window
# exists only in the jobs.ch filter, so the Europe path neither sets nor reports it.
WINDOW_NOTE=""
if [ "$REGION" = "ch" ]; then
  if [ "$DAYS" -le 0 ] 2>/dev/null; then
    if [ -f "$CHECKPOINT" ]; then DAYS=3; else DAYS=30; fi
    WINDOW_NOTE="last $DAYS days (auto)"
  else
    WINDOW_NOTE="last $DAYS days"
  fi
fi

args=(
  run_leads.py
  --countries "$COUNTRIES"
  --roles "$ROLES"
  --target "$TARGET"
  # No early stop. The default (target x 3 *email-bearing* leads, counted across
  # every role) trips long before enough target-role people accumulate, and the
  # crawl quits before reaching the companies that had them.
  --overfetch 0
  --recrawl-after "$RECRAWL_AFTER"

  --only-new
  --checkpoint "$CHECKPOINT"
  --output "$OUTPUT"
)
if [ "$REGION" = "ch" ]; then
  args+=(--config configs/leads/swiss-it.yaml
         --jobsch-pages "$PAGES"
         --jobsch-days "$DAYS"
         --jobsch-categories "$CATEGORIES")
  [ -n "$TERM_QUERY" ] && args+=(--jobsch-term "$TERM_QUERY")
else
  # The Europe seed is geography-first: it asks "who is hiring in Sweden?" rather
  # than filtering a global list down afterwards. The keyword and location lists
  # are the targeting, and both ship in configs/leads/.
  args+=(--config configs/leads/eu-it.yaml
         --search-keywords configs/leads/keywords.txt
         --search-keywords-multilingual configs/leads/keywords-multilingual.txt
         --search-max-pages "$SEARCH_PAGES"
         # This host blocked a whole run after roughly 1500 requests, and a seed
         # that gets itself blocked is worth less than a slower one that does not.
         --search-delay 0.4
         --arbeitnow-pages 20)
  [ "$CITIES" = "1" ] && args+=(--search-locations configs/leads/locations-eu.txt)
fi
[ -n "$JOBSCH_URL" ] && args+=(--jobsch-url "$JOBSCH_URL")
[ "$NO_SMTP" = "1" ]  && args+=(--no-smtp)
[ "$REGISTER" = "1" ] && args+=(--register)

echo "HarvestKit daily run - $TODAY"
echo "  region     : $REGION ($COUNTRIES)"
[ -n "$WINDOW_NOTE" ] && echo "  window     : $WINDOW_NOTE"
echo "  checkpoint : $CHECKPOINT"
echo "  output     : $OUTPUT"
echo "  log        : $LOGFILE"
echo

set +e
"$PYTHON" "${args[@]}" 2>&1 | tee "$LOGFILE"
code="${PIPESTATUS[0]}"
set -e

echo
if [ "$code" = "0" ] || [ "$code" = "2" ]; then
  # Gate the file before anyone sees it. The verifier exits non-zero only when a
  # hard guarantee is broken (a row missing a name, an address, a company or a
  # country); everything else it prints is a quality signal for a human.
  echo "Checking the file..."
  # --min-rows 1 is the point of the gate. Every other check here is a per-row
  # aggregation, so a zero-row file passes all of them: 0 missing fields, 0
  # duplicates, 0 role accounts. A blocked machine produces exactly that file,
  # and the operator was told it was good.
  if ! "$PYTHON" tools/verify_leads.py "$OUTPUT" --min-rows 1; then
    echo
    echo "DO NOT SEND THIS FILE. It failed verification above."
    exit 3
  fi
fi

echo
case "$code" in
  0) echo "Done. $OUTPUT" ;;
  # Not a failure. The supply ran out before the target did, which on a daily run
  # is the normal state once the first full sweep is behind you.
  2) echo "Done, but short of $TARGET rows. See the SHORTFALL line above." ;;
  # 4 is the one exit code that must never be read as a thin market: the run
  # harvested *nothing*, which on a laptop means the network refused us
  # everywhere. Reporting it as a shortfall is how an operator spends a week
  # concluding Europe has no IT employers.
  4)
    echo "HARVEST FAILED: nothing was harvested at all."
    echo "That is almost always the network, not the market. Check:"
    echo "  $PYTHON tools/doctor.py"
    echo "  $PYTHON tools/proxy_sources.py --print-setup"
    ;;
  *) echo "Run failed (exit $code). Full log: $LOGFILE" ;;
esac
exit "$code"
