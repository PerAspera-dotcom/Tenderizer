"""FastAPI layer — thin read/trigger API over the Scout engine.

No matching, normalisation, or fetching logic lives here.
Reads via store.*, config.*; only POST /api/run triggers the engine.
"""
import sys, pathlib, json, logging, os, uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from dotenv import load_dotenv

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

load_dotenv()

# Optional — a no-op unless SENTRY_DSN is set (same "unconfigured = inert, not
# an error" convention as CLERK_JWKS_URL/OPS_API_TOKEN elsewhere in this file).
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(dsn=_sentry_dsn, send_default_pii=False)

import store, config, auth, vault, composer
import run as engine

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Depends, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from typing import Optional

ROOT          = _HERE.parent
DB_PATH       = str(ROOT / "data" / "tenders.db")
LAST_RUN_PATH = str(ROOT / "data" / "last_run.json")


def _report_path(tenant_id: int) -> str:
    """Per-tenant report path (phase2/3 step 6 follow-up). Used to be a single
    shared REPORT_PATH constant — a pre-multi-tenancy leftover that meant
    every tenant's /api/run overwrote the same reports/tenders.xlsx and
    GET /api/reports/latest served whoever ran last, regardless of caller.
    """
    return str(ROOT / "reports" / f"tenders_{tenant_id}.xlsx")

# CR-002 E: per-tenant upload root, tenant_id in the path itself as a second
# layer of isolation beyond the DB-row tenant check in get_document().
UPLOAD_DIR = ROOT / "data" / "uploads"
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB — a minimal-slice sanity cap, not a product decision

DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173"


def parse_allowed_origins(env_value):
    """Comma-separated ALLOWED_ORIGINS -> list[str], trimmed, empties dropped.

    Pulled out as a pure function so the parsing itself is unit-testable
    without booting the FastAPI app.
    """
    raw = env_value if env_value is not None else DEFAULT_ALLOWED_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


_scheduler = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _scheduler
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(_run_all_tenants, "cron", hour=2, minute=0, id="daily_scrape")
        _scheduler.start()
    yield
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Tenderizer API", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(os.getenv("ALLOWED_ORIGINS")),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db():
    return store.init_db(DB_PATH)


# auto_error=False so a missing header reaches get_current_tenant_id() as
# `None` rather than FastAPI's HTTPBearer raising its own 403 — we want 401
# for every auth failure (missing, malformed, expired, or bad-signature
# token), not a mix of 403/401 depending on which layer caught it.
_bearer = HTTPBearer(auto_error=False)


