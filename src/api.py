"""FastAPI layer — thin read/trigger API over the Scout engine.

No matching, normalisation, or fetching logic lives here.
Reads via store.*, config.*; only POST /api/run triggers the engine.
"""
import sys, pathlib, json, logging, os, uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
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

import store, config, auth, vault, composer, relevance
import run as engine
from schema import RELEVANCE_REASON_CATEGORIES

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
        _scheduler.add_job(_run_all_tenants, "cron", hour=DAILY_SCRAPE_HOUR_UTC, minute=0, id="daily_scrape")
        # CR-004 F4: daily backup, after the scrape so the freshest data is
        # captured. backup.run_backup() never raises (catches + alerts
        # internally) — wrapped again here as defense in depth so a bug in
        # that handling still can't take the scheduler down.
        _scheduler.add_job(_run_backup_job, "cron", hour=3, minute=0, id="daily_backup")
        # Notifications & workflow: daily digest, after the scrape + backup
        # so it reports the freshest data and doesn't compete with them.
        _scheduler.add_job(_run_daily_digest, "cron", hour=4, minute=0, id="daily_digest")
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


def _verify_claims(creds: Optional[HTTPAuthorizationCredentials]) -> dict:
    """Shared by get_current_tenant_id and get_current_identity below — both
    need a verified claims dict, just project different fields out of it.
    401 on a missing or invalid/expired token.
    """
    if creds is None:
        raise HTTPException(401, "Missing bearer token")
    try:
        return auth.verify_token(creds.credentials)
    except auth.AuthError as e:
        raise HTTPException(401, f"Invalid or expired token: {e}")
    # auth.AuthNotConfigured (CLERK_JWKS_URL unset) deliberately propagates
    # uncaught -> FastAPI's default 500 — that's a server misconfiguration,
    # not a bad token, and every token would fail identically until fixed.


def _org_id_from_claims(claims: dict) -> Optional[str]:
    """CR-007 Phase A: Clerk includes org-membership claims automatically
    whenever the caller has an active organization selected. Confirmed
    against a real prod session token (Organizations enabled, active org
    set): the default compact claim `o: {id, rol, slg}`, not a top-level
    `org_id` — this instance doesn't customise the token to add an
    `org_id` shortcode (only `email` is added; see get_current_identity
    below). The `org_id` check is kept as a defensive fallback in case that
    ever changes, but `o.id` is the one that's actually live.
    """
    org_id = claims.get("org_id")
    if org_id:
        return org_id
    org = claims.get("o")
    if isinstance(org, dict):
        return org.get("id")
    return None


def _resolve_tenant_id(claims: dict) -> int:
    clerk_user_id = claims["sub"]
    conn = _db()
    org_id = _org_id_from_claims(claims)
    if org_id:
        # CR-007 Phase A: an active org makes the *org* the tenant, shared by
        # every member — link_or_create_tenant_for_clerk_org self-heals a
        # pre-Phase-A user's existing solo tenant onto their first org rather
        # than needing a bulk data migration (see its docstring).
        return store.link_or_create_tenant_for_clerk_org(
            conn, org_id, clerk_user_id, claims.get("email"))
    # No active org selected (not yet created/joined one, or Organizations
    # isn't enabled on this instance) — fall back to the pre-Phase-A
    # per-Clerk-user tenant so nothing breaks mid-rollout.
    tenant_id = store.get_tenant_id_by_clerk_user_id(conn, clerk_user_id)
    if tenant_id is None:
        tenant_id = store.create_tenant_for_clerk_user(conn, clerk_user_id, claims.get("email"))
    return tenant_id


