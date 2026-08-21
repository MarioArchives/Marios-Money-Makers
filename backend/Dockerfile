# Multi-stage image for the FastAPI backend.
#
#   dev  -> `docker compose up` (docker-compose.yml): dev deps, --reload,
#           source bind-mounted by compose.
#   prod -> docker-compose.prod.yml: locked runtime deps only, non-root,
#           no reload, behind Caddy on the same box.
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./

# ---------------------------------------------------------------- dev ----
FROM base AS dev
RUN uv sync --frozen
COPY app ./app
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --------------------------------------------------------------- prod ----
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY app ./app
# SQLite lives in /app/data (config.DB_PATH default); the named volume in
# docker-compose.prod.yml inherits this ownership on first use.
RUN useradd --system --uid 10001 --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
