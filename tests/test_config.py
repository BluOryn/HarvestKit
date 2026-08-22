"""Config loading, validation and path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_scraper.config import load_general_config
from job_scraper.config import candidate_config_paths, load_config, resolve_config_path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS = REPO_ROOT / "configs"

MINIMAL = """
run:
  confirm_permission: true
targets:
  - name: t
    url: https://example.test/jobs
"""


def _write(tmp_path: Path, body: str) -> str:
    path = tmp_path / "c.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_every_shipped_job_config_loads():
    job_configs = [p for p in CONFIGS.rglob("*.yaml") if "general" not in p.name]
    assert job_configs, "no configs found — did configs/ move?"
    for path in job_configs:
        config = load_config(str(path))
        # A lead config may seed entirely from CLI flags (--jobsch-pages,
        # --boards, --search-keywords), so `targets` is optional there. A
        # scraper config with no targets scrapes nothing and is still a bug.
        if "leads" not in path.parts:
            assert config.targets, f"{path} has no targets"
        for target in config.targets:
            assert target.url.startswith("http"), f"{path}: {target.name} has a non-URL target"


def test_general_example_config_loads():
    config = load_general_config(str(CONFIGS / "general.example.yaml"))
    assert config.targets
    assert config.targets[0].pagination.param == "start"
    assert config.targets[0].selectors["card"]


def test_bare_names_resolve_into_configs_tree():
    assert resolve_config_path("example").endswith("example.yaml")
    assert Path(resolve_config_path("norway-big")).parent.name == "regions"
    assert Path(resolve_config_path("jobsch")).parent.name == "sites"


def test_missing_config_lists_where_it_looked():
    with pytest.raises(FileNotFoundError) as exc:
        resolve_config_path("definitely-not-a-config")
    assert "Looked in" in str(exc.value)
    assert candidate_config_paths("definitely-not-a-config")


def test_valid_minimal_config_loads(tmp_path):
    config = load_config(_write(tmp_path, MINIMAL))
    assert config.run.confirm_permission is True
    assert config.targets[0].adapter == "auto"


def test_unknown_run_key_is_rejected(tmp_path):
    """A typo used to be silently ignored, leaving the default in force."""
    path = _write(tmp_path, "run:\n  deep_concurency: 12\n")
    with pytest.raises(ValueError, match="deep_concurency"):
        load_config(path)


def test_general_mode_config_is_rejected_by_the_job_loader(tmp_path):
    path = _write(tmp_path, "mode: general\nrun:\n  confirm_permission: true\n")
    with pytest.raises(ValueError, match="--general"):
        load_config(path)


def test_target_without_url_is_rejected(tmp_path):
    path = _write(tmp_path, "targets:\n  - name: broken\n")
    with pytest.raises(ValueError, match="missing required key"):
        load_config(path)


def test_empty_target_url_is_rejected(tmp_path):
    path = _write(tmp_path, "targets:\n  - name: broken\n    url: ''\n")
    with pytest.raises(ValueError, match="no url"):
        load_config(path)


def test_scalar_where_a_list_belongs_is_wrapped_not_exploded(tmp_path):
    """`include: python` must mean one keyword, not eight single characters."""
    path = _write(tmp_path, "keywords:\n  include: python\n")
    assert load_config(path).keywords.include == ["python"]


def test_proxies_file_is_merged_and_deduped(tmp_path):
    (tmp_path / "proxies.txt").write_text("# comment\nhttp://a:1\n\n b:2 \nhttp://a:1\n", encoding="utf-8")
    path = _write(
        tmp_path,
        "run:\n  proxies: ['http://a:1']\n  proxies_file: proxies.txt\n",
    )
    assert load_config(path).run.proxies == ["http://a:1", "http://b:2"]


def test_unknown_general_selector_key_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "mode: general\ntargets:\n  - name: t\n    url: https://e.test\n"
        "    selectors:\n      card: div\n      titel: a\n",
    )
    with pytest.raises(ValueError, match="titel"):
        load_general_config(path)
