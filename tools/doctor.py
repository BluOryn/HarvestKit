#!/usr/bin/env python3
"""Check this machine, end to end, and say plainly what will not work.

`pip install succeeded` and `this laptop can produce leads` are different
claims, and only the second one matters tomorrow morning. This checks the
second, on the machine it is run on, against the live internet:

    python tools/doctor.py              # everything
    python tools/doctor.py --quick      # skip the network checks
    python tools/doctor.py --json       # machine-readable, for the panel

Every check reports one of three things, and the distinction is deliberate:

    OK    — verified working, here, now.
    WARN  — degraded. The run will work but produce less, and the line says why.
    FAIL  — the run will not produce leads until this is fixed.

Exit code is 0 unless something FAILed, so this can gate an install script.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> Check:
        check = Check(name, status, detail, fix)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if check.status == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for check in self.checks if check.status == WARN)


# --------------------------------------------------------------- the checks


def check_python(report: Report) -> None:
    version = sys.version_info
    if version >= (3, 10):
        report.add("Python", OK, f"{platform.python_version()} at {sys.executable}")
    else:
        report.add(
            "Python",
            FAIL,
            f"{platform.python_version()} is too old",
            "Install Python 3.10 or newer, then run scripts/install.ps1 again.",
        )


def check_dependencies(report: Report) -> None:
    required = {
        "requests": "HTTP",
        "bs4": "HTML parsing",
        "lxml": "HTML parsing",
        "yaml": "config files",
        "dns": "MX lookups",
        "tldextract": "domain parsing",
        "defusedxml": "safe XML",
    }
    optional = {
        "curl_cffi": (
            "the impersonation rung — the single highest-yield anti-blocking measure, "
            "worth 3 of 12 European domains in testing"
        ),
        "patchright": "the stealth browser rung (preferred over playwright)",
        "playwright": "the browser rung",
        "socks": "SOCKS5 proxies (socks5h:// entries)",
        "gspread": "writing results to Google Sheets",
    }
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        report.add(
            "Dependencies",
            FAIL,
            "missing: " + ", ".join(missing),
            f"{sys.executable} -m pip install -r requirements.txt",
        )
    else:
        report.add("Dependencies", OK, f"{len(required)} required packages present")

    for name, why in optional.items():
        if importlib.util.find_spec(name) is None:
            if name == "playwright" and importlib.util.find_spec("patchright") is not None:
                continue
            report.add(
                f"Optional: {name}",
                WARN,
                f"not installed — {why}",
                f"{sys.executable} -m pip install {name}",
            )
        else:
            report.add(f"Optional: {name}", OK, why)


def check_browser(report: Report) -> None:
    try:
        from job_scraper import browser as browser_module
    except Exception as exc:
        report.add("Stealth browser", WARN, f"could not be imported: {exc}")
        return
    driver = browser_module.driver_name()
    if not driver:
        report.add(
            "Stealth browser",
            WARN,
            "no driver installed — the last transport rung is off",
            f"{sys.executable} -m pip install patchright && "
            f"{sys.executable} -m patchright install chromium",
        )
        return
    worker = browser_module.BrowserWorker(headless=True)
    try:
        if worker.start():
            report.add("Stealth browser", OK, f"{driver} launched chromium successfully")
        else:
            report.add(
                "Stealth browser",
                WARN,
                f"{driver} is installed but chromium would not launch",
                f"{sys.executable} -m {driver} install chromium",
            )
    finally:
        worker.close()


def check_writable(report: Report) -> None:
    trouble = []
    for name in (".cache", "output", "output/runs"):
        path = ROOT / name
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as exc:
            trouble.append(f"{name}: {exc}")
    if trouble:
        report.add(
            "Writable folders",
            FAIL,
            "; ".join(trouble),
            "Move the HarvestKit folder somewhere your user account can write to — "
            "your Documents folder, not Program Files.",
        )
    else:
        report.add("Writable folders", OK, ".cache and output are writable")

    free = shutil.disk_usage(ROOT).free / (1024**3)
    if free < 2:
        report.add("Disk space", FAIL, f"{free:.1f} GB free", "Free up space; a run needs a couple of GB.")
    elif free < 10:
        report.add("Disk space", WARN, f"{free:.1f} GB free — tight for the browser cache")
    else:
        report.add("Disk space", OK, f"{free:.0f} GB free")


def check_configs(report: Report) -> None:
    try:
        from job_scraper.config import load_config
    except Exception as exc:
        report.add("Configs", FAIL, f"the config loader would not import: {exc}")
        return
    broken = []
    found = sorted((ROOT / "configs" / "leads").glob("*.yaml"))
    for path in found:
        try:
            load_config(str(path))
        except Exception as exc:
            broken.append(f"{path.name}: {exc}")
    if not found:
        report.add("Configs", FAIL, "no market configs found in configs/leads/")
    elif broken:
        report.add("Configs", FAIL, "; ".join(broken), "Restore the shipped configs from git.")
    else:
        report.add("Configs", OK, f"{len(found)} market configs load cleanly")


def check_checkpoint(report: Report) -> None:
    path = ROOT / ".cache" / "leadgen.sqlite"
    if not path.exists():
        report.add("Checkpoint", OK, "none yet — a fresh machine, which is fine")
        return
    try:
        connection = sqlite3.connect(str(path))
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        leads = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        connection.close()
    except Exception as exc:
        report.add(
            "Checkpoint",
            FAIL,
            f"{path.name} is unreadable: {exc}",
            "Rename it and start fresh. Everything already delivered stays delivered, "
            "but people in it may be sent again.",
        )
        return
    if integrity != "ok":
        report.add("Checkpoint", FAIL, f"integrity check says: {integrity}")
    else:
        report.add("Checkpoint", OK, f"{leads:,} leads banked, database healthy")


def check_dns(report: Report) -> None:
    try:
        socket.getaddrinfo("www.siemens.com", 443)
    except Exception as exc:
        report.add(
            "DNS",
            FAIL,
            f"cannot resolve a public hostname: {exc}",
            "This machine has no working internet connection, or DNS is filtered.",
        )
        return
    report.add("DNS", OK, "public hostnames resolve")


def check_smtp_port(report: Report) -> None:
    """Whether outbound port 25 is open, which most office networks block."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(5)
    try:
        probe.connect(("gmail-smtp-in.l.google.com", 25))
        report.add("SMTP probing", OK, "outbound port 25 is open, so mailbox probing works")
    except Exception:
        report.add(
            "SMTP probing",
            WARN,
            "outbound port 25 is blocked on this network",
            "Turn 'Skip mail-server probing' on in the panel. Nothing else changes; "
            "addresses are simply labelled from their evidence rather than probed.",
        )
    finally:
        probe.close()


