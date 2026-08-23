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

# Rows wanted today. The run stops short and says so rather than padding.
TARGET="${TARGET:-300}"
# jobs.ch pages to walk. The shipped filter currently matches ~1,400 jobs = ~65 pages.
PAGES="${PAGES:-65}"
# "hr,tech_leadership,executive" for decision makers, "any" for every named person.
ROLES="${ROLES:-hr,tech_leadership,executive}"
COUNTRIES="${COUNTRIES:-CH}"
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

TODAY="$(date +%F)"
mkdir -p output logs .cache

CHECKPOINT="$ROOT/.cache/daily.sqlite"
OUTPUT="$ROOT/output/leads-$TODAY.csv"
LOGFILE="$ROOT/logs/run-$TODAY.log"

PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

args=(
  run_leads.py
  --config configs/leads/swiss-it.yaml
  --jobsch-pages "$PAGES"
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
[ -n "$JOBSCH_URL" ] && args+=(--jobsch-url "$JOBSCH_URL")
[ "$NO_SMTP" = "1" ]  && args+=(--no-smtp)
[ "$REGISTER" = "1" ] && args+=(--register)

echo "HarvestKit daily run - $TODAY"
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
  if ! "$PYTHON" tools/verify_leads.py "$OUTPUT"; then
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
  *) echo "Run failed (exit $code). Full log: $LOGFILE" ;;
esac
exit "$code"
