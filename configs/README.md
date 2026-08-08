# configs/

Every HarvestKit config lives here. Nothing is loaded from the repo root any more —
previously the YAML files sat at the root while `docker-compose.yml` mounted a
`./configs` directory that did not exist, so the documented Docker invocation
could never find a config.

## Layout

```text
configs/
  example.yaml            Job mode — start here, copy and edit
  general.example.yaml    General mode (businesses/places) — Yelp + Maps template
  regions/                Multi-target country/region fan-outs
    germany.yaml          ~100 Arbeitsagentur queries
    norway-big.yaml       Norway IT, wide fan-out
    norway-it.yaml        Norway IT, past 30 days
    norway-top5.yaml      Norway top-5 boards, verified URLs
  sites/                  Single-site smoke configs
    finn.yaml
    jobsch.yaml
    karrierestart.yaml
    nav.yaml
```

## Picking one

`--config` takes a bare name, a filename, or a path. Bare names are resolved
against `configs/`, `configs/regions/` and `configs/sites/`, relative to both the
current directory and the repo root:

```bash
python run.py --config example            # → configs/example.yaml
python run.py --config norway-big         # → configs/regions/norway-big.yaml
python run.py --config jobsch.yaml        # → configs/sites/jobsch.yaml
python run.py --config /abs/path/my.yaml  # → used as-is
```

The default is `example.yaml`. Docker mounts this directory at `/app/configs`,
which is also on the search path.

## Keeping private configs out of git

`.gitignore` excludes `configs/local/` plus any `*.local.yaml` / `*.private.yaml`.
Put anything with credentials, internal URLs, or a proxy list there.

## Schema

Unknown keys are now a hard error rather than being silently ignored — a typo in
`deep_concurency:` used to leave the default in place with no warning. The full
key reference is in the root [README](../README.md#config--full-reference); the
authoritative definition is the dataclass set in
[`src/job_scraper/config.py`](../src/job_scraper/config.py).

General-mode configs additionally need a top-level `mode: general` and are run
with `python run.py --general --config general.example`.
