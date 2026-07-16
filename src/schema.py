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
from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, MetaData,
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
    # CR-002 C2: optional note captured on dismiss. Nullable, no server_default —
    # absent means NULL, never '' (see store.upsert's _NULL_DEFAULT handling).
    Column("dismiss_note", Text, nullable=True),
    # CR-002 A: additive classification tag, always populated (never blank —
    # see classification.classify's DEFAULT_TYPE fallback). Award fields are
    # best-effort extraction (classification.extract_award_info) and stay
    # NULL, not '', when nothing was found.
    Column("notice_type", Text, nullable=False, server_default="tender"),
    Column("awarded_to", Text, nullable=True),
    Column("awarded_value", Text, nullable=True),
    Column("awarded_currency", Text, nullable=True),
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

# CR-002 E (D-C decided: minimal slice now — upload + store only, no
# requirement parsing/translation; that full pipeline is Composer's Phase 2
# Ingest & Config, POST /api/composer/ingest, deliberately not built here).
# `storage_path` is a server-generated (uuid-based) on-disk path, never the
# user-supplied filename, so a malicious filename can't path-traverse; the
# original name is kept separately, display-only, in `filename`.
documents = Table(
    "documents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("pub_number", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("content_type", Text, nullable=False, server_default=""),
    Column("size", Integer, nullable=False, server_default="0"),
    Column("storage_path", Text, nullable=False),
    Column("uploaded_at", Text, nullable=False, server_default=""),
)

translations = Table(
    "translations", metadata,
    Column("content_hash", String, primary_key=True),
    Column("translated_text", Text, nullable=True),
    Column("cached_at", Text, nullable=True),
)

# Vault — tenant-wide technical-document library (evidence pool for Composer's
# later retrieval; see src/vault.py). Not tender-scoped, unlike `documents`
# above — a datasheet/certificate/drawing is uploaded once and reused across
# tenders. `storage_path` follows `documents`' same uuid-based, path-traversal
# -safe convention. `metadata` is intentionally an open JSON dict (fields vary
# by doc type — a datasheet has material/water-column/fire-rating, a cert has
# issuer/standard/valid-until — there's no single fixed schema), not a fixed
# set of columns.
vault_documents = Table(
    "vault_documents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), nullable=False),
    Column("filename", Text, nullable=False),
    Column("content_type", Text, nullable=False, server_default=""),
    Column("size", Integer, nullable=False, server_default="0"),
    Column("storage_path", Text, nullable=False),
    Column("doc_type", Text, nullable=True),
    Column("status", Text, nullable=False, server_default="processing"),
    Column("metadata_json", Text, nullable=False, server_default="{}"),
    Column("cpv_codes", Text, nullable=False, server_default="[]"),
    Column("confidence", Float, nullable=True),
    Column("fields_extracted", Integer, nullable=True),
    Column("uploaded_at", Text, nullable=False, server_default=""),
)
VAULT_DOCUMENTS_COLUMNS = [c.name for c in vault_documents.columns]

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

# Phase2/3 Settings screen follow-up: stored preferences only. There is no
# scheduler (phase 3, not started — run_frequency/run_window_* are read by
# nothing yet, runs still happen via POST /api/run or the CLI script) and no
# email/SMTP provider (notify_on_complete/notify_email are equally inert).
# Same single-row-per-tenant shape as tenant_keywords.
tenant_settings = Table(
    "tenant_settings", metadata,
    Column("tenant_id", Integer, ForeignKey("tenants.id"), primary_key=True),
    Column("run_frequency", Text, nullable=False, server_default="daily"),        # "daily" | "weekly" | "paused"
    Column("run_window_start", Text, nullable=False, server_default="02:00"),     # "HH:MM"
    Column("run_window_end", Text, nullable=False, server_default="06:00"),       # "HH:MM"
    Column("notify_on_complete", Boolean, nullable=False, server_default="0"),
    Column("notify_email", Text, nullable=False, server_default=""),
)
