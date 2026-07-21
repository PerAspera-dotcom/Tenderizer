"""CR-004 F4 — automated backups + restore.

Backs up whichever database is actually configured (`db.database_url()`):
Postgres in production (via `pg_dump`'s custom format, restorable with
`pg_restore`), or the local SQLite file in dev (via sqlite3's own online
backup API — safe against a concurrent writer, unlike a raw file copy).

Upload target is S3-compatible object storage, configured via BACKUP_S3_*
env vars. AWS S3, Backblaze B2, and GCS (interop mode) all speak this same
API via a custom `endpoint_url` — one code path covers all three rather than
a separate SDK per provider. Unconfigured = backups still run and land in
the local `data/backups/` directory (useful for dev/manual testing), but
never leave the box — that's a real gap for disaster recovery, not a design
choice, and is logged as a warning every run so it can't go unnoticed.
"""
import glob
import json
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import alerts
import db

logger = logging.getLogger(__name__)

BACKUP_DIR = "data/backups"
LOG_PATH = "data/backups/backup_log.json"
DEFAULT_RETENTION_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def _local_sqlite_path(url):
    # sqlite:///relative/path.db or sqlite:////absolute/path.db
    return url.split("sqlite:///", 1)[1]


def create_dump(out_dir=None):
    """Writes a fresh local dump file and returns its path. Raises on
    failure (pg_dump missing/erroring, sqlite file missing) — the caller
    decides how to alert, this just does the work.

    `out_dir` defaults to the *current* module-level BACKUP_DIR, read at
    call time rather than bound as the parameter's default — a default
    value of `out_dir=BACKUP_DIR` would freeze in the constant at import
    time, so a test (or any caller) monkeypatching `backup.BACKUP_DIR`
    afterwards would silently be ignored by run_backup()'s no-arg call.
    """
    out_dir = out_dir or BACKUP_DIR
    os.makedirs(out_dir, exist_ok=True)
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    url = db.database_url()

    if url.startswith("sqlite"):
        src_path = _local_sqlite_path(url)
        out_path = os.path.join(out_dir, f"tenders_{stamp}.sqlite")
        src_conn = sqlite3.connect(src_path)
        try:
            dst_conn = sqlite3.connect(out_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        return out_path

    out_path = os.path.join(out_dir, f"tenders_{stamp}.dump")
    result = subprocess.run(
        ["pg_dump", "--format=custom", f"--file={out_path}", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {result.stderr[:2000]}")
    return out_path


def _s3_client():
    bucket = os.getenv("BACKUP_S3_BUCKET")
    if not bucket:
        return None, None
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("BACKUP_S3_ENDPOINT_URL") or None,
        aws_access_key_id=os.getenv("BACKUP_S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("BACKUP_S3_SECRET_ACCESS_KEY") or None,
        region_name=os.getenv("BACKUP_S3_REGION") or None,
    )
    return client, bucket


def upload(local_path):
    """Uploads to S3-compatible storage if BACKUP_S3_BUCKET is configured;
    otherwise the file already sits in BACKUP_DIR from create_dump(), so
    there's nothing more to do. Returns the remote key, or None when only a
    local copy exists.
    """
    client, bucket = _s3_client()
    if client is None:
        logger.warning("BACKUP_S3_BUCKET not configured — backup stayed local only (%s)", local_path)
        return None
    key = os.path.basename(local_path)
    client.upload_file(local_path, bucket, key)
    return key


def _list_local_backups():
    paths = sorted(glob.glob(os.path.join(BACKUP_DIR, "tenders_*.sqlite"))
                    + glob.glob(os.path.join(BACKUP_DIR, "tenders_*.dump")))
    return paths


def _list_remote_backups():
    client, bucket = _s3_client()
    if client is None:
        return []
    resp = client.list_objects_v2(Bucket=bucket, Prefix="tenders_")
    return [obj["Key"] for obj in resp.get("Contents", [])]


def cleanup_old_backups(retention_days=None):
    """Deletes local (and, if configured, remote) backups older than the
    retention window. Returns the list of paths/keys removed.
    """
    retention_days = retention_days or int(os.getenv("BACKUP_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    cutoff = _now() - timedelta(days=retention_days)
    removed = []

    for path in _list_local_backups():
        if datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc) < cutoff:
            os.remove(path)
            removed.append(path)

    client, bucket = _s3_client()
    if client is not None:
        resp = client.list_objects_v2(Bucket=bucket, Prefix="tenders_")
        for obj in resp.get("Contents", []):
            if obj["LastModified"] < cutoff:
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                removed.append(obj["Key"])
    return removed


def _append_log(entry):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    history = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            history = json.load(f)
    history.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(history[-90:], f, indent=2)  # ~3 months of daily entries


def run_backup():
    """The daily job: dump -> upload -> cleanup -> log. Never raises — a
    failure is caught, alerted (Sentry + email, both inert if unconfigured),
    and logged, so it can never take down the scheduler or a manual CLI run.
    """
    entry = {"timestamp": _now().isoformat(timespec="seconds")}
    try:
        local_path = create_dump()
        remote_key = upload(local_path)
        removed = cleanup_old_backups()
        entry.update({"status": "ok", "local_path": local_path, "remote_key": remote_key,
                      "removed": removed})
    except Exception as e:
        entry.update({"status": "failed", "error": str(e)})
        alerts.send_alert("Tenderizer backup failed", str(e))
        logger.exception("backup failed")
    _append_log(entry)
    return entry


def _download(key, dest_path):
    client, bucket = _s3_client()
    if client is None:
        raise RuntimeError("BACKUP_S3_BUCKET not configured — nothing to download from")
    client.download_file(bucket, key, dest_path)


def _find_backup_for_date(date_str):
    """The most recent backup (local, then remote) whose filename stamp
    starts with `date_str` (YYYYMMDD or YYYY-MM-DD, either works).
    """
    needle = date_str.replace("-", "")
    for path in reversed(_list_local_backups()):
        if needle in os.path.basename(path):
            return path, None
    for key in reversed(_list_remote_backups()):
        if needle in key:
            return None, key
    return None, None


def restore_backup(date_str, confirm=False):
    """Downloads (if needed), integrity-checks, then swaps the backup for
    `date_str` into place. Destructive — refuses to run without confirm=True
    (the CLI is the only caller and always requires an explicit --yes flag;
    this is the last line of defense against an accidental call).
    """
    if not confirm:
        raise RuntimeError("restore_backup requires confirm=True — this overwrites the live database")

    local_path, remote_key = _find_backup_for_date(date_str)
    if local_path is None and remote_key is None:
        raise RuntimeError(f"No backup found for {date_str}")
    if local_path is None:
        local_path = os.path.join(BACKUP_DIR, remote_key)
        _download(remote_key, local_path)

    url = db.database_url()
    if url.startswith("sqlite"):
        # Integrity-check the candidate before touching the live file.
        check_conn = sqlite3.connect(local_path)
        try:
            ok = check_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            check_conn.close()
        if not ok:
            raise RuntimeError(f"Backup {local_path} failed PRAGMA integrity_check — refusing to restore")
        live_path = _local_sqlite_path(url)
        shutil.copyfile(local_path, live_path)
        return {"restored_from": local_path, "target": live_path}

    if os.path.getsize(local_path) == 0:
        raise RuntimeError(f"Backup {local_path} is empty — refusing to restore")
    result = subprocess.run(["pg_restore", "--clean", "--if-exists", f"--dbname={url}", local_path],
                             capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore failed (exit {result.returncode}): {result.stderr[:2000]}")
    return {"restored_from": local_path, "target": "DATABASE_URL"}
