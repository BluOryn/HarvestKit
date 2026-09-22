"""Start a harvest, watch it, and turn its log into something a person can read.

The operators this exists for do not have Python, do not have a terminal open,
and should never be asked to read a stack trace. So this module does three
things the CLI cannot do for itself:

* **Runs it as a child process, not in-process.** A harvest is hours long and
  may need to be stopped; a thread cannot be killed safely and a crash in the
  pipeline would take the control panel down with it. The subprocess also means
  the UI keeps working while a run is wedged.
* **Keeps the whole log, and a readable summary alongside it.** The CLI already
  prints everything worth knowing — `seed:`, `funnel:`, `reachability:` — but it
  prints it among thousands of lines. `RunState` pulls the few that answer "is
  this working?" into fields a UI can render.
* **Never lets a failure be silent.** Exit code 4 (harvest produced nothing) and
  exit 2 (short of target) mean very different things, and both are reported in
  words rather than as a number.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

#: Kept in memory so the UI can be opened mid-run and still show the history.
#: Ten thousand lines is a couple of megabytes and covers a full day's run at
#: INFO; older lines are on disk in the log file either way.
MAX_LINES = 10000

#: What each exit code means **for a harvest**, in the words the operator
#: needs. The numbers are the CLI's, and it matters that 2 and 4 are not
#: confused: "the market was thin" and "we were blocked out of every site" look
#: identical in a CSV.
EXIT_MEANING: dict[int, tuple[str, str]] = {
    0: ("ok", "Finished. The file is ready."),
    2: (
        "short",
        "Finished, but short of the number of rows you asked for. The file is usable — "
        "there were simply fewer people found than requested.",
    ),
    3: ("error", "The run stopped with an error. The log below says where."),
    4: (
        "blocked",
        "Harvested nothing at all. This is almost always the network blocking us, not an "
        "empty market. Run the egress check, and set up a proxy before trying again.",
    ),
}

#: The same, for a check. A check produces a verdict, not a file, and saying
#: "Finished. The file is ready." after the health check was both wrong and
#: confusing — it is the one job that writes nothing at all.
CHECK_EXIT_MEANING: dict[int, tuple[str, str]] = {
    0: ("ok", "All good. Nothing here will stop this machine working."),
    1: ("error", "Something needs attention — the log below says what, and how to fix it."),
    2: ("short", "Finished with warnings. Read the log."),
}


def meaning(exit_code: int, kind: str) -> tuple[str, str]:
    """(status, sentence) for an exit code, in this job's own vocabulary."""
    table = CHECK_EXIT_MEANING if kind == "check" else EXIT_MEANING
    fallback = (
        "error",
        (
            f"The check ended with exit code {exit_code}."
            if kind == "check"
            else f"The run ended with exit code {exit_code}."
        ),
    )
    return table.get(exit_code, fallback)


_SEED_TOTAL = re.compile(r"seed: (\d+) listings total")
_SEED_COMPANIES = re.compile(r"seed: (\d+) unique companies")
_FUNNEL = re.compile(r"funnel: (\{.*\})")
_REACHABILITY = re.compile(r"reachability: (.+)$")
_WROTE = re.compile(r"wrote (\d+) rows to (.+)$")
_SHORTFALL = re.compile(r"SHORTFALL: (\d+) rows short")
_HARVEST_FAILED = re.compile(r"HARVEST FAILED: (.+)$")
_PROGRESS = re.compile(r"(?:companies|deep-scrape): (\d+)/(\d+)")


@dataclass
class RunState:
    """Everything the control panel shows about one run."""

    id: str = ""
    label: str = ""
    #: "harvest" or "check" — decides which vocabulary reports this run.
    kind: str = "harvest"
    command: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    exit_code: Optional[int] = None
    status: str = "idle"  # idle | running | ok | short | blocked | error | stopped
    message: str = ""
    log_path: str = ""
    output_path: str = ""

    listings: int = 0
    companies: int = 0
    rows_written: int = 0
    funnel: dict[str, int] = field(default_factory=dict)
    reachability: str = ""
    shortfall: int = 0
    done: int = 0
    total: int = 0

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "command": " ".join(shlex.quote(part) for part in self.command),
            "status": self.status,
            "message": self.message,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(elapsed, 1),
            "listings": self.listings,
            "companies": self.companies,
            "rows_written": self.rows_written,
            "funnel": self.funnel,
            "reachability": self.reachability,
            "shortfall": self.shortfall,
            "done": self.done,
            "total": self.total,
            "output_path": self.output_path,
            "log_path": self.log_path,
        }


