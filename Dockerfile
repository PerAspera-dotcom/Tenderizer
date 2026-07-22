FROM python:3.12-slim

WORKDIR /app

# postgresql-client: pg_dump/pg_restore for CR-004 F4's backup/restore
# (src/backup.py) — not needed for the app itself, only for the daily
# backup job and `tenderizer restore`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY config/ config/
COPY alembic.ini .
COPY alembic/ alembic/

RUN pip install --no-cache-dir .

EXPOSE 8000
# Migrate before serving — runs inside Railway's private network on every
# boot, so schema changes land without ever needing the Postgres TCP proxy
# reopened again (see DEPLOYMENT.md). Idempotent: alembic no-ops when
# already at head.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
