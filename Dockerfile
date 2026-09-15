# syntax=docker/dockerfile:1.27.0@sha256:bde3983e9c939224420ddaf6b784cc30e09b035a4dea01f581230c50809f372e
FROM docker.io/astral/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ARG ATLAS_BUILD_COMMIT_SHA=unknown

LABEL org.opencontainers.image.revision=$ATLAS_BUILD_COMMIT_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    ATLAS_EMBEDDING_CACHE_DIR=/opt/atlas-models \
    ATLAS_EMBEDDING_MODEL_PATH=/opt/atlas-models/bge-small-en-v1.5 \
    ATLAS_EMBEDDING_LOCAL_FILES_ONLY=true \
    ATLAS_BUILD_COMMIT_SHA=$ATLAS_BUILD_COMMIT_SHA \
    PATH="/opt/venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get upgrade --yes \
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
