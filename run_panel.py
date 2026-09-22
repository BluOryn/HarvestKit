#!/usr/bin/env python3
"""Open the HarvestKit control panel.

This is the entry point for anyone who should never have to open a terminal.
Double-clicking `HarvestKit.bat` (Windows) or `harvestkit.command` (macOS) runs
exactly this, which starts a small local web server and opens the browser at it.

    python run_panel.py                 # opens http://127.0.0.1:8787/
    python run_panel.py --port 9000
    python run_panel.py --no-browser

It listens on 127.0.0.1 unless told otherwise: the panel can start runs on this
machine, so exposing it to a network is a deliberate act, not a default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from harvestkit_ui.server import serve  # noqa: E402  — after the path is set up


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the HarvestKit control panel.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on. Anything other than 127.0.0.1 makes the panel — and "
        "therefore the ability to start runs on this machine — reachable from the network.",
    )
    parser.add_argument("--port", type=int, default=8787, help="port (a free one is used if taken)")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window automatically"
    )
    args = parser.parse_args(argv)

    serve(ROOT, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
