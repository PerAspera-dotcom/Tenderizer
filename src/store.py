"""SQLite storage with hash-based dedup."""
import sqlite3, json
from datetime import date
from normalize import record_hash

COLUMNS = ["hash", "source", "pub_number", "tag_line", "description", "buyer", "country",
           "place", "category", "procedure", "pub_date", "deadline", "cpv_codes",
           "matched_terms", "match_source", "url", "first_seen", "status", "exclude_reason",
           "value", "value_currency", "value_eur", "fx_rate_date", "supersedes",
           "language", "tag_line_en", "description_en", "translation_status"]
_JSON = {"cpv_codes", "matched_terms", "supersedes"}

PIPELINE_FIELDS = {"submission_status", "deadline_override", "owner", "notes",
                   "submitted_date", "result_due", "outcome"}

def init_db(path):
    conn = sqlite3.connect(path)
    cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS tenders({cols}, PRIMARY KEY(hash))")
    # Additive migrations for existing DBs that predate these columns
    for stmt in ("ALTER TABLE tenders ADD COLUMN status TEXT DEFAULT 'new'",
                 "ALTER TABLE tenders ADD COLUMN exclude_reason TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN value TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN value_currency TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN value_eur TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN fx_rate_date TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN supersedes TEXT DEFAULT '[]'",
                 "ALTER TABLE tenders ADD COLUMN language TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN tag_line_en TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN description_en TEXT DEFAULT ''",
                 "ALTER TABLE tenders ADD COLUMN translation_status TEXT DEFAULT ''"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    init_pipeline(conn)
    init_translations(conn)
    return conn

def init_translations(conn):
    """CR-001 R3/C1: translation cache, keyed by content hash (sha256 of the
    source text) — so the same notice/document text is never sent to DeepL
    twice, across runs. Generic (not tied to `tenders`): also usable by
    Composer's document-ingest translation once that pipeline exists.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS translations(
        content_hash TEXT PRIMARY KEY,
        translated_text TEXT,
        cached_at TEXT
    )""")
    conn.commit()

def get_cached_translation(conn, content_hash):
    row = conn.execute("SELECT translated_text FROM translations WHERE content_hash=?",
                        (content_hash,)).fetchone()
    return row[0] if row else None

def cache_translation(conn, content_hash, translated_text):
    conn.execute(
        "INSERT OR REPLACE INTO translations(content_hash, translated_text, cached_at) VALUES (?,?,?)",
        (content_hash, translated_text, date.today().isoformat()))
    conn.commit()

