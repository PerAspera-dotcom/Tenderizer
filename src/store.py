"""SQLAlchemy-backed storage with hash-based dedup — tenant-scoped.

Step 3 of the Postgres/multi-tenancy migration: every function below now
takes a `tenant_id`, and reads/writes are scoped to it (see schema.py for
why the primary keys became composite (tenant_id, hash) /
(tenant_id, pub_number) rather than just adding a column). `conn` is a
SQLAlchemy Engine, pointed at SQLite or Postgres depending on DATABASE_URL
(step 4).

get_cached_translation/cache_translation are the one exception: the
translation cache is deliberately NOT tenant-scoped (see schema.py).

Step 5 adds per-tenant CPV/keywords/portals config (tenant_cpv,
tenant_keywords, tenant_portals) — see ensure_tenant() and the get_tenant_*/
set_tenant_* functions below.
"""
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

import config
import db
import filters
import match
from normalize import record_hash
from schema import DEFAULT_TENANT_ID, TENDERS_COLUMNS as COLUMNS
from schema import composer_documents, composer_matrix, composer_requirements
from schema import composer_style_examples, tenant_composer_settings, tenant_style_guide
from schema import tenant_vault_rules, tenant_vault_settings
from schema import documents, metadata, pipeline, pipeline_history, source_health, tenant_cpv, tenant_keywords
from schema import tenant_portals, tenant_settings, tenants, tenders, translations, vault_documents

_JSON = {"cpv_codes", "matched_terms", "supersedes"}
_EMPTY_DEFAULT = {"value", "value_currency", "value_eur", "fx_rate_date",
                  "language", "tag_line_en", "description_en", "translation_status"}
# CR-002 C2/A: columns that must stay real SQL NULL when absent, distinguishable
# from "" (dismiss_note: schema.py comment; awarded_*: CR-002 A1's "never
# fabricated" rule — a null award field must not look like a found-but-empty one).
_NULL_DEFAULT = {"dismiss_note", "awarded_to", "awarded_value", "awarded_currency"}
# Same "never fabricate absence" rule as _NULL_DEFAULT, but the value itself
# is a JSON object rather than plain text when present — needs its own
# json.dumps/loads pass, unlike _JSON's columns (which always default to an
# empty array/object, never real NULL).
_NULLABLE_JSON = {"award_detail"}

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
    "ALTER TABLE tenders ADD COLUMN dismiss_note TEXT DEFAULT NULL",
    "ALTER TABLE tenders ADD COLUMN notice_type TEXT DEFAULT 'tender'",
    "ALTER TABLE tenders ADD COLUMN awarded_to TEXT DEFAULT NULL",
    "ALTER TABLE tenders ADD COLUMN awarded_value TEXT DEFAULT NULL",
    "ALTER TABLE tenders ADD COLUMN awarded_currency TEXT DEFAULT NULL",
]


def init_db(path):
    """`path` is a SQLite file path, used unless DATABASE_URL is explicitly
    configured (step 4: Postgres cutover) — in which case that takes over and
    `path` is ignored for connection purposes (callers still use it to derive
    e.g. last_run.json's directory). Tests never set DATABASE_URL (see
    conftest.py), so they keep getting isolated per-test SQLite files.
    """
    engine = db.get_engine(db.configured_url() or f"sqlite:///{path}")
    metadata.create_all(engine, checkfirst=True)
    if engine.dialect.name == "sqlite":
        # Additive fixups for existing on-disk SQLite files that predate later
        # columns. Not needed on Postgres: a fresh DB already has every column
        # via metadata.create_all() above, and re-running these against
        # Postgres would raise (it doesn't consider "column exists" an
        # OperationalError like SQLite does).
        for stmt in _TENDERS_MIGRATIONS:
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
            except OperationalError:
                pass
    ensure_tenant(engine, DEFAULT_TENANT_ID)
    return engine


def get_tenant_id_by_clerk_user_id(conn, clerk_user_id):
    """The tenant id for an already-provisioned Clerk user, or None (step 6:
    caller should then provision one via create_tenant_for_clerk_user).
    """
    with conn.connect() as c:
        row = c.execute(select(tenants.c.id).where(
            tenants.c.clerk_user_id == clerk_user_id)).fetchone()
    return row[0] if row else None


def list_provisioned_tenant_ids(conn):
    """Tenant ids with a real Clerk user attached — excludes DEFAULT_TENANT_ID's
    seed/pre-multi-tenancy row (clerk_user_id is empty/None there), which
    nobody is actually logged into. Used by the in-process daily scheduler
    (prod) to know which tenants to run — it must not re-scrape on behalf of
    a tenant no customer owns.
    """
    with conn.connect() as c:
        rows = c.execute(select(tenants.c.id).where(
            (tenants.c.clerk_user_id.isnot(None)) & (tenants.c.clerk_user_id != "")
        )).fetchall()
    return [r[0] for r in rows]


def create_tenant_for_clerk_user(conn, clerk_user_id, email=None):
    """Auto-provision a brand-new tenant on a Clerk user's first login (step
    6) — unlike ensure_tenant(), the id isn't known ahead of time; the DB
    assigns it. Seeds the new tenant's config same as ensure_tenant().

    A brand-new user's first page load fires several API calls at once
    (Layout's stats/health/tenders + the page's own), each hitting
    get_current_tenant_id -> this function concurrently before any of them
    has committed a row. Only one insert wins the `clerk_user_id` unique
    constraint; the rest must resolve to that row instead of 500ing (an
    uncaught IntegrityError here previously surfaced to the browser as a
    CORS error, since Starlette's error middleware sits outside CORSMiddleware
    and its fallback response carries no CORS headers).
    """
    try:
        with conn.begin() as c:
            result = c.execute(insert(tenants).values(
                clerk_user_id=clerk_user_id, email=email, created_at=date.today().isoformat()))
            new_id = result.inserted_primary_key[0]
    except IntegrityError:
        new_id = get_tenant_id_by_clerk_user_id(conn, clerk_user_id)
    _seed_tenant_config(conn, new_id)
    return new_id


