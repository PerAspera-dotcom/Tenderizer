"""CR-004 F4 — backup/restore (src/backup.py). Every test forces the sqlite
path (monkeypatches db.database_url) and a temp BACKUP_DIR/LOG_PATH so
nothing here ever touches the real data/backups/ directory or needs a real
Postgres/S3 endpoint.
"""
import json
import os
import sqlite3

import pytest

import backup
import db


@pytest.fixture(autouse=True)
def _isolated_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(backup, "LOG_PATH", str(tmp_path / "backups" / "backup_log.json"))


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    db_path = tmp_path / "tenders.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "database_url", lambda: f"sqlite:///{db_path}")
    return db_path


def test_create_dump_sqlite_copies_data(sqlite_db, tmp_path):
    out_dir = str(tmp_path / "out")
    dump_path = backup.create_dump(out_dir)
    assert os.path.exists(dump_path)
    conn = sqlite3.connect(dump_path)
    assert conn.execute("SELECT v FROM t").fetchone() == ("hello",)


def test_upload_without_s3_config_stays_local(sqlite_db, monkeypatch, tmp_path):
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    dump_path = backup.create_dump(str(tmp_path / "out"))
    assert backup.upload(dump_path) is None  # no remote key — nothing configured


def test_run_backup_ok_writes_log(sqlite_db, monkeypatch):
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    result = backup.run_backup()
    assert result["status"] == "ok"
    assert os.path.exists(backup.LOG_PATH)
    with open(backup.LOG_PATH) as f:
        log = json.load(f)
    assert log[-1]["status"] == "ok"


def test_run_backup_failure_alerts_and_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "database_url", lambda: f"sqlite:///{tmp_path / 'nope' / 'missing.db'}")
    calls = []
    monkeypatch.setattr("alerts.send_alert", lambda subject, message: calls.append((subject, message)))
    result = backup.run_backup()
    assert result["status"] == "failed"
    assert len(calls) == 1
    with open(backup.LOG_PATH) as f:
        log = json.load(f)
    assert log[-1]["status"] == "failed"


def test_restore_refuses_without_confirm(sqlite_db):
    dump_path = backup.create_dump(backup.BACKUP_DIR)
    with pytest.raises(RuntimeError, match="confirm"):
        backup.restore_backup("2026-07-01")


def test_restore_roundtrip_sqlite(sqlite_db, monkeypatch):
    # Create a backup while the DB has one row, then corrupt/replace the live
    # DB, then restore and confirm the original row comes back.
    dump_path = backup.create_dump(backup.BACKUP_DIR)
    stamp = os.path.basename(dump_path).split("_", 1)[1].split(".")[0]  # YYYYMMDD_HHMMSS
    date_str = stamp.split("_")[0]

    live_path = backup._local_sqlite_path(db.database_url())
    conn = sqlite3.connect(live_path)
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t (v) VALUES ('corrupted')")
    conn.commit()
    conn.close()

    result = backup.restore_backup(date_str, confirm=True)
    assert result["restored_from"] == dump_path

    conn = sqlite3.connect(live_path)
    assert conn.execute("SELECT v FROM t").fetchone() == ("hello",)


def test_restore_raises_when_no_backup_found(sqlite_db):
    with pytest.raises(RuntimeError, match="No backup found"):
        backup.restore_backup("19990101", confirm=True)


def test_cleanup_old_backups_removes_stale_local_files(sqlite_db, tmp_path, monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone
    dump_path = backup.create_dump(backup.BACKUP_DIR)
    old_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(dump_path, (old_time, old_time))
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    removed = backup.cleanup_old_backups(retention_days=30)
    assert dump_path in removed
    assert not os.path.exists(dump_path)