def init_pipeline(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pipeline(
        pub_number TEXT PRIMARY KEY,
        submission_status TEXT DEFAULT 'not_started',
        deadline_override TEXT,
        owner TEXT,
        notes TEXT,
        submitted_date TEXT,
        result_due TEXT,
        outcome TEXT DEFAULT 'pending'
    )""")
    conn.commit()

def upsert(conn, record):
    h = record_hash(record)
    if conn.execute("SELECT 1 FROM tenders WHERE hash=?", (h,)).fetchone():
        return False
    fs = record.get("first_seen") or date.today().isoformat()
    values = []
    for c in COLUMNS:
        if c == "hash":
            values.append(h)
        elif c == "first_seen":
            values.append(fs)
        elif c == "status":
            values.append(record.get("status", "new"))
        elif c in _JSON:
            values.append(json.dumps(record.get(c, [])))
        elif c in ("value", "value_currency", "value_eur", "fx_rate_date",
                   "language", "tag_line_en", "description_en", "translation_status"):
            values.append(record.get(c) or "")  # None (no value/not translated) -> ''
        else:
            values.append(record.get(c, ""))
    cols_str = ", ".join(COLUMNS)
    placeholders = ", ".join("?" * len(COLUMNS))
    conn.execute(f"INSERT INTO tenders({cols_str}) VALUES ({placeholders})", values)
    conn.commit()
    return True

def all_records(conn):
    out = []
    for r in conn.execute(f"SELECT {','.join(COLUMNS)} FROM tenders"):
        rec = dict(zip(COLUMNS, r))
        for c in _JSON:
            rec[c] = json.loads(rec[c])
        out.append(rec)
    return out

def set_status(conn, pub_number, status):
    conn.execute("UPDATE tenders SET status=? WHERE pub_number=?", (status, pub_number))
    conn.commit()

def set_translation(conn, pub_number, tag_line_en, description_en, status):
    """CR-001 R3: record a translation attempt's outcome. status is 'ok' or
    'unavailable' — the fields are stored either way so a partially-successful
    attempt (e.g. title translated, description call failed) isn't discarded.
    """
    conn.execute(
        "UPDATE tenders SET tag_line_en=?, description_en=?, translation_status=? WHERE pub_number=?",
        (tag_line_en, description_en, status, pub_number))
    conn.commit()

def mark_superseded(conn, kept_pub_number, superseded_records):
    """CR-001 D-DUP: collapse republished duplicates into `kept_pub_number`.

    Each record in `superseded_records` (full record dicts, so their own prior
    `supersedes` can be folded in — a multi-generation republish chain still
    shows full version history on the latest kept record) gets
    exclude_reason='superseded' (auditable, not deleted). The kept record's
    `supersedes` accumulates their pub_numbers.
    """
    all_superseded = []
    for r in superseded_records:
        all_superseded.append(r["pub_number"])
        all_superseded.extend(r.get("supersedes") or [])
        conn.execute("UPDATE tenders SET exclude_reason=? WHERE pub_number=?",
                     ("superseded", r["pub_number"]))
    row = conn.execute("SELECT supersedes FROM tenders WHERE pub_number=?",
                        (kept_pub_number,)).fetchone()
    existing = json.loads(row[0]) if row and row[0] else []
    merged = sorted(set(existing) | set(all_superseded))
    conn.execute("UPDATE tenders SET supersedes=? WHERE pub_number=?",
                 (json.dumps(merged), kept_pub_number))
    conn.commit()

# ── Portal workflow store (§5.4) ─────────────────────────────────────────────

def ensure_pipeline_entry(conn, pub_number):
    conn.execute("INSERT OR IGNORE INTO pipeline(pub_number) VALUES (?)", (pub_number,))
    conn.commit()

def set_pipeline_entry(conn, pub_number, fields):
    valid = {k: v for k, v in fields.items() if k in PIPELINE_FIELDS}
    if not valid:
        return
    sets = ", ".join(f"{k}=?" for k in valid)
    conn.execute(f"UPDATE pipeline SET {sets} WHERE pub_number=?",
                 (*valid.values(), pub_number))
    conn.commit()

def get_pipeline_entries(conn):
    """Shortlisted tenders joined with their pipeline state."""
    t_select = ", ".join(f"t.{c}" for c in COLUMNS)
    rows = conn.execute(f"""
        SELECT {t_select},
               COALESCE(p.submission_status, 'not_started') AS submission_status,
               p.deadline_override, p.owner, p.notes
        FROM tenders t
        LEFT JOIN pipeline p ON t.pub_number = p.pub_number
        WHERE t.status = 'shortlisted'
    """).fetchall()
    p_cols = ["submission_status", "deadline_override", "owner", "notes"]
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for c in _JSON:
            rec[c] = json.loads(rec[c] or "[]")
        out.append(rec)
    return out

def get_followup_entries(conn):
    """Pipeline entries with submission_status='submitted' joined with tender data."""
    t_select = ", ".join(f"t.{c}" for c in COLUMNS)
    rows = conn.execute(f"""
        SELECT {t_select},
               p.submission_status, p.deadline_override, p.owner, p.notes,
               p.submitted_date, p.result_due, p.outcome
        FROM tenders t
        JOIN pipeline p ON t.pub_number = p.pub_number
        WHERE p.submission_status = 'submitted'
    """).fetchall()
    p_cols = ["submission_status", "deadline_override", "owner", "notes",
              "submitted_date", "result_due", "outcome"]
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for c in _JSON:
            rec[c] = json.loads(rec[c] or "[]")
        out.append(rec)
    return out
