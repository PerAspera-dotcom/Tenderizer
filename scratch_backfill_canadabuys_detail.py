"""One-off (2026-10-18): CanadaBuys rows stored before notice_detail existed
have it NULL. store.upsert never updates an existing row, so this re-reads
today's open-tender CSV and fills notice_detail (solicitation number, notice
type, contact, contract period, …) for every stored row whose reference is
in it. Notices that have since closed are no longer in the open-tender file
and stay NULL — their detail panel simply shows no Notice Details section.

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_backfill_canadabuys_detail.py -Raw | railway ssh -- python - --apply
"""
import csv
import io
import json
import sys

sys.path.insert(0, "src")

import requests
from sqlalchemy import select, update

import store
from connectors import canadabuys
from schema import tenders

APPLY = "--apply" in sys.argv

resp = requests.get(canadabuys.CSV_URL, headers={"User-Agent": canadabuys.USER_AGENT}, timeout=120)
resp.raise_for_status()
by_ref = {}
for row in csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))):
    detail = canadabuys.notice_detail(row)
    if detail:
        by_ref[row.get("referenceNumber-numeroReference") or ""] = detail
print(f"open notices in today's CSV with detail: {len(by_ref)}")

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    rows = c.execute(select(tenders.c.tenant_id, tenders.c.pub_number).where(
        (tenders.c.source == "CanadaBuys") & tenders.c.notice_detail.is_(None))).fetchall()
updates = [(r.tenant_id, r.pub_number, by_ref[r.pub_number]) for r in rows if r.pub_number in by_ref]
print(f"stored CanadaBuys rows without detail: {len(rows)}, fillable from today's CSV: {len(updates)}")
for u in updates[:2]:
    print("  e.g.", u[1], json.dumps(u[2])[:200])
if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

with conn.begin() as c:
    for tenant_id, pub_number, detail in updates:
        c.execute(update(tenders).where((tenders.c.tenant_id == tenant_id) & (tenders.c.source == "CanadaBuys")
                                        & (tenders.c.pub_number == pub_number)).values(notice_detail=json.dumps(detail)))
print(f"rows updated: {len(updates)}")
