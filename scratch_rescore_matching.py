"""One-off re-score: re-run match.match_keywords/classify_match + filters.apply_filters
against every already-stored record's existing fields, after a match.py/filters.py fix
that changes matching behavior without changing any source data (e.g. the 2026-07 French
elision fold — "location d'engins" now matches "location de").

Unlike scratch_backfill_boamp_cpv.py, this needs no re-fetch: cpv_codes/tag_line/
description are already correct in the DB, only the matching *logic* changed. Runs over
every tenant and source (the elision fold is generic, not BOAMP/rental-specific).

Run from the project root:  python scratch_rescore_matching.py
"""
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "src")

import config
import filters
import match
import store
from schema import tenders


def rescore_tenant(conn, tenant_id, now):
    records = store.all_records(conn, tenant_id)
    if not records:
        return None

    tenant_kw = store.get_tenant_keywords(conn, tenant_id)
    full_keywords = [w for lang in tenant_kw["terms"].values() for w in lang]
    cpv_set = set(store.get_tenant_cpv(conn, tenant_id))
    exclusions = dict(config.exclusions())
    exclusions["_distinctive_keywords"] = tenant_kw["distinctive"]

    stats = Counter()
    for rec in records:
        text = f"{rec.get('tag_line', '')} {rec.get('description', '')}"
        hits = match.match_keywords(text, full_keywords)
        has_cpv = bool(set(rec.get("cpv_codes") or []) & cpv_set)
        new_match_source = match.classify_match(has_cpv, hits)

        scored = dict(rec, matched_terms=hits, match_source=new_match_source)
        new_exclude_reason = filters.apply_filters(scored, exclusions, now) or ""

        changed = (hits != (rec.get("matched_terms") or [])
                   or new_match_source != rec.get("match_source")
                   or new_exclude_reason != (rec.get("exclude_reason") or ""))
        if changed:
            store.update_tagging(conn, tenant_id, rec["pub_number"],
                                  cpv_codes=rec.get("cpv_codes") or [], matched_terms=hits,
                                  match_source=new_match_source, exclude_reason=new_exclude_reason)
            stats["updated"] += 1
            if new_exclude_reason and not rec.get("exclude_reason"):
                stats["newly_excluded"] += 1
            if new_exclude_reason:
                stats[f"newly_excluded_as_{new_exclude_reason}"] += 1
        else:
            stats["unchanged"] += 1
    stats["total"] = len(records)
    return stats


def main():
    conn = store.init_db("data/tenders.db")
    now = datetime.now(timezone.utc)
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    for tenant_id in tenant_ids:
        stats = rescore_tenant(conn, tenant_id, now)
        if stats is None:
            continue
        print(f"tenant {tenant_id}: {dict(stats)}")


if __name__ == "__main__":
    main()