def ensure_tenant(conn, tenant_id, clerk_user_id=None, email=None):
    """Create a `tenants` row if `tenant_id` doesn't exist yet. Used both for
    the migrated single-tenant default (init_db, unconditionally) and for
    auto-provisioning a new tenant on a Clerk user's first login (step 6).

    Also seeds this tenant's CPV/keywords/portals config (step 5) from the
    shipped config/*.yaml defaults — but only the tables that don't already
    have a row for this tenant, so calling this again (e.g. every init_db)
    never clobbers a tenant's own customisations.
    """
    with conn.begin() as c:
        exists = c.execute(select(tenants.c.id).where(tenants.c.id == tenant_id)).fetchone()
        if not exists:
            c.execute(insert(tenants).values(
                id=tenant_id, clerk_user_id=clerk_user_id, email=email,
                created_at=date.today().isoformat()))
    _seed_tenant_config(conn, tenant_id)


def _seed_tenant_config(conn, tenant_id):
    """Seed once, on first sight of this tenant — gated on tenant_keywords
    having a row, since that table has exactly one row per seeded tenant
    (unlike tenant_cpv/tenant_portals, whose *row count* can legitimately hit
    zero after a tenant customises them down to nothing, which must not look
    like "never seeded" and trigger a reset back to the YAML defaults).
    """
    with conn.connect() as c:
        already_seeded = c.execute(select(tenant_keywords.c.tenant_id).where(
            tenant_keywords.c.tenant_id == tenant_id)).first()
    if already_seeded:
        return

    set_tenant_cpv(conn, tenant_id, config.cpv_codes())
    kw = config._load("keywords.yaml")
    set_tenant_keywords(conn, tenant_id,
                         {"terms": kw.get("terms", {}), "distinctive": kw.get("distinctive", [])})
    with conn.begin() as c:
        for portal in config.portals():
            c.execute(insert(tenant_portals).values(
                tenant_id=tenant_id, name=portal["name"],
                type=portal.get("type", "api"), enabled=bool(portal.get("enabled", True))))
    set_tenant_settings(conn, tenant_id, {})


# ── Per-tenant config (step 5) ───────────────────────────────────────────────

def get_tenant_cpv(conn, tenant_id):
    """This tenant's active CPV code list — same shape as config.cpv_codes()."""
    with conn.connect() as c:
        rows = c.execute(select(tenant_cpv.c.code).where(
            tenant_cpv.c.tenant_id == tenant_id)).fetchall()
    return [r[0] for r in rows]


def set_tenant_cpv(conn, tenant_id, codes):
    """Overwrite (not merge) this tenant's active CPV set — mirrors config.write_cpv()."""
    with conn.begin() as c:
        c.execute(tenant_cpv.delete().where(tenant_cpv.c.tenant_id == tenant_id))
        if codes:
            c.execute(insert(tenant_cpv), [{"tenant_id": tenant_id, "code": code} for code in codes])


