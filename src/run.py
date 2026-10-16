"""Step 11 — end-to-end pipeline / scheduler entrypoint.

run_pipeline(sources, db_path, out_path) -> health(dict)
  Each source = {"name": str, "fetch": callable->list[raw], "normalize": callable(raw)->record}.
  Runs every source, tags each record (matched terms + match source), dedups into SQLite,
  and writes the combined report. A failing source is captured in `health`, never fatal.
  Idempotent: re-running over the same data adds no duplicates (hash dedup in store).

Run directly (uses the real TED + BOAMP sources):  python src/run.py
Schedule it with Windows Task Scheduler; set "Start in" to the project root so the
data/ and reports/ paths resolve.
"""
import config
import store
import match
import normalize
import filters
import classification
import currency
import dedup
import translate
import alerts
import screening
from schema import DEFAULT_TENANT_ID
from report import build_report
import json, os
from datetime import datetime, timezone

SOURCE_FAILURE_ALERT_THRESHOLD = int(os.getenv("SOURCE_FAILURE_ALERT_THRESHOLD", "3"))


def _tag(rec, full_keywords, cpv_set):
    """Annotate a record with which keywords fired and how it qualified."""
    text = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
    hits = match.match_keywords(text, full_keywords)
    has_cpv = bool(set(rec.get("cpv_codes") or []) & cpv_set)
    rec["matched_terms"] = hits
    rec["match_source"] = match.classify_match(has_cpv, hits)
    return rec


def process_record(conn, tenant_id, rec, full_keywords, cpv_set, exclusions, now, fx_rates, run_date):
    """The per-record tag -> expiry-check -> FX -> filter -> classify -> store
    sequence every connector's fetch loop runs below — factored out so
    CR-012 §3's email-alert webhook (api._ingest_email_alert_record) can
    feed one already-normalized record through the exact same pipeline the
    instant it arrives, rather than waiting for the next scheduled
    run_pipeline cycle. Returns True if the record was stored, False if its
    deadline had already passed (skipped, same as the inline check below
    used to do) — the caller doesn't need the record itself back, every
    caller already has it.
    """
    rec = _tag(rec, full_keywords, cpv_set)
    dl = (rec.get("deadline") or "")[:10]
    if dl and dl < run_date:
        return False  # expired deadline — skip ingest
    rec["value_eur"], rec["fx_rate_date"] = currency.to_eur(
        rec.get("value"), rec.get("value_currency"), fx_rates)
    rec["exclude_reason"] = filters.apply_filters(rec, exclusions, now) or ""
    # CR-002 A: additive, post-filter — tags what's already kept (or
    # excluded), never changes it.
    rec["notice_type"] = classification.classify(rec)
    rec["awarded_to"], rec["awarded_value"], rec["awarded_currency"], rec["award_detail"] = (
        classification.extract_award_info(rec)
        if rec["notice_type"] == "past_tender" else (None, None, None, None)
    )
    store.upsert(conn, tenant_id, rec)
    return True


def _maybe_alert_consecutive_failures(conn, tenant_id, source):
    """CR-004 F4: escalate (Sentry + email, both inert if unconfigured) once
    a source hits SOURCE_FAILURE_ALERT_THRESHOLD consecutive failures — but
    never pauses the source automatically, that stays an analyst decision.
    """
    summary = store.get_source_health(conn, tenant_id, source)
    if summary["consecutive_failures"] >= SOURCE_FAILURE_ALERT_THRESHOLD:
        alerts.send_alert(
            f"Tenderizer: {source} has failed {summary['consecutive_failures']} runs in a row",
            f"tenant_id={tenant_id} source={source} last_failure={summary['last_failure']}")


def run_pipeline(sources, db_path, out_path, tenant_id=DEFAULT_TENANT_ID, now=None, fx_rates=None):
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, tenant_id)  # make sure this tenant (and its default config) exists
    tenant_kw = store.get_tenant_keywords(conn, tenant_id)
    full_keywords = [w for lang in tenant_kw["terms"].values() for w in lang]
    cpv_set = set(store.get_tenant_cpv(conn, tenant_id))
    # config.exclusions() stays global (not a per-tenant config per the build
    # doc) — only distinctive keywords are tenant-scoped, injected here so
    # filters.check_no_core_signal doesn't need its own tenant_id/conn.
    exclusions = dict(config.exclusions())
    exclusions["_distinctive_keywords"] = tenant_kw["distinctive"]
    now = now or datetime.now(timezone.utc)  # one snapshot for the whole run
    # ECB daily rates (D2) — fetch once per run, never per tender. `fx_rates` is
    # injectable for tests; production (no arg) does one live fetch here.
    fx_rates = fx_rates or currency.fetch_ecb_rates_or_fallback()
    health = {}
    # Only this run's own fetches — kept separate from store.all_records()'s
    # cumulative, all-time set so _write_last_run's matched_total stays paired
    # with notices_scanned at the same scope (both "this run"), not one per-run
    # and one all-time (previously: matched_total silently drifted further from
    # notices_scanned every run as the tenant's tenders table grew).
    this_run_records = []

    run_date = now.date().isoformat()
    for src in sources:
        name = src["name"]
        try:
            raws = src["fetch"]()
            normalize_fn = src["normalize"]
            for raw in raws:
                rec = normalize_fn(raw)
                if process_record(conn, tenant_id, rec, full_keywords, cpv_set, exclusions, now, fx_rates, run_date):
                    this_run_records.append(rec)
            health[name] = f"ok ({len(raws)})"
            store.record_source_health(conn, tenant_id, name, run_date, "ok",
                                        notices_pulled=len(raws))
        except Exception as e:                       # one source failing must not abort the run
            health[name] = f"error: {e}"
            store.record_source_health(conn, tenant_id, name, run_date, "failed",
                                        error_detail=str(e))
            _maybe_alert_consecutive_failures(conn, tenant_id, name)

    # CR-001 D-DUP: cross-record pass, so it runs once here over everything
    # ingested so far — not per-record like the filters above.
    # CR-008 P0 (simplified 2026-08-26, client direction): never auto-merge
    # a same-source match — every candidate in a group is surfaced as a
    # same-source possible-duplicate link instead (the CR-007 Phase C
    # "surface, never hide" pattern cross-portal dedup below already uses),
    # so a reviewer sees and decides rather than a tender silently vanishing.
    # This replaces the earlier guarded-auto-supersede logic (dedup.
    # is_protected/kept_order_is_confident, retired) — a plain label beats
    # gating who's safe to auto-merge.
    for group in dedup.find_duplicate_groups(store.all_records(conn, tenant_id)):
        kept, *candidates = group
        for r in candidates:
            store.upsert_tender_duplicate(conn, tenant_id, kept["pub_number"],
                                           r["pub_number"], "same_source")

    # CR-001 R3 (D1 — DeepL, Free tier): translate non-English SURFACED tenders
    # only — after dedup, so a just-superseded record never spends DeepL quota.
    # 'ok' records are skipped (already done); 'unavailable' ones are retried
    # in case DeepL is back up. translate_cached() itself dedupes by content
    # hash (and is deliberately NOT tenant-scoped — see schema.py), so identical
    # text across notices, or across tenants, is never sent twice either.
    translated_this_run = 0
    for r in store.all_records(conn, tenant_id):
        if r.get("exclude_reason") or not r.get("language") or r["language"] == "eng":
            continue
        if r.get("translation_status") == "ok":
            continue
        tag_en, tag_status = translate.translate_cached(conn, r.get("tag_line"))
        desc_en, desc_status = translate.translate_cached(conn, r.get("description"))
        status = "ok" if tag_status == "ok" and desc_status == "ok" else "unavailable"
        store.set_translation(conn, tenant_id, r["pub_number"], tag_en or "", desc_en or "", status)
        if status == "ok":
            translated_this_run += 1

    # CR-011 §1: DeepL's own health, independent of whether any record above
    # actually needed translating today — a lightweight ping (see
    # translate.check_health's docstring on why it's safe to call every run),
    # recorded through the same source_health table/get_source_health shape
    # TED/BOAMP/etc. already use, so /api/health surfaces it the same way
    # with no new storage or API shape needed.
    deepl_status, deepl_detail = translate.check_health()
    store.record_source_health(
        conn, tenant_id, "DeepL", run_date,
        "ok" if deepl_status == "ok" else "failed",
        notices_pulled=translated_this_run if deepl_status == "ok" else None,
        error_detail=None if deepl_status == "ok" else (deepl_detail or deepl_status))

    # CR-007 Phase C: cross-portal duplicate detection (e.g. the same tender
    # on TED and BOAMP) — runs after translation, unlike the same-source pass
    # above, since signal 2 needs description_en. Never hides/supersedes
    # anything (see dedup.find_cross_portal_duplicates' docstring) — every
    # detected pair is just recorded for the API to surface on both records.
    dedup_settings = store.get_dedup_settings(conn, tenant_id)
    for pub_a, pub_b, match_type, similarity in dedup.find_cross_portal_duplicates(
            store.all_records(conn, tenant_id), dedup_settings["similarity_threshold"]):
        store.upsert_tender_duplicate(conn, tenant_id, pub_a, pub_b, match_type, similarity)

    # CR-011 §3: screening questions — after translation (same requirement
    # as cross-portal dedup above: operates on tag_line_en/description_en).
    # Backfills whatever's MISSING for every surfaced tender, not just this
    # run's own fetches — the same mechanism that answers a brand-new
    # tender's questions also answers an already-ingested tender's newly-
    # added/edited question, with no separate backfill script needed (an
    # edited question's stale answers were already deleted by store.
    # update_screening_question, making them "missing" again here).
    questions = store.get_tenant_screening_questions(conn, tenant_id, enabled_only=True)
    if questions:
        question_ids = [q["id"] for q in questions]
        by_id = {q["id"]: q for q in questions}
        for r in store.all_records(conn, tenant_id):
            if r.get("exclude_reason"):
                continue
            missing_ids = store.missing_screening_question_ids(conn, tenant_id, r["pub_number"], question_ids)
            if not missing_ids:
                continue
            answers = screening.answer_questions(r, [by_id[q_id] for q_id in missing_ids])
            for q_id, (answer_text, snippet, status) in answers.items():
                store.set_screening_answer(conn, tenant_id, r["pub_number"], q_id, answer_text, snippet, status)

    records = store.all_records(conn, tenant_id)
    surfaced = [r for r in records if not r.get("exclude_reason")]
    build_report(surfaced, health, out_path)
    _write_last_run(db_path, tenant_id, health, this_run_records)
    return health


