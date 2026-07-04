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


def get_engine(url=None):
    url = url or database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)
