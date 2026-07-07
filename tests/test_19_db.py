"""Step 19 — SQLAlchemy engine factory (Postgres/multi-tenancy migration, step 1).
  db.database_url() -> str, from DATABASE_URL env var or a SQLite default.
  db.get_engine(url=None) -> sqlalchemy.Engine
"""
import pytest

import db


@pytest.fixture(autouse=True)
def _reset_engine_cache(monkeypatch):
    # get_engine() memoizes its Engine at module scope (see its docstring) —
    # every test gets a clean slate regardless of test order.
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_engine_url", None)


def test_database_url_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.database_url() == db.DEFAULT_SQLITE_URL


def test_database_url_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/tenderizer")
    assert db.database_url() == "postgresql://u:p@localhost/tenderizer"


def test_get_engine_sqlite(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 't.db'}")
    assert engine.dialect.name == "sqlite"


def test_get_engine_postgres_url_does_not_need_a_live_connection():
    # get_engine() only builds the Engine object; SQLAlchemy engines are lazy
    # and don't connect until first use, so this must not require a live DB.
    engine = db.get_engine("postgresql://u:p@localhost/tenderizer")
    assert engine.dialect.name == "postgresql"


def test_get_engine_returns_the_same_engine_for_the_same_url():
    # Regression: api.py calls get_engine() (via store.init_db/_db()) on
    # every single request. Before this cached by URL, that created a brand
    # new Engine (and its own connection pool) per request, none ever
    # disposed — confirmed leaking real idle Postgres connections under load
    # (16 -> 31 idle after 20 concurrent requests against the real DB).
    url = "postgresql://u:p@localhost/tenderizer"
    assert db.get_engine(url) is db.get_engine(url)


def test_get_engine_returns_a_new_engine_for_a_different_url():
    # The cache must key on URL, not just "has an engine ever been built" —
    # otherwise a second call with a different URL (e.g. a different test's
    # tmp_path SQLite file) would wrongly get the first URL's engine.
    first = db.get_engine("sqlite:///a.db")
    second = db.get_engine("sqlite:///b.db")
    assert first is not second
