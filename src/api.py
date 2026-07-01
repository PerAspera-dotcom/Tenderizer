"""FastAPI layer — thin read/trigger API over the Scout engine.

No matching, normalisation, or fetching logic lives here.
Reads via store.*, config.*; only POST /api/run triggers the engine.
"""
import sys, pathlib, json, os
from datetime import date, timedelta

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import store, config
import run as engine

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

ROOT          = _HERE.parent
DB_PATH       = str(ROOT / "data" / "tenders.db")
REPORT_PATH   = str(ROOT / "reports" / "tenders.xlsx")
LAST_RUN_PATH = str(ROOT / "data" / "last_run.json")

app = FastAPI(title="Tenderizer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db():
    return store.init_db(DB_PATH)


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
    source:       Optional[str]  = None,
    category:     Optional[str]  = None,
    match_source: Optional[str]  = None,
    country:      Optional[str]  = None,
    q:            Optional[str]  = None,
    status:       Optional[str]  = None,
    has_deadline: Optional[bool] = None,
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0,   ge=0),
    sort:   str = "deadline",
):
    records = store.all_records(_db())

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
def get_tender(pub_number: str):
    for r in store.all_records(_db()):
        if r["pub_number"] == pub_number:
            return r
    raise HTTPException(404, "Tender not found")


class StatusBody(BaseModel):
    status: str

_VALID_STATUSES = {"new", "reviewed", "shortlisted", "dismissed"}

@app.patch("/api/tenders/{pub_number}")
def patch_tender(pub_number: str, body: StatusBody):
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")
    conn = _db()
    if not any(r["pub_number"] == pub_number for r in store.all_records(conn)):
        raise HTTPException(404, "Tender not found")
    store.set_status(conn, pub_number, body.status)
    return {"pub_number": pub_number, "status": body.status}


# ── Stats & health ────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    records  = store.all_records(_db())
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
def get_health():
    last_run_health = _last_run().get("health", {})
    result = []
    for portal in _PORTAL_META:
        entry = dict(portal)
        if portal["name"] in last_run_health:
            entry["last_result"] = last_run_health[portal["name"]]
        result.append(entry)
    return result


# ── Run now ───────────────────────────────────────────────────────────────────

def _do_run():
    os.makedirs(str(ROOT / "data"), exist_ok=True)
    os.makedirs(str(ROOT / "reports"), exist_ok=True)
    since   = date.today() - timedelta(days=30)
    sources = engine._default_sources(since)
    engine.run_pipeline(sources, DB_PATH, REPORT_PATH)

@app.post("/api/run")
def post_run(background: BackgroundTasks):
    background.add_task(_do_run)
    return {"status": "started"}


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config/cpv")
def get_cpv_config():
    active = set(config.cpv_codes())
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
def put_cpv_config(body: CpvBody):
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config.write_cpv(body.codes)
    return {"saved": True, "warnings": [str(w.message) for w in caught]}


class KeywordsBody(BaseModel):
    terms:       Optional[dict] = None
    distinctive: Optional[list] = None

@app.get("/api/config/keywords")
def get_keywords_config():
    kw = config._load("keywords.yaml")
    return {"terms": kw.get("terms", {}), "distinctive": kw.get("distinctive", [])}

@app.put("/api/config/keywords")
def put_keywords_config(body: KeywordsBody):
    config.write_keywords(body.model_dump(exclude_none=True))
    return {"saved": True}


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/api/reports/latest")
def get_latest_report():
    if not os.path.exists(REPORT_PATH):
        raise HTTPException(404, "No report found — run the pipeline first")
    return FileResponse(
        REPORT_PATH,
        filename="tenders.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Portal: pipeline & follow-up ──────────────────────────────────────────────

@app.get("/api/pipeline")
def get_pipeline():
    return store.get_pipeline_entries(_db())


class PipelinePatch(BaseModel):
    submission_status: Optional[str] = None
    deadline_override: Optional[str] = None
    notes:             Optional[str] = None
    owner:             Optional[str] = None

_VALID_SUBMISSION = {"not_started", "drafting", "submitted"}

@app.patch("/api/pipeline/{pub_number}")
def patch_pipeline(pub_number: str, body: PipelinePatch):
    if body.submission_status and body.submission_status not in _VALID_SUBMISSION:
        raise HTTPException(422, f"submission_status must be one of {_VALID_SUBMISSION}")
    conn   = _db()
    store.ensure_pipeline_entry(conn, pub_number)
    fields = body.model_dump(exclude_none=True)
    store.set_pipeline_entry(conn, pub_number, fields)
    return {"pub_number": pub_number, **fields}


@app.get("/api/followup")
def get_followup():
    return store.get_followup_entries(_db())


class FollowupPatch(BaseModel):
    outcome: str

_VALID_OUTCOMES = {"pending", "won", "lost"}

@app.patch("/api/followup/{pub_number}")
def patch_followup(pub_number: str, body: FollowupPatch):
    if body.outcome not in _VALID_OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {_VALID_OUTCOMES}")
    conn = _db()
    store.ensure_pipeline_entry(conn, pub_number)
    store.set_pipeline_entry(conn, pub_number, {"outcome": body.outcome})
    return {"pub_number": pub_number, "outcome": body.outcome}


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
