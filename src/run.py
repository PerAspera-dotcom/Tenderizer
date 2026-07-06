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
import currency
import dedup
import translate
from schema import DEFAULT_TENANT_ID
from report import build_report
import json, os
from datetime import datetime, date, timezone


def _tag(rec, full_keywords, cpv_set):
    """Annotate a record with which keywords fired and how it qualified."""
    text = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
    hits = match.match_keywords(text, full_keywords)
    has_cpv = bool(set(rec.get("cpv_codes") or []) & cpv_set)
    rec["matched_terms"] = hits
    rec["match_source"] = match.classify_match(has_cpv, hits)
    return rec


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

    for src in sources:
        name = src["name"]
        try:
            raws = src["fetch"]()
            normalize_fn = src["normalize"]
            for raw in raws:
                rec = _tag(normalize_fn(raw), full_keywords, cpv_set)
                dl = (rec.get("deadline") or "")[:10]
                if dl and dl < date.today().isoformat():
                    continue  # expired deadline — skip ingest
                rec["value_eur"], rec["fx_rate_date"] = currency.to_eur(
                    rec.get("value"), rec.get("value_currency"), fx_rates)
                rec["exclude_reason"] = filters.apply_filters(rec, exclusions, now) or ""
                store.upsert(conn, tenant_id, rec)
                this_run_records.append(rec)
            health[name] = f"ok ({len(raws)})"
        except Exception as e:                       # one source failing must not abort the run
            health[name] = f"error: {e}"

    # CR-001 D-DUP: cross-record pass, so it runs once here over everything
    # ingested so far — not per-record like the filters above.
    for group in dedup.find_duplicate_groups(store.all_records(conn, tenant_id)):
        kept, *superseded = group
        store.mark_superseded(conn, tenant_id, kept["pub_number"], superseded)

    # CR-001 R3 (D1 — DeepL, Free tier): translate non-English SURFACED tenders
    # only — after dedup, so a just-superseded record never spends DeepL quota.
    # 'ok' records are skipped (already done); 'unavailable' ones are retried
    # in case DeepL is back up. translate_cached() itself dedupes by content
    # hash (and is deliberately NOT tenant-scoped — see schema.py), so identical
    # text across notices, or across tenants, is never sent twice either.
    for r in store.all_records(conn, tenant_id):
        if r.get("exclude_reason") or not r.get("language") or r["language"] == "eng":
            continue
        if r.get("translation_status") == "ok":
            continue
        tag_en, tag_status = translate.translate_cached(conn, r.get("tag_line"))
        desc_en, desc_status = translate.translate_cached(conn, r.get("description"))
        status = "ok" if tag_status == "ok" and desc_status == "ok" else "unavailable"
        store.set_translation(conn, tenant_id, r["pub_number"], tag_en or "", desc_en or "", status)

    records = store.all_records(conn, tenant_id)
    surfaced = [r for r in records if not r.get("exclude_reason")]
    build_report(surfaced, health, out_path)
    _write_last_run(db_path, health, this_run_records)
    return health


def _write_last_run(db_path, health, this_run_records):
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
    with open(os.path.join(data_dir, "last_run.json"), "w") as f:
        json.dump(meta, f, indent=2)


def _default_sources(conn, tenant_id, since):
    """The TED + BOAMP sources for a scheduled run, scoped to this tenant's
    CPV/keyword config and filtered to their enabled portals (step 5).
    """
    from connectors import ted, boamp
    cpv_codes = store.get_tenant_cpv(conn, tenant_id)
    distinctive = store.get_tenant_keywords(conn, tenant_id)["distinctive"]
    enabled = store.get_enabled_portal_names(conn, tenant_id)
    sources = [
        {"name": "TED",
         "fetch": lambda: ted.fetch(cpv_codes, distinctive, since),
         "normalize": normalize.normalize_ted},
        {"name": "BOAMP",
         "fetch": lambda: boamp.fetch(distinctive, cpv_codes, since),
         "normalize": normalize.normalize_boamp},
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
