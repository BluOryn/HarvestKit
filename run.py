"""Single entrypoint. Dispatches to the job scraper or the general scraper.

Usage:
  python run.py                              # job scraper, configs/example.yaml
  python run.py --config norway-big          # job scraper, configs/regions/norway-big.yaml
  python run.py --general                    # general scraper, configs/general.example.yaml
  python run.py --general --config my.yaml

Mode is chosen by `--general`, or automatically when the resolved config
declares `mode: general` at its top level.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _config_arg(argv: list) -> str:
    """Read --config out of argv without consuming it (argparse still needs it)."""
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return ""


def _is_general_mode(argv: list) -> bool:
    if "--general" in argv:
        argv.remove("--general")
        return True

    # Sniff the config for `mode: general` so users can flip modes via YAML alone.
    from general_scraper.config import is_general_config
    from job_scraper.config import resolve_config_path

    name = _config_arg(argv)
    if not name:
        return False
    try:
        return is_general_config(resolve_config_path(name))
    except (FileNotFoundError, OSError):
        # Let the real entrypoint report the missing/unreadable config.
        return False


def main() -> None:
    if _is_general_mode(sys.argv):
        from general_scraper.main import main as general_main

        general_main()
    else:
        from job_scraper.main import main as job_main

        job_main()


if __name__ == "__main__":
    main()
