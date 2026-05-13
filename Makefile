.PHONY: help install dev test lint format type-check check clean run smoke ext-build ext-watch docker docker-run

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip

help:
	@echo "HarvestKit — common tasks"
	@echo ""
	@echo "  make install        install runtime deps into current venv"
	@echo "  make dev            install dev + llm + exports extras"
	@echo "  make test           run pytest"
	@echo "  make lint           ruff check src/"
	@echo "  make format         black src/"
	@echo "  make type-check     mypy src/job_scraper/"
	@echo "  make check          lint + type-check + test"
	@echo "  make clean          remove caches + outputs"
	@echo "  make smoke          tiny smoke run (config.example.yaml)"
	@echo "  make run CONFIG=... run with a specific config"
	@echo "  make ext-build      build the Chrome extension once"
	@echo "  make ext-watch      rebuild extension on file change"
	@echo "  make docker         docker build (Playwright variant)"
	@echo "  make docker-run     docker compose run --rm harvestkit --help"

install:
	$(PIP) install -r requirements.txt

dev:
	$(PIP) install -e ".[dev,llm,exports]"
	$(PYTHON) -m playwright install chromium

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src/

format:
	$(PYTHON) -m black src/

type-check:
	$(PYTHON) -m mypy src/job_scraper/ || true

check: lint type-check test

clean:
	rm -rf .cache/ output/*.csv output/*.json .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

smoke:
	$(PYTHON) run.py --config config.example.yaml --confirm-permission --max-pages 1

CONFIG ?= config.example.yaml
run:
	$(PYTHON) run.py --config $(CONFIG)

ext-build:
	cd extension && npm ci && node build.mjs

ext-watch:
	cd extension && node build.mjs --watch

docker:
	docker build --target playwright -t harvestkit:local .

docker-run:
	docker compose run --rm harvestkit --help
