# syntax=docker/dockerfile:1.7

FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY evals/scenarios ./evals/scenarios
COPY policies ./policies

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[anthropic,google,otel]"


FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system nightjar \
    && useradd --system --gid nightjar --home /app --shell /usr/sbin/nologin nightjar

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=nightjar:nightjar alembic.ini ./
COPY --chown=nightjar:nightjar alembic ./alembic
COPY --chown=nightjar:nightjar src ./src
COPY --chown=nightjar:nightjar evals ./evals
COPY --chown=nightjar:nightjar policies ./policies

USER nightjar

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["nightjar-api"]
