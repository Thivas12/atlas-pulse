# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    ATLAS_EMBEDDING_CACHE_DIR=/opt/atlas-models \
    ATLAS_EMBEDDING_MODEL_PATH=/opt/atlas-models/bge-small-en-v1.5 \
    ATLAS_EMBEDDING_LOCAL_FILES_ONLY=true \
    PATH="/opt/venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system atlas \
    && useradd --system --gid atlas --create-home atlas

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /opt/atlas-models \
    && python scripts/cache_embedding_model.py \
    && mkdir -p /app/data/raw \
    && chown -R atlas:atlas /app/data /opt/atlas-models

USER atlas
EXPOSE 8000

CMD ["uvicorn", "atlas_pulse.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