class Runner:
    """Owns at most one child process, and everything known about it.

    One at a time is deliberate. Two concurrent harvests share a checkpoint
    database, a response cache and — critically — an egress IP, and the second
    one is how a machine that was working gets itself rate-limited.
    """

    def __init__(self, project_root: Path, log_dir: Path) -> None:
        self.root = project_root
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state = RunState()
        self._process: Optional[subprocess.Popen] = None
        self._lines: deque[str] = deque(maxlen=MAX_LINES)
        self._lock = threading.RLock()
        self._sequence = 0
        self._subscribers: list[Callable[[], None]] = []

    # -- state -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = self.state.as_dict()
            data["running"] = self.running
            data["sequence"] = self._sequence
            return data

    def lines_since(self, offset: int) -> tuple[int, list[str]]:
        """Log lines the caller has not seen, and the new offset.

        The deque drops the oldest lines once it is full, so an offset older
        than the window would silently skip. `start` below is recomputed from
        the current length rather than trusted.
        """
        with self._lock:
            total = self._sequence
            window = list(self._lines)
        first_available = total - len(window)
        start = max(offset, first_available)
        return total, window[start - first_available :]

    def subscribe(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._subscribers):
            with suppress(Exception):
                callback()

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        command: list[str],
        *,
        label: str,
        output_path: str = "",
        kind: str = "harvest",
    ) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("a run is already in progress")
            run_id = time.strftime("%Y%m%d-%H%M%S")
            log_path = self.log_dir / f"{run_id}.log"
            self.state = RunState(
                id=run_id,
                label=label,
                kind=kind,
                command=command,
                started_at=time.time(),
                status="running",
                message="Starting…",
                log_path=str(log_path),
                output_path=output_path,
            )
            self._lines.clear()
            self._sequence = 0

        environment = os.environ.copy()
        # Unbuffered, or the log arrives in 8 KB bursts and the panel looks
        # frozen for minutes at a time.
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        # Windows consoles default to a legacy code page, which mangles every
        # umlaut and slashed o in the log — and those are most of the names.
        environment.setdefault("PYTHONUTF8", "1")

        creation_flags = 0
        start_new_session = False
        if os.name == "nt":
            # Its own process group, so stopping the run does not also signal
            # the control panel that launched it.
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True

        self._process = subprocess.Popen(  # noqa: S603 — argv built here, never a shell string
            command,
            cwd=str(self.root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
            start_new_session=start_new_session,
        )
        threading.Thread(target=self._pump, args=(log_path,), daemon=True).start()
        return self.snapshot()

    def stop(self) -> bool:
        """Ask the run to stop. Returns False when there was nothing to stop."""
        process = self._process
        if process is None or process.poll() is not None:
            return False
        with self._lock:
            self.state.status = "stopped"
            self.state.message = "Stopped by you."
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
        except Exception:
            pass

        # Give the checkpoint a moment to flush; a hard kill mid-write is how a
        # day's banked leads become an unreadable SQLite file.
        def _reap() -> None:
            try:
                process.wait(timeout=20)
            except Exception:
                with suppress(Exception):
                    process.kill()

        threading.Thread(target=_reap, daemon=True).start()
        return True

    # -- the pump ----------------------------------------------------------

    def _pump(self, log_path: Path) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            for raw in process.stdout:
                line = raw.rstrip("\n")
                log_file.write(line + "\n")
                log_file.flush()
                with self._lock:
                    self._lines.append(line)
                    self._sequence += 1
                    self._absorb(line)
                self._notify()
        code = process.wait()
        with self._lock:
            self.state.exit_code = code
            self.state.finished_at = time.time()
            if self.state.status != "stopped":
                status, message = meaning(code, self.state.kind)
                self.state.status = status
                if self.state.message.startswith("HARVEST FAILED"):
                    message = f"{message}\n{self.state.message}"
                self.state.message = message
        self._notify()

    def _absorb(self, line: str) -> None:
        """Pull the handful of lines that answer 'is this working?'."""
        state = self.state
        match = _SEED_TOTAL.search(line)
        if match:
            state.listings = int(match.group(1))
            state.message = f"Seeded {state.listings} listings."
            return
        match = _SEED_COMPANIES.search(line)
        if match:
            state.companies = int(match.group(1))
            state.message = f"{state.companies} companies to crawl."
            return
        match = _FUNNEL.search(line)
        if match:
            # The funnel is a Counter repr. A malformed one is a cosmetic loss,
            # never a reason to stop reading the log.
            with suppress(Exception):
                state.funnel = {str(key): int(value) for key, value in _parse_counter(match.group(1)).items()}
            return
        match = _REACHABILITY.search(line)
        if match:
            state.reachability = match.group(1).strip()
            return
        match = _WROTE.search(line)
        if match:
            state.rows_written = int(match.group(1))
            state.output_path = match.group(2).strip()
            return
        match = _SHORTFALL.search(line)
        if match:
            state.shortfall = int(match.group(1))
            return
        match = _HARVEST_FAILED.search(line)
        if match:
            state.message = f"HARVEST FAILED: {match.group(1).strip()}"
            return
        match = _PROGRESS.search(line)
        if match:
            state.done, state.total = int(match.group(1)), int(match.group(2))


def _parse_counter(text: str) -> dict[str, Any]:
    """Parse the `{'key': 1, ...}` repr the CLI logs, without eval."""
    import ast

    value = ast.literal_eval(text)
    return dict(value) if isinstance(value, dict) else {}


def python_executable(root: Path) -> str:
    """The interpreter to run the pipeline with.

    Prefers the project's own virtualenv so the control panel can be launched
    from anywhere — including a desktop shortcut pointing at a bundled runtime
    — and still run the pipeline with the dependencies that were installed for
    it.
    """
    for candidate in (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable
