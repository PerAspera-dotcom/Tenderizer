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
from report import build_report
import json, os
from datetime import datetime, date


def _tag(rec, full_keywords, cpv_set):
    """Annotate a record with which keywords fired and how it qualified."""
    text = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
    hits = match.match_keywords(text, full_keywords)
    has_cpv = bool(set(rec.get("cpv_codes") or []) & cpv_set)
    rec["matched_terms"] = hits
    rec["match_source"] = match.classify_match(has_cpv, hits)
    return rec


def run_pipeline(sources, db_path, out_path):
    conn = store.init_db(db_path)
    full_keywords = config.keywords()
    cpv_set = set(config.cpv_codes())
    exclusions = config.exclusions()
    health = {}

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
                rec["exclude_reason"] = filters.apply_filters(rec, exclusions) or ""
                store.upsert(conn, rec)
            health[name] = f"ok ({len(raws)})"
        except Exception as e:                       # one source failing must not abort the run
            health[name] = f"error: {e}"

    records = store.all_records(conn)
    surfaced = [r for r in records if not r.get("exclude_reason")]
    build_report(surfaced, health, out_path)
    _write_last_run(db_path, health, records)
    return health


def _write_last_run(db_path, health, records):
    notices_scanned = 0
    for v in health.values():
        if v.startswith("ok ("):
            try:
                notices_scanned += int(v[4:-1])
            except ValueError:
                pass
    matched_total = sum(1 for r in records if r.get("match_source") not in (None, "None", ""))
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "health": health,
        "notices_scanned": notices_scanned,
        "matched_total": matched_total,
    }
    data_dir = os.path.dirname(os.path.abspath(db_path))
    with open(os.path.join(data_dir, "last_run.json"), "w") as f:
        json.dump(meta, f, indent=2)


def _default_sources(since):
    """The real TED + BOAMP sources for a scheduled run."""
    from connectors import ted, boamp
    return [
        {"name": "TED",
         "fetch": lambda: ted.fetch(config.cpv_codes(), config.distinctive_keywords(), since),
         "normalize": normalize.normalize_ted},
        {"name": "BOAMP",
         "fetch": lambda: boamp.fetch(config.distinctive_keywords(), config.cpv_codes(), since),
         "normalize": normalize.normalize_boamp},
    ]


if __name__ == "__main__":
    import os
    from datetime import date, timedelta
    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)
    since = date.today() - timedelta(days=30)
    health = run_pipeline(_default_sources(since), "data/tenders.db", "reports/tenders.xlsx")
    print("run complete:")
    for name, status in health.items():
        print(f"  {name}: {status}")
