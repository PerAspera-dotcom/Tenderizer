"""One-off backfill: re-derive cpv_codes (and downstream match_source/
matched_terms/exclude_reason) for already-stored BOAMP records, now that
normalize._boamp_cpv_codes actually extracts CPV from the `donnees` field
(2026-07 fix — see src/normalize.py).

upsert() is insert-only, so existing rows never picked up the fix on their
own; this re-fetches each stored BOAMP notice by idweb (raw payloads were
never cached — normalize.py discards `donnees` after extraction) and
re-scores it with store.update_tagging().

Run from the project root:  python scratch_backfill_boamp_cpv.py
"""
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "src")

import config
import filters
import match
import store
from connectors import boamp
from normalize import normalize_boamp
from schema import tenders

DB_PATH = "data/tenders.db"
BATCH = 50


def _refetch_raw_by_idweb(idwebs):
    """BOAMP's ODSQL supports `idweb in (...)` for exact batch lookups —
    verified live (2026-07) — cheaper and more precise than re-querying by
    date range and hoping every stored notice is still in the window.
    """
    raw_by_idweb = {}
    idwebs = sorted(idwebs)
    for i in range(0, len(idwebs), BATCH):
        chunk = idwebs[i:i + BATCH]
        where = "idweb in (" + ", ".join(f'"{w}"' for w in chunk) + ")"
        import requests
        resp = requests.get(boamp.ENDPOINT, params={"where": where, "limit": BATCH}, timeout=60)
        resp.raise_for_status()
        for raw in boamp.parse_response(resp.json()):
            raw_by_idweb[raw.get("idweb")] = raw
    return raw_by_idweb


def backfill_tenant(conn, tenant_id, now):
    records = store.all_records(conn, tenant_id)
    boamp_records = [r for r in records if r.get("source") == "BOAMP"]
    if not boamp_records:
        return None

    tenant_kw = store.get_tenant_keywords(conn, tenant_id)
    full_keywords = [w for lang in tenant_kw["terms"].values() for w in lang]
    cpv_set = set(store.get_tenant_cpv(conn, tenant_id))
    exclusions = dict(config.exclusions())
    exclusions["_distinctive_keywords"] = tenant_kw["distinctive"]

    raw_by_idweb = _refetch_raw_by_idweb([r["pub_number"] for r in boamp_records])

    stats = Counter()
    for rec in boamp_records:
        raw = raw_by_idweb.get(rec["pub_number"])
        if raw is None:
            stats["not_found_on_live_boamp"] += 1  # e.g. withdrawn/archived since first scraped
            continue

        new_cpv = normalize_boamp(raw)["cpv_codes"]
        text = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
        hits = match.match_keywords(text, full_keywords)
        has_cpv = bool(set(new_cpv) & cpv_set)
        new_match_source = match.classify_match(has_cpv, hits)

        scored = dict(rec, cpv_codes=new_cpv, matched_terms=hits, match_source=new_match_source)
        new_exclude_reason = filters.apply_filters(scored, exclusions, now) or ""

        changed = (new_cpv != rec.get("cpv_codes")
                   or new_match_source != rec.get("match_source")
                   or new_exclude_reason != (rec.get("exclude_reason") or ""))
        if changed:
            store.update_tagging(conn, tenant_id, rec["pub_number"],
                                  cpv_codes=new_cpv, matched_terms=hits,
                                  match_source=new_match_source, exclude_reason=new_exclude_reason)
            stats["updated"] += 1
            if new_cpv:
                stats["gained_cpv_codes"] += 1
            if new_exclude_reason and not rec.get("exclude_reason"):
                stats["newly_excluded"] += 1
        else:
            stats["unchanged"] += 1
    stats["total_boamp"] = len(boamp_records)
    return stats


def main():
    conn = store.init_db(DB_PATH)
    now = datetime.now(timezone.utc)
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    for tenant_id in tenant_ids:
        stats = backfill_tenant(conn, tenant_id, now)
        if stats is None:
            continue
        print(f"tenant {tenant_id}: {dict(stats)}")


if __name__ == "__main__":
    main()
