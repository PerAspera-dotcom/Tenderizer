"""SQLAlchemy Core table definitions — single source of truth for the schema,
shared by store.py (application reads/writes) and Alembic (migrations).

Step 3 of the Postgres/multi-tenancy migration: adds `tenants` and a
`tenant_id` column on `tenders`/`pipeline`. Composite primary keys, not just
an added column — two different tenants can legitimately track the same
public TED/BOAMP notice (same source|pub_number, same `hash`), so `hash`
alone can no longer be unique on its own; the PK becomes (tenant_id, hash).
Same reasoning for pipeline's (tenant_id, pub_number).

`translations` (the DeepL cache) deliberately has NO tenant_id — see the
phase2/3 plan: it's a pure content-hash cache of arbitrary text, and two
tenants translating the same French phrase should share one cached result
rather than each spending their own DeepL quota on it.
"""
from sqlalchemy import (Boolean, Column, ForeignKey, Integer, MetaData,
                         PrimaryKeyConstraint, String, Table, Text)

metadata = MetaData()

# 1 Clerk user = 1 tenant (confirmed choice — no Organization concept). Column
# is named tenant_id everywhere rather than clerk_user_id so a future move to
# shared/org-based tenants is a smaller migration if ever needed.
DEFAULT_TENANT_ID = 1

tenants = Table(
    "tenants", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("clerk_user_id", String, nullable=True, unique=True),
    Column("email", Text, nullable=True),
    Column("created_at", Text, nullable=False, server_default=""),
)

tenders = Table(
    "tenders", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("hash", String, nullable=False),
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
    PrimaryKeyConstraint("tenant_id", "hash"),
)

# Column order matters to callers that build dicts positionally — keep it
# identical to the table's declared order (also store.py's previous COLUMNS list).
TENDERS_COLUMNS = [c.name for c in tenders.columns]

pipeline = Table(
    "pipeline", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("pub_number", String, nullable=False),
    Column("submission_status", Text, nullable=False, server_default="not_started"),
    Column("deadline_override", Text, nullable=True),
    Column("owner", Text, nullable=True),
    Column("notes", Text, nullable=True),
    Column("submitted_date", Text, nullable=True),
    Column("result_due", Text, nullable=True),
    Column("outcome", Text, nullable=False, server_default="pending"),
    PrimaryKeyConstraint("tenant_id", "pub_number"),
)

PIPELINE_COLUMNS = [c.name for c in pipeline.columns]

translations = Table(
    "translations", metadata,
    Column("content_hash", String, primary_key=True),
    Column("translated_text", Text, nullable=True),
    Column("cached_at", Text, nullable=True),
)

# Step 5 of the Postgres/multi-tenancy migration: per-tenant config rows for
# CPV set, keywords, and enabled portals. A new tenant is seeded from the
# shipped config/*.yaml defaults (see store.ensure_tenant) and can then
# customise independently — the YAML files remain the *default* content, not
# the live config, once a tenant has its own rows.

tenant_cpv = Table(
    "tenant_cpv", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("code", String, nullable=False),
    PrimaryKeyConstraint("tenant_id", "code"),
)

tenant_keywords = Table(
    "tenant_keywords", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), primary_key=True),
    Column("terms", Text, nullable=False, server_default="{}"),        # JSON: {lang: [terms]}
    Column("distinctive", Text, nullable=False, server_default="[]"),  # JSON: [terms]
)

tenant_portals = Table(
    "tenant_portals", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False, server_default="api"),
    Column("enabled", Boolean, nullable=False),
    PrimaryKeyConstraint("tenant_id", "name"),
)
