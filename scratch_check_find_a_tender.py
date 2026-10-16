"""CR-012 follow-up: Find a Tender's connector-level CPV pre-filter reported
2 new records pulled this run, but none appear in the Review Queue. Read-only
inspection of those stored records' match_source/exclude_reason/status to
see why, without guessing.

Run on Railway:
    Get-Content scratch_check_find_a_tender.py -Raw | railway ssh -- python -
"""
import sys

sys.path.insert(0, "src")

import store

conn = store.init_db("data/tenders.db")
for tenant_id in store.list_provisioned_tenant_ids(conn):
    records = [r for r in store.all_records(conn, tenant_id) if r.get("source") == "Find a Tender"]
    if not records:
        continue
    print(f"tenant {tenant_id}: {len(records)} Find a Tender record(s)")
    for r in records:
        print(f"  pub_number={r.get('pub_number')!r}")
        print(f"    tag_line={r.get('tag_line')!r}")
        print(f"    cpv_codes={r.get('cpv_codes')!r}")
        print(f"    matched_terms={r.get('matched_terms')!r}")
        print(f"    match_source={r.get('match_source')!r}")
        print(f"    exclude_reason={r.get('exclude_reason')!r}")
        print(f"    notice_type={r.get('notice_type')!r}")
        print(f"    status={r.get('status')!r}")
        print(f"    deadline={r.get('deadline')!r}")
