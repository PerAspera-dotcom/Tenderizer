"""SQLAlchemy engine factory (Postgres/multi-tenancy migration).

Single source of truth for how the app connects to its database. Defaults to
a local SQLite file for backward compatibility / fast unit tests; production
points DATABASE_URL at Postgres (see docker-compose.yml's `postgres` service).
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DEFAULT_SQLITE_URL = "sqlite:///data/tenders.db"


def database_url():
    return os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)


def configured_url():
    """The explicitly-configured DATABASE_URL, or None if unset.

    Distinct from database_url(), which always returns a URL (falling back to
    the SQLite default) — Alembic needs that. store.init_db() needs to tell
    "no DATABASE_URL set" apart from "set to the SQLite default" so it can
    keep using its caller-supplied per-run SQLite path (tests rely on this
    for isolation) unless a real DATABASE_URL is configured.
    """
    return os.getenv("DATABASE_URL")


_engine = None
_engine_url = None


def get_engine(url=None):
    """Cached by URL (same pattern as auth._get_jwks_client) — api.py calls
    this on every single request via store.init_db()/_db(), so creating a
    fresh Engine (and its own connection pool) each time leaked one idle
    Postgres connection per request: verified by firing 20 concurrent
    requests and watching pg_stat_activity's idle count jump from 16 to 31.
    Under sustained real usage that climbs toward Postgres's max_connections
    and starts failing whichever request is unlucky enough to need a new
    connection when the pool is exhausted — an intermittent, load-dependent
    failure, not a per-request one, which is why it doesn't reproduce in a
    single isolated call. Tests still get per-test isolation: each uses a
    distinct tmp_path SQLite URL, so the cache simply holds one engine per
    distinct URL rather than one forever.
    """
    global _engine, _engine_url
    url = url or database_url()
    if _engine is None or _engine_url != url:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        _engine_url = url
    return _engine
