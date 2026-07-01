"""SQLite storage with hash-based dedup."""
import sqlite3, json
from datetime import date
from normalize import record_hash

COLUMNS = ["hash", "source", "pub_number", "tag_line", "description", "buyer", "country",
           "place", "category", "procedure", "pub_date", "deadline", "cpv_codes",
           "matched_terms", "match_source", "url", "first_seen", "status", "exclude_reason"]
_JSON = {"cpv_codes", "matched_terms"}

PIPELINE_FIELDS = {"submission_status", "deadline_override", "owner", "notes",
                   "submitted_date", "result_due", "outcome"}

def init_db(path):
    conn = sqlite3.connect(path)
    cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS tenders({cols}, PRIMARY KEY(hash))")
    # Additive migrations for existing DBs that predate these columns
    for stmt in ("ALTER TABLE tenders ADD COLUMN status TEXT DEFAULT 'new'",
                 "ALTER TABLE tenders ADD COLUMN exclude_reason TEXT DEFAULT ''"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    init_pipeline(conn)
    return conn

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
