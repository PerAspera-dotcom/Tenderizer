"""FastAPI layer — thin read/trigger API over the Scout engine.

No matching, normalisation, or fetching logic lives here.
Reads via store.*, config.*; only POST /api/run triggers the engine.
"""
import sys, pathlib, json, logging, os
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

import store, config, auth
import run as engine

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Depends
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
    store.set_status(conn, tenant_id, pub_number, body.status)
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

    for r in records:
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
    return {"saved": True, "warnings": [str(w.message) for w in caught]}


class KeywordsBody(BaseModel):
    terms:       Optional[dict] = None
    distinctive: Optional[list] = None

@app.get("/api/config/keywords")
def get_keywords_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_tenant_keywords(_db(), tenant_id)

@app.put("/api/config/keywords")
def put_keywords_config(body: KeywordsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_tenant_keywords(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


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


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
