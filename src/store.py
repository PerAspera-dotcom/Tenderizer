"""SQLAlchemy-backed storage with hash-based dedup.

Step 2 of the Postgres/multi-tenancy migration: ported from raw sqlite3 to
SQLAlchemy Core (see schema.py for the table definitions), but every function
signature and behavior is unchanged — `conn` is now a SQLAlchemy Engine
instead of a sqlite3.Connection, but callers never touch it directly, they
only ever pass it back into store.*() functions, so this swap is invisible
to them. Still SQLite-only at this step; tenant_id and the Postgres cutover
are steps 3-4.
"""
import json
from datetime import date

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import OperationalError

import db
from normalize import record_hash
from schema import TENDERS_COLUMNS as COLUMNS
from schema import metadata, pipeline, tenders, translations

_JSON = {"cpv_codes", "matched_terms", "supersedes"}
_EMPTY_DEFAULT = {"value", "value_currency", "value_eur", "fx_rate_date",
                  "language", "tag_line_en", "description_en", "translation_status"}

PIPELINE_FIELDS = {"submission_status", "deadline_override", "owner", "notes",
                   "submitted_date", "result_due", "outcome"}

# Additive migrations for SQLite DBs that predate later columns. A fresh DB
# already has every column via metadata.create_all() below, so these are
# harmless no-ops there (caught by the try/except, one statement at a time
# so a single failure can't poison the others).
_TENDERS_MIGRATIONS = [
    "ALTER TABLE tenders ADD COLUMN status TEXT DEFAULT 'new'",
    "ALTER TABLE tenders ADD COLUMN exclude_reason TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN value TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN value_currency TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN value_eur TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN fx_rate_date TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN supersedes TEXT DEFAULT '[]'",
    "ALTER TABLE tenders ADD COLUMN language TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN tag_line_en TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN description_en TEXT DEFAULT ''",
    "ALTER TABLE tenders ADD COLUMN translation_status TEXT DEFAULT ''",
]


def init_db(path):
    engine = db.get_engine(f"sqlite:///{path}")
    metadata.create_all(engine, checkfirst=True)
    for stmt in _TENDERS_MIGRATIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except OperationalError:
            pass
    return engine


def upsert(conn, record):
    h = record_hash(record)
    with conn.begin() as c:
        if c.execute(select(tenders.c.hash).where(tenders.c.hash == h)).fetchone():
            return False
        fs = record.get("first_seen") or date.today().isoformat()
        values = {"hash": h, "first_seen": fs}
        for col in COLUMNS:
            if col in ("hash", "first_seen"):
                continue
            elif col == "status":
                values[col] = record.get("status", "new")
            elif col in _JSON:
                values[col] = json.dumps(record.get(col, []))
            elif col in _EMPTY_DEFAULT:
                values[col] = record.get(col) or ""  # None (no value/not translated) -> ''
            else:
                values[col] = record.get(col, "")
        c.execute(insert(tenders).values(**values))
    return True


def all_records(conn):
    with conn.connect() as c:
        rows = c.execute(select(*[tenders.c[col] for col in COLUMNS])).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col])
        out.append(rec)
    return out


def set_status(conn, pub_number, status):
    with conn.begin() as c:
        c.execute(update(tenders).where(tenders.c.pub_number == pub_number).values(status=status))


def set_translation(conn, pub_number, tag_line_en, description_en, status):
    with conn.begin() as c:
        c.execute(update(tenders).where(tenders.c.pub_number == pub_number).values(
            tag_line_en=tag_line_en, description_en=description_en, translation_status=status))


def mark_superseded(conn, kept_pub_number, superseded_records):
    """CR-001 D-DUP: collapse republished duplicates into `kept_pub_number`.

    Each record in `superseded_records` (full record dicts, so their own prior
    `supersedes` can be folded in — a multi-generation republish chain still
    shows full version history on the latest kept record) gets
    exclude_reason='superseded' (auditable, not deleted). The kept record's
    `supersedes` accumulates their pub_numbers.
    """
    all_superseded = []
    with conn.begin() as c:
        for r in superseded_records:
            all_superseded.append(r["pub_number"])
            all_superseded.extend(r.get("supersedes") or [])
            c.execute(update(tenders).where(tenders.c.pub_number == r["pub_number"])
                      .values(exclude_reason="superseded"))
        row = c.execute(select(tenders.c.supersedes)
                         .where(tenders.c.pub_number == kept_pub_number)).fetchone()
        existing = json.loads(row[0]) if row and row[0] else []
        merged = sorted(set(existing) | set(all_superseded))
        c.execute(update(tenders).where(tenders.c.pub_number == kept_pub_number)
                  .values(supersedes=json.dumps(merged)))


# ── Translation cache (CR-001 R3/C1) ─────────────────────────────────────────

def get_cached_translation(conn, content_hash):
    with conn.connect() as c:
        row = c.execute(select(translations.c.translated_text)
                         .where(translations.c.content_hash == content_hash)).fetchone()
    return row[0] if row else None


def cache_translation(conn, content_hash, translated_text):
    values = {"translated_text": translated_text, "cached_at": date.today().isoformat()}
    with conn.begin() as c:
        exists = c.execute(select(translations.c.content_hash)
                            .where(translations.c.content_hash == content_hash)).fetchone()
        if exists:
            c.execute(update(translations).where(translations.c.content_hash == content_hash)
                      .values(**values))
        else:
            c.execute(insert(translations).values(content_hash=content_hash, **values))


# ── Portal workflow store (§5.4) ─────────────────────────────────────────────

def ensure_pipeline_entry(conn, pub_number):
    with conn.begin() as c:
        exists = c.execute(select(pipeline.c.pub_number)
                            .where(pipeline.c.pub_number == pub_number)).fetchone()
        if not exists:
            c.execute(insert(pipeline).values(pub_number=pub_number))


def set_pipeline_entry(conn, pub_number, fields):
    valid = {k: v for k, v in fields.items() if k in PIPELINE_FIELDS}
    if not valid:
        return
    with conn.begin() as c:
        c.execute(update(pipeline).where(pipeline.c.pub_number == pub_number).values(**valid))


def get_pipeline_entries(conn):
    """Shortlisted tenders joined with their pipeline state."""
    p_cols = ["submission_status", "deadline_override", "owner", "notes"]
    cols = [tenders.c[col] for col in COLUMNS] + [
        func.coalesce(pipeline.c.submission_status, "not_started").label("submission_status"),
        pipeline.c.deadline_override, pipeline.c.owner, pipeline.c.notes,
    ]
    stmt = (select(*cols)
            .select_from(tenders.outerjoin(pipeline, tenders.c.pub_number == pipeline.c.pub_number))
            .where(tenders.c.status == "shortlisted"))
    with conn.connect() as c:
        rows = c.execute(stmt).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col] or "[]")
        out.append(rec)
    return out


def get_followup_entries(conn):
    """Pipeline entries with submission_status='submitted' joined with tender data."""
    p_cols = ["submission_status", "deadline_override", "owner", "notes",
              "submitted_date", "result_due", "outcome"]
    cols = [tenders.c[col] for col in COLUMNS] + [pipeline.c[pc] for pc in p_cols]
    stmt = (select(*cols)
            .select_from(tenders.join(pipeline, tenders.c.pub_number == pipeline.c.pub_number))
            .where(pipeline.c.submission_status == "submitted"))
    with conn.connect() as c:
        rows = c.execute(stmt).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col] or "[]")
        out.append(rec)
    return out
