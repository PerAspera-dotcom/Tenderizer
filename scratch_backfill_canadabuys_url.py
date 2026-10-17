"""One-off (2026-10-17): CanadaBuys notices posted on CanadaBuys itself
("cb-..." references) were stored with an empty url — the CSV's noticeURL
column is only filled for notices hosted elsewhere (MERX). connectors/
canadabuys.notice_url now builds their CanadaBuys page link; store.upsert
never updates an existing row, so this fills the stored ones.

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_backfill_canadabuys_url.py -Raw | railway ssh -- python - --apply
"""
import sys

sys.path.insert(0, "src")

from sqlalchemy import select, update

import store
from connectors import canadabuys
from schema import tenders

APPLY = "--apply" in sys.argv

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    rows = c.execute(select(tenders.c.tenant_id, tenders.c.pub_number).where(
        (tenders.c.source == "CanadaBuys") & (tenders.c.url == ""))).fetchall()
updates = [(r.tenant_id, r.pub_number, canadabuys.notice_url("", r.pub_number)) for r in rows]
updates = [u for u in updates if u[2]]
print(f"CanadaBuys rows without a url: {len(rows)}, fillable: {len(updates)}")
for u in updates[:3]:
    print("  e.g.", u[1], "->", u[2])
if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

with conn.begin() as c:
    for tenant_id, pub_number, url in updates:
        c.execute(update(tenders).where((tenders.c.tenant_id == tenant_id) & (tenders.c.source == "CanadaBuys")
                                        & (tenders.c.pub_number == pub_number)).values(url=url))
print(f"rows updated: {len(updates)}")