def get_tenant_keywords(conn, tenant_id):
    """{"terms": {lang: [...]}, "distinctive": [...]} — same shape as keywords.yaml."""
    with conn.connect() as c:
        row = c.execute(select(tenant_keywords.c.terms, tenant_keywords.c.distinctive).where(
            tenant_keywords.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return {"terms": {}, "distinctive": []}
    return {"terms": json.loads(row[0]), "distinctive": json.loads(row[1])}


def set_tenant_keywords(conn, tenant_id, data):
    """Merge semantics — mirrors config.write_keywords(): only overwrites the
    keys present in `data` ('terms' and/or 'distinctive'), leaving the other
    untouched.
    """
    current = get_tenant_keywords(conn, tenant_id)
    if "terms" in data:
        current["terms"] = data["terms"]
    if "distinctive" in data:
        current["distinctive"] = data["distinctive"]
    values = {"terms": json.dumps(current["terms"]), "distinctive": json.dumps(current["distinctive"])}
    with conn.begin() as c:
        exists = c.execute(select(tenant_keywords.c.tenant_id).where(
            tenant_keywords.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_keywords).where(
                tenant_keywords.c.tenant_id == tenant_id).values(**values))
        else:
            c.execute(insert(tenant_keywords).values(tenant_id=tenant_id, **values))


def get_tenant_portals(conn, tenant_id):
    with conn.connect() as c:
        rows = c.execute(select(tenant_portals.c.name, tenant_portals.c.type, tenant_portals.c.enabled)
                          .where(tenant_portals.c.tenant_id == tenant_id)).fetchall()
    return [{"name": r[0], "type": r[1], "enabled": bool(r[2])} for r in rows]


def get_enabled_portal_names(conn, tenant_id):
    return {p["name"] for p in get_tenant_portals(conn, tenant_id) if p["enabled"]}


def set_tenant_portal_enabled(conn, tenant_id, name, enabled):
    with conn.begin() as c:
        c.execute(update(tenant_portals).where(
            (tenant_portals.c.tenant_id == tenant_id) & (tenant_portals.c.name == name)
        ).values(enabled=enabled))


_DEFAULT_SETTINGS = {
    "run_frequency": "daily",
    "run_window_start": "02:00",
    "run_window_end": "06:00",
    "notify_on_complete": False,
    "notify_email": "",
}


def get_tenant_settings(conn, tenant_id):
    """Stored preferences only — see schema.py's tenant_settings comment for
    why nothing downstream reads run_frequency/run_window_*/notify_* yet.
    """
    with conn.connect() as c:
        row = c.execute(select(
            tenant_settings.c.run_frequency, tenant_settings.c.run_window_start,
            tenant_settings.c.run_window_end, tenant_settings.c.notify_on_complete,
            tenant_settings.c.notify_email,
        ).where(tenant_settings.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return dict(_DEFAULT_SETTINGS)
    return {
        "run_frequency": row[0], "run_window_start": row[1], "run_window_end": row[2],
        "notify_on_complete": bool(row[3]), "notify_email": row[4],
    }


def set_tenant_settings(conn, tenant_id, data):
    """Merge semantics, like set_tenant_keywords: only overwrites the keys
    present in `data`, leaving the rest untouched.
    """
    current = get_tenant_settings(conn, tenant_id)
    for key in _DEFAULT_SETTINGS:
        if key in data:
            current[key] = data[key]
    with conn.begin() as c:
        exists = c.execute(select(tenant_settings.c.tenant_id).where(
            tenant_settings.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_settings).where(
                tenant_settings.c.tenant_id == tenant_id).values(**current))
        else:
            c.execute(insert(tenant_settings).values(tenant_id=tenant_id, **current))


_DEFAULT_VAULT_RULES = {"hints": []}


def get_vault_rules(conn, tenant_id):
    with conn.connect() as c:
        row = c.execute(select(tenant_vault_rules.c.hints).where(
            tenant_vault_rules.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return dict(_DEFAULT_VAULT_RULES)
    return {"hints": json.loads(row[0])}


def set_vault_rules(conn, tenant_id, hints):
    """Full overwrite — the frontend always sends the complete hint list
    (add/remove happens client-side before Save), unlike the merge-semantics
    settings tables above.
    """
    values = {"hints": json.dumps(hints)}
    with conn.begin() as c:
        exists = c.execute(select(tenant_vault_rules.c.tenant_id).where(
            tenant_vault_rules.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_vault_rules).where(
                tenant_vault_rules.c.tenant_id == tenant_id).values(**values))
        else:
            c.execute(insert(tenant_vault_rules).values(tenant_id=tenant_id, **values))


_DEFAULT_VAULT_SETTINGS = {"confidence_threshold": 0.6}


def get_vault_settings(conn, tenant_id):
    with conn.connect() as c:
        row = c.execute(select(tenant_vault_settings.c.confidence_threshold).where(
            tenant_vault_settings.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return dict(_DEFAULT_VAULT_SETTINGS)
    return {"confidence_threshold": row[0]}


def set_vault_settings(conn, tenant_id, data):
    """Merge semantics, like set_tenant_settings: only overwrites keys
    present in `data`.
    """
    current = get_vault_settings(conn, tenant_id)
    for key in _DEFAULT_VAULT_SETTINGS:
        if key in data:
            current[key] = data[key]
    with conn.begin() as c:
        exists = c.execute(select(tenant_vault_settings.c.tenant_id).where(
            tenant_vault_settings.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_vault_settings).where(
                tenant_vault_settings.c.tenant_id == tenant_id).values(**current))
        else:
            c.execute(insert(tenant_vault_settings).values(tenant_id=tenant_id, **current))


_DEFAULT_COMPOSER_SETTINGS = {"good_similarity": 0.35, "partial_similarity": 0.20, "top_k": 5}


def get_composer_settings(conn, tenant_id):
    with conn.connect() as c:
        row = c.execute(select(
            tenant_composer_settings.c.good_similarity, tenant_composer_settings.c.partial_similarity,
            tenant_composer_settings.c.top_k,
        ).where(tenant_composer_settings.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return dict(_DEFAULT_COMPOSER_SETTINGS)
    return {"good_similarity": row[0], "partial_similarity": row[1], "top_k": row[2]}


def set_composer_settings(conn, tenant_id, data):
    current = get_composer_settings(conn, tenant_id)
    for key in _DEFAULT_COMPOSER_SETTINGS:
        if key in data:
            current[key] = data[key]
    with conn.begin() as c:
        exists = c.execute(select(tenant_composer_settings.c.tenant_id).where(
            tenant_composer_settings.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_composer_settings).where(
                tenant_composer_settings.c.tenant_id == tenant_id).values(**current))
        else:
            c.execute(insert(tenant_composer_settings).values(tenant_id=tenant_id, **current))


def get_style_guide(conn, tenant_id):
    """{style_guide, source_doc_count, generated_at} — style_guide is None
    until the first extract/save (no default text is fabricated).
    """
    with conn.connect() as c:
        row = c.execute(select(
            tenant_style_guide.c.style_guide, tenant_style_guide.c.source_doc_count,
            tenant_style_guide.c.generated_at,
        ).where(tenant_style_guide.c.tenant_id == tenant_id)).fetchone()
    if not row:
        return {"style_guide": None, "source_doc_count": 0, "generated_at": None}
    return {"style_guide": row[0], "source_doc_count": row[1], "generated_at": row[2]}


def set_style_guide(conn, tenant_id, style_guide, source_doc_count, generated_at):
    """Full overwrite — one style guide per tenant, replaced wholesale on
    each extract or manual edit (see schema.py's tenant_style_guide comment).
    """
    values = {"style_guide": style_guide, "source_doc_count": source_doc_count,
              "generated_at": generated_at}
    with conn.begin() as c:
        exists = c.execute(select(tenant_style_guide.c.tenant_id).where(
            tenant_style_guide.c.tenant_id == tenant_id)).fetchone()
        if exists:
            c.execute(update(tenant_style_guide).where(
                tenant_style_guide.c.tenant_id == tenant_id).values(**values))
        else:
            c.execute(insert(tenant_style_guide).values(tenant_id=tenant_id, **values))


def add_style_example(conn, tenant_id, filename, content_type, size, storage_path, extracted_text):
    with conn.begin() as c:
        result = c.execute(insert(composer_style_examples).values(
            tenant_id=tenant_id, filename=filename, content_type=content_type or "",
            size=size, storage_path=storage_path, extracted_text=extracted_text or "",
            uploaded_at=date.today().isoformat()))
        return result.inserted_primary_key[0]


def list_style_examples(conn, tenant_id):
    with conn.connect() as c:
        rows = c.execute(select(
            composer_style_examples.c.id, composer_style_examples.c.filename,
            composer_style_examples.c.size, composer_style_examples.c.uploaded_at,
        ).where(composer_style_examples.c.tenant_id == tenant_id)
         .order_by(composer_style_examples.c.id)).fetchall()
    return [{"id": r[0], "filename": r[1], "size": r[2], "uploaded_at": r[3]} for r in rows]


def get_style_example_texts(conn, tenant_id):
    """Just the extracted text of every uploaded example — what
    extract_style_guide needs, without the row metadata list_style_examples
    returns for the UI.
    """
    with conn.connect() as c:
        rows = c.execute(select(composer_style_examples.c.extracted_text).where(
            composer_style_examples.c.tenant_id == tenant_id)).fetchall()
    return [r[0] for r in rows if r[0]]


def get_style_example(conn, tenant_id, example_id):
    with conn.connect() as c:
        row = c.execute(select(
            composer_style_examples.c.filename, composer_style_examples.c.storage_path,
        ).where(
            (composer_style_examples.c.tenant_id == tenant_id)
            & (composer_style_examples.c.id == example_id)
        )).fetchone()
    if not row:
        return None
    return {"filename": row[0], "storage_path": row[1]}


def delete_style_example(conn, tenant_id, example_id):
    with conn.begin() as c:
        c.execute(composer_style_examples.delete().where(
            (composer_style_examples.c.tenant_id == tenant_id)
            & (composer_style_examples.c.id == example_id)))


def upsert(conn, tenant_id, record):
    h = record_hash(record)
    with conn.begin() as c:
        exists = c.execute(select(tenders.c.hash).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.hash == h))).fetchone()
        if exists:
            return False
        fs = record.get("first_seen") or date.today().isoformat()
        values = {"tenant_id": tenant_id, "hash": h, "first_seen": fs}
        for col in COLUMNS:
            if col in ("tenant_id", "hash", "first_seen"):
                continue
            elif col == "status":
                values[col] = record.get("status", "new")
            elif col == "notice_type":
                values[col] = record.get("notice_type") or "tender"
            elif col in _JSON:
                values[col] = json.dumps(record.get(col, []))
            elif col in _EMPTY_DEFAULT:
                values[col] = record.get(col) or ""  # None (no value/not translated) -> ''
            elif col in _NULL_DEFAULT:
                values[col] = record.get(col)  # None stays None (no note) — never ''
            elif col in _NULLABLE_JSON:
                values[col] = json.dumps(record[col]) if record.get(col) else None
            else:
                values[col] = record.get(col, "")
        c.execute(insert(tenders).values(**values))
    return True


def all_records(conn, tenant_id):
    with conn.connect() as c:
        rows = c.execute(select(*[tenders.c[col] for col in COLUMNS])
                          .where(tenders.c.tenant_id == tenant_id)).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col])
        for col in _NULLABLE_JSON:
            rec[col] = json.loads(rec[col]) if rec[col] else None
        out.append(rec)
    return out


def pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
    """True if `pub_number` belongs to some tenant other than `tenant_id`
    (step 6: distinguishes "doesn't exist" (404) from "exists, but not
    yours" (403) at the API layer).
    """
    with conn.connect() as c:
        row = c.execute(select(tenders.c.tenant_id).where(
            (tenders.c.pub_number == pub_number) & (tenders.c.tenant_id != tenant_id)
        ).limit(1)).fetchone()
    return row is not None


def set_status(conn, tenant_id, pub_number, status, dismiss_note=None):
    """CR-002 C2: `dismiss_note` is only ever written here, alongside the
    status change that produced it — never as a standalone update — so a note
    can't outlive or predate the dismiss action it was attached to. Passing
    None leaves the existing stored note untouched (e.g. a later Shortlist
    after a dismiss note was recorded doesn't need to clear it).
    """
    values = {"status": status}
    if dismiss_note is not None:
        values["dismiss_note"] = dismiss_note
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(**values))


def set_translation(conn, tenant_id, pub_number, tag_line_en, description_en, status):
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(tag_line_en=tag_line_en, description_en=description_en, translation_status=status))


def update_tagging(conn, tenant_id, pub_number, cpv_codes, matched_terms, match_source, exclude_reason):
    """Overwrite an already-stored record's match/filter fields in place.

    upsert() is deliberately insert-only (a normal pipeline run must never
    silently rewrite a record another run already tagged) — this is the
    escape hatch for the one genuine exception: a one-off backfill re-scoring
    already-stored rows after a normalize.py extraction fix (e.g. BOAMP CPV
    codes, 2026-07), where the underlying source data didn't change but what
    we know about it did.
    """
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(cpv_codes=json.dumps(cpv_codes), matched_terms=json.dumps(matched_terms),
                  match_source=match_source, exclude_reason=exclude_reason or ""))


def update_classification(conn, tenant_id, pub_number, notice_type, awarded_to, awarded_value,
                           awarded_currency, award_detail=None):
    """CR-002 A backfill escape hatch, same shape as update_tagging() — for
    already-stored rows whose notice_type was never computed (ingested before
    classification.classify existed, so upsert()'s insert-only rule left them
    at the notice_type column's server_default). See scratch_backfill_notice_type.py.

    `award_detail` (dict or None) is the richer per-winner/lot/contract detail
    added for the Past Tenders data-coverage follow-up — see
    scratch_backfill_award_detail.py, which is the only current caller that
    passes a non-None value.
    """
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(notice_type=notice_type, awarded_to=awarded_to,
                  awarded_value=awarded_value, awarded_currency=awarded_currency,
                  award_detail=json.dumps(award_detail) if award_detail else None))


def update_language(conn, tenant_id, pub_number, language):
    """Backfill escape hatch, same shape as update_tagging/update_classification
    — for rows ingested before CR-001 R3's language-tagging existed, left at
    the `language` column's "" default by upsert()'s insert-only rule (see
    scratch_backfill_language.py).
    """
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(language=language))


def rescore_pending(conn, tenant_id, now=None):
    """CR-003 G3 — re-run matching/filtering against `status='new'` rows only.

    upsert() is deliberately insert-only (see update_tagging's docstring), so a
    row ingested before a cpv.yaml/keywords.yaml change existed stays stale
    forever unless something revisits it. Unlike scratch_rescore_matching.py
    (a one-off, all-rows script), this is the routine escape hatch: called
    automatically after a config save (PUT /api/config/cpv,
    PUT /api/config/keywords) and on demand via POST /api/config/rescore.
    Bounded to `status='new'` — a tender the customer has already triaged
    (dismissed/shortlisted) keeps its tagging as it was when they acted on it;
    only pending, still-in-queue rows get re-tagged.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    records = [r for r in all_records(conn, tenant_id) if r.get("status") == "new"]
    if not records:
        return Counter()

    tenant_kw = get_tenant_keywords(conn, tenant_id)
    full_keywords = [w for lang in tenant_kw["terms"].values() for w in lang]
    cpv_set = set(get_tenant_cpv(conn, tenant_id))
    exclusions = dict(config.exclusions())
    exclusions["_distinctive_keywords"] = tenant_kw["distinctive"]

    stats = Counter()
    for rec in records:
        text_ = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
        hits = match.match_keywords(text_, full_keywords)
        has_cpv = bool(set(rec.get("cpv_codes") or []) & cpv_set)
        new_match_source = match.classify_match(has_cpv, hits)

        scored = dict(rec, matched_terms=hits, match_source=new_match_source)
        new_exclude_reason = filters.apply_filters(scored, exclusions, now) or ""

        changed = (hits != (rec.get("matched_terms") or [])
                   or new_match_source != rec.get("match_source")
                   or new_exclude_reason != (rec.get("exclude_reason") or ""))
        if changed:
            update_tagging(conn, tenant_id, rec["pub_number"],
                            cpv_codes=rec.get("cpv_codes") or [], matched_terms=hits,
                            match_source=new_match_source, exclude_reason=new_exclude_reason)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    stats["total"] = len(records)
    return stats


def mark_superseded(conn, tenant_id, kept_pub_number, superseded_records):
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
            c.execute(update(tenders).where(
                (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == r["pub_number"])
            ).values(exclude_reason="superseded"))
        row = c.execute(select(tenders.c.supersedes).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == kept_pub_number)
        )).fetchone()
        existing = json.loads(row[0]) if row and row[0] else []
        merged = sorted(set(existing) | set(all_superseded))
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == kept_pub_number)
        ).values(supersedes=json.dumps(merged)))


# ── Translation cache (CR-001 R3/C1) — global, NOT tenant-scoped ────────────

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

def ensure_pipeline_entry(conn, tenant_id, pub_number):
    with conn.begin() as c:
        exists = c.execute(select(pipeline.c.pub_number).where(
            (pipeline.c.tenant_id == tenant_id) & (pipeline.c.pub_number == pub_number)
        )).fetchone()
        if not exists:
            c.execute(insert(pipeline).values(tenant_id=tenant_id, pub_number=pub_number))


def record_pipeline_change(conn, tenant_id, pub_number, field, old_value, new_value):
    with conn.begin() as c:
        c.execute(insert(pipeline_history).values(
            tenant_id=tenant_id, pub_number=pub_number, field=field,
            old_value=old_value, new_value=new_value,
            changed_at=datetime.now(timezone.utc).isoformat()))


def get_pipeline_history(conn, tenant_id, pub_number):
    with conn.connect() as c:
        rows = c.execute(select(
            pipeline_history.c.field, pipeline_history.c.old_value,
            pipeline_history.c.new_value, pipeline_history.c.changed_at,
        ).where(
            (pipeline_history.c.tenant_id == tenant_id) & (pipeline_history.c.pub_number == pub_number)
        ).order_by(pipeline_history.c.id.desc())).fetchall()
    return [{"field": r[0], "old_value": r[1], "new_value": r[2], "changed_at": r[3]} for r in rows]


def set_pipeline_entry(conn, tenant_id, pub_number, fields):
    """Diffs against the current row and logs only the fields that actually
    changed — inlines the history insert (rather than calling
    record_pipeline_change) so the read-diff-write-log sequence stays one
    atomic transaction. Returns {field: (old_value, new_value)} for every
    field that actually changed (empty dict if nothing did) — callers (e.g.
    api.patch_pipeline, for the owner-handoff email) use this instead of
    re-deriving a diff themselves.
    """
    valid = {k: v for k, v in fields.items() if k in PIPELINE_FIELDS}
    if not valid:
        return {}
    with conn.begin() as c:
        current = c.execute(select(*(pipeline.c[f] for f in valid)).where(
            (pipeline.c.tenant_id == tenant_id) & (pipeline.c.pub_number == pub_number)
        )).fetchone()
        changed = ({f: (current[i], valid[f]) for i, f in enumerate(valid) if current[i] != valid[f]}
                   if current is not None else {})
        c.execute(update(pipeline).where(
            (pipeline.c.tenant_id == tenant_id) & (pipeline.c.pub_number == pub_number)
        ).values(**valid))
        for field, (old_value, new_value) in changed.items():
            c.execute(insert(pipeline_history).values(
                tenant_id=tenant_id, pub_number=pub_number, field=field,
                old_value=old_value, new_value=new_value,
                changed_at=datetime.now(timezone.utc).isoformat()))
    return changed


def get_pipeline_entries(conn, tenant_id):
    """Shortlisted tenders joined with their pipeline state (this tenant only)."""
    p_cols = ["submission_status", "deadline_override", "owner", "notes"]
    cols = [tenders.c[col] for col in COLUMNS] + [
        func.coalesce(pipeline.c.submission_status, "not_started").label("submission_status"),
        pipeline.c.deadline_override, pipeline.c.owner, pipeline.c.notes,
    ]
    join_cond = ((tenders.c.pub_number == pipeline.c.pub_number)
                 & (tenders.c.tenant_id == pipeline.c.tenant_id))
    stmt = (select(*cols)
            .select_from(tenders.outerjoin(pipeline, join_cond))
            .where((tenders.c.tenant_id == tenant_id) & (tenders.c.status == "shortlisted")))
    with conn.connect() as c:
        rows = c.execute(stmt).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col] or "[]")
        for col in _NULLABLE_JSON:
            rec[col] = json.loads(rec[col]) if rec[col] else None
        out.append(rec)
    return out


def add_document(conn, tenant_id, pub_number, filename, content_type, size, storage_path):
    with conn.begin() as c:
        result = c.execute(insert(documents).values(
            tenant_id=tenant_id, pub_number=pub_number, filename=filename,
            content_type=content_type or "", size=size, storage_path=storage_path,
            uploaded_at=date.today().isoformat()))
        return result.inserted_primary_key[0]


def list_documents(conn, tenant_id, pub_number):
    with conn.connect() as c:
        rows = c.execute(select(
            documents.c.id, documents.c.filename, documents.c.content_type,
            documents.c.size, documents.c.uploaded_at,
        ).where(
            (documents.c.tenant_id == tenant_id) & (documents.c.pub_number == pub_number)
        ).order_by(documents.c.id)).fetchall()
    return [{"id": r[0], "filename": r[1], "content_type": r[2], "size": r[3], "uploaded_at": r[4]}
            for r in rows]


def get_document(conn, tenant_id, document_id):
    """A document row scoped to this tenant, or None — the tenant check lives
    here (not just at the filesystem layer) so a caller from another tenant
    can't download by guessing another tenant's document id.
    """
    with conn.connect() as c:
        row = c.execute(select(
            documents.c.pub_number, documents.c.filename, documents.c.content_type,
            documents.c.size, documents.c.storage_path, documents.c.uploaded_at,
        ).where(
            (documents.c.tenant_id == tenant_id) & (documents.c.id == document_id)
        )).fetchone()
    if not row:
        return None
    return {"pub_number": row[0], "filename": row[1], "content_type": row[2],
            "size": row[3], "storage_path": row[4], "uploaded_at": row[5]}


def add_vault_document(conn, tenant_id, filename, content_type, size, storage_path):
    """Row starts `status='processing'` (schema default) — flipped to
    'indexed' by update_vault_document_metadata once ingest_and_embed/
    extract_metadata (src/vault.py) finish.
    """
    with conn.begin() as c:
        result = c.execute(insert(vault_documents).values(
            tenant_id=tenant_id, filename=filename, content_type=content_type or "",
            size=size, storage_path=storage_path, uploaded_at=date.today().isoformat()))
        return result.inserted_primary_key[0]


def _vault_doc_row(r):
    return {"id": r[0], "filename": r[1], "doc_type": r[2], "status": r[3],
            "metadata": json.loads(r[4]), "cpv_codes": json.loads(r[5]),
            "confidence": r[6], "fields_extracted": r[7], "tags": json.loads(r[8])}


_VAULT_LIST_COLS = (vault_documents.c.id, vault_documents.c.filename, vault_documents.c.doc_type,
                     vault_documents.c.status, vault_documents.c.metadata_json,
                     vault_documents.c.cpv_codes, vault_documents.c.confidence,
                     vault_documents.c.fields_extracted, vault_documents.c.tags)


def list_vault_documents(conn, tenant_id, q=None, tag=None):
    """[{id, filename, doc_type, status, metadata, cpv_codes, confidence,
    fields_extracted, tags}], matching the VaultDoc shape the frontend
    expects (frontend/src/types.ts). `q` matches against filename only —
    metadata values vary too much in shape (numbers, units, free text) for a
    single LIKE to search meaningfully across all of them. `tag` matches
    exact membership in the tags array (post-filtered in Python, like
    find_vault_documents' cpv filter, since the tag set is small).
    """
    where = vault_documents.c.tenant_id == tenant_id
    if q:
        where = where & vault_documents.c.filename.ilike(f"%{q}%")
    with conn.connect() as c:
        rows = c.execute(select(*_VAULT_LIST_COLS).where(where)
                          .order_by(vault_documents.c.id)).fetchall()
    docs = [_vault_doc_row(r) for r in rows]
    if tag:
        docs = [d for d in docs if tag in d["tags"]]
    return docs


def set_vault_document_tags(conn, tenant_id, document_id, tags):
    with conn.begin() as c:
        c.execute(update(vault_documents).where(
            (vault_documents.c.tenant_id == tenant_id) & (vault_documents.c.id == document_id)
        ).values(tags=json.dumps(tags)))


def list_vault_tags(conn, tenant_id):
    """Distinct tags in use across this tenant's library, for the Tags
    browse page — sorted for a stable UI order.
    """
    with conn.connect() as c:
        rows = c.execute(select(vault_documents.c.tags).where(
            vault_documents.c.tenant_id == tenant_id)).fetchall()
    tags = {t for (raw,) in rows for t in json.loads(raw)}
    return sorted(tags)


def get_vault_document(conn, tenant_id, document_id):
    """Tenant-scoped row incl. `storage_path`/`content_type`, or None."""
    with conn.connect() as c:
        row = c.execute(select(
            vault_documents.c.filename, vault_documents.c.content_type,
            vault_documents.c.storage_path,
        ).where(
            (vault_documents.c.tenant_id == tenant_id) & (vault_documents.c.id == document_id)
        )).fetchone()
    if not row:
        return None
    return {"filename": row[0], "content_type": row[1], "storage_path": row[2]}


def update_vault_document_metadata(conn, tenant_id, document_id, doc_type, metadata,
                                    cpv_codes, confidence, fields_extracted, status):
    with conn.begin() as c:
        c.execute(update(vault_documents).where(
            (vault_documents.c.tenant_id == tenant_id) & (vault_documents.c.id == document_id)
        ).values(doc_type=doc_type, metadata_json=json.dumps(metadata),
                  cpv_codes=json.dumps(cpv_codes), confidence=confidence,
                  fields_extracted=fields_extracted, status=status))


def find_vault_documents(conn, tenant_id, cpv=None, material=None, tag=None):
    """CR-004 F3 — Composer's "Source materials" panel: indexed Vault
    documents filtered by CPV code and/or a substring match against
    extracted metadata values. Deliberately separate from
    list_vault_documents' `q` (filename-only, used by the Library screen) —
    metadata values vary too much in shape for one substring filter to serve
    both call sites' expectations identically.
    """
    docs = [d for d in list_vault_documents(conn, tenant_id) if d["status"] == "indexed"]
    if cpv:
        docs = [d for d in docs if cpv in d["cpv_codes"]]
    if material:
        needle = material.lower()
        docs = [d for d in docs if needle in json.dumps(d["metadata"]).lower()]
    if tag:
        docs = [d for d in docs if tag in d["tags"]]
    return docs


def delete_vault_document(conn, tenant_id, document_id):
    with conn.begin() as c:
        c.execute(vault_documents.delete().where(
            (vault_documents.c.tenant_id == tenant_id) & (vault_documents.c.id == document_id)))


def update_vault_document_metadata_fields(conn, tenant_id, document_id, metadata):
    """CR-004 F1: analyst confirms/corrects extracted fields
    (POST /api/vault/validate-metadata) — replaces only `metadata`/
    `fields_extracted`, leaving doc_type/cpv_codes/confidence (the
    extraction's own outputs) untouched.
    """
    with conn.begin() as c:
        c.execute(update(vault_documents).where(
            (vault_documents.c.tenant_id == tenant_id) & (vault_documents.c.id == document_id)
        ).values(metadata_json=json.dumps(metadata), fields_extracted=len(metadata)))


def add_composer_document(conn, tenant_id, pub_number, filename, content_type, size,
                           storage_path, role):
    with conn.begin() as c:
        result = c.execute(insert(composer_documents).values(
            tenant_id=tenant_id, pub_number=pub_number, filename=filename,
            content_type=content_type or "", size=size, storage_path=storage_path,
            role=role, uploaded_at=date.today().isoformat()))
        return result.inserted_primary_key[0]


_COMPOSER_DOC_COLS = (composer_documents.c.id, composer_documents.c.filename,
                       composer_documents.c.role, composer_documents.c.role_override,
                       composer_documents.c.status, composer_documents.c.pages,
                       composer_documents.c.chunks, composer_documents.c.image_heavy)


def _composer_doc_row(r):
    return {"id": r[0], "filename": r[1], "role": r[3] or r[2], "status": r[4],
            "pages": r[5], "chunks": r[6], "image_heavy": bool(r[7])}


def list_composer_documents(conn, tenant_id, pub_number):
    with conn.connect() as c:
        rows = c.execute(select(*_COMPOSER_DOC_COLS).where(
            (composer_documents.c.tenant_id == tenant_id)
            & (composer_documents.c.pub_number == pub_number)
        ).order_by(composer_documents.c.id)).fetchall()
    return [_composer_doc_row(r) for r in rows]


def get_composer_document(conn, tenant_id, document_id):
    """Tenant-scoped row incl. `storage_path`/`content_type`, or None."""
    with conn.connect() as c:
        row = c.execute(select(
            composer_documents.c.pub_number, composer_documents.c.filename,
            composer_documents.c.content_type, composer_documents.c.storage_path,
            composer_documents.c.role, composer_documents.c.role_override,
        ).where(
            (composer_documents.c.tenant_id == tenant_id) & (composer_documents.c.id == document_id)
        )).fetchone()
    if not row:
        return None
    return {"pub_number": row[0], "filename": row[1], "content_type": row[2],
            "storage_path": row[3], "role": row[5] or row[4]}


def update_composer_document_status(conn, tenant_id, document_id, status, pages, chunks,
                                     image_heavy):
    with conn.begin() as c:
        c.execute(update(composer_documents).where(
            (composer_documents.c.tenant_id == tenant_id) & (composer_documents.c.id == document_id)
        ).values(status=status, pages=pages, chunks=chunks, image_heavy=image_heavy))


def set_composer_document_role(conn, tenant_id, document_id, role):
    with conn.begin() as c:
        c.execute(update(composer_documents).where(
            (composer_documents.c.tenant_id == tenant_id) & (composer_documents.c.id == document_id)
        ).values(role_override=role))


def set_composer_matrix(conn, tenant_id, pub_number, filename, storage_path, requirement_count):
    """One matrix per tender — replaces any existing row for this
    (tenant_id, pub_number) rather than accumulating duplicates on re-upload.
    """
    with conn.begin() as c:
        c.execute(composer_matrix.delete().where(
            (composer_matrix.c.tenant_id == tenant_id) & (composer_matrix.c.pub_number == pub_number)))
        result = c.execute(insert(composer_matrix).values(
            tenant_id=tenant_id, pub_number=pub_number, filename=filename,
            storage_path=storage_path, requirement_count=requirement_count,
            uploaded_at=date.today().isoformat()))
        return result.inserted_primary_key[0]


def get_composer_matrix(conn, tenant_id, pub_number):
    with conn.connect() as c:
        row = c.execute(select(
            composer_matrix.c.filename, composer_matrix.c.storage_path,
            composer_matrix.c.requirement_count, composer_matrix.c.filled_path,
        ).where(
            (composer_matrix.c.tenant_id == tenant_id) & (composer_matrix.c.pub_number == pub_number)
        )).fetchone()
    if not row:
        return None
    return {"filename": row[0], "storage_path": row[1], "requirement_count": row[2],
            "filled_path": row[3]}


def set_composer_matrix_filled_path(conn, tenant_id, pub_number, filled_path):
    with conn.begin() as c:
        c.execute(update(composer_matrix).where(
            (composer_matrix.c.tenant_id == tenant_id) & (composer_matrix.c.pub_number == pub_number)
        ).values(filled_path=filled_path))


def add_composer_requirements(conn, tenant_id, pub_number, requirements):
    """Bulk insert from composer.extract_requirements's output
    ([{title, extracted, source, confidence}]) — one row per requirement,
    validation defaults to 'pending' (schema default).
    """
    if not requirements:
        return []
    now = date.today().isoformat()
    with conn.begin() as c:
        ids = []
        for req in requirements:
            result = c.execute(insert(composer_requirements).values(
                tenant_id=tenant_id, pub_number=pub_number, title=req["title"],
                extracted_snippet=req["extracted"], source_ref=req["source"],
                confidence=req["confidence"], created_at=now))
            ids.append(result.inserted_primary_key[0])
    return ids


_COMPOSER_REQ_COLS = (
    composer_requirements.c.id, composer_requirements.c.title,
    composer_requirements.c.extracted_snippet, composer_requirements.c.source_ref,
    composer_requirements.c.confidence, composer_requirements.c.validation,
    composer_requirements.c.gap_status, composer_requirements.c.similarity,
    composer_requirements.c.response_text, composer_requirements.c.citations_json,
    composer_requirements.c.resolved, composer_requirements.c.version,
    composer_requirements.c.version_history_json,
)


def _composer_req_row(r):
    return {"id": r[0], "title": r[1], "extracted": r[2], "source": r[3],
            "confidence": r[4], "validation": r[5], "gap_status": r[6],
            "similarity": r[7], "response": r[8], "citations": json.loads(r[9]),
            "resolved": bool(r[10]), "version": r[11],
            "version_history": json.loads(r[12])}


def list_composer_requirements(conn, tenant_id, pub_number):
    with conn.connect() as c:
        rows = c.execute(select(*_COMPOSER_REQ_COLS).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.pub_number == pub_number)
        ).order_by(composer_requirements.c.id)).fetchall()
    return [_composer_req_row(r) for r in rows]


def get_composer_requirement(conn, tenant_id, requirement_id):
    with conn.connect() as c:
        row = c.execute(select(*_COMPOSER_REQ_COLS, composer_requirements.c.pub_number).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.id == requirement_id)
        )).fetchone()
    if not row:
        return None
    out = _composer_req_row(row[:-1])
    out["pub_number"] = row[-1]
    return out


def update_composer_requirement_validation(conn, tenant_id, requirement_id, status):
    with conn.begin() as c:
        c.execute(update(composer_requirements).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.id == requirement_id)
        ).values(validation=status))


def update_composer_requirement_result(conn, tenant_id, requirement_id, gap_status,
                                        similarity, response_text, citations):
    """Write-once-per-generate-run result (composer.run_generate) — resets
    version back to 1 and clears history, since a fresh full generate run
    starts a new draft lineage for this requirement.
    """
    with conn.begin() as c:
        c.execute(update(composer_requirements).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.id == requirement_id)
        ).values(gap_status=gap_status, similarity=similarity, response_text=response_text,
                  citations_json=json.dumps(citations), version=1, version_history_json="[]"))


def update_composer_requirement_refined(conn, tenant_id, requirement_id, new_text, feedback,
                                         extra_citations=None):
    """Section-scoped regenerate (composer.refine_section) — bumps version
    and appends the prior draft + the feedback that triggered the change to
    version_history_json, rather than overwriting silently.

    `extra_citations` (CR-004 F3): Vault sources the analyst explicitly
    pulled in via the Source materials panel, appended to the requirement's
    existing citations list. None/[] (the default) leaves citations
    untouched — a plain regenerate-from-feedback still doesn't re-derive
    them, same as before this parameter existed.
    """
    req = get_composer_requirement(conn, tenant_id, requirement_id)
    if req is None:
        return
    history = req["version_history"] + [{
        "text": req["response"], "feedback": feedback,
        "at": datetime.now(timezone.utc).isoformat(),
    }]
    citations = req["citations"] + (extra_citations or [])
    with conn.begin() as c:
        c.execute(update(composer_requirements).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.id == requirement_id)
        ).values(response_text=new_text, version=req["version"] + 1,
                  version_history_json=json.dumps(history), citations_json=json.dumps(citations)))


def mark_composer_requirement_resolved(conn, tenant_id, requirement_id):
    with conn.begin() as c:
        c.execute(update(composer_requirements).where(
            (composer_requirements.c.tenant_id == tenant_id)
            & (composer_requirements.c.id == requirement_id)
        ).values(resolved=True))


def get_followup_entries(conn, tenant_id):
    """Pipeline entries with submission_status='submitted' (this tenant only)."""
    p_cols = ["submission_status", "deadline_override", "owner", "notes",
              "submitted_date", "result_due", "outcome"]
    cols = [tenders.c[col] for col in COLUMNS] + [pipeline.c[pc] for pc in p_cols]
    join_cond = ((tenders.c.pub_number == pipeline.c.pub_number)
                 & (tenders.c.tenant_id == pipeline.c.tenant_id))
    stmt = (select(*cols)
            .select_from(tenders.join(pipeline, join_cond))
            .where((tenders.c.tenant_id == tenant_id) & (pipeline.c.submission_status == "submitted")))
    with conn.connect() as c:
        rows = c.execute(stmt).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(COLUMNS + p_cols, r))
        for col in _JSON:
            rec[col] = json.loads(rec[col] or "[]")
        for col in _NULLABLE_JSON:
            rec[col] = json.loads(rec[col]) if rec[col] else None
        out.append(rec)
    return out


# CR-004 F4 — scheduled-scrape health logging (source_health). One row per
# (tenant, source, run), appended by run.run_pipeline after every attempt
# regardless of outcome — never updated in place, so this is a real history,
# not just "last run's status" (the pre-existing last_run.json behaviour,
# left untouched alongside this).

def record_source_health(conn, tenant_id, source, run_date, status,
                          notices_pulled=None, error_detail=None):
    """Appends one source_health row and returns it (dict) including the
    freshly-computed `streak_ok_days` snapshot: this run's own +1 on top of
    the immediately-prior row's streak if that row was 'ok', reset to 0 on
    'failed' or when there's no prior row yet.
    """
    with conn.begin() as c:
        prev = c.execute(
            select(source_health.c.status, source_health.c.streak_ok_days)
            .where((source_health.c.tenant_id == tenant_id) & (source_health.c.source == source))
            .order_by(source_health.c.id.desc()).limit(1)
        ).fetchone()
        streak = 0
        if status == "ok":
            streak = (prev[1] + 1) if (prev is not None and prev[0] == "ok") else 1
        c.execute(insert(source_health).values(
            tenant_id=tenant_id, source=source, run_date=run_date, status=status,
            notices_pulled=notices_pulled, error_detail=error_detail, streak_ok_days=streak,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return {"source": source, "run_date": run_date, "status": status,
            "notices_pulled": notices_pulled, "error_detail": error_detail,
            "streak_ok_days": streak}


def get_source_health(conn, tenant_id, source, days=7, now=None):
    """Rolled-up health summary for one source, matching CR-004's
    /api/health shape: {source, last_result, streak_ok_days, failures_7d,
    last_failure, consecutive_failures}. `consecutive_failures` counts
    backward from the most recent row while status stays 'failed' (not
    windowed by `days` — an ongoing outage should keep escalating past a
    week). None of these fields require a prior row to exist: a source with
    no history yet reads as all-zero/None, not an error.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).date().isoformat()
    with conn.connect() as c:
        rows = c.execute(
            select(source_health.c.run_date, source_health.c.status,
                   source_health.c.notices_pulled, source_health.c.error_detail,
                   source_health.c.streak_ok_days)
            .where((source_health.c.tenant_id == tenant_id) & (source_health.c.source == source))
            .order_by(source_health.c.id.desc()).limit(60)
        ).fetchall()
    if not rows:
        return {"source": source, "last_result": None, "streak_ok_days": 0,
                "failures_7d": 0, "last_failure": None, "consecutive_failures": 0}

    latest = rows[0]
    last_result = (f"ok ({latest[2]} new)" if latest[1] == "ok" else f"error: {latest[3]}")
    streak_ok_days = latest[4] if latest[1] == "ok" else 0

    failures_7d = sum(1 for r in rows if r[1] == "failed" and r[0] >= cutoff)
    last_failure = next((r[0] for r in rows if r[1] == "failed"), None)

    consecutive_failures = 0
    for r in rows:
        if r[1] != "failed":
            break
        consecutive_failures += 1

    return {"source": source, "last_result": last_result, "streak_ok_days": streak_ok_days,
            "failures_7d": failures_7d, "last_failure": last_failure,
            "consecutive_failures": consecutive_failures}
