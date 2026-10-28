"""One-off (2026-10-28): PhilGEPS got its own €50k value floor (was the global
€200k — see config/exclusions.yaml value_floor.by_source). Stored PhilGEPS rows
excluded as below_value_floor under the old floor stay excluded until re-filtered,
since store.upsert never updates an existing row. This re-runs
filters.apply_filters on exactly those rows, so one now worth €50k+ either
surfaces or picks up whichever later check (construction_works,
no_core_signal) it would have hit.

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_refilter_philgeps_floor.py -Raw | railway ssh -- python - --apply
"""
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "src")

import config
import filters
import store

APPLY = "--apply" in sys.argv


def main():
    conn = store.db.get_engine(store.db.configured_url())
    now = datetime.now(timezone.utc)
    stats = Counter()
    for tenant_id in store.list_provisioned_tenant_ids(conn):
        exclusions = dict(config.exclusions())
        exclusions["_distinctive_keywords"] = store.get_tenant_keywords(conn, tenant_id)["distinctive"]
        for rec in store.all_records(conn, tenant_id):
            if rec.get("source") != "PhilGEPS" or rec.get("exclude_reason") != "below_value_floor":
                continue
            new_reason = filters.apply_filters(rec, exclusions, now) or ""
            stats["checked"] += 1
            if new_reason == "below_value_floor":
                continue
            stats[f"now_{new_reason or 'kept'}"] += 1
            print(f"  {tenant_id} {rec['pub_number']}: EUR {float(rec['value_eur']):,.0f} -> "
                  f"{new_reason or 'kept'} | {(rec.get('tag_line') or '')[:50]}")
            if APPLY:
                store.update_tagging(conn, tenant_id, rec["pub_number"],
                                     cpv_codes=rec.get("cpv_codes") or [],
                                     matched_terms=rec.get("matched_terms") or [],
                                     match_source=rec.get("match_source") or "",
                                     exclude_reason=new_reason)
    print(dict(stats))
    if not APPLY:
        print("dry run — pass --apply to write")


if __name__ == "__main__":
    main()