def check_egress(report: Report, sample: int) -> None:
    """The check that actually predicts whether a run yields anything."""
    try:
        from job_scraper.http import HttpClient
        from job_scraper.transport import Outcome
    except Exception as exc:
        report.add("Reading European sites", FAIL, f"the HTTP client would not import: {exc}")
        return

    hosts = [
        "https://www.siemens.com/",
        "https://www.sap.com/",
        "https://www.zalando.de/",
        "https://www.celonis.com/",
        "https://www.trivago.com/",
        "https://www.klarna.com/",
        "https://www.adyen.com/",
        "https://www.bolt.eu/",
    ][:sample]

    proxies = _configured_proxies()
    client = HttpClient(
        user_agent="HarvestKit doctor",
        delay_seconds=0.3,
        obey_robots=False,
        timeout_seconds=20.0,
        cache_enabled=False,
        proxies=proxies,
    )
    readable = blocked = failed = 0
    try:
        for url in hosts:
            try:
                result = client.fetch(url)
            except Exception:
                failed += 1
                continue
            if result.outcome is Outcome.OK:
                readable += 1
            elif result.outcome in (Outcome.BLOCKED, Outcome.RATE_LIMITED):
                blocked += 1
            else:
                failed += 1
    finally:
        client.close()

    total = len(hosts)
    detail = f"{readable}/{total} readable, {blocked} blocked, {failed} unreachable"
    if readable == 0:
        report.add(
            "Reading European sites",
            FAIL,
            detail,
            "Nothing was readable, so a run would harvest nothing. Set up free egress: "
            "python tools/proxy_sources.py --print-setup",
        )
    elif readable < total * 0.6:
        report.add(
            "Reading European sites",
            WARN,
            detail,
            "Fewer than 60% readable. Expect a thin run. " "python tools/proxy_sources.py --print-setup",
        )
    else:
        report.add("Reading European sites", OK, detail)


def check_proxies(report: Report) -> None:
    proxies = _configured_proxies()
    if not proxies:
        report.add(
            "Proxies",
            WARN,
            "none configured — this machine's own address is what every site sees",
            "Fine at home; usually blocked on a company network. "
            "python tools/proxy_sources.py --print-setup",
        )
        return
    report.add("Proxies", OK, f"{len(proxies)} configured")


