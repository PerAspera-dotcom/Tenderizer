"""One-off (2026-10-18): TenderNed rows stored before notice_detail existed
have it NULL. store.upsert never updates an existing row, so this fetches
each stored notice's detail (the same endpoint the connector calls) and
fills notice_detail — notice type, procedure, tender platform, notice PDF, …

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_backfill_tenderned_detail.py -Raw | railway ssh -- python - --apply
"""
import json
import sys
import time

sys.path.insert(0, "src")

import requests
from sqlalchemy import select, update

import store
from connectors import tenderned
from schema import tenders

APPLY = "--apply" in sys.argv

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    rows = c.execute(select(tenders.c.tenant_id, tenders.c.pub_number).where(
        (tenders.c.source == "TenderNed") & tenders.c.notice_detail.is_(None))).fetchall()
print(f"stored TenderNed rows without detail: {len(rows)}")

session = requests.Session()
session.headers.update({"User-Agent": tenderned.USER_AGENT, "Accept": "application/json"})
updates = []
for r in rows:
    try:
        resp = session.get(tenderned.DETAIL_ENDPOINT.format(id=r.pub_number), timeout=30)
        resp.raise_for_status()
        detail = tenderned.notice_detail(resp.json())
    except Exception as e:
        print(f"  {r.pub_number}: fetch failed ({type(e).__name__})")
        detail = None
    time.sleep(tenderned.REQUEST_DELAY_SECONDS)
    if detail:
        print(f"  {r.pub_number}: {json.dumps(detail)[:200]}")
        updates.append((r.tenant_id, r.pub_number, detail))
print(f"rows to update: {len(updates)}")
if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

with conn.begin() as c:
    for tenant_id, pub_number, detail in updates:
        c.execute(update(tenders).where((tenders.c.tenant_id == tenant_id) & (tenders.c.source == "TenderNed")
                                        & (tenders.c.pub_number == pub_number)).values(notice_detail=json.dumps(detail)))
print(f"rows updated: {len(updates)}")
