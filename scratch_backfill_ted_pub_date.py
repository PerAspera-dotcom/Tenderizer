"""One-off: fill pub_date on TED tenders stored before ted.FIELDS requested
publication-date (fixed 2026-10-17). store.upsert never updates an existing
row, so already-stored TED tenders keep an empty pub_date without this.

Only writes the pub_date column, only where it is empty. Dry run unless
--apply is passed.

Run on Railway:
    Get-Content scratch_backfill_ted_pub_date.py -Raw | railway ssh -- python - --apply
"""
import sys

sys.path.insert(0, "src")

import requests
from sqlalchemy import select, update

import store
from connectors import ted
from normalize import _ted_pub_date
from schema import tenders

APPLY = "--apply" in sys.argv
BATCH = 50

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    pub_numbers = sorted({r[0] for r in c.execute(
        select(tenders.c.pub_number).where((tenders.c.source == "TED") & (tenders.c.pub_date == "")))})
print(f"TED notices with empty pub_date (all tenants): {len(pub_numbers)}")

found = {}
for i in range(0, len(pub_numbers), BATCH):
    chunk = pub_numbers[i:i + BATCH]
    body = {"query": f"publication-number IN ({' '.join(chunk)})",
            "fields": ["publication-number", "publication-date"], "limit": 100, "page": 1}
    resp = requests.post(ted.ENDPOINT, json=body, timeout=60)
    resp.raise_for_status()
    for n in resp.json().get("notices", []):
        iso = _ted_pub_date(n.get("publication-date"))
        if iso:
            found[n["publication-number"]] = iso
print(f"publication dates found on TED: {len(found)} / {len(pub_numbers)}")

if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

updated = 0
with conn.begin() as c:
    for pub_number, iso in found.items():
        updated += c.execute(update(tenders).where(
            (tenders.c.source == "TED") & (tenders.c.pub_number == pub_number) & (tenders.c.pub_date == "")
        ).values(pub_date=iso)).rowcount
print(f"rows updated: {updated}")
