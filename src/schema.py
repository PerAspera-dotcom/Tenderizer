"""SQLAlchemy Core table definitions — single source of truth for the schema,
shared by store.py (application reads/writes) and Alembic (migrations).

Step 2 of the Postgres/multi-tenancy migration: mirrors the exact current
SQLite schema (same columns, all TEXT-typed, same defaults) — no tenant_id
yet (that's step 3), no JSONB (cpv_codes/matched_terms/supersedes stay
TEXT-encoded JSON, matching current store.py behavior; migrating those to a
native JSON type is a separate, later improvement).
"""
from sqlalchemy import Column, MetaData, String, Table, Text

metadata = MetaData()

tenders = Table(
    "tenders", metadata,
    Column("hash", String, primary_key=True),
    Column("source", Text, nullable=False, server_default=""),
    Column("pub_number", Text, nullable=False, server_default=""),
    Column("tag_line", Text, nullable=False, server_default=""),
    Column("description", Text, nullable=False, server_default=""),
    Column("buyer", Text, nullable=False, server_default=""),
    Column("country", Text, nullable=False, server_default=""),
    Column("place", Text, nullable=False, server_default=""),
    Column("category", Text, nullable=False, server_default=""),
    Column("procedure", Text, nullable=False, server_default=""),
    Column("pub_date", Text, nullable=False, server_default=""),
    Column("deadline", Text, nullable=False, server_default=""),
    Column("cpv_codes", Text, nullable=False, server_default="[]"),
    Column("matched_terms", Text, nullable=False, server_default="[]"),
    Column("match_source", Text, nullable=True),
    Column("url", Text, nullable=False, server_default=""),
    Column("first_seen", Text, nullable=True),
    Column("status", Text, nullable=False, server_default="new"),
    Column("exclude_reason", Text, nullable=False, server_default=""),
    Column("value", Text, nullable=False, server_default=""),
    Column("value_currency", Text, nullable=False, server_default=""),
    Column("value_eur", Text, nullable=False, server_default=""),
    Column("fx_rate_date", Text, nullable=False, server_default=""),
    Column("supersedes", Text, nullable=False, server_default="[]"),
    Column("language", Text, nullable=False, server_default=""),
    Column("tag_line_en", Text, nullable=False, server_default=""),
    Column("description_en", Text, nullable=False, server_default=""),
    Column("translation_status", Text, nullable=False, server_default=""),
)

# Column order matters to callers that build dicts positionally — keep it
# identical to the table's declared order (also store.py's previous COLUMNS list).
TENDERS_COLUMNS = [c.name for c in tenders.columns]

pipeline = Table(
    "pipeline", metadata,
    Column("pub_number", String, primary_key=True),
    Column("submission_status", Text, nullable=False, server_default="not_started"),
    Column("deadline_override", Text, nullable=True),
    Column("owner", Text, nullable=True),
    Column("notes", Text, nullable=True),
    Column("submitted_date", Text, nullable=True),
    Column("result_due", Text, nullable=True),
    Column("outcome", Text, nullable=False, server_default="pending"),
)

PIPELINE_COLUMNS = [c.name for c in pipeline.columns]

translations = Table(
    "translations", metadata,
    Column("content_hash", String, primary_key=True),
    Column("translated_text", Text, nullable=True),
    Column("cached_at", Text, nullable=True),
)
