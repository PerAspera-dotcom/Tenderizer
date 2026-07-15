"""One-off backfill: compute classification.classify() + extract_award_info()
against every already-stored record, after the CR-002 notice_type/award-field
columns shipped. store.upsert() is insert-only (never rewrites a record
another run already tagged — see store.update_tagging's docstring), so every
row ingested *before* this feature existed was left at notice_type's column
default ("tender") regardless of what it should actually classify as (e.g. a
past_tender-shaped row with an empty deadline, silently never routed to the
Past Tenders page and still sitting in the Review Queue's candidate pool).

Unlike scratch_backfill_boamp_cpv.py, this needs no re-fetch: tag_line/
description/deadline are already correct in the DB, only the classification
*logic* is new. Runs over every tenant.

Run from the project root:  python scratch_backfill_notice_type.py
"""
import sys
from collections import Counter

sys.path.insert(0, "src")

import classification
import store
from schema import tenders


def backfill_tenant(conn, tenant_id):
    records = store.all_records(conn, tenant_id)
    if not records:
        return None

    stats = Counter()
    for rec in records:
        new_type = classification.classify(rec)
        if new_type == "past_tender":
            awarded_to, awarded_value, awarded_currency = classification.extract_award_info(rec)
        else:
            awarded_to, awarded_value, awarded_currency = None, None, None

        changed = (new_type != (rec.get("notice_type") or "tender")
                   or awarded_to != rec.get("awarded_to")
                   or awarded_value != rec.get("awarded_value")
                   or awarded_currency != rec.get("awarded_currency"))
        if changed:
            store.update_classification(conn, tenant_id, rec["pub_number"],
                                         new_type, awarded_to, awarded_value, awarded_currency)
            stats["updated"] += 1
            stats[f"reclassified_as_{new_type}"] += 1
        else:
            stats["unchanged"] += 1
    stats["total"] = len(records)
    return stats


def main():
    conn = store.init_db("data/tenders.db")
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    for tenant_id in tenant_ids:
        stats = backfill_tenant(conn, tenant_id)
        if stats is None:
            continue
        print(f"tenant {tenant_id}: {dict(stats)}")


if __name__ == "__main__":
    main()