def _configured_proxies() -> list[str]:
    path = ROOT / "configs" / "proxies.txt"
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def check_pipeline(report: Report) -> None:
    """Run the real extraction path over a page we control, offline.

    This is the check that catches a half-installed parser: everything above
    can pass while `lxml` is subtly broken, and the first sign would otherwise
    be a four-hour run producing nothing.
    """
    try:
        from leadgen.assemble import CompanyContext, build_leads
        from leadgen.person.hit import PersonHit
        from leadgen.person.strategies import team
    except Exception as exc:
        report.add("Extraction pipeline", FAIL, f"would not import: {exc}")
        return

    page = """
    <html><body><div class="team">
      <div class="card"><h3>Anna Schmidt</h3><p>CTO</p>
        <a href="mailto:a.schmidt@firma.de">mail</a></div>
      <div class="card"><h3>Peter Wolf</h3><p>Head of Engineering</p></div>
    </div></body></html>
    """
    try:
        hits = team.extract(page, "https://firma.de/team")
    except Exception as exc:
        report.add("Extraction pipeline", FAIL, f"the team-page parser raised: {exc}")
        return
    names = {hit.name for hit in hits}
    if "Anna Schmidt" not in names or "Peter Wolf" not in names:
        report.add(
            "Extraction pipeline",
            FAIL,
            f"found {sorted(names) or 'nobody'} on a page naming two people",
        )
        return

    anna = next(hit for hit in hits if hit.name == "Anna Schmidt")
    if anna.email != "a.schmidt@firma.de":
        report.add("Extraction pipeline", FAIL, f"wrong address for Anna: {anna.email!r}")
        return
    peter = next(hit for hit in hits if hit.name == "Peter Wolf")
    if peter.email:
        report.add(
            "Extraction pipeline",
            FAIL,
            f"Peter Wolf was given {peter.email!r} — that is Anna's address",
        )
        return

    try:
        leads = build_leads(
            CompanyContext(name="Firma GmbH", domain="firma.de"),
            [PersonHit(**vars(hit)) for hit in hits],
            smtp=False,
        )
    except Exception as exc:
        report.add("Extraction pipeline", FAIL, f"lead assembly raised: {exc}")
        return
    if len(leads) != 2:
        report.add("Extraction pipeline", FAIL, f"assembled {len(leads)} leads from 2 people")
        return
    report.add(
        "Extraction pipeline",
        OK,
        "parsed a team page, attributed the address correctly, assembled 2 leads",
    )


def check_csv_safety(report: Report) -> None:
    from job_scraper.csv_safe import neutralise

    hostile = "=cmd|' /C calc'!A0"
    if neutralise(hostile).startswith("="):
        report.add("Spreadsheet safety", FAIL, "formula injection is not neutralised")
        return
    if neutralise("+41 44 215 15 78") != "+41 44 215 15 78":
        report.add("Spreadsheet safety", WARN, "phone numbers are being mangled by the escaper")
        return
    report.add("Spreadsheet safety", OK, "formulas defused, phone numbers left readable")


# ------------------------------------------------------------------- output


def render(report: Report) -> None:
    width = max(len(check.name) for check in report.checks) + 2
    colour = {
        OK: "\033[32m",
        WARN: "\033[33m",
        FAIL: "\033[31m",
    }
    use_colour = sys.stdout.isatty() and os.name != "nt"
    print()
    print("  HarvestKit health check")
    print("  " + "=" * 74)
    for check in report.checks:
        tag = check.status
        if use_colour:
            tag = f"{colour[check.status]}{check.status}\033[0m"
        print(f"  {tag:<6} {check.name:<{width}} {check.detail}")
        if check.fix and check.status != OK:
            for line in _wrap(check.fix, 66):
                print(f"         {' ' * width} {line}")
    print("  " + "=" * 74)
    if report.failed:
        print(f"  {report.failed} thing(s) will stop this machine producing leads.")
    elif report.warned:
        print(f"  Everything essential works. {report.warned} thing(s) would make it better.")
    else:
        print("  Everything checks out. This machine is ready to run.")
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        # A single word longer than the column must not push out a blank line
        # before itself — long file paths are the common case here.
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check this machine can produce leads.")
    parser.add_argument("--quick", action="store_true", help="skip the live network checks")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--sample", type=int, default=8, help="how many sites to test reading")
    args = parser.parse_args(argv)

    report = Report()
    started = time.time()

    check_python(report)
    check_dependencies(report)
    check_writable(report)
    check_configs(report)
    check_checkpoint(report)
    check_csv_safety(report)
    check_pipeline(report)
    if not args.quick:
        check_dns(report)
        check_proxies(report)
        check_smtp_port(report)
        check_browser(report)
        check_egress(report, max(1, args.sample))

    if args.json:
        print(
            json.dumps(
                {
                    "checks": [asdict(check) for check in report.checks],
                    "failed": report.failed,
                    "warned": report.warned,
                    "seconds": round(time.time() - started, 1),
                },
                indent=2,
            )
        )
    else:
        render(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
