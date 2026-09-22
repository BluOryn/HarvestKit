"""The control panel: what it will run, and what it refuses to.

The panel exists so somebody with no terminal can operate this, which makes it
the one component whose input is a web form rather than an argv the operator
wrote. So the interesting tests are all about what a submitted value can and
cannot become: never a flag name, never a path outside the project, never a
shell string.
"""

from __future__ import annotations

import json
from pathlib import Path

from harvestkit_ui import jobs as catalogue
from harvestkit_ui.runner import EXIT_MEANING, Runner, RunState
from harvestkit_ui.server import Panel

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- the catalogue


def test_every_job_names_a_script_that_exists():
    for job in catalogue.JOBS.values():
        assert (ROOT / job.script).is_file(), f"{job.key} -> {job.script}"


def test_the_catalogue_renders_with_real_config_choices():
    described = catalogue.describe(ROOT)
    leads = next(job for job in described if job["key"] == "leads")
    market = next(field for field in leads["fields"] if field["name"] == "config")
    assert market["choices"], "the market dropdown must list the shipped configs"
    assert all(choice.startswith("configs/leads/") for choice in market["choices"])


# ------------------------------------------------------------ command building


def build(job_key: str, values: dict) -> list[str]:
    return catalogue.build_command(catalogue.JOBS[job_key], values, "python", ROOT)


def test_a_form_becomes_an_argv_with_the_flags_it_declared():
    command = build("leads", {"config": "configs/leads/eu-it.yaml", "target": 50, "only_new": True})
    assert "--config" in command and "configs/leads/eu-it.yaml" in command
    assert "--target" in command and "50" in command
    assert "--only-new" in command


def test_a_toggle_left_off_emits_nothing():
    command = build("leads", {"only_new": False, "no_smtp": False})
    assert "--only-new" not in command
    assert "--no-smtp" not in command


def test_an_unknown_field_is_dropped_rather_than_forwarded():
    command = build("leads", {"target": 10, "rm_rf": "/", "--evil": "x"})
    assert "rm_rf" not in command and "/" not in command
    assert "--evil" not in command


def test_a_value_can_never_become_a_flag():
    """Submitting something that looks like an option must not act like one."""
    command = build("leads", {"output": "--checkpoint"})
    assert "--checkpoint" not in command


def test_a_path_cannot_climb_out_of_the_project():
    """And the rejection is the declared default, not silence.

    Dropping the flag entirely would hand the run the CLI's own default, which
    need not be the file the panel showed the operator.
    """
    for attempt in ("../../etc/passwd", "..\\..\\Windows\\win.ini", "a/../../b"):
        command = build("leads", {"output": attempt})
        assert attempt not in command, attempt
        assert command[command.index("--output") + 1] == "output/leads.csv", attempt

    command = build("verify", {"path": "../../etc/passwd"})
    assert command[-1] == "output/leads.csv"


def test_a_number_is_clamped_to_the_range_the_field_declares():
    """Concurrency above 8 is where polite crawling stops; the form cannot ask for it.

    The clamp lands exactly on the default here, and a value equal to the
    default is left off the argv entirely — so the guarantee to assert is that
    no number above the ceiling ever reaches the command, not that the flag is
    present.
    """
    command = build("leads", {"concurrency": 400})
    assert "400" not in command
    if "--concurrency" in command:
        assert command[command.index("--concurrency") + 1] == "8"

    # A ceiling that is not also the default does emit the clamped value.
    command = build("leads", {"max_pages": 9999})
    assert command[command.index("--max-pages") + 1] == "40"


def test_a_number_that_is_not_a_number_is_skipped_not_passed_through():
    command = build("leads", {"target": "; rm -rf /"})
    assert "--target" not in command


def test_the_verify_job_passes_its_file_as_a_positional():
    command = build("verify", {"path": "output/leads.csv"})
    assert command[-1] == "output/leads.csv"


# ----------------------------------------------------------------- the panel


def test_the_panel_refuses_paths_outside_the_project(tmp_path):
    panel = Panel(ROOT, token="t")
    assert panel._safe_path("../../etc/passwd") is None
    assert panel._safe_path("") is None
    assert panel._safe_path("output") is not None


def test_proxies_round_trip_and_get_a_scheme_added(tmp_path):
    panel = Panel(tmp_path, token="t")
    written = panel.write_proxies(["198.51.100.7:8080", "  ", "# a comment", "socks5h://127.0.0.1:1080"])
    assert written == 2
    assert panel.read_proxies() == ["http://198.51.100.7:8080", "socks5h://127.0.0.1:1080"]


def test_settings_survive_a_restart(tmp_path):
    panel = Panel(tmp_path, token="t")
    panel.save_settings({"form:leads": {"target": 42}})
    assert Panel(tmp_path, token="t").load_settings()["form:leads"]["target"] == 42


def test_a_csv_is_previewed_with_its_columns(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "x.csv").write_text(
        "person_name,person_email\nAnna Schmidt,a.schmidt@firma.de\n", encoding="utf-8"
    )
    panel = Panel(tmp_path, token="t")
    data = panel.read_csv("output/x.csv")
    assert data["columns"] == ["person_name", "person_email"]
    assert data["total"] == 1
    assert data["rows"][0]["person_name"] == "Anna Schmidt"


