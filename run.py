"""Single entrypoint. Pass `--general` (or set `mode: general` in config) to run the
general-purpose scraper instead of the job scraper.

Usage:
  python run.py                        # job scraper, default config.yaml
  python run.py --config foo.yaml      # job scraper, custom config
  python run.py --general              # general scraper, config.general.yaml
  python run.py --general --config x.yaml
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _is_general_mode() -> bool:
    if "--general" in sys.argv:
        sys.argv.remove("--general")
        return True
    # Sniff the config path for `mode: general` so users can flip via YAML alone.
    import yaml
    cfg_path = "config.yaml"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i + 1 < len(sys.argv):
            cfg_path = sys.argv[i + 1]
            break
        if arg.startswith("--config="):
            cfg_path = arg.split("=", 1)[1]
            break
    try:
        with open(cfg_path, "r", encoding="utf-8") as h:
            data = yaml.safe_load(h) or {}
        return (data.get("mode") or "").lower() == "general"
    except Exception:
        return False


if __name__ == "__main__":
    if _is_general_mode():
        from general_scraper.main import main as general_main
        general_main()
    else:
        from job_scraper.main import main
        main()
