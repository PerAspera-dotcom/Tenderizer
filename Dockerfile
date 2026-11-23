# Pinned to the Python the working production image ran (3.12.16), not the
# floating 3.12-slim tag — see docs/INCIDENT_2026-11-23_BACKEND_DEPLOY.md.
FROM python:3.12.16-slim

WORKDIR /app

# postgresql-client: pg_dump/pg_restore for CR-004 F4's backup/restore
# (src/backup.py) — not needed for the app itself, only for the daily
# backup job and `tenderizer restore`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.lock ./
COPY src/ src/
COPY config/ config/
COPY alembic.ini .
COPY alembic/ alembic/

# -c requirements.lock: every package, transitive ones included, installs at
# the exact version production ran. An unpinned rebuild on 2026-11-23 pulled
# sentry-sdk 3.0.0 and crashed the app at startup.
RUN pip install --no-cache-dir -c requirements.lock .

EXPOSE 8000
# Migrate before serving — runs inside Railway's private network on every
# boot, so schema changes land without ever needing the Postgres TCP proxy
# reopened again (see DEPLOYMENT.md). Idempotent: alembic no-ops when
# already at head.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
