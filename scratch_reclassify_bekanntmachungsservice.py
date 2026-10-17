"""One-off (2026-10-17): German (Bekanntmachungsservice) tenders stored before
classification used the eForms form type were all filed as past_tender
(no deadline in the export => "past"), hiding open tenders from the Feed and
Review Queue. store.upsert never updates an existing row, so re-runs don't
fix them. This re-fetches the window, recomputes notice_type (and the
best-effort deadline) with the current code, and updates only rows whose
values differ. Award columns are cleared when a row stops being past_tender.

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_reclassify_bekanntmachungsservice.py -Raw | railway ssh -- python - --apply
"""
import datetime
import sys

sys.path.insert(0, "src")

from sqlalchemy import select, update

import classification
import normalize
import store
from connectors import bekanntmachungsservice
from schema import tenders

APPLY = "--apply" in sys.argv
SOURCE = "Bekanntmachungsservice"
since = datetime.date.today() - datetime.timedelta(days=30)

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    stored = c.execute(select(tenders.c.tenant_id, tenders.c.pub_number, tenders.c.notice_type, tenders.c.deadline)
                       .where(tenders.c.source == SOURCE)).fetchall()
print(f"stored {SOURCE} rows: {len(stored)}")

fresh = {}
for tenant_id in sorted({r.tenant_id for r in stored}):
    for raw in bekanntmachungsservice.fetch(store.get_tenant_cpv(conn, tenant_id), [], since):
        rec = normalize.normalize_bekanntmachungsservice(raw)
        fresh[(tenant_id, rec["pub_number"])] = (classification.classify(rec), rec["deadline"])

changes = []
for r in stored:
    new = fresh.get((r.tenant_id, r.pub_number))
    if new is None:
        print(f"  not in current window, left as is: {r.pub_number}")
        continue
    notice_type, deadline = new
    deadline = deadline or r.deadline
    if (notice_type, deadline) != (r.notice_type, r.deadline):
        changes.append((r.tenant_id, r.pub_number, notice_type, deadline))
        print(f"  {r.pub_number}: {r.notice_type}/{r.deadline or '-'} -> {notice_type}/{deadline or '-'}")
print(f"rows to update: {len(changes)}")

if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

with conn.begin() as c:
    for tenant_id, pub_number, notice_type, deadline in changes:
        values = {"notice_type": notice_type, "deadline": deadline}
        if notice_type != "past_tender":
            values.update(awarded_to=None, awarded_value=None, awarded_currency=None, award_detail=None)
        c.execute(update(tenders).where((tenders.c.tenant_id == tenant_id) & (tenders.c.source == SOURCE)
                                        & (tenders.c.pub_number == pub_number)).values(**values))
print(f"rows updated: {len(changes)}")
