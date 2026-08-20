"""CR-008 P0 one-off recovery: reports (and optionally restores) tenders that
were auto-superseded by the old, unguarded dedup pass — the reported
incident being tender 563438-2026 (Lithuania, camp beds), which vanished
from the Review Queue after an auto-sync collided it with a near-duplicate.

The pipeline fix (dedup.is_protected + run.py's guarded supersede loop)
stops this going forward; this script is only for cleaning up whatever the
old, unguarded code already did to existing data.

Default mode is REPORT ONLY — lists every superseded tender per tenant,
which record superseded it (via the kept record's `supersedes` list), and
whether the superseded record had a status/assignee/note at the time (a
strong signal it was a false-positive collapse of an in-review tender, since
a genuine republish collapses a tender nobody had touched yet). Nothing is
written unless --restore is passed.

Usage (run from the project root):
    python scratch_recover_superseded.py                # report only
    python scratch_recover_superseded.py --restore 563438-2026
    python scratch_recover_superseded.py --restore-all-flagged   # restores
        every superseded record this report flags as "had review state"

Point DATABASE_URL at the target DB before running (see store.init_db) —
unset, this defaults to the local SQLite dev DB, which is not where the
reported incident lives.
"""
import argparse
import sys

sys.path.insert(0, "src")

import store
from schema import tenders


def _tenant_ids(conn):
    from sqlalchemy import select
    with conn.connect() as c:
        return [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]


def report(conn, tenant_id):
    records = store.all_records(conn, tenant_id)
    by_pub = {r["pub_number"]: r for r in records}
    superseded = [r for r in records if r.get("exclude_reason") == "superseded"]
    if not superseded:
        return []

    # Reverse-index: which kept record's `supersedes` list names this pub_number.
    superseded_by = {}
    for r in records:
        for sup in r.get("supersedes") or []:
            superseded_by[sup] = r["pub_number"]

    flagged = []
    print(f"\ntenant {tenant_id}: {len(superseded)} superseded record(s)")
    for r in superseded:
        kept_pub = superseded_by.get(r["pub_number"], "?")
        had_review_state = (
            (r.get("status") or "new") != "new"
            or bool(r.get("assigned_to"))
            or bool(r.get("reason_category"))
        )
        flag = "  <-- HAD REVIEW STATE, likely false-positive collapse" if had_review_state else ""
        print(f"  {r['pub_number']}  buyer={r.get('buyer')!r}  status={r.get('status')!r}"
              f"  superseded_by={kept_pub}{flag}")
        if had_review_state:
            flagged.append(r["pub_number"])
    return flagged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="append", default=[],
                         help="pub_number to restore; repeatable")
    parser.add_argument("--restore-all-flagged", action="store_true",
                         help="restore every record the report flags as had-review-state")
    parser.add_argument("--restored-by", default="cr-008-recovery")
    args = parser.parse_args()

    conn = store.init_db("data/tenders.db")
    to_restore = set(args.restore)
    for tenant_id in _tenant_ids(conn):
        flagged = report(conn, tenant_id)
        if args.restore_all_flagged:
            to_restore |= set(flagged)

    if not to_restore:
        print("\nNo --restore target given — report only, nothing written.")
        return

    print(f"\nRestoring: {sorted(to_restore)}")
    for tenant_id in _tenant_ids(conn):
        records = {r["pub_number"] for r in store.all_records(conn, tenant_id)}
        for pub_number in to_restore & records:
            store.restore_superseded(conn, tenant_id, pub_number, restored_by=args.restored_by)
            print(f"  restored {pub_number} (tenant {tenant_id})")


if __name__ == "__main__":
    main()
