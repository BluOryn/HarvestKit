# HarvestKit — Python CLI image with Playwright + Chromium baked in.
# Build:   docker build -t harvestkit:local .
# Run:     docker run --rm -v "$(pwd)/output:/app/output" harvestkit:local --config example
#          docker run --rm -v "$(pwd)/configs:/app/configs" -v "$(pwd)/output:/app/output" \
#                     harvestkit:local --config norway-big
#
# The bundled configs/ tree ships in the image, so `--config example` works with
# no mount. Mount your own configs/ over /app/configs to use private ones.
#
# Image is ~1.2 GB (Playwright + Chromium account for ~500 MB).
# For a slim build without Playwright, use the `slim` stage:
#   docker build --target slim -t harvestkit:slim .
FROM python:3.11-slim AS slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for lxml + tldextract
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev libffi-dev \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt README.md LICENSE ./
COPY src ./src
COPY run.py ./

RUN pip install -e ".[exports,llm]"

# Ship the reference configs so `--config example` works without a mount;
# a bind-mount at /app/configs simply shadows them.
COPY configs ./configs

RUN mkdir -p /app/output /app/.cache

ENTRYPOINT ["python", "run.py"]
CMD ["--help"]


# ---- Playwright stage (default) ----
FROM slim AS playwright

# Playwright requires more system libs for headless Chromium
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m playwright install --with-deps chromium
