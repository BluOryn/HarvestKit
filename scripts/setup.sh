#!/usr/bin/env bash
# First-time setup. Run once after cloning; never again.
#
# Creates the virtualenv, installs the dependencies, and then proves the install
# actually works by harvesting a handful of real leads. That last step is the
# point: "pip install succeeded" and "this machine can scrape" are different
# claims, and only the second one matters tomorrow morning.
#
# Usage:
#   ./scripts/setup.sh
#   SKIP_SMOKE_TEST=1 ./scripts/setup.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }

step "Checking Python"
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "Python is not on PATH. Install 3.10 or newer."; exit 1; }
VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python $VERSION found; 3.10 or newer is required."; exit 1; }
ok "Python $VERSION"

step "Creating the virtualenv (.venv)"
if [ -d .venv ]; then ok "already exists, reusing it"; else "$PY" -m venv .venv; ok "created"; fi
PYTHON="$ROOT/.venv/bin/python"

step "Installing dependencies"
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r requirements.txt --quiet
ok "installed"

step "Checking the install"
"$PYTHON" - <<'PYEOF'
import sys
sys.path.insert(0, 'src')
import requests, bs4, lxml, yaml, dns.resolver  # noqa: F401
from leadgen.cli import build_parser
from job_scraper.config import load_config, resolve_config_path
load_config(resolve_config_path('configs/leads/swiss-it.yaml'))
build_parser().parse_args(['--config', 'x'])
print('    imports, config and CLI all load')
PYEOF
ok "ready"

step "Checking outbound port 25 (mailbox verification)"
# Every home network, most offices and every major cloud provider block outbound
# 25. Knowing which side of that line this machine sits on decides whether the
# daily run needs NO_SMTP=1, and finding out now beats finding out at hour three.
if "$PYTHON" -c "import socket; socket.create_connection(('gmail-smtp-in.l.google.com', 25), 5).close()" 2>/dev/null; then
  SMTP_OPEN=1
  ok "open — set NO_SMTP=0 and addresses get verified properly"
else
  SMTP_OPEN=0
  ok "blocked — leave NO_SMTP=1 (the default). Addresses will be inferred, not verified."
fi

if [ "${SKIP_SMOKE_TEST:-0}" != "1" ]; then
  step "Proving it works (real run, ~5 minutes)"
  echo "    Harvesting 2 pages of jobs.ch for a handful of real leads."
  args=(run_leads.py --config configs/leads/swiss-it.yaml
        --jobsch-pages 2 --countries CH --target 10 --overfetch 0
        --checkpoint .cache/setup-check.sqlite --output output/setup-check.csv)
  [ "$SMTP_OPEN" = "0" ] && args+=(--no-smtp)
  "$PYTHON" "${args[@]}" 2>&1 | grep -v "WARNING Retrying" | tail -12
  [ -f output/setup-check.csv ] || { echo "Smoke run produced no file. Setup is not complete."; exit 1; }
  "$PYTHON" tools/verify_leads.py output/setup-check.csv
  ok "real leads harvested and verified"
fi

printf '\nSetup complete.\n'
echo "Run the harvest with:  ./scripts/daily.sh"
echo "Read docs/ONBOARDING.md for what the numbers mean."
