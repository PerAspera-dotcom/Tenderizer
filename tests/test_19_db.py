"""Step 19 — SQLAlchemy engine factory (Postgres/multi-tenancy migration, step 1).
  db.database_url() -> str, from DATABASE_URL env var or a SQLite default.
  db.get_engine(url=None) -> sqlalchemy.Engine
"""
import db


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
