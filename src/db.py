"""SQLAlchemy engine factory (Postgres/multi-tenancy migration, step 1).

Single source of truth for how the app connects to its database. Defaults to
a local SQLite file for backward compatibility / fast unit tests; production
points DATABASE_URL at Postgres (see docker-compose.yml's `postgres` service).

Scaffolding only at this step — nothing calls get_engine() yet.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DEFAULT_SQLITE_URL = "sqlite:///data/tenders.db"


def database_url():
    return os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)


def get_engine(url=None):
    url = url or database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)
