"""The control panel: a local web page that runs HarvestKit so nobody has to.

Standard library only, on purpose. This is the thing that has to start on a
machine where a dependency install may have half-failed, and the whole point is
that the operator never sees a traceback — so it cannot itself depend on
anything that might not be there.

Security shape, stated plainly because it is easy to get wrong:

* Bound to 127.0.0.1 by default. Nothing outside this machine can reach it.
* Every request must carry a token generated at startup. That stops any other
  page the operator has open in the same browser from driving this one (a
  website cannot read the token, so it cannot forge a request that works).
* There is no free-text command box. The panel runs a fixed catalogue of
  commands with typed, validated options — see `jobs.py` for why.
* Files are served from, and written to, the project directory only.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import secrets
import socket
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import jobs as job_catalogue
from .runner import Runner, python_executable

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

#: Where the panel remembers what the operator last chose, so a daily run is
#: "open it, press the button" and not "fill the form in again".
SETTINGS_NAME = "panel-settings.json"

MAX_BODY = 1 << 20  # 1 MB: a form submission, never a file upload.
MAX_PREVIEW_ROWS = 500


class Panel:
    """Everything the handlers need. One per process."""

    def __init__(self, root: Path, *, token: str) -> None:
        self.root = root
        self.token = token
        self.runner = Runner(root, root / "output" / "runs")
        self.python = python_executable(root)
        self.settings_path = root / ".cache" / SETTINGS_NAME
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)

    # -- settings ----------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_settings(self, values: dict[str, Any]) -> None:
        merged = self.load_settings()
        merged.update(values)
        self.settings_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    # -- proxies -----------------------------------------------------------

    def proxy_file(self) -> Path:
        return self.root / "configs" / "proxies.txt"

    def read_proxies(self) -> list[str]:
        path = self.proxy_file()
        if not path.exists():
            return []
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def write_proxies(self, lines: list[str]) -> int:
        cleaned = []
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if "://" not in entry:
                entry = f"http://{entry}"
            cleaned.append(entry)
        path = self.proxy_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# One proxy per line: http://host:port, or socks5h://host:port.\n"
            "# Written by the HarvestKit control panel. Lines starting with # are ignored.\n"
        )
        path.write_text(header + "\n".join(cleaned) + "\n", encoding="utf-8")
        return len(cleaned)

    # -- results -----------------------------------------------------------

    def read_csv(self, relative: str, limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
        path = self._safe_path(relative)
        if path is None or not path.exists():
            return {"columns": [], "rows": [], "total": 0, "path": relative, "exists": False}
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = []
            total = 0
            for row in reader:
                total += 1
                if len(rows) < limit:
                    rows.append(row)
        return {
            "columns": columns,
            "rows": rows,
            "total": total,
            "path": relative,
            "exists": True,
            "modified": path.stat().st_mtime,
        }

    def list_outputs(self) -> list[dict[str, Any]]:
        directory = self.root / "output"
        if not directory.exists():
            return []
        found = []
        for path in sorted(directory.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
            found.append(
                {
                    "path": os.path.relpath(path, self.root).replace("\\", "/"),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                }
            )
        return found[:40]

    def _safe_path(self, relative: str) -> Path | None:
        """A path inside the project, or None.

        Resolved and then checked against the root, so `..`, a symlink and an
        absolute path elsewhere are all refused by the same test.
        """
        if not relative:
            return None
        try:
            candidate = (self.root / relative).resolve()
            candidate.relative_to(self.root.resolve())
        except (ValueError, OSError):
            return None
        return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "HarvestKit"
    sys_version = ""
    panel: Panel  # set on the server, read through self.server

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        # The panel's own access log is noise next to the run log it is showing.
        return

    @property
    def app(self) -> Panel:
        return self.server.panel  # type: ignore[attr-defined]

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        supplied = (query.get("token") or [""])[0] or self.headers.get("X-HarvestKit-Token", "")
        return secrets.compare_digest(supplied, self.app.token)

    def _host_is_expected(self) -> bool:
        """Refuse a request whose Host header is not one we bound to.

        This is the DNS-rebinding guard. A hostile page can make the browser
        resolve `evil.example` to 127.0.0.1 and then talk to this server as
        same-origin, which would let it read the token out of the page. It
        cannot change the Host header, so checking it closes that door.
        """
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1", self.server.server_address[0]}

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        # No third-party anything: the page is self-contained, so the strictest
        # policy that still works is the right one.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; connect-src 'self'; form-action 'none'; base-uri 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except Exception:
            return {}

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path

        if not self._host_is_expected():
            return self._json({"error": "unexpected Host header"}, HTTPStatus.FORBIDDEN)

        if route in ("/", "/index.html"):
            return self._serve_page()
        if route.startswith("/static/"):
            return self._serve_static(route[len("/static/") :])

        if not self._authorised(query):
            return self._json({"error": "bad or missing token"}, HTTPStatus.FORBIDDEN)

        if route == "/api/catalogue":
            return self._json(
                {
                    "jobs": job_catalogue.describe(self.app.root),
                    "settings": self.app.load_settings(),
                    "proxies": self.app.read_proxies(),
                    "python": self.app.python,
                    "root": str(self.app.root),
                }
            )
        if route == "/api/state":
            offset = int((query.get("offset") or ["0"])[0] or 0)
            total, lines = self.app.runner.lines_since(offset)
            return self._json({**self.app.runner.snapshot(), "log": lines, "offset": total})
        if route == "/api/outputs":
            return self._json({"files": self.app.list_outputs()})
        if route == "/api/results":
            relative = (query.get("path") or ["output/leads.csv"])[0]
            return self._json(self.app.read_csv(relative))
        if route == "/api/download":
            return self._download((query.get("path") or [""])[0])
        return self._json({"error": "no such endpoint"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_is_expected():
            return self._json({"error": "unexpected Host header"}, HTTPStatus.FORBIDDEN)
        if not self._authorised(query):
            return self._json({"error": "bad or missing token"}, HTTPStatus.FORBIDDEN)

        route = parsed.path
        payload = self._body()

        if route == "/api/run":
            return self._start(payload)
        if route == "/api/stop":
            stopped = self.app.runner.stop()
            return self._json({"stopped": stopped, **self.app.runner.snapshot()})
        if route == "/api/settings":
            values = payload.get("settings")
            if isinstance(values, dict):
                self.app.save_settings(values)
            return self._json({"settings": self.app.load_settings()})
        if route == "/api/proxies":
            raw = payload.get("proxies")
            lines = raw if isinstance(raw, list) else str(raw or "").splitlines()
            written = self.app.write_proxies([str(line) for line in lines])
            return self._json({"written": written, "proxies": self.app.read_proxies()})
        return self._json({"error": "no such endpoint"}, HTTPStatus.NOT_FOUND)

    # -- actions -----------------------------------------------------------

    def _start(self, payload: dict[str, Any]) -> None:
        key = str(payload.get("job") or "")
        job = job_catalogue.JOBS.get(key)
        if job is None:
            return self._json({"error": f"unknown job {key!r}"}, HTTPStatus.BAD_REQUEST)
        if self.app.runner.running:
            return self._json({"error": "A run is already going. Stop it first."}, HTTPStatus.CONFLICT)
        values = payload.get("values")
        values = values if isinstance(values, dict) else {}
        command = job_catalogue.build_command(job, values, self.app.python, self.app.root)
        output = ""
        if job.output_field:
            output = str(values.get(job.output_field) or "")
        # Remember the form so tomorrow is one click.
        self.app.save_settings({f"form:{key}": values})
        try:
            state = self.app.runner.start(command, label=job.title, output_path=output, kind=job.kind)
        except RuntimeError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        return self._json(state)

    def _download(self, relative: str) -> None:
        path = self.app._safe_path(relative)
        if path is None or not path.is_file():
            return self._json({"error": "no such file"}, HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    # -- static ------------------------------------------------------------

    def _serve_page(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        # The token is injected rather than typed. It never leaves this machine
        # and is regenerated every time the panel starts.
        html = html.replace("__HARVESTKIT_TOKEN__", self.app.token)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str) -> None:
        if "/" in name or "\\" in name or name.startswith("."):
            return self._json({"error": "no"}, HTTPStatus.FORBIDDEN)
        path = STATIC / name
        if not path.is_file():
            return self._json({"error": "no such file"}, HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)


def _free_port(host: str, preferred: int) -> int:
    """`preferred` if it is free, else whatever the OS hands out."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def serve(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
) -> None:
    """Run the control panel until interrupted."""
    token = secrets.token_urlsafe(24)
    panel = Panel(root, token=token)
    chosen = _free_port(host, port)

    server = ThreadingHTTPServer((host, chosen), Handler)
    server.daemon_threads = True
    server.panel = panel  # type: ignore[attr-defined]

    url = f"http://{host}:{chosen}/"
    print()
    print("  HarvestKit control panel")
    print("  " + "-" * 40)
    print(f"  Open this in your browser:  {url}")
    if host not in ("127.0.0.1", "localhost"):
        print()
        print("  WARNING: this is listening on a network address, not just this machine.")
        print("  Anyone who can reach it and guess nothing — the link above carries the")
        print("  token — can start runs on this computer. Use --host 127.0.0.1 unless you")
        print("  specifically intend otherwise.")
    print()
    print("  Leave this window open while you use it. Close it to shut the panel down.")
    print()

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down…")
    finally:
        panel.runner.stop()
        server.shutdown()
        server.server_close()