# ----------------------------------------------------------------- the runner


def test_exit_codes_are_translated_into_something_a_person_can_act_on():
    assert EXIT_MEANING[0][0] == "ok"
    assert EXIT_MEANING[2][0] == "short"
    assert EXIT_MEANING[4][0] == "blocked"
    # The distinction that matters: "thin market" and "blocked out of every
    # site" must not read the same.
    assert EXIT_MEANING[2][1] != EXIT_MEANING[4][1]
    assert "proxy" in EXIT_MEANING[4][1].lower()


def test_the_runner_pulls_the_lines_that_answer_is_this_working(tmp_path):
    runner = Runner(tmp_path, tmp_path / "runs")
    runner.state = RunState(status="running")
    for line in (
        "10:00:01 INFO    seed: 1109 listings total",
        "10:00:02 INFO    seed: 102 unique companies with a resolved own-domain",
        "10:05:00 INFO    funnel: {'people_found': 86, 'with_email': 86, 'target_role': 17}",
        "10:05:01 INFO    reachability: 0/22 companies (0%) were blocked before any page was read",
        "wrote 10 rows to E:\\Projects\\HarvestKit\\output\\smoke.csv",
    ):
        runner._absorb(line)
    assert runner.state.listings == 1109
    assert runner.state.companies == 102
    assert runner.state.funnel["people_found"] == 86
    assert "blocked before any page" in runner.state.reachability
    assert runner.state.rows_written == 10
    assert runner.state.output_path.endswith("smoke.csv")


def test_a_harvest_failure_line_is_kept_verbatim(tmp_path):
    runner = Runner(tmp_path, tmp_path / "runs")
    runner.state = RunState(status="running")
    runner._absorb("HARVEST FAILED: no seed listing was harvested at all")
    assert runner.state.message.startswith("HARVEST FAILED:")


def test_the_log_window_never_hands_back_lines_it_has_dropped(tmp_path):
    runner = Runner(tmp_path, tmp_path / "runs")
    for index in range(20):
        runner._lines.append(f"line {index}")
        runner._sequence += 1
    total, lines = runner.lines_since(15)
    assert total == 20
    assert lines == [f"line {index}" for index in range(15, 20)]


def test_state_serialises_to_json_for_the_browser(tmp_path):
    runner = Runner(tmp_path, tmp_path / "runs")
    json.dumps(runner.snapshot())  # must not raise


# ------------------------------------------------- the shipped form must work


def test_the_default_form_carries_a_seed_source():
    """The bug that made the panel's own default produce `seed: 0 listings`.

    Every seeding branch in the CLI is gated on a seed flag being *present*.
    `build_command` used to skip any flag whose value equalled the field's
    default — and the panel's default for `search_keywords` is a real file
    while the CLI's is `""`, so the one flag that decides whether anything is
    harvested at all was the one silently dropped.
    """
    leads = catalogue.JOBS["leads"]
    untouched = {item.name: item.default for item in leads.fields}
    command = catalogue.build_command(leads, untouched, "python", ROOT)

    seed_flags = {"--search-keywords", "--boards", "--jobsch-pages", "--arbeitnow-pages"}
    assert seed_flags & set(command), "the shipped form must seed something"
    assert "--search-keywords" in command
    assert command[command.index("--search-keywords") + 1] == "configs/leads/keywords.txt"


def test_a_value_equal_to_its_default_is_still_passed():
    command = build("leads", {"countries": "eu", "target": 300})
    assert command[command.index("--countries") + 1] == "eu"
    assert command[command.index("--target") + 1] == "300"


# ------------------------------------------------ a check is not a harvest


def test_checks_and_harvests_are_labelled_as_what_they_are():
    assert catalogue.JOBS["leads"].kind == "harvest"
    assert catalogue.JOBS["jobs"].kind == "harvest"
    for key in ("doctor", "egress", "proxies", "verify"):
        assert catalogue.JOBS[key].kind == "check", key


def test_a_check_is_not_reported_in_the_harvests_vocabulary():
    """Running the health check reported "Finished. The file is ready." over a
    row of zeroes — every number correct, and the whole thing nonsense, because
    a check writes no file and seeds no listings."""
    from harvestkit_ui.runner import meaning

    check_status, check_message = meaning(0, "check")
    harvest_status, harvest_message = meaning(0, "harvest")
    assert check_status == harvest_status == "ok"
    assert check_message != harvest_message
    assert "file" not in check_message.lower()

    # A failing check is an error, not a "shortfall".
    assert meaning(1, "check")[0] == "error"


def test_the_kind_reaches_the_browser(tmp_path):
    from harvestkit_ui.runner import Runner, RunState

    runner = Runner(tmp_path, tmp_path / "runs")
    runner.state = RunState(kind="check", label="Check this machine")
    assert runner.snapshot()["kind"] == "check"


def test_the_page_only_shows_the_scoreboard_for_a_harvest():
    """The fix lives in the page, so the page is where it has to be asserted."""
    page = (ROOT / "src" / "harvestkit_ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'state.kind !== "check"' in page
    assert "isHarvest" in page
