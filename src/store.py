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
from datetime import date, datetime, timezone

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

import config
import db
import filters
import match
from normalize import record_hash
from schema import DEFAULT_TENANT_ID, TENDERS_COLUMNS as COLUMNS
from schema import documents, metadata, pipeline, tenant_cpv, tenant_keywords, tenant_portals
from schema import tenant_settings, tenants, tenders, translations, vault_documents

_JSON = {"cpv_codes", "matched_terms", "supersedes"}
_EMPTY_DEFAULT = {"value", "value_currency", "value_eur", "fx_rate_date",
                  "language", "tag_line_en", "description_en", "translation_status"}
# CR-002 C2/A: columns that must stay real SQL NULL when absent, distinguishable
# from "" (dismiss_note: schema.py comment; awarded_*: CR-002 A1's "never
# fabricated" rule — a null award field must not look like a found-but-empty one).
_NULL_DEFAULT = {"dismiss_note", "awarded_to", "awarded_value", "awarded_currency"}

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


def update_classification(conn, tenant_id, pub_number, notice_type, awarded_to, awarded_value, awarded_currency):
    """CR-002 A backfill escape hatch, same shape as update_tagging() — for
    already-stored rows whose notice_type was never computed (ingested before
    classification.classify existed, so upsert()'s insert-only rule left them
    at the notice_type column's server_default). See scratch_backfill_notice_type.py.
    """
    with conn.begin() as c:
        c.execute(update(tenders).where(
            (tenders.c.tenant_id == tenant_id) & (tenders.c.pub_number == pub_number)
        ).values(notice_type=notice_type, awarded_to=awarded_to,
                  awarded_value=awarded_value, awarded_currency=awarded_currency))


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


def set_pipeline_entry(conn, tenant_id, pub_number, fields):
    valid = {k: v for k, v in fields.items() if k in PIPELINE_FIELDS}
    if not valid:
        return
    with conn.begin() as c:
        c.execute(update(pipeline).where(
            (pipeline.c.tenant_id == tenant_id) & (pipeline.c.pub_number == pub_number)
        ).values(**valid))


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
            "confidence": r[6], "fields_extracted": r[7]}


_VAULT_LIST_COLS = (vault_documents.c.id, vault_documents.c.filename, vault_documents.c.doc_type,
                     vault_documents.c.status, vault_documents.c.metadata_json,
                     vault_documents.c.cpv_codes, vault_documents.c.confidence,
                     vault_documents.c.fields_extracted)


def list_vault_documents(conn, tenant_id, q=None):
    """[{id, filename, doc_type, status, metadata, cpv_codes, confidence,
    fields_extracted}], matching the VaultDoc shape the frontend expects
    (frontend/src/types.ts). `q` matches against filename only — metadata
    values vary too much in shape (numbers, units, free text) for a single
    LIKE to search meaningfully across all of them.
    """
    where = vault_documents.c.tenant_id == tenant_id
    if q:
        where = where & vault_documents.c.filename.ilike(f"%{q}%")
    with conn.connect() as c:
        rows = c.execute(select(*_VAULT_LIST_COLS).where(where)
                          .order_by(vault_documents.c.id)).fetchall()
    return [_vault_doc_row(r) for r in rows]


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
        out.append(rec)
    return out
