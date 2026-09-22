#!/usr/bin/env bash
# Set HarvestKit up on a macOS or Linux machine.
#
# The companion to scripts/install.ps1, and the same promise: after this, the
# whole tool is one command — ./harvestkit — that opens a page in the browser.
# Nobody has to know what a virtualenv is.
#
#   ./scripts/install.sh
#   ./scripts/install.sh --skip-browser      # no stealth rung, saves ~150 MB
#   ./scripts/install.sh --skip-check
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_BROWSER=0
SKIP_CHECK=0
for argument in "$@"; do
    case "$argument" in
        --skip-browser) SKIP_BROWSER=1 ;;
        --skip-check)   SKIP_CHECK=1 ;;
        *) echo "unknown option: $argument" >&2; exit 2 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
die()  { printf '\n!!! %s\n' "$1" >&2; exit 1; }

printf '\n  HarvestKit installer\n'
printf '  ----------------------------------------\n'

# ------------------------------------------------------------------ python
step "Looking for Python 3.10 or newer"
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    # Unlike Windows there is no single safe unattended installer to fall back
    # on, and silently installing a system Python is not this script's business.
    cat <<'MESSAGE'
    No Python 3.10+ found.

      macOS   brew install python@3.12
              (or install it from https://www.python.org/downloads/)
      Debian  sudo apt install python3 python3-venv
      Fedora  sudo dnf install python3

    Then run this script again.
MESSAGE
    exit 1
fi
ok "Found $PYTHON"

# -------------------------------------------------------------- virtualenv
step "Setting up the project's own Python environment"
if [ ! -x ".venv/bin/python" ]; then
    "$PYTHON" -m venv .venv || die "Could not create .venv (on Debian you may need python3-venv)."
    ok "Created .venv"
else
    ok "Already there."
fi
VENV_PYTHON="$ROOT/.venv/bin/python"

# ------------------------------------------------------------ dependencies
step "Installing the dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r requirements.txt --quiet || die "Dependency install failed."
ok "Done."

step "Installing the stealth browser fork (optional but recommended)"
if "$VENV_PYTHON" -m pip install patchright --quiet; then ok "Installed."
else ok "patchright would not install — the first two transport rungs still work."; fi

if [ "$SKIP_BROWSER" -eq 0 ]; then
    step "Downloading Chromium for the stealth rung (about 150 MB, once)"
    "$VENV_PYTHON" -m patchright install chromium \
        || "$VENV_PYTHON" -m playwright install chromium \
        || ok "The browser did not install. Everything except the last rung still works."
fi

# --------------------------------------------------------------- launcher
step "Making the launcher"
cat > harvestkit <<'LAUNCHER'
#!/usr/bin/env bash
# Opens the HarvestKit control panel in your browser.
cd "$(dirname "$0")"
exec .venv/bin/python run_panel.py "$@"
LAUNCHER
chmod +x harvestkit
ok "Wrote ./harvestkit"

if [ "$(uname -s)" = "Darwin" ]; then
    # A .command file opens in Terminal when double-clicked from Finder, which
    # is the macOS equivalent of the Windows shortcut.
    cat > HarvestKit.command <<'LAUNCHER'
#!/usr/bin/env bash
cd "$(dirname "$0")"
exec .venv/bin/python run_panel.py
LAUNCHER
    chmod +x HarvestKit.command
    ok "Wrote HarvestKit.command — double-click it from Finder."
fi

# ------------------------------------------------------------------ proof
if [ "$SKIP_CHECK" -eq 0 ]; then
    step "Checking this machine can actually read European websites"
    printf '    (The check that matters. "pip install worked" and "this laptop can\n'
    printf '     scrape" are different claims.)\n'
    "$VENV_PYTHON" tools/check_egress.py || true
fi

printf '\n  Done.\n'
printf '  Start it with:  ./harvestkit\n\n'