def _write_last_run(db_path, tenant_id, health, this_run_records):
    """CR-011 §5: found while verifying a fresh empty tenant's dashboard
    stays isolated — this used to be one shared data/last_run.json
    regardless of which tenant's run wrote it, so EVERY tenant's Dashboard
    "last sync / notices scanned / matched" row showed whichever tenant's
    pipeline happened to run most recently, not their own. The exact same
    bug class api._report_path's own docstring already documents being
    fixed for reports/tenders.xlsx ("every tenant's /api/run overwrote the
    same ... regardless of caller") — this file just never got the same
    treatment. Per-tenant filename now, same convention.
    """
    notices_scanned = 0
    for v in health.values():
        if v.startswith("ok ("):
            try:
                notices_scanned += int(v[4:-1])
            except ValueError:
                pass
    # Scoped to this_run_records (this run's own fetches), matching
    # notices_scanned's scope — NOT store.all_records()'s cumulative, all-time
    # set (see run_pipeline's this_run_records comment for why that was a bug).
    matched_total = sum(1 for r in this_run_records if r.get("match_source") not in (None, "None", ""))
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "health": health,
        "notices_scanned": notices_scanned,
        "matched_total": matched_total,
    }
    data_dir = os.path.dirname(os.path.abspath(db_path))
    with open(os.path.join(data_dir, f"last_run_{tenant_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)


def _default_sources(conn, tenant_id, since):
    """The TED + BOAMP + e-Procurement + Bekanntmachungsservice + (CR-011 §4)
    Find a Tender + Contracts Finder + CanadaBuys + TenderNed + PhilGEPS
    sources for a scheduled run, scoped to this tenant's CPV/keyword config
    and filtered to their enabled portals (step 5). All five CR-011 §4
    sources ship `enabled: false` (same "clean on the law, gated on a manual
    business go-live decision" shape e-Procurement/Bekanntmachungsservice set
    in CR-010 §4) — present in the list so any of them runs the moment a
    tenant/portal config enables it, same as any other source.
    """
    from connectors import (
        ted, boamp, eprocurement, bekanntmachungsservice,
        find_a_tender, contracts_finder, canadabuys, tenderned, philgeps,
    )
    cpv_codes = store.get_tenant_cpv(conn, tenant_id)
    distinctive = store.get_tenant_keywords(conn, tenant_id)["distinctive"]
    enabled = store.get_enabled_portal_names(conn, tenant_id)
    sources = [
        {"name": "TED",
         "fetch": lambda: ted.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_ted},
        {"name": "BOAMP",
         "fetch": lambda: boamp.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_boamp},
        {"name": "e-Procurement",
         "fetch": lambda: eprocurement.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_eprocurement},
        {"name": "Bekanntmachungsservice",
         "fetch": lambda: bekanntmachungsservice.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_bekanntmachungsservice},
        {"name": "Find a Tender",
         "fetch": lambda: find_a_tender.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_find_a_tender},
        {"name": "Contracts Finder",
         "fetch": lambda: contracts_finder.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_contracts_finder},
        {"name": "CanadaBuys",
         "fetch": lambda: canadabuys.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_canadabuys},
        {"name": "TenderNed",
         "fetch": lambda: tenderned.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_tenderned},
        {"name": "PhilGEPS",
         "fetch": lambda: philgeps.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_philgeps},
    ]
    return [s for s in sources if s["name"] in enabled]


if __name__ == "__main__":
    import os
    from datetime import date, timedelta
    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)
    since = date.today() - timedelta(days=30)
    conn = store.init_db("data/tenders.db")
    store.ensure_tenant(conn, DEFAULT_TENANT_ID)
    sources = _default_sources(conn, DEFAULT_TENANT_ID, since)
    health = run_pipeline(sources, "data/tenders.db", "reports/tenders.xlsx")
    print("run complete:")
    for name, status in health.items():
        print(f"  {name}: {status}")