def get_current_tenant_id(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> int:
    """Real Clerk session-token resolver (phase2/3 step 6) — every route
    below depends on this function rather than a hardcoded value, so this is
    the only place that changed when the step 3 stub became real auth.

    401 on a missing or invalid/expired token. A Clerk user seen for the
    first time is auto-provisioned a new tenant (1 Clerk user = 1 tenant,
    confirmed choice — see schema.py) rather than rejected.
    """
    if creds is None:
        raise HTTPException(401, "Missing bearer token")
    try:
        claims = auth.verify_token(creds.credentials)
    except auth.AuthError as e:
        raise HTTPException(401, f"Invalid or expired token: {e}")
    # auth.AuthNotConfigured (CLERK_JWKS_URL unset) deliberately propagates
    # uncaught -> FastAPI's default 500 — that's a server misconfiguration,
    # not a bad token, and every token would fail identically until fixed.

    clerk_user_id = claims["sub"]
    conn = _db()
    tenant_id = store.get_tenant_id_by_clerk_user_id(conn, clerk_user_id)
    if tenant_id is None:
        tenant_id = store.create_tenant_for_clerk_user(conn, clerk_user_id, claims.get("email"))
    return tenant_id


def require_ops_access(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Gate for operational endpoints that carry cross-tenant operational
    data, not a single tenant's business data — currently just
    GET /api/reports/latest (NOT /api/health, which despite its name is the
    Phase-1 tenant-facing Portal Health panel — see get_health()). A static
    service token (OPS_API_TOKEN), not any tenant's Clerk session, since no
    regular user login should be able to pull the latest run's report.

    401 with no token at all (no credentials presented); 403 if a token was
    presented but doesn't match (wrong privilege, not "who are you").
    """
    if creds is None:
        raise HTTPException(401, "Missing bearer token")
    if not auth.verify_ops_token(creds.credentials):
        raise HTTPException(403, "Forbidden")
    # auth.AuthNotConfigured (OPS_API_TOKEN unset) deliberately propagates
    # uncaught -> FastAPI's default 500, same reasoning as get_current_tenant_id().


def _last_run() -> dict:
    if os.path.exists(LAST_RUN_PATH):
        with open(LAST_RUN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/api/health-check")
def health_check():
    return {"status": "ok"}


# ── Tenders ───────────────────────────────────────────────────────────────────

@app.get("/api/tenders")
def list_tenders(
    source:           Optional[str]  = None,
    category:         Optional[str]  = None,
    match_source:     Optional[str]  = None,
    country:          Optional[str]  = None,
    q:                Optional[str]  = None,
    status:           Optional[str]  = None,
    has_deadline:     Optional[bool] = None,
    notice_type:      Optional[str]  = None,
    include_excluded: bool           = False,
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0,   ge=0),
    sort:   str = "deadline",
    tenant_id: int = Depends(get_current_tenant_id),
):
    records = store.all_records(_db(), tenant_id)

    # CR-001: every F1-F8/D-DUP exclusion sets exclude_reason — hide those by
    # default so they don't surface here even though the report already hid
    # them (run.py's `surfaced`). include_excluded=true is the audit escape
    # hatch (e.g. to show why a notice didn't make the cut).
    if not include_excluded:
        records = [r for r in records if not r.get("exclude_reason")]

    if source:
        records = [r for r in records if (r.get("source") or "").upper() == source.upper()]
    if category:
        records = [r for r in records if (r.get("category") or "").lower() == category.lower()]
    if match_source:
        _none_vals = (None, "None", "none", "")
        if match_source == "none":
            records = [r for r in records if r.get("match_source") in _none_vals]
        else:
            records = [r for r in records if r.get("match_source") == match_source]
    if country:
        records = [r for r in records if (r.get("country") or "").upper() == country.upper()]
    if q:
        ql = q.lower()
        records = [r for r in records
                   if ql in (r.get("tag_line") or "").lower()
                   or ql in (r.get("buyer") or "").lower()]
    if status:
        records = [r for r in records if (r.get("status") or "new") == status]
    if has_deadline is True:
        records = [r for r in records if r.get("deadline")]
    elif has_deadline is False:
        records = [r for r in records if not r.get("deadline")]

    # CR-002 B1: past_tender notices never surface in the default Tender
    # Feed/Review Queue view — they have their own Past Tenders page/query.
    # notice_type=past_tender is the one way to explicitly ask for them back;
    # any other explicit notice_type filters normally.
    if notice_type:
        records = [r for r in records if (r.get("notice_type") or "tender") == notice_type]
    else:
        records = [r for r in records if (r.get("notice_type") or "tender") != "past_tender"]

    # Default: hide expired deadlines; show future deadlines and empty-deadline rows
    today = date.today().isoformat()
    records = [r for r in records
               if not r.get("deadline") or r["deadline"][:10] >= today]

    records.sort(key=lambda r: (r.get("deadline") or "9999-99-99"))
    return {"total": len(records), "results": records[offset: offset + limit]}


@app.get("/api/tenders/{pub_number}")
def get_tender(pub_number: str, include_excluded: bool = False,
               tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    for r in store.all_records(conn, tenant_id):
        if r["pub_number"] == pub_number:
            if r.get("exclude_reason") and not include_excluded:
                raise HTTPException(404, "Tender not found")
            return r
    # Own tenant has no such record: 403 if it belongs to another tenant
    # (exists, just not yours) vs 404 if it doesn't exist anywhere.
    if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
        raise HTTPException(403, "Forbidden")
    raise HTTPException(404, "Tender not found")


class StatusBody(BaseModel):
    status: str
    note: Optional[str] = None  # CR-002 C2: optional dismiss note

_VALID_STATUSES = {"new", "reviewed", "shortlisted", "dismissed"}

@app.patch("/api/tenders/{pub_number}")
def patch_tender(pub_number: str, body: StatusBody,
                  tenant_id: int = Depends(get_current_tenant_id)):
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")
    conn = _db()
    if not any(r["pub_number"] == pub_number for r in store.all_records(conn, tenant_id)):
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    # note is only ever persisted alongside status="dismissed" — a note sent
    # with any other status is silently ignored rather than 422ing, since the
    # field only makes sense on the dismiss action (CR-002 C2).
    note = body.note if body.status == "dismissed" else None
    store.set_status(conn, tenant_id, pub_number, body.status, dismiss_note=note)
    return {"pub_number": pub_number, "status": body.status}


# ── Stats & health ────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats(tenant_id: int = Depends(get_current_tenant_id)):
    records  = store.all_records(_db(), tenant_id)
    last_run = _last_run()
    today    = date.today().isoformat()

    by_match = {"cpv": 0, "both": 0, "keyword": 0, "none": 0}
    by_cat   = {"Supply": 0, "Services": 0, "Works": 0, "Training": 0, "Other": 0}
    new_today = 0
    past_tenders = 0

    for r in records:
        # CR-002 B2: dashboard KPIs count active tenders only — past tenders
        # get their own count, not folded into by_match/by_category/new_today.
        if (r.get("notice_type") or "tender") == "past_tender":
            past_tenders += 1
            continue

        ms = r.get("match_source")
        if ms in (None, "None", "none", ""):
            by_match["none"] += 1
        elif ms in by_match:
            by_match[ms] += 1
        else:
            by_match["none"] += 1

        cat = r.get("category") or "Other"
        if cat not in by_cat:
            cat = "Other"
        by_cat[cat] += 1

        if r.get("first_seen") == today:
            new_today += 1

    return {
        "last_sync":       last_run.get("timestamp"),
        "next_run":        None,
        "notices_scanned": last_run.get("notices_scanned", 0),
        "matched_total":   last_run.get("matched_total", 0),
        "new_today":       new_today,
        "by_match":        by_match,
        "by_category":     by_cat,
        "portals_active":  "2/4",
        "past_tenders":    past_tenders,
    }


_PORTAL_META = [
    {"name": "TED",           "region": "EU",      "status": "live"},
    {"name": "BOAMP",         "region": "France",  "status": "live"},
    {"name": "e-Procurement", "region": "Belgium", "status": "planned"},
    {"name": "DTVP",          "region": "Germany", "status": "paused",
     "detail": "Scraper paused — ToS review pending"},
]

@app.get("/api/health")
def get_health(tenant_id: int = Depends(get_current_tenant_id)):
    # Tenant-gated, not ops-gated: this is the Scout Dashboard's Portal
    # Health panel (TENDERIZER_HANDOFF.md §6/§8) — real, Phase-1,
    # tenant-facing data, unlike /api/reports/latest. tenant_id isn't used
    # yet: _last_run()/LAST_RUN_PATH is a single shared file, not
    # tenant-scoped (a pre-existing gap from before tenant_id existed, still
    # not fixed here) — but the route itself needs a regular tenant session,
    # not the ops secret.
    last_run_health = _last_run().get("health", {})
    result = []
    for portal in _PORTAL_META:
        entry = dict(portal)
        if portal["name"] in last_run_health:
            entry["last_result"] = last_run_health[portal["name"]]
        result.append(entry)
    return result


# ── Run now ───────────────────────────────────────────────────────────────────

def _do_run(tenant_id):
    os.makedirs(str(ROOT / "data"), exist_ok=True)
    os.makedirs(str(ROOT / "reports"), exist_ok=True)
    since   = date.today() - timedelta(days=30)
    conn    = _db()
    store.ensure_tenant(conn, tenant_id)
    sources = engine._default_sources(conn, tenant_id, since)
    engine.run_pipeline(sources, DB_PATH, _report_path(tenant_id), tenant_id=tenant_id)

@app.post("/api/run")
def post_run(background: BackgroundTasks, tenant_id: int = Depends(get_current_tenant_id)):
    background.add_task(_do_run, tenant_id)
    return {"status": "started"}


# ── Scheduled run (prod) ─────────────────────────────────────────────────────
# Replaces Windows Task Scheduler (local dev never actually had a scheduled
# task configured — every run so far was a manual "Run now" click, so there
# was no existing cadence to match). Runs in-process via APScheduler rather
# than a separate host-level cron job, since a single always-on service is
# simpler to operate than coordinating two processes for one low-traffic
# customer. Started/stopped from _lifespan above; ENABLE_SCHEDULER defaults
# on, set to "false" to disable (e.g. if ever running more than one instance
# of this service, so only one of them schedules).

def _run_all_tenants():
    conn = _db()
    for tenant_id in store.list_provisioned_tenant_ids(conn):
        try:
            _do_run(tenant_id)
        except Exception:
            # One tenant's failure must not skip the rest. run.py's own
            # health dict already isolates per-source failures within a
            # single tenant's run; this is the same principle one level up.
            logging.exception(f"scheduled run failed for tenant {tenant_id}")


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config/cpv")
def get_cpv_config(tenant_id: int = Depends(get_current_tenant_id)):
    active = set(store.get_tenant_cpv(_db(), tenant_id))
    ref    = config.cpv_reference()
    return [
        {
            "code": code,
            "labels": {
                "en": entry.get("en"), "fr": entry.get("fr"),
                "nl": entry.get("nl"), "de": entry.get("de"),
            },
            "group":    entry.get("group"),
            "category": entry.get("category"),
        }
        for code, entry in ref.items()
        if code in active
    ]


class CpvBody(BaseModel):
    codes: list[str]

@app.put("/api/config/cpv")
def put_cpv_config(body: CpvBody, tenant_id: int = Depends(get_current_tenant_id)):
    import warnings
    ref = config.cpv_reference()  # official reference stays global, not per-tenant
    unknown = [c for c in body.codes if c not in ref]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if unknown:
            warnings.warn(f"Unknown CPV codes (not in cpv_reference.json): {unknown}")
    store.set_tenant_cpv(_db(), tenant_id, body.codes)
    rescored = store.rescore_pending(_db(), tenant_id)
    return {"saved": True, "warnings": [str(w.message) for w in caught], "rescored": dict(rescored)}


class KeywordsBody(BaseModel):
    terms:       Optional[dict] = None
    distinctive: Optional[list] = None

@app.get("/api/config/keywords")
def get_keywords_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_tenant_keywords(_db(), tenant_id)

@app.put("/api/config/keywords")
def put_keywords_config(body: KeywordsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_tenant_keywords(_db(), tenant_id, body.model_dump(exclude_none=True))
    rescored = store.rescore_pending(_db(), tenant_id)
    return {"saved": True, "rescored": dict(rescored)}


@app.post("/api/config/rescore")
def rescore_config(tenant_id: int = Depends(get_current_tenant_id)):
    """CR-003 G3 — on-demand re-tag of `status='new'` rows against current
    CPV/keyword config, for cases the automatic post-save rescore (above)
    predates (e.g. a row ingested before this endpoint existed).
    """
    rescored = store.rescore_pending(_db(), tenant_id)
    return {"rescored": dict(rescored)}


class SettingsBody(BaseModel):
    run_frequency:      Optional[str]  = None
    run_window_start:   Optional[str]  = None
    run_window_end:     Optional[str]  = None
    notify_on_complete: Optional[bool] = None
    notify_email:       Optional[str]  = None

@app.get("/api/config/settings")
def get_settings_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_tenant_settings(_db(), tenant_id)

@app.put("/api/config/settings")
def put_settings_config(body: SettingsBody, tenant_id: int = Depends(get_current_tenant_id)):
    # Stored preferences only — no scheduler or email/SMTP infra reads these
    # yet (see schema.py's tenant_settings comment).
    store.set_tenant_settings(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/api/reports/latest")
def get_latest_report(tenant_id: int = Depends(get_current_tenant_id)):
    # Tenant-gated (phase2/3 step 6 follow-up) — was require_ops_access
    # (a static shared secret), which meant no real tenant's Clerk session
    # could ever call this. Now paired with _report_path's per-tenant file,
    # so this also closes the cross-tenant leak a same-path-for-everyone
    # report would have reopened under a tenant-callable auth check.
    path = _report_path(tenant_id)
    if not os.path.exists(path):
        raise HTTPException(404, "No report found — run the pipeline first")
    return FileResponse(
        path,
        filename="tenders.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Portal: pipeline & follow-up ──────────────────────────────────────────────

@app.get("/api/pipeline")
def get_pipeline(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_pipeline_entries(_db(), tenant_id)


class PipelinePatch(BaseModel):
    submission_status: Optional[str] = None
    deadline_override: Optional[str] = None
    notes:             Optional[str] = None
    owner:             Optional[str] = None

_VALID_SUBMISSION = {"not_started", "drafting", "submitted"}

@app.patch("/api/pipeline/{pub_number}")
def patch_pipeline(pub_number: str, body: PipelinePatch,
                    tenant_id: int = Depends(get_current_tenant_id)):
    if body.submission_status and body.submission_status not in _VALID_SUBMISSION:
        raise HTTPException(422, f"submission_status must be one of {_VALID_SUBMISSION}")
    conn   = _db()
    store.ensure_pipeline_entry(conn, tenant_id, pub_number)
    fields = body.model_dump(exclude_none=True)
    store.set_pipeline_entry(conn, tenant_id, pub_number, fields)
    return {"pub_number": pub_number, **fields}


@app.get("/api/followup")
def get_followup(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_followup_entries(_db(), tenant_id)


class FollowupPatch(BaseModel):
    outcome: str

_VALID_OUTCOMES = {"pending", "won", "lost"}

@app.patch("/api/followup/{pub_number}")
def patch_followup(pub_number: str, body: FollowupPatch,
                    tenant_id: int = Depends(get_current_tenant_id)):
    if body.outcome not in _VALID_OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {_VALID_OUTCOMES}")
    conn = _db()
    store.ensure_pipeline_entry(conn, tenant_id, pub_number)
    store.set_pipeline_entry(conn, tenant_id, pub_number, {"outcome": body.outcome})
    return {"pub_number": pub_number, "outcome": body.outcome}


# ── Documents (CR-002 E) — minimal upload slice, shortlisted tenders only ───
# D-C decided: upload + store only, no requirement parsing/translation — that
# full pipeline is Composer's Phase 2 Ingest & Config (POST /api/composer/
# ingest), deliberately not built here. Scoped tightly to shortlisted tenders
# so this can't grow into a parallel, untethered upload feature.

def _find_tender(conn, tenant_id, pub_number):
    for r in store.all_records(conn, tenant_id):
        if r["pub_number"] == pub_number:
            return r
    return None


@app.post("/api/tenders/{pub_number}/documents")
async def upload_document(pub_number: str, file: UploadFile = File(...),
                           tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    tender = _find_tender(conn, tenant_id, pub_number)
    if tender is None:
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    if tender.get("status") != "shortlisted":
        raise HTTPException(409, "Documents can only be uploaded for shortlisted tenders")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large")

    tenant_dir = UPLOAD_DIR / str(tenant_id) / pub_number
    tenant_dir.mkdir(parents=True, exist_ok=True)
    # Server-generated name — the user-supplied filename is never used as a
    # path component (see schema.py's documents comment).
    ext = pathlib.Path(file.filename or "").suffix[:10]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = tenant_dir / stored_name
    storage_path.write_bytes(content)

    doc_id = store.add_document(conn, tenant_id, pub_number, file.filename or stored_name,
                                 file.content_type, len(content), str(storage_path))
    doc = store.get_document(conn, tenant_id, doc_id)
    return {"id": doc_id, "filename": doc["filename"], "content_type": doc["content_type"],
            "size": doc["size"], "uploaded_at": doc["uploaded_at"]}


@app.get("/api/tenders/{pub_number}/documents")
def list_documents(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    if _find_tender(conn, tenant_id, pub_number) is None:
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    return store.list_documents(conn, tenant_id, pub_number)


@app.get("/api/documents/{document_id}")
def download_document(document_id: int, tenant_id: int = Depends(get_current_tenant_id)):
    doc = store.get_document(_db(), tenant_id, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    if not os.path.exists(doc["storage_path"]):
        raise HTTPException(404, "Document not found")
    return FileResponse(doc["storage_path"], filename=doc["filename"],
                         media_type=doc["content_type"] or "application/octet-stream")


# ── Vault — tenant-wide technical-document library ──────────────────────────
# Unlike the CR-002 documents slice above, these aren't tied to a specific
# tender: a datasheet/certificate is uploaded once and reused across tenders
# as the evidence library Composer's later generation step will retrieve
# from. Same upload-size cap and uuid-based storage-path safety as `documents`.

VAULT_UPLOAD_DIR = ROOT / "data" / "vault_uploads"


def _run_vault_processing(tenant_id, doc_id, path, content_type):
    result = vault.process_upload(tenant_id, doc_id, path, content_type)
    store.update_vault_document_metadata(
        _db(), tenant_id, doc_id, doc_type=result["doc_type"], metadata=result["metadata"],
        cpv_codes=result["cpv_codes"], confidence=result["confidence"],
        fields_extracted=result["fields_extracted"], status=result["status"])


@app.post("/api/vault/ingest")
async def ingest_vault_document(background: BackgroundTasks, file: UploadFile = File(...),
                                 tenant_id: int = Depends(get_current_tenant_id)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large")

    tenant_dir = VAULT_UPLOAD_DIR / str(tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    ext = pathlib.Path(file.filename or "").suffix[:10]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = tenant_dir / stored_name
    storage_path.write_bytes(content)

    conn = _db()
    doc_id = store.add_vault_document(conn, tenant_id, file.filename or stored_name,
                                       file.content_type, len(content), str(storage_path))
    background.add_task(_run_vault_processing, tenant_id, doc_id, str(storage_path), file.content_type)
    docs = store.list_vault_documents(conn, tenant_id)
    created = next(d for d in docs if d["id"] == doc_id)
    return created


@app.get("/api/vault/docs")
def get_vault_docs(q: Optional[str] = None, tenant_id: int = Depends(get_current_tenant_id)):
    results = store.list_vault_documents(_db(), tenant_id, q=q)
    processing = sum(1 for d in results if d["status"] == "processing")
    return {"total": len(results), "processing": processing, "results": results}


# ── Composer — per-tender proposal drafting pipeline ────────────────────────
# Tender-scoped and gated to shortlisted tenders, same reasoning as the
# CR-002 `documents` slice above, but with its own tables/roles/pipeline
# (src/composer.py). The generate-gate (403 until every requirement is
# validated) is enforced here, not just client-side — an explicit design
# requirement, not just a UI nicety.

COMPOSER_UPLOAD_DIR = ROOT / "data" / "composer_uploads"
COMPOSER_OUTPUT_DIR = ROOT / "data" / "composer_output"
_VALID_COMPOSER_ROLES = {"sow", "tech", "background", "parta", "example", "unknown"}
# "pending" included so the Ingest screen's "Undo" action can revert a
# validated/flagged requirement back to pending, not just toggle between the two.
_VALID_COMPOSER_VALIDATION = {"pending", "validated", "flagged"}


def _require_shortlisted_tender(conn, tenant_id, pub_number):
    tender = _find_tender(conn, tenant_id, pub_number)
    if tender is None:
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    if tender.get("status") != "shortlisted":
        raise HTTPException(409, "Composer is only available for shortlisted tenders")
    return tender


def _composer_output_path(tenant_id, pub_number, filename):
    return COMPOSER_OUTPUT_DIR / str(tenant_id) / pub_number / filename


def _ensure_composer_output_dir(tenant_id, pub_number):
    d = COMPOSER_OUTPUT_DIR / str(tenant_id) / pub_number
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_pdf_path(path):
    return path.lower().endswith(".pdf")


def _public_matrix(row):
    """Strips server-local storage paths — the frontend only needs to know
    it's loaded, how many requirements it holds, and whether a filled export
    is ready to download.
    """
    if row is None:
        return None
    return {"filename": row["filename"], "requirement_count": row["requirement_count"],
            "filled": bool(row["filled_path"])}


def _run_composer_ingest(tenant_id, pub_number, doc_id, path, content_type, role):
    if role == "example":
        # Style-learning docs are stored but never embedded/retrieved — Style
        # Guide (extract_style.py) stays a stub this pass, so there's nothing
        # to feed it into yet; matches proposal_tool/ingest.py's own
        # "example role is skipped from ingestion" behavior.
        store.update_composer_document_status(_db(), tenant_id, doc_id, status="style_only",
                                               pages=None, chunks=0, image_heavy=False)
        return
    n_chunks = composer.ingest_document(tenant_id, pub_number, doc_id, path, content_type, role)
    image_heavy = composer.detect_image_heavy(path, content_type)
    pages = len(composer._pdf_pages_text(path)) if _is_pdf_path(path) else None
    store.update_composer_document_status(_db(), tenant_id, doc_id, status="ingested",
                                           pages=pages, chunks=n_chunks, image_heavy=image_heavy)


@app.post("/api/composer/{pub_number}/documents")
async def upload_composer_document(pub_number: str, background: BackgroundTasks,
                                    file: UploadFile = File(...), role: Optional[str] = Form(None),
                                    tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    _require_shortlisted_tender(conn, tenant_id, pub_number)
    if role is not None and role not in _VALID_COMPOSER_ROLES:
        raise HTTPException(422, f"role must be one of {_VALID_COMPOSER_ROLES}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large")

    tenant_dir = COMPOSER_UPLOAD_DIR / str(tenant_id) / pub_number
    tenant_dir.mkdir(parents=True, exist_ok=True)
    ext = pathlib.Path(file.filename or "").suffix[:10]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = tenant_dir / stored_name
    storage_path.write_bytes(content)

    detected_role = role or composer.get_role(file.filename or "")
    doc_id = store.add_composer_document(conn, tenant_id, pub_number, file.filename or stored_name,
                                          file.content_type, len(content), str(storage_path), detected_role)
    background.add_task(_run_composer_ingest, tenant_id, pub_number, doc_id, str(storage_path),
                         file.content_type, detected_role)
    docs = store.list_composer_documents(conn, tenant_id, pub_number)
    return next(d for d in docs if d["id"] == doc_id)


class ComposerRoleBody(BaseModel):
    role: str

@app.patch("/api/composer/{pub_number}/documents/{document_id}")
def patch_composer_document_role(pub_number: str, document_id: int, body: ComposerRoleBody,
                                  tenant_id: int = Depends(get_current_tenant_id)):
    if body.role not in _VALID_COMPOSER_ROLES:
        raise HTTPException(422, f"role must be one of {_VALID_COMPOSER_ROLES}")
    conn = _db()
    doc = store.get_composer_document(conn, tenant_id, document_id)
    if doc is None or doc["pub_number"] != pub_number:
        raise HTTPException(404, "Document not found")
    store.set_composer_document_role(conn, tenant_id, document_id, body.role)
    return {"id": document_id, "role": body.role}


@app.post("/api/composer/{pub_number}/matrix")
async def upload_composer_matrix(pub_number: str, file: UploadFile = File(...),
                                  tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    _require_shortlisted_tender(conn, tenant_id, pub_number)
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large")

    tenant_dir = COMPOSER_UPLOAD_DIR / str(tenant_id) / pub_number
    tenant_dir.mkdir(parents=True, exist_ok=True)
    storage_path = tenant_dir / f"matrix_{uuid.uuid4().hex}.xlsx"
    storage_path.write_bytes(content)

    try:
        requirement_count = len(composer._load_matrix_requirements(str(storage_path)))
    except Exception:
        raise HTTPException(422, "Could not parse compliance matrix — expected the standard column layout")

    store.set_composer_matrix(conn, tenant_id, pub_number, file.filename or "compliance_matrix.xlsx",
                               str(storage_path), requirement_count)
    return _public_matrix(store.get_composer_matrix(conn, tenant_id, pub_number))


def _run_composer_enrich(tenant_id, pub_number):
    conn = _db()
    for doc in store.list_composer_documents(conn, tenant_id, pub_number):
        if not doc["image_heavy"]:
            continue
        full = store.get_composer_document(conn, tenant_id, doc["id"])
        if not full or not os.path.exists(full["storage_path"]):
            continue
        text = composer.enrich_datasheet(full["storage_path"])
        if not text:
            continue
        chunks = vault.chunk_text(text)
        new_chunk_count = 0
        if chunks:
            embeddings = vault._embedding_model().encode(chunks).tolist()
            ids = [f"doc{doc['id']}_enriched_chunk{i}" for i in range(len(chunks))]
            metadatas = [{"source": full["filename"], "doc_id": doc["id"], "role": full["role"]}
                         for _ in chunks]
            collection = composer._chroma_collection(tenant_id, pub_number)
            collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
            new_chunk_count = len(chunks)
        store.update_composer_document_status(
            conn, tenant_id, doc["id"], status="ingested",
            pages=doc["pages"], chunks=(doc["chunks"] or 0) + new_chunk_count, image_heavy=False)


@app.post("/api/composer/{pub_number}/enrich")
def trigger_composer_enrich(pub_number: str, background: BackgroundTasks,
                             tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    _require_shortlisted_tender(conn, tenant_id, pub_number)
    background.add_task(_run_composer_enrich, tenant_id, pub_number)
    return {"status": "started"}


def _run_composer_interpret(tenant_id, pub_number):
    conn = _db()
    inputs = []
    for doc in store.list_composer_documents(conn, tenant_id, pub_number):
        if doc["role"] not in ("sow", "parta"):
            continue
        full = store.get_composer_document(conn, tenant_id, doc["id"])
        if not full or not os.path.exists(full["storage_path"]):
            continue
        if _is_pdf_path(full["storage_path"]):
            pages = composer._pdf_pages_text(full["storage_path"])
        else:
            pages = [vault.parse_document(full["storage_path"], full["content_type"]) or ""]
        if doc["role"] == "parta":
            pages = [composer.extract_parta_section("\n".join(pages))]
        inputs.append({"filename": full["filename"], "role": doc["role"], "pages": pages})
    requirements = composer.extract_requirements(inputs)
    store.add_composer_requirements(conn, tenant_id, pub_number, requirements)


@app.post("/api/composer/{pub_number}/interpret")
def trigger_composer_interpret(pub_number: str, background: BackgroundTasks,
                                tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    _require_shortlisted_tender(conn, tenant_id, pub_number)
    background.add_task(_run_composer_interpret, tenant_id, pub_number)
    return {"status": "started"}


@app.get("/api/composer/session/{pub_number}")
def get_composer_session(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    tender = _find_tender(conn, tenant_id, pub_number)
    if tender is None:
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    return {
        "pub_number": pub_number,
        "tender_title": tender.get("tag_line", ""),
        "source": tender.get("source", ""),
        "deadline": tender.get("deadline", ""),
        "docs": store.list_composer_documents(conn, tenant_id, pub_number),
        "matrix": _public_matrix(store.get_composer_matrix(conn, tenant_id, pub_number)),
        "requirements": store.list_composer_requirements(conn, tenant_id, pub_number),
    }


class ComposerValidationBody(BaseModel):
    status: str

@app.patch("/api/composer/requirements/{requirement_id}")
def patch_composer_requirement(requirement_id: int, body: ComposerValidationBody,
                                tenant_id: int = Depends(get_current_tenant_id)):
    if body.status not in _VALID_COMPOSER_VALIDATION:
        raise HTTPException(422, f"status must be one of {_VALID_COMPOSER_VALIDATION}")
    conn = _db()
    if store.get_composer_requirement(conn, tenant_id, requirement_id) is None:
        raise HTTPException(404, "Requirement not found")
    store.update_composer_requirement_validation(conn, tenant_id, requirement_id, body.status)
    return {"id": requirement_id, "status": body.status}


@app.post("/api/composer/requirements/{requirement_id}/resolve")
def resolve_composer_requirement(requirement_id: int, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    if store.get_composer_requirement(conn, tenant_id, requirement_id) is None:
        raise HTTPException(404, "Requirement not found")
    store.mark_composer_requirement_resolved(conn, tenant_id, requirement_id)
    return {"id": requirement_id, "resolved": True}


def _run_composer_generate(tenant_id, pub_number):
    conn = _db()
    requirements = store.list_composer_requirements(conn, tenant_id, pub_number)
    for r in composer.run_generate(tenant_id, pub_number, requirements):
        store.update_composer_requirement_result(conn, tenant_id, r["id"], r["gap_status"],
                                                  r["similarity"], r["response_text"], r["citations"])

    final = store.list_composer_requirements(conn, tenant_id, pub_number)
    out_dir = _ensure_composer_output_dir(tenant_id, pub_number)
    composer.build_proposal_docx(final, str(out_dir / "technical_proposal.docx"))
    composer.build_gaps_report(final, str(out_dir / "gaps_report.txt"))

    matrix = store.get_composer_matrix(conn, tenant_id, pub_number)
    if matrix and os.getenv("ANTHROPIC_API_KEY"):
        filled_path = out_dir / "matrix_filled.xlsx"
        composer.fill_compliance_matrix(tenant_id, pub_number, matrix["storage_path"], str(filled_path))
        store.set_composer_matrix_filled_path(conn, tenant_id, pub_number, str(filled_path))


def _run_composer_refine(tenant_id, pub_number, requirement_id, feedback):
    conn = _db()
    req = store.get_composer_requirement(conn, tenant_id, requirement_id)
    if req is None:
        return
    query = f"{req['title']} {req['extracted']} {feedback}"
    tech_chunks = composer.retrieve_evidence(tenant_id, pub_number, query, roles=["tech"])
    new_text = composer.refine_section(req["extracted"], req["response"] or "", feedback, tech_chunks)
    store.update_composer_requirement_refined(conn, tenant_id, requirement_id, new_text, feedback)


class ComposerGenerateBody(BaseModel):
    requirement_id: Optional[int] = None
    feedback:       Optional[str] = None

@app.post("/api/composer/{pub_number}/generate")
def post_composer_generate(pub_number: str, background: BackgroundTasks,
                            body: ComposerGenerateBody = ComposerGenerateBody(),
                            tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    _require_shortlisted_tender(conn, tenant_id, pub_number)

    # Section-scoped regenerate (Proposal Review's "Regenerate section") —
    # dual-purpose per the design, distinguished by the presence of
    # requirement_id in the body rather than a separate endpoint.
    if body.requirement_id is not None:
        req = store.get_composer_requirement(conn, tenant_id, body.requirement_id)
        if req is None or req["pub_number"] != pub_number:
            raise HTTPException(404, "Requirement not found")
        if not body.feedback:
            raise HTTPException(422, "feedback is required for a section-scoped regenerate")
        background.add_task(_run_composer_refine, tenant_id, pub_number, body.requirement_id, body.feedback)
        return {"status": "started", "requirement_id": body.requirement_id}

    requirements = store.list_composer_requirements(conn, tenant_id, pub_number)
    if not requirements:
        raise HTTPException(409, "No requirements to generate from — run Interpret first")
    # Server-side gate, not just the UI's disabled button — every requirement
    # must be validated before a full draft run (design's explicit requirement).
    if any(r["validation"] != "validated" for r in requirements):
        raise HTTPException(403, "Every requirement must be validated before generating a draft")
    background.add_task(_run_composer_generate, tenant_id, pub_number)
    return {"status": "started"}


@app.get("/api/composer/{pub_number}/download/proposal.docx")
def download_composer_proposal(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    path = _composer_output_path(tenant_id, pub_number, "technical_proposal.docx")
    if not path.exists():
        raise HTTPException(404, "No proposal generated yet")
    return FileResponse(str(path), filename="technical_proposal.docx",
                         media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/api/composer/{pub_number}/download/matrix.xlsx")
def download_composer_matrix(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    matrix = store.get_composer_matrix(_db(), tenant_id, pub_number)
    if not matrix or not matrix["filled_path"] or not os.path.exists(matrix["filled_path"]):
        raise HTTPException(404, "No filled matrix available yet")
    return FileResponse(matrix["filled_path"], filename="matrix_filled.xlsx",
                         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/composer/{pub_number}/download/gaps_report.txt")
def download_composer_gaps(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    path = _composer_output_path(tenant_id, pub_number, "gaps_report.txt")
    if not path.exists():
        raise HTTPException(404, "No gaps report generated yet")
    return FileResponse(str(path), filename="gaps_report.txt", media_type="text/plain")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