def get_current_tenant_id(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> int:
    """Real Clerk session-token resolver (phase2/3 step 6) — every route
    below depends on this function rather than a hardcoded value, so this is
    the only place that changed when the step 3 stub became real auth.

    401 on a missing or invalid/expired token. CR-007 Phase A: the tenant is
    now the caller's active Clerk *organization* when one is selected
    (shared by every org member), falling back to the legacy 1-Clerk-user =
    1-tenant resolution otherwise — see _resolve_tenant_id.
    """
    claims = _verify_claims(creds)
    return _resolve_tenant_id(claims)


class Identity:
    """tenant_id + a human-readable account name + the raw Clerk user id,
    all taken from a verified Clerk token — never client-supplied. CR-006
    D3: dismissal attribution needs "who", not just "which tenant", which
    get_current_tenant_id alone doesn't expose — clerk_user_id/account_name
    matter once tenant_id can be shared by a whole org (see api.patch_tender,
    auth.list_organization_members). Kept as its own dependency rather than
    changing get_current_tenant_id's return type, to avoid touching every
    existing route.
    """
    def __init__(self, tenant_id: int, account_name: str, clerk_user_id: str, org_id: Optional[str] = None):
        self.tenant_id = tenant_id
        self.account_name = account_name
        self.clerk_user_id = clerk_user_id
        # None when the caller has no active Clerk organization selected
        # (pre-Phase-A solo tenant, or Organizations disabled) — see
        # auth.list_organization_members, the one consumer of this.
        self.org_id = org_id


def get_current_identity(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Identity:
    claims = _verify_claims(creds)
    tenant_id = _resolve_tenant_id(claims)
    # Clerk session tokens always carry 'sub'; 'email' is only present if the
    # Clerk instance's session token template includes it (true here — see
    # create_tenant_for_clerk_user's claims.get("email") above). Fall back to
    # the Clerk user id so attribution never silently ends up empty.
    account_name = claims.get("email") or claims["sub"]
    return Identity(tenant_id=tenant_id, account_name=account_name, clerk_user_id=claims["sub"],
                     org_id=_org_id_from_claims(claims))


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


# Matches the scheduled scrape's own cron time (see _lifespan's daily_scrape
# job below) — kept as one constant so the two can't silently drift apart.
DAILY_SCRAPE_HOUR_UTC = 2


def _next_scheduled_run(now=None) -> datetime:
    """The next occurrence of the daily scrape's 02:00 UTC cron from `now`
    (real UTC clock by default). Dashboard's "Next run in" display was
    hardcoded to None/never-shown before ENABLE_SCHEDULER's job existed for
    real (CR-004 F4) — now that it does, this is a real countdown, not a
    placeholder.
    """
    now = now or datetime.now(timezone.utc)
    candidate = now.replace(hour=DAILY_SCRAPE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/api/health-check")
def health_check():
    return {"status": "ok"}


# ── Tenders ───────────────────────────────────────────────────────────────────

def _add_dismissal_reason_category(records):
    """Post-CR-007: the whole Review Queue is org-shared now — `all_records`
    already returns the one true `status`/dismissal_reason/dismissed_by/
    dismissed_at/assigned_to straight off the `tenders` row, no per-account
    merge needed (see schema.py's tender_reviews retirement comment). The
    one translation still needed: the DB column is `reason_category` (it
    lives right next to `dismissal_reason` on `tenders`), but the API/
    frontend contract has always called it `dismissal_reason_category` (see
    types.ts) — renamed here rather than churning every existing caller.
    Mutates and returns `records` in place.
    """
    for r in records:
        r["dismissal_reason_category"] = r.pop("reason_category", None)
    return records


def _attach_relevance(conn, tenant_id, records):
    """CR-007 Phase B (B3): computes relevance_score/relevance_reasoning for
    every tender still awaiting a decision (status "new" or "needs_review")
    — a decided tender (shortlisted/dismissed_final) doesn't need one. The
    positive/negative training pools are pulled straight from this same
    `records` list (must be the tenant's *full* record set, not an already-
    filtered/paginated slice) — an org-wide aggregate by construction now
    that status/reason_category live on the one shared `tenders` row (post-
    CR-007; used to need a separate store.get_org_dismissed_final_reviews
    join here, back when dismissal was personal per-account). Must run
    before _add_dismissal_reason_category renames that field. Mutates
    `records` in place and returns it.
    """
    positive = [r for r in records if r.get("status") == "shortlisted"]
    negative = [r for r in records if r.get("status") == "dismissed_final"]
    overrides = store.get_relevance_overrides(conn, tenant_id)

    for r in records:
        if r.get("status") not in ("new", "needs_review"):
            continue
        override = overrides.get(r["pub_number"])
        if override:
            r["relevance_score"] = override["score"]
            r["relevance_reasoning"] = override["note"] or "Corrected by a reviewer."
            r["relevance_corrected"] = True
            r["relevance_corrected_by"] = override["account_name"]
        else:
            result = relevance.score_relevance(r, positive, negative)
            r["relevance_score"] = result["score"]
            r["relevance_reasoning"] = result["reasoning"]
            r["relevance_corrected"] = False
            r["relevance_corrected_by"] = None
    return records


def _attach_duplicates(conn, tenant_id, records):
    """CR-007 Phase C: attaches a `duplicates` list to every tender that has
    at least one detected cross-portal match (store.
    get_tender_duplicates_for_tenant stores one row per pair; this expands
    it back onto *both* pub_numbers at read time, so e.g. the TED record
    shows "also listed on BOAMP" and the BOAMP record shows "also listed on
    TED" — never a one-sided link). Always an empty list, never a missing
    key, when there's no duplicate. `records` should be the tenant's full
    set (not a filtered/paginated slice) so both sides of a pair resolve
    even if one side would otherwise be filtered out of this particular
    response.
    """
    by_pub = {r["pub_number"]: r for r in records}
    for r in records:
        r["duplicates"] = []
    for d in store.get_tender_duplicates_for_tenant(conn, tenant_id):
        rec_a, rec_b = by_pub.get(d["pub_number_a"]), by_pub.get(d["pub_number_b"])
        if rec_a is None or rec_b is None:
            continue
        # `url` is the counterpart's own stored portal URL — already a direct
        # national-portal link when the counterpart is e.g. a BOAMP record
        # (see normalize_boamp's url field), so C2 ("instant national-portal
        # link") needs no separate plumbing once this is included here.
        rec_a["duplicates"].append({"pub_number": rec_b["pub_number"], "source": rec_b["source"],
                                     "url": rec_b["url"], "match_type": d["match_type"],
                                     "similarity": d["similarity"]})
        rec_b["duplicates"].append({"pub_number": rec_a["pub_number"], "source": rec_a["source"],
                                     "url": rec_a["url"], "match_type": d["match_type"],
                                     "similarity": d["similarity"]})
    return records


# CR-007 Phase D (D1): how long a presence heartbeat counts as "currently
# viewing" — long enough to survive one missed heartbeat (the frontend pings
# every 20s), short enough that the warning disappears promptly once someone
# actually navigates away. A constant, not a tenant setting — this is an
# implementation detail of the heartbeat cadence, not a business rule.
PRESENCE_WINDOW = timedelta(minutes=2)


def _attach_presence(conn, tenant_id, records, identity):
    """CR-007 Phase D (D1): attaches a `viewers` list — every *other* account
    (never the caller themselves) with a presence heartbeat inside
    PRESENCE_WINDOW, grouped by pub_number. Lightweight awareness only
    ("X is currently working this tender") — never blocks or disables any
    action, no locking (see schema.py's tender_presence comment).
    """
    since_iso = (datetime.now(timezone.utc) - PRESENCE_WINDOW).isoformat()
    by_pub = {r["pub_number"]: r for r in records}
    for r in records:
        r["viewers"] = []
    for v in store.get_tender_viewers(conn, tenant_id, since_iso):
        if v["clerk_user_id"] == identity.clerk_user_id:
            continue
        rec = by_pub.get(v["pub_number"])
        if rec is not None:
            rec["viewers"].append({"account_name": v["account_name"], "last_seen_at": v["last_seen_at"]})
    return records


def _attach_notifications(conn, tenant_id, records):
    """CR-008 W2: attaches row-level notification metadata — `forwarded_to`
    (the most recent forward's recipient, or None) and `last_notification_at`
    (the most recent ping of any kind, or None) — so the Review Queue list
    can show this without opening each tender. Org-shared like the rest of
    the queue (see store.get_last_notification_times' docstring), unlike
    _attach_presence's personal viewer list or get_notifications' personal
    feed.
    """
    last_notification = store.get_last_notification_times(conn, tenant_id)
    forwarded_to = store.get_last_forwarded_to(conn, tenant_id)
    for r in records:
        r["forwarded_to"] = forwarded_to.get(r["pub_number"])
        r["last_notification_at"] = last_notification.get(r["pub_number"])
    return records


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
    identity: Identity = Depends(get_current_identity),
):
    tenant_id = identity.tenant_id
    conn = _db()
    records = store.all_records(conn, tenant_id)
    _attach_relevance(conn, tenant_id, records)
    _add_dismissal_reason_category(records)
    _attach_duplicates(conn, tenant_id, records)
    _attach_presence(conn, tenant_id, records, identity)
    _attach_notifications(conn, tenant_id, records)

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

    # Default: hide expired deadlines; show future deadlines and empty-deadline
    # rows. CR-006: a dismissed tender is being reviewed for its dismissal, not
    # its live deadline — an old dismissal with a since-passed deadline must
    # still show up on the Dismissed tab, so this filter doesn't apply there.
    # CR-007 Phase B (B1): only the *final* dismissed tab gets that exemption
    # now — a soft-dismissed ("dismissed") tender is still an active Review
    # Queue item and stays subject to the normal expiry filter like anything
    # else still awaiting a decision.
    if status != "dismissed_final":
        today = date.today().isoformat()
        records = [r for r in records
                   if not r.get("deadline") or r["deadline"][:10] >= today]

    records.sort(key=lambda r: (r.get("deadline") or "9999-99-99"))
    return {"total": len(records), "results": records[offset: offset + limit]}


@app.get("/api/tenders/{pub_number}")
def get_tender(pub_number: str, include_excluded: bool = False,
               identity: Identity = Depends(get_current_identity)):
    tenant_id = identity.tenant_id
    conn = _db()
    # The full org record set, not just the one requested, so a relevance
    # score for it (see _attach_relevance) is computed against the org's
    # real positive/negative pools rather than an empty one, and so a
    # duplicate's counterpart (see _attach_duplicates) is resolvable too.
    records = store.all_records(conn, tenant_id)
    _attach_relevance(conn, tenant_id, records)
    _add_dismissal_reason_category(records)
    _attach_duplicates(conn, tenant_id, records)
    _attach_presence(conn, tenant_id, records, identity)
    _attach_notifications(conn, tenant_id, records)
    for r in records:
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
    note: Optional[str] = None  # CR-006: dismissal reason — required when status is a dismiss stage
    # CR-007 Phase B (B3): fixed dismiss-reason tag, required alongside `note`
    # on either dismiss stage — see schema.RELEVANCE_REASON_CATEGORIES.
    reason_category: Optional[str] = None
    # Post-CR-007: optional colleague to ping on a needs_review parking —
    # see _send_needs_review_ping_email below. Ignored for any other status.
    assigned_to: Optional[str] = None

_VALID_STATUSES = {"new", "reviewed", "shortlisted", "dismissed", "dismissed_final", "needs_review"}
# CR-007 Phase B (B1): both dismiss stages require their own mandatory note
# (plain "dismissed" is stage 1/soft — still visible, greyed out, in the
# Review Queue; "dismissed_final" is stage 2, moves it to the Dismissed tab)
# and, per B3, a fixed reason_category alongside it. B2's "needs_review"
# reuses the same mandatory-note requirement for its parking comment, but
# has no category (that's a dismiss-only aggregation signal).
_DISMISS_STATUSES = {"dismissed", "dismissed_final"}
_NOTE_REQUIRED_STATUSES = _DISMISS_STATUSES | {"needs_review"}

def _send_needs_review_ping_email(tenant_id, pub_number, assigned_to, note, sender_name):
    """Post-CR-007: pings the colleague named in a needs_review parking —
    same alerts.send_tenant_email primitive as _send_owner_handoff_email /
    CR-007 G1's _send_forward_email, addressed to that specific colleague
    rather than the tenant's single notify_email. Fire-and-forget, no new
    notification subsystem, same shape as its siblings above.
    """
    import alerts
    conn = _db()
    tender = _find_tender(conn, tenant_id, pub_number)
    label = f"{tender['tag_line']} ({pub_number})" if tender else pub_number
    lines = [f"{sender_name} flagged a tender for your review:", "", label, "", note]
    if tender and tender.get("deadline"):
        lines.append(f"Deadline: {tender['deadline'][:10]}")
    alerts.send_tenant_email(assigned_to, f"Tenderizer — {sender_name} needs your review", "\n".join(lines))


@app.patch("/api/tenders/{pub_number}")
def patch_tender(pub_number: str, body: StatusBody, background: BackgroundTasks = None,
                  identity: Identity = Depends(get_current_identity)):
    # background defaults to None (unlike forward_tender/patch_pipeline's bare
    # `background: BackgroundTasks`) so the many existing direct-call test
    # sites that predate the assigned_to ping don't all need updating —
    # FastAPI still injects a real BackgroundTasks for actual HTTP requests
    # regardless of the default; only a direct Python call can leave it None.
    tenant_id = identity.tenant_id
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")
    conn = _db()
    records = store.all_records(conn, tenant_id)
    current = next((r for r in records if r["pub_number"] == pub_number), None)
    if current is None:
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    # CR-006 D2 / CR-007 B1-B2: a note is only relevant, and now required, on
    # a dismiss stage or a "needs further review" — a note sent with any
    # other status is silently ignored rather than 422ing, same as CR-002
    # C2's original optional-note behavior.
    if body.status in _NOTE_REQUIRED_STATUSES and not (body.note or "").strip():
        verb = "dismiss" if body.status in _DISMISS_STATUSES else "park for further review"
        raise HTTPException(400, f"A reason is required to {verb} a tender")
    if body.status in _DISMISS_STATUSES and body.reason_category not in RELEVANCE_REASON_CATEGORIES:
        raise HTTPException(400, f"reason_category must be one of {RELEVANCE_REASON_CATEGORIES}")
    assigned_to = (body.assigned_to or "").strip() or None
    if assigned_to and body.status != "needs_review":
        assigned_to = None  # only meaningful alongside a needs_review parking
    elif assigned_to and "@" not in assigned_to:
        raise HTTPException(422, "assigned_to must be a valid email address")

    # Post-CR-007: every transition writes the one shared `tenders` row now
    # (client feedback reversed CR-007 Phase A's personal-per-account split —
    # see schema.py's tender_reviews retirement comment) — "shortlisted" is
    # no longer a special case among statuses.
    store.set_status(
        conn, tenant_id, pub_number, body.status,
        dismissal_reason=body.note.strip() if body.status in _NOTE_REQUIRED_STATUSES else None,
        dismissed_by=identity.account_name if body.status in _DISMISS_STATUSES else None,
        dismissed_at=datetime.now(timezone.utc).isoformat() if body.status in _DISMISS_STATUSES else None,
        reason_category=body.reason_category if body.status in _DISMISS_STATUSES else None,
        assigned_to=assigned_to)
    if assigned_to:
        # CR-008 W1: in-app record, written synchronously — see
        # forward_tender's identical reasoning. Unconditional on `background`
        # (unlike the email enqueue below) since it's not deferrable work.
        store.create_tender_notification(conn, tenant_id, pub_number, "needs_review_ping",
                                          identity.account_name, assigned_to,
                                          body.note.strip(), body.status)
        if background is not None:
            background.add_task(_send_needs_review_ping_email, tenant_id, pub_number,
                                 assigned_to, body.note.strip(), identity.account_name)
    return {"pub_number": pub_number, "status": body.status}


class RelevanceBody(BaseModel):
    score: int
    note: Optional[str] = None


@app.patch("/api/tenders/{pub_number}/relevance")
def patch_tender_relevance(pub_number: str, body: RelevanceBody,
                            identity: Identity = Depends(get_current_identity)):
    """CR-007 Phase B (B3): a reviewer's correction to a computed relevance
    score — org-shared (see schema.tender_relevance_overrides), and fed back
    as-is on the next read via _attach_relevance rather than the computed
    value. Doesn't change the tender's triage status; purely a scoring note.
    """
    tenant_id = identity.tenant_id
    if not 0 <= body.score <= 100:
        raise HTTPException(422, "score must be between 0 and 100")
    conn = _db()
    if not any(r["pub_number"] == pub_number for r in store.all_records(conn, tenant_id)):
        if store.pub_number_exists_for_other_tenant(conn, tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    store.upsert_relevance_override(
        conn, tenant_id, pub_number, body.score, body.note, identity.account_name)
    return {"pub_number": pub_number, "relevance_score": body.score, "relevance_corrected": True}


@app.post("/api/tenders/{pub_number}/presence")
def post_tender_presence(pub_number: str, identity: Identity = Depends(get_current_identity)):
    """CR-007 Phase D (D1): a heartbeat — the frontend calls this on opening
    a tender and every ~20s while it stays open (ReviewQueue.tsx). No lock,
    no rejection — anyone can always still act on the tender; this only
    powers the informational "X is currently working this tender" banner
    (see _attach_presence). Doesn't require the tender to exist in this
    tenant beyond the usual identity/tenant scoping — a stray presence ping
    for a pub_number that later turns out invalid is harmless and self-heals
    (it simply never surfaces, since _attach_presence only expands onto
    records that exist in the tenant's own record set).
    """
    store.upsert_tender_presence(
        _db(), identity.tenant_id, pub_number, identity.clerk_user_id, identity.account_name)
    return {"ok": True}


class ForwardBody(BaseModel):
    to_email: str
    message: Optional[str] = None


def _send_forward_email(tenant_id, pub_number, to_email, message, sender_name):
    """CR-007 Phase G (G1): an explicit account-to-account reminder/forward
    — "please review this tender" from the Review Queue, or a deadline
    nudge from the Pipeline — reusing the same alerts.send_tenant_email
    primitive _send_owner_handoff_email already established for tenant-
    facing mail, just addressed to a specific colleague's inbox (`to_email`)
    rather than the tenant's single notify_email. Deliberately no new
    notification subsystem — rides the existing one, per the CR's own
    instruction ("build it as part of that notification layer, not a
    separate silo"); no new persistence either, same fire-and-forget shape
    as the owner-handoff email.
    """
    import alerts
    conn = _db()
    tender = _find_tender(conn, tenant_id, pub_number)
    label = f"{tender['tag_line']} ({pub_number})" if tender else pub_number
    lines = [f"{sender_name} sent you a reminder about a tender:", "", label]
    if tender and tender.get("deadline"):
        lines.append(f"Deadline: {tender['deadline'][:10]}")
    if message:
        lines += ["", message]
    alerts.send_tenant_email(to_email, f"Tenderizer — {sender_name} sent you a reminder", "\n".join(lines))


@app.post("/api/tenders/{pub_number}/forward")
def forward_tender(pub_number: str, body: ForwardBody, background: BackgroundTasks,
                    identity: Identity = Depends(get_current_identity)):
    """CR-007 Phase G (G1): forward a tender to a specific colleague's email
    — a deadline reminder or a "please review this" nudge, sender and
    recipient both explicit accounts, never a broadcast.
    """
    to_email = body.to_email.strip()
    if "@" not in to_email:
        raise HTTPException(422, "to_email must be a valid email address")
    conn = _db()
    tender = next((r for r in store.all_records(conn, identity.tenant_id) if r["pub_number"] == pub_number), None)
    if tender is None:
        if store.pub_number_exists_for_other_tenant(conn, identity.tenant_id, pub_number):
            raise HTTPException(403, "Forbidden")
        raise HTTPException(404, "Tender not found")
    message = (body.message or "").strip() or None
    # CR-008 W1: in-app record, written synchronously (unlike the email
    # below, this is a cheap DB write with no reason to defer) so it's
    # immediately visible in the recipient's notification feed.
    store.create_tender_notification(conn, identity.tenant_id, pub_number, "forward",
                                      identity.account_name, to_email, message, tender["status"])
    background.add_task(_send_forward_email, identity.tenant_id, pub_number, to_email,
                         message, identity.account_name)
    return {"sent": True}


@app.get("/api/org/members")
def get_org_members(identity: Identity = Depends(get_current_identity)):
    """The real Clerk org roster (self excluded) that feeds the "forward to
    a colleague" and "assign for review" pickers — see
    auth.list_organization_members. Always a plain list, never an error: no
    active org, or CLERK_SECRET_KEY unset, both just mean an empty list, and
    the frontend falls back to manual email entry either way.
    """
    members = auth.list_organization_members(identity.org_id)
    return [m for m in members if m["clerk_user_id"] != identity.clerk_user_id]


@app.get("/api/notifications")
def get_notifications(identity: Identity = Depends(get_current_identity)):
    """CR-008 W1: the caller's own in-app notification feed — every forward/
    needs_review ping addressed to them (`to_email` matched against their
    own Identity.account_name, always their email — see auth.py), newest
    first, plus the unread count for the profile badge.
    """
    conn = _db()
    notifications = store.get_notifications_for_recipient(conn, identity.tenant_id, identity.account_name)
    unread = sum(1 for n in notifications if n["read_at"] is None)
    return {"notifications": notifications, "unread_count": unread}


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, identity: Identity = Depends(get_current_identity)):
    store.mark_notification_read(_db(), identity.tenant_id, notification_id, identity.account_name)
    return {"ok": True}


@app.get("/api/tenders/{pub_number}/history")
def get_tender_history(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    return {"pub_number": pub_number, "history": store.get_tender_history(_db(), tenant_id, pub_number)}


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
        "next_run":        _next_scheduled_run().isoformat(),
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
    # CR-008: investigated adding this as a fourth source. Its listings API
    # (POST /api/sea/search/publications, found via the SPA's own network
    # calls) 403s scripted requests — including same-session fetch() calls
    # made from within the live page itself, not just cold requests — so this
    # isn't a fetchable connector today. BOSA's ToU (bosa.belgium.be) and
    # publicprocurement.be carry no robots.txt and no published API/scraping
    # policy either way — nothing to build against or clear compliance-wise
    # until BOSA confirms automated access is permitted. Same paused shape as
    # DTVP below rather than "planned", since "planned" implied only that
    # nobody had gotten to it yet.
    {"name": "Belgian e-Procurement (BDA)", "region": "Belgium", "status": "paused",
     "detail": "Scraper paused — no API access confirmed, search endpoint blocks scripted requests"},
    {"name": "DTVP",          "region": "Germany", "status": "paused",
     "detail": "Scraper paused — ToS review pending"},
]

@app.get("/api/health")
def get_health(tenant_id: int = Depends(get_current_tenant_id)):
    # Tenant-gated, not ops-gated: this is the Scout Dashboard's Portal
    # Health panel (TENDERIZER_HANDOFF.md §6/§8) — real, Phase-1,
    # tenant-facing data, unlike /api/reports/latest. tenant_id isn't used
    # for last_result any more (was: a single shared last_run.json file, not
    # tenant-scoped) — CR-004 F4's source_health table replaces it as the
    # source of truth for last_result too, now tenant-scoped for real.
    conn = _db()
    result = []
    for portal in _PORTAL_META:
        entry = dict(portal)
        # CR-004 F4: streak/failure history, real once source_health has
        # accumulated rows (empty/zeroed for a source with no run history
        # yet, e.g. "planned"/"paused" ones that never actually fetch).
        entry.update(store.get_source_health(conn, tenant_id, portal["name"]))
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


def _run_backup_job():
    try:
        import backup
        backup.run_backup()
    except Exception:
        logging.exception("scheduled backup job crashed outside run_backup()'s own handling")


def _run_daily_digest():
    import alerts, digest
    conn = _db()
    for tenant_id in store.list_provisioned_tenant_ids(conn):
        try:
            settings = store.get_tenant_settings(conn, tenant_id)
            if not settings["notify_on_complete"] or not settings["notify_email"]:
                continue
            body = digest.build_daily_digest(conn, tenant_id)
            if body:
                alerts.send_tenant_email(settings["notify_email"], "Tenderizer — daily digest", body)
        except Exception:
            logging.exception(f"daily digest failed for tenant {tenant_id}")


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/cpv/labels")
def get_cpv_labels(codes: str = Query(..., description="comma-separated CPV codes")):
    """CR-008 W5: human-readable labels for arbitrary CPV codes — unlike
    /api/config/cpv (which only ever returns the tenant's *active* set),
    this looks up whatever codes the caller asks for, since a tender's own
    cpv_codes commonly includes codes outside the tenant's active list
    (e.g. matched via keyword, not CPV). Not tenant-scoped — the reference
    itself is global (config.cpv_reference's own comment). Unknown codes
    are simply omitted, matching config.cpv_label's own fallback-to-the-
    raw-code behavior on the frontend.
    """
    ref = config.cpv_reference()
    wanted = [c.strip() for c in codes.split(",") if c.strip()]
    return {
        code: {"en": entry.get("en"), "fr": entry.get("fr"),
               "nl": entry.get("nl"), "de": entry.get("de")}
        for code in wanted
        if (entry := ref.get(code)) is not None
    }


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


def _send_owner_handoff_email(tenant_id, pub_number, old_owner, new_owner):
    import alerts
    conn = _db()
    settings = store.get_tenant_settings(conn, tenant_id)
    if not settings["notify_on_complete"] or not settings["notify_email"]:
        return
    tender = _find_tender(conn, tenant_id, pub_number)
    label = f"{tender['tag_line']} ({pub_number})" if tender else pub_number
    body = f"Pipeline owner for {label} changed: {old_owner or 'unassigned'} → {new_owner}"
    alerts.send_tenant_email(settings["notify_email"], "Tenderizer — pipeline owner changed", body)


@app.patch("/api/pipeline/{pub_number}")
def patch_pipeline(pub_number: str, body: PipelinePatch, background: BackgroundTasks,
                    tenant_id: int = Depends(get_current_tenant_id)):
    if body.submission_status and body.submission_status not in _VALID_SUBMISSION:
        raise HTTPException(422, f"submission_status must be one of {_VALID_SUBMISSION}")
    conn   = _db()
    store.ensure_pipeline_entry(conn, tenant_id, pub_number)
    fields = body.model_dump(exclude_none=True)
    changed = store.set_pipeline_entry(conn, tenant_id, pub_number, fields)
    if "owner" in changed:
        old_owner, new_owner = changed["owner"]
        background.add_task(_send_owner_handoff_email, tenant_id, pub_number, old_owner, new_owner)
    return {"pub_number": pub_number, **fields}


@app.get("/api/pipeline/{pub_number}/history")
def get_pipeline_history(pub_number: str, tenant_id: int = Depends(get_current_tenant_id)):
    return {"pub_number": pub_number, "history": store.get_pipeline_history(_db(), tenant_id, pub_number)}


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
    conn = _db()
    hints = store.get_vault_rules(conn, tenant_id)["hints"]
    threshold = store.get_vault_settings(conn, tenant_id)["confidence_threshold"]
    result = vault.process_upload(tenant_id, doc_id, path, content_type,
                                   extra_hints=hints, confidence_threshold=threshold)
    store.update_vault_document_metadata(
        _db(), tenant_id, doc_id, doc_type=result["doc_type"], metadata=result["metadata"],
        cpv_codes=result["cpv_codes"], confidence=result["confidence"],
        fields_extracted=result["fields_extracted"], status=result["status"],
        valid_until=result["valid_until"])


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


# CR-007 Phase E (E1): the "aging" window before outright expiry — a doc
# whose valid_until falls within this many days gets flagged expiring_soon,
# not just expired. Not tenant-configurable (unlike Phase D's deadline
# window) — the CR doesn't ask for that here, and a document's own stated
# validity is a fixed fact, not a business-tunable urgency threshold.
EXPIRY_WARNING_DAYS = 30


def _attach_expiry(docs):
    """CR-007 Phase E (E1): computed, not stored — `expired` (valid_until
    has passed) / `expiring_soon` (within EXPIRY_WARNING_DAYS) flags purely
    from valid_until vs today. A doc with no valid_until (most of them, per
    vault.py's "never guess" extraction rule) gets both False, never a
    fabricated urgency. Mutates `docs` in place and returns it.
    """
    today = date.today()
    for d in docs:
        expired = expiring_soon = False
        vu = d.get("valid_until")
        if vu:
            try:
                vu_date = date.fromisoformat(vu)
                expired = vu_date < today
                expiring_soon = not expired and (vu_date - today).days <= EXPIRY_WARNING_DAYS
            except ValueError:
                pass
        d["expired"] = expired
        d["expiring_soon"] = expiring_soon
    return docs


def _attach_suggested_filename(docs):
    """CR-007 Phase E (E2): a `suggested_filename` computed from what
    extraction already found (vault.suggest_filename) — omitted entirely
    when it wouldn't actually change anything, so the frontend only shows
    an "Accept" affordance when there's a real suggestion to accept.
    """
    for d in docs:
        suggestion = vault.suggest_filename(d.get("doc_type"), d.get("metadata"),
                                             d.get("cpv_codes"), d["filename"])
        d["suggested_filename"] = suggestion if suggestion != d["filename"] else None
    return docs


@app.get("/api/vault/docs")
def get_vault_docs(q: Optional[str] = None, tag: Optional[str] = None,
                    tenant_id: int = Depends(get_current_tenant_id)):
    results = store.list_vault_documents(_db(), tenant_id, q=q, tag=tag)
    _attach_expiry(results)
    _attach_suggested_filename(results)
    processing = sum(1 for d in results if d["status"] == "processing")
    return {"total": len(results), "processing": processing, "results": results}


@app.get("/api/vault/docs/{document_id}")
def get_vault_doc_detail(document_id: int, tenant_id: int = Depends(get_current_tenant_id)):
    doc = next((d for d in store.list_vault_documents(_db(), tenant_id) if d["id"] == document_id), None)
    if doc is None:
        raise HTTPException(404, "Document not found")
    _attach_expiry([doc])
    _attach_suggested_filename([doc])
    return doc


@app.delete("/api/vault/docs/{document_id}")
def delete_vault_doc(document_id: int, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    doc = store.get_vault_document(conn, tenant_id, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    store.delete_vault_document(conn, tenant_id, document_id)
    try:
        vault._chroma_collection(tenant_id).delete(where={"doc_id": document_id})
    except Exception:
        logging.exception(f"failed to drop Chroma chunks for vault doc {document_id}")
    if os.path.exists(doc["storage_path"]):
        os.remove(doc["storage_path"])
    return {"deleted": True}


class VaultMetadataValidationBody(BaseModel):
    document_id: int
    metadata: dict


@app.post("/api/vault/validate-metadata")
def validate_vault_metadata(body: VaultMetadataValidationBody,
                             tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    if store.get_vault_document(conn, tenant_id, body.document_id) is None:
        raise HTTPException(404, "Document not found")
    store.update_vault_document_metadata_fields(conn, tenant_id, body.document_id, body.metadata)
    return {"id": body.document_id, "metadata": body.metadata}


def _valid_until_by_doc_id(conn, tenant_id):
    """{doc_id: valid_until} for this tenant's whole Vault library — the
    input vault.rank_chunks_by_expiry needs; cheap (no join, one already-
    indexed query) and small enough to compute per-request rather than cache.
    """
    return {d["id"]: d["valid_until"] for d in store.list_vault_documents(conn, tenant_id)}


def _expiry_rank_vault_chunks(conn, tenant_id, chunks):
    """CR-007 Phase F: the same expired-doc down-ranking search_vault_endpoint
    applies inline, factored out so vault.search_vault's *other* callers
    (the refine merge, run_generate's Vault blending — see _run_composer_
    refine/_run_composer_generate) apply it too. Previously only the manual
    search panel did, which meant a refine could cite an expired Vault
    document with no down-ranking at all — a real gap against the CR's own
    E1 acceptance line ("down-rank expired documents in Composer evidence
    search"), since refine *is* Composer's evidence search.
    """
    if not chunks:
        return chunks
    return vault.rank_chunks_by_expiry(chunks, _valid_until_by_doc_id(conn, tenant_id))


@app.get("/api/vault/search")
def search_vault_endpoint(query: Optional[str] = None, cpv: Optional[str] = None,
                           material: Optional[str] = None, top_k: int = Query(8, ge=1, le=50),
                           tenant_id: int = Depends(get_current_tenant_id)):
    """CR-004 F3 — Composer's "Source materials" panel: search the Vault
    library by CPV code and/or material type, optionally ranked by semantic
    similarity to a free-text query. Without `query`, returns the matching
    documents themselves (highest-confidence first); with it, returns
    chunk-level hits (ranked, joined back to their parent doc's metadata).
    """
    conn = _db()
    candidates = store.find_vault_documents(conn, tenant_id, cpv=cpv, material=material)
    if not candidates:
        return {"results": []}
    # CR-007 Phase E (E1): down-rank, never exclude — an expired doc can
    # still surface (the CR's own "don't silently delete either" theme,
    # same as Phase C's duplicate handling), just sorted after every
    # non-expired candidate rather than purely by confidence/similarity.
    _attach_expiry(candidates)
    if not query:
        ranked = sorted(candidates, key=lambda d: (d["expired"], -(d["confidence"] or 0)))[:top_k]
        return {"results": [
            {"doc_id": d["id"], "filename": d["filename"], "metadata": d["metadata"],
             "cpv_codes": d["cpv_codes"], "confidence": d["confidence"], "text": None, "similarity": None,
             "expired": d["expired"], "expiring_soon": d["expiring_soon"]}
            for d in ranked
        ]}
    by_id = {d["id"]: d for d in candidates}
    chunks = vault.search_vault(tenant_id, list(by_id.keys()), query, top_k=top_k)
    results = []
    for c in chunks:
        d = by_id.get(c["doc_id"])
        if d is None:
            continue
        results.append({"doc_id": d["id"], "filename": d["filename"], "metadata": d["metadata"],
                         "cpv_codes": d["cpv_codes"], "confidence": d["confidence"],
                         "text": c["text"], "similarity": c["similarity"],
                         "expired": d["expired"], "expiring_soon": d["expiring_soon"]})
    # Re-sort within what Chroma already returned: non-expired first, then
    # by similarity — search_vault itself has no doc metadata to do this
    # sort with, so it happens here instead (see vault.search_vault's
    # docstring for the over-fetch that keeps this from starving evidence).
    results.sort(key=lambda r: (r["expired"], -(r["similarity"] or 0)))
    return {"results": results}


class VaultTagsBody(BaseModel):
    tags: list[str]

@app.patch("/api/vault/docs/{document_id}/tags")
def patch_vault_doc_tags(document_id: int, body: VaultTagsBody,
                          tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    if store.get_vault_document(conn, tenant_id, document_id) is None:
        raise HTTPException(404, "Document not found")
    store.set_vault_document_tags(conn, tenant_id, document_id, body.tags)
    return {"id": document_id, "tags": body.tags}


class VaultFilenameBody(BaseModel):
    filename: str

@app.patch("/api/vault/docs/{document_id}/filename")
def patch_vault_doc_filename(document_id: int, body: VaultFilenameBody,
                              tenant_id: int = Depends(get_current_tenant_id)):
    """CR-007 Phase E (E2): accept (or edit) the suggested filename — never
    applied automatically, always an explicit user action.
    """
    if not body.filename.strip():
        raise HTTPException(400, "filename cannot be empty")
    conn = _db()
    if store.get_vault_document(conn, tenant_id, document_id) is None:
        raise HTTPException(404, "Document not found")
    store.rename_vault_document(conn, tenant_id, document_id, body.filename.strip())
    return {"id": document_id, "filename": body.filename.strip()}


@app.get("/api/vault/tags")
def get_vault_tags(tenant_id: int = Depends(get_current_tenant_id)):
    return {"tags": store.list_vault_tags(_db(), tenant_id)}


class VaultRulesBody(BaseModel):
    hints: list[str]

@app.get("/api/vault/rules")
def get_vault_rules_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_vault_rules(_db(), tenant_id)

@app.put("/api/vault/rules")
def put_vault_rules_config(body: VaultRulesBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_vault_rules(_db(), tenant_id, body.hints)
    return {"saved": True}


class VaultSettingsBody(BaseModel):
    confidence_threshold: Optional[float] = None

@app.get("/api/vault/settings")
def get_vault_settings_config(tenant_id: int = Depends(get_current_tenant_id)):
    settings = store.get_vault_settings(_db(), tenant_id)
    return {**settings, "extraction_model": vault.CLAUDE_MODEL}

@app.put("/api/vault/settings")
def put_vault_settings_config(body: VaultSettingsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_vault_settings(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


# CR-007 Phase C (C1b): cross-portal dedup's configurable similarity
# threshold — signal 1 (buyer-reference match) has no threshold, only
# signal 2 (translated-description similarity) does.
class DedupSettingsBody(BaseModel):
    similarity_threshold: Optional[float] = None

@app.get("/api/scout/dedup-settings")
def get_dedup_settings_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_dedup_settings(_db(), tenant_id)

@app.put("/api/scout/dedup-settings")
def put_dedup_settings_config(body: DedupSettingsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_dedup_settings(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


# CR-007 Phase D (D2): the deadline-urgency window — formerly a hard exclude
# (filters.py's removed check_deadline_too_soon), now purely advisory. See
# _attach_relevance/_attach_duplicates above for the same settings-endpoint
# shape; "closing soon" itself is computed client-side from this value.
class ScoutSettingsBody(BaseModel):
    deadline_floor_hours: Optional[int] = None

@app.get("/api/scout/settings")
def get_scout_settings_config(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_scout_settings(_db(), tenant_id)

@app.put("/api/scout/settings")
def put_scout_settings_config(body: ScoutSettingsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_scout_settings(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


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


def _cpv_scoped_vault_doc_ids(conn, tenant_id, pub_number):
    """CR-007 Phase F: the tender's own CPV codes are the same relevance
    boundary the manual Vault search panel uses (search_vault_endpoint's
    `cpv` param) — reused here so the automated generate pass only pulls in
    Vault documents plausibly relevant to *this* tender, not the whole
    library. [] (not an error) when the tender can't be found or has no
    CPV codes — composer.run_generate treats that as "no Vault blending".
    """
    tender = next((r for r in store.all_records(conn, tenant_id) if r["pub_number"] == pub_number), None)
    if not tender:
        return []
    seen = {}
    for cpv in tender.get("cpv_codes") or []:
        for d in store.find_vault_documents(conn, tenant_id, cpv=cpv):
            seen[d["id"]] = True
    return list(seen.keys())


def _run_composer_generate(tenant_id, pub_number):
    conn = _db()
    requirements = store.list_composer_requirements(conn, tenant_id, pub_number)
    style_guide = store.get_style_guide(conn, tenant_id)["style_guide"]
    settings = store.get_composer_settings(conn, tenant_id)
    # CR-007 Phase F: blend the tenant's own Vault library into the
    # automated pass too (previously Vault only reached Composer through a
    # manual refine with explicitly attached documents) — CPV-scoped to
    # this tender, expiry down-ranked like every other Vault evidence path.
    vault_doc_ids = _cpv_scoped_vault_doc_ids(conn, tenant_id, pub_number)
    valid_until_by_doc_id = _valid_until_by_doc_id(conn, tenant_id) if vault_doc_ids else {}
    for r in composer.run_generate(tenant_id, pub_number, requirements, style_guide=style_guide,
                                    top_k=settings["top_k"], good_similarity=settings["good_similarity"],
                                    partial_similarity=settings["partial_similarity"],
                                    vault_doc_ids=vault_doc_ids,
                                    valid_until_by_doc_id=valid_until_by_doc_id):
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


def _run_composer_refine(tenant_id, pub_number, requirement_id, feedback, vault_document_ids=None):
    conn = _db()
    req = store.get_composer_requirement(conn, tenant_id, requirement_id)
    if req is None:
        return
    query = f"{req['title']} {req['extracted']} {feedback}"
    tech_chunks = composer.retrieve_evidence(tenant_id, pub_number, query, roles=["tech"])
    # CR-004 F3 — Composer -> Vault: the analyst drags specific Vault
    # documents (found via the Source materials panel / GET /api/vault/
    # search) into a regenerate; their chunks get merged into the evidence
    # pool ahead of the tender's own "tech" docs, and get their own citation
    # rows (prefixed so they're visibly distinct from tender-scoped sources).
    vault_chunks, extra_citations = [], []
    if vault_document_ids:
        vault_chunks = vault.search_vault(tenant_id, vault_document_ids, query, top_k=5)
        # CR-007 Phase E/F: down-rank an expired doc's chunks rather than
        # citing them with no lifecycle awareness at all (see
        # _expiry_rank_vault_chunks's docstring for why this was missing).
        _expiry_rank_vault_chunks(conn, tenant_id, vault_chunks)
        extra_citations = [{"doc": f"Vault: {c['source']}", "score": c["similarity"]} for c in vault_chunks]
    new_text = composer.refine_section(req["extracted"], req["response"] or "", feedback,
                                        vault_chunks + tech_chunks)
    store.update_composer_requirement_refined(conn, tenant_id, requirement_id, new_text, feedback,
                                               extra_citations=extra_citations)


class ComposerGenerateBody(BaseModel):
    requirement_id:     Optional[int]       = None
    feedback:           Optional[str]       = None
    vault_document_ids: Optional[list[int]] = None

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
        background.add_task(_run_composer_refine, tenant_id, pub_number, body.requirement_id,
                             body.feedback, body.vault_document_ids)
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
    _require_shortlisted_tender(_db(), tenant_id, pub_number)
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
    _require_shortlisted_tender(_db(), tenant_id, pub_number)
    path = _composer_output_path(tenant_id, pub_number, "gaps_report.txt")
    if not path.exists():
        raise HTTPException(404, "No gaps report generated yet")
    return FileResponse(str(path), filename="gaps_report.txt", media_type="text/plain")


# ── Composer Settings + Style Guide — tenant-wide, not tender-scoped ────────

class ComposerSettingsBody(BaseModel):
    good_similarity:    Optional[float] = None
    partial_similarity: Optional[float] = None
    top_k:              Optional[int]   = None

@app.get("/api/composer/settings")
def get_composer_settings_config(tenant_id: int = Depends(get_current_tenant_id)):
    settings = store.get_composer_settings(_db(), tenant_id)
    return {**settings, "model": composer.CLAUDE_MODEL}

@app.put("/api/composer/settings")
def put_composer_settings_config(body: ComposerSettingsBody, tenant_id: int = Depends(get_current_tenant_id)):
    store.set_composer_settings(_db(), tenant_id, body.model_dump(exclude_none=True))
    return {"saved": True}


@app.get("/api/composer/style")
def get_composer_style(tenant_id: int = Depends(get_current_tenant_id)):
    return store.get_style_guide(_db(), tenant_id)


class ComposerStyleBody(BaseModel):
    style_guide: str

@app.put("/api/composer/style")
def put_composer_style(body: ComposerStyleBody, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    current = store.get_style_guide(conn, tenant_id)
    store.set_style_guide(conn, tenant_id, body.style_guide, current["source_doc_count"],
                           datetime.now(timezone.utc).isoformat())
    return store.get_style_guide(conn, tenant_id)


STYLE_UPLOAD_DIR = ROOT / "data" / "composer_style_uploads"


@app.post("/api/composer/style/examples")
async def upload_style_example(file: UploadFile = File(...),
                                 tenant_id: int = Depends(get_current_tenant_id)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large")

    tenant_dir = STYLE_UPLOAD_DIR / str(tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    ext = pathlib.Path(file.filename or "").suffix[:10]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = tenant_dir / stored_name
    storage_path.write_bytes(content)

    extracted_text = vault.parse_document(str(storage_path), file.content_type) or ""
    doc_id = store.add_style_example(_db(), tenant_id, file.filename or stored_name,
                                      file.content_type, len(content), str(storage_path), extracted_text)
    return {"id": doc_id, "filename": file.filename or stored_name}


@app.get("/api/composer/style/examples")
def list_style_examples(tenant_id: int = Depends(get_current_tenant_id)):
    return {"results": store.list_style_examples(_db(), tenant_id)}


@app.delete("/api/composer/style/examples/{example_id}")
def delete_style_example(example_id: int, tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    example = store.get_style_example(conn, tenant_id, example_id)
    if example is None:
        raise HTTPException(404, "Example not found")
    store.delete_style_example(conn, tenant_id, example_id)
    if os.path.exists(example["storage_path"]):
        os.remove(example["storage_path"])
    return {"deleted": True}


@app.post("/api/composer/style/extract")
def extract_composer_style(tenant_id: int = Depends(get_current_tenant_id)):
    conn = _db()
    texts = store.get_style_example_texts(conn, tenant_id)
    if not texts:
        raise HTTPException(409, "Upload at least one example proposal before extracting a style guide")
    guide = composer.extract_style_guide(texts)
    if guide is None:
        raise HTTPException(409, "ANTHROPIC_API_KEY is not configured")
    store.set_style_guide(conn, tenant_id, guide, len(texts), datetime.now(timezone.utc).isoformat())
    return store.get_style_guide(conn, tenant_id)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
