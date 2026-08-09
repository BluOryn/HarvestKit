"""Lead-list entrypoint.

Usage:
  python run_leads.py --config configs/leads/eu-it.yaml --target 1000
  python run_leads.py --config configs/leads/eu-it.yaml --target 10 --max-pages 3   # smoke
  python run_leads.py --config configs/leads/eu-it.yaml --select-only               # re-cut

Bootstraps src/ onto sys.path so the package runs without being installed,
matching run.py.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from leadgen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
