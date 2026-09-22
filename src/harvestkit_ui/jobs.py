"""The commands the control panel is allowed to run, and their forms.

Deliberately a fixed catalogue rather than a shell. The panel exists so that
somebody with no Python and no terminal can operate this, and the honest way to
give them that is a small set of known-good commands with their options spelled
out — not a text box that runs whatever it is handed. A text box would also be
a remote-execution hole the moment the port is reachable from anywhere but this
machine, and "it only listens on localhost" is one firewall rule away from
being untrue.

Each field here becomes one control in the browser, with its help text taken
from the same place the CLI's `--help` takes it, so the two can never drift into
saying different things.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

FieldKind = Literal["text", "number", "choice", "toggle", "path", "textarea"]


@dataclass
class Field:
    name: str
    label: str
    kind: FieldKind = "text"
    default: Any = ""
    help: str = ""
    #: CLI flag this field becomes. A toggle emits the flag alone when true.
    flag: str = ""
    choices: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    #: Glob, relative to the project root, that fills a `choice` at render time.
    from_glob: str = ""
    #: Group heading in the UI. "basic" is shown by default; the rest folds away.
    section: str = "basic"
    #: Emit the flag even when the value equals the default.
    always: bool = False


@dataclass
class Job:
    key: str
    title: str
    blurb: str
    script: str
    fields: list[Field] = field(default_factory=list)
    #: Long-running jobs get the progress panel; quick checks just get output.
    long_running: bool = True
    #: Field whose value names the file the run produces, for the results table.
    output_field: str = ""


def _config_choices(root: Path, pattern: str) -> list[str]:
    found = sorted(
        os.path.relpath(path, root).replace("\\", "/")
        for path in glob.glob(str(root / pattern), recursive=True)
    )
    return found


COUNTRY_PRESETS = [
    "eu",
    "dach",
    "nordics",
    "CH",
    "DE",
    "AT",
    "FR",
    "IT",
    "ES",
    "NL",
    "BE",
    "SE",
    "NO",
    "DK",
    "FI",
    "PL",
    "IE",
    "PT",
    "CZ",
]


LEADS_JOB = Job(
    key="leads",
    title="Find leads",
    blurb=(
        "The main job. Seeds companies from job boards, crawls each employer's own site for "
        "named people, works out their email address, and writes a CSV."
    ),
    script="run_leads.py",
    output_field="output",
    fields=[
        Field(
            "config",
            "Market",
            "choice",
            "configs/leads/eu-it.yaml",
            "Which market to harvest. Europe-wide, or Switzerland only.",
            "--config",
            from_glob="configs/leads/*.yaml",
            always=True,
        ),
        Field(
            "target",
            "Rows wanted",
            "number",
            300,
            "How many people you want in the file. The run stops short and says so rather "
            "than padding it with guesses.",
            "--target",
            minimum=1,
            maximum=20000,
            always=True,
        ),
        Field(
            "output",
            "Save the file as",
            "path",
            "output/leads.csv",
            "Where the CSV is written.",
            "--output",
            always=True,
        ),
        Field(
            "countries",
            "Countries",
            "text",
            "eu",
            "Which countries to keep. A preset name (eu, dach, nordics) or a comma-separated "
            "list of two-letter codes.",
            "--countries",
            always=True,
        ),
        Field(
            "only_new",
            "Only people never sent before",
            "toggle",
            True,
            "What makes a daily run deliver fresh people instead of re-sending yesterday's "
            "file with today's date on it.",
            "--only-new",
        ),
        # --- seeds -------------------------------------------------------
        Field(
            "search_keywords",
            "Job-search keywords file",
            "choice",
            "configs/leads/keywords.txt",
            "Search terms paired with every country above. This is the seed that makes the "
            "run geography-first.",
            "--search-keywords",
            from_glob="configs/leads/keywords*.txt",
            section="seeds",
        ),
        Field(
            "search_max_pages",
            "Pages per keyword",
            "number",
            8,
            "Pages walked per country/keyword pairing before moving on.",
            "--search-max-pages",
            minimum=1,
            maximum=50,
            section="seeds",
        ),
        Field(
            "search_delay",
            "Seconds between search requests",
            "number",
            0.5,
            "Extra pacing for the search APIs. One of them blocked a whole run after about "
            "1500 requests, and a seed that gets itself blocked is worth less than a slow one.",
            "--search-delay",
            minimum=0,
            maximum=10,
            section="seeds",
        ),
        Field(
            "jobsch_pages",
            "jobs.ch pages",
            "number",
            0,
            "Pages of the jobs.ch search to walk (0 = skip it). The Swiss seed: 83% of its "
            "employers publish their own website in the posting, which skips the slowest "
            "step entirely.",
            "--jobsch-pages",
            minimum=0,
            maximum=200,
            section="seeds",
        ),
        Field(
            "jobsch_days",
            "jobs.ch: how recent",
            "number",
            7,
            "How old a posting may be, in days.",
            "--jobsch-days",
            minimum=1,
            maximum=60,
            section="seeds",
        ),
        Field(
            "boards",
            "ATS board list",
            "choice",
            "",
            "A file of Greenhouse/Lever/Personio board slugs to pull directly.",
            "--boards",
            from_glob="configs/leads/boards*.txt",
            section="seeds",
        ),
        Field(
            "arbeitnow_pages",
            "Arbeitnow pages",
            "number",
            0,
            "Pages of the German Arbeitnow feed (0 = skip). Paced at 5 s a page because the "
            "API refuses anything faster.",
            "--arbeitnow-pages",
            minimum=0,
            maximum=100,
            section="seeds",
        ),
        # --- crawl -------------------------------------------------------
        Field(
            "concurrency",
            "Parallel companies",
            "number",
            8,
            "How many employers are crawled at once. Above 8 is where polite crawling stops "
            "and the host starts refusing you.",
            "--concurrency",
            minimum=1,
            maximum=8,
            section="crawl",
        ),
        Field(
            "max_pages",
            "Pages per company",
            "number",
            8,
            "How many candidate pages (team, impressum, about…) are fetched per employer.",
            "--max-pages",
            minimum=1,
            maximum=40,
            section="crawl",
        ),
        Field(
            "max_person_pages",
            "Person pages per company",
            "number",
            6,
            "How many individual profile pages are followed per employer.",
            "--max-person-pages",
            minimum=0,
            maximum=40,
            section="crawl",
        ),
        Field(
            "recrawl_after",
            "Re-crawl a company after (days)",
            "number",
            0,
            "0 means never. An employer crawled in January may name three new directors by "
            "June, and 'crawled once' meaning 'crawled forever' never finds them.",
            "--recrawl-after",
            minimum=0,
            maximum=365,
            section="crawl",
        ),
        # --- email -------------------------------------------------------
        Field(
            "no_smtp",
            "Skip mail-server probing",
            "toggle",
            False,
            "Faster, and invisible to the domains being checked. Leave it off unless a "
            "network blocks outbound port 25 — many office networks do.",
            "--no-smtp",
            section="email",
        ),
        Field(
            "no_guess",
            "Never guess an address",
            "toggle",
            False,
            "Only ship addresses with evidence behind them. Far fewer rows, every one of " "them anchored.",
            "--no-guess",
            section="email",
        ),
        Field(
            "roles",
            "Roles wanted",
            "text",
            "any",
            "Comma-separated role families, or 'any'.",
            "--roles",
            section="email",
        ),
        Field(
            "verbose",
            "Detailed log",
            "toggle",
            False,
            "Every request, not just the milestones. Useful when something is wrong.",
            "--verbose",
            section="crawl",
        ),
    ],
)

JOBS_JOB = Job(
    key="jobs",
    title="Scrape job ads",
    blurb="Harvest job postings themselves — titles, salaries, descriptions — rather than people.",
    script="run.py",
    output_field="",
    fields=[
        Field(
            "config",
            "Site set",
            "choice",
            "",
            "Which sites to scrape.",
            "--config",
            from_glob="configs/**/*.yaml",
            always=True,
        ),
        Field(
            "verbose",
            "Detailed log",
            "toggle",
            False,
            "Every request, not just the milestones.",
            "--verbose",
        ),
    ],
)

DOCTOR_JOB = Job(
    key="doctor",
    title="Check this machine",
    blurb=(
        "The full health check. Python, the dependencies, the browser, disk space, the "
        "configs, the saved leads, whether mailbox probing is possible on this network, "
        "and whether real European sites can actually be read from here. Run it after "
        "installing, and whenever something stops working."
    ),
    script="tools/doctor.py",
    long_running=False,
    fields=[
        Field(
            "quick",
            "Skip the network checks",
            "toggle",
            False,
            "Much faster, but it cannot tell you whether this machine can actually read "
            "the sites — which is the question that matters most.",
            "--quick",
        ),
    ],
)

EGRESS_JOB = Job(
    key="egress",
    title="Check this machine can read European sites",
    blurb=(
        "Fetches a dozen real European company sites and reports which ones actually "
        "returned a page. Run this first if a harvest comes back empty — it tells you "
        "whether the problem is the network or the market."
    ),
    script="tools/check_egress.py",
    long_running=False,
    fields=[],
)

PROXY_JOB = Job(
    key="proxies",
    title="Find and test free proxies",
    blurb=(
        "Collects free public proxies, then keeps only the ones that are genuinely "
        "anonymous, do not tamper with TLS, and can reach an HTTPS site. Most free "
        "proxies fail at least one of those, and a proxy that fails the TLS test can "
        "read everything this tool fetches."
    ),
    script="tools/proxy_sources.py",
    long_running=False,
    fields=[
        Field(
            "free_lists",
            "Collect from public lists",
            "toggle",
            True,
            "Pull candidates from the public aggregators.",
            "--free-lists",
        ),
        Field(
            "check",
            "Test each one",
            "toggle",
            True,
            "Verify anonymity and TLS before keeping it. Never skip this.",
            "--check",
        ),
        Field(
            "out",
            "Save the working ones to",
            "path",
            "configs/proxies.txt",
            "The file the harvest reads its proxies from.",
            "--out",
            always=True,
        ),
    ],
)

VERIFY_JOB = Job(
    key="verify",
    title="Check a finished file",
    blurb=(
        "Re-reads a CSV and checks the guarantees: every row has a person, an address and "
        "a source URL; no duplicates; no shared inboxes in the person column; nothing that "
        "would run as a formula in Excel."
    ),
    script="tools/verify_leads.py",
    long_running=False,
    fields=[
        Field(
            "path",
            "File to check",
            "path",
            "output/leads.csv",
            "The CSV to verify.",
            "",
            always=True,
        ),
    ],
)

JOBS: dict[str, Job] = {
    job.key: job for job in (LEADS_JOB, JOBS_JOB, DOCTOR_JOB, EGRESS_JOB, PROXY_JOB, VERIFY_JOB)
}


def describe(root: Path) -> list[dict[str, Any]]:
    """The catalogue as JSON, with file choices resolved against the project."""
    out: list[dict[str, Any]] = []
    for job in JOBS.values():
        fields: list[dict[str, Any]] = []
        for item in job.fields:
            entry = {
                "name": item.name,
                "label": item.label,
                "kind": item.kind,
                "default": item.default,
                "help": item.help,
                "choices": list(item.choices),
                "min": item.minimum,
                "max": item.maximum,
                "section": item.section,
            }
            if item.from_glob:
                found = _config_choices(root, item.from_glob)
                entry["choices"] = ([""] if not item.always else []) + found
                entry["kind"] = "choice"
            fields.append(entry)
        out.append(
            {
                "key": job.key,
                "title": job.title,
                "blurb": job.blurb,
                "fields": fields,
                "long_running": job.long_running,
                "output_field": job.output_field,
            }
        )
    return out


def build_command(job: Job, values: dict[str, Any], python: str, root: Path) -> list[str]:
    """Turn a form submission into an argv, validating every value on the way.

    Nothing here ever reaches a shell, and no value becomes part of a flag name
    — a submitted value can only ever be a single argv element after a flag this
    module chose.
    """
    command = [python, str(root / job.script)]
    by_name = {item.name: item for item in job.fields}
    positional: list[str] = []

    for name, raw in values.items():
        spec = by_name.get(name)
        if spec is None:
            continue  # An unknown key is dropped, never forwarded.
        value = _coerce(spec, raw)
        if value is None:
            continue
        if spec.kind == "toggle":
            if value:
                command.append(spec.flag)
            continue
        if not spec.flag:
            positional.append(str(value))
            continue
        if not spec.always and str(value) == str(spec.default):
            continue
        if str(value) == "":
            continue
        command.extend([spec.flag, str(value)])

    command.extend(positional)
    return command


def _coerce(spec: Field, raw: Any) -> Any:
    """The submitted value, in the type the field declares, or None to skip."""
    if spec.kind == "toggle":
        return bool(raw) if not isinstance(raw, str) else raw.lower() in ("1", "true", "on", "yes")
    if spec.kind == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if spec.minimum is not None:
            number = max(spec.minimum, number)
        if spec.maximum is not None:
            number = min(spec.maximum, number)
        return int(number) if float(number).is_integer() else number
    text = str(raw if raw is not None else "").strip()
    # A path is written into argv, not into a shell, so quoting is not the risk
    # — escaping the project is. Anything that climbs out of the tree or looks
    # like a flag falls back to the declared default rather than being dropped:
    # dropping it silently hands the run the CLI's own default instead, which
    # need not be the file the panel showed the operator.
    if spec.kind in ("path", "choice") and (
        text.startswith("-") or ".." in text.replace("\\", "/").split("/")
    ):
        return str(spec.default or "")
    if spec.choices and text and text not in spec.choices:
        return text if spec.from_glob else str(spec.default or "")
    return text
