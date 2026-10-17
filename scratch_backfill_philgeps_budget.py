"""One-off (2026-10-17): PhilGEPS tenders stored before the connector read
each notice's approved budget have no value, so the value floor never
applied — the Review Queue filled with PHP 100,000 negotiated purchases
(e.g. 28 from one barangay). store.upsert never updates an existing row, so
this fetches the budget for every surfaced PhilGEPS row with no value,
stores value/value_currency/value_eur/fx_rate_date, and sets exclude_reason
"below_value_floor" where the EUR value is under the floor — the same rule
filters.check_value_floor applies to new notices.

Dry run unless --apply is passed.

Run on Railway:
    Get-Content scratch_backfill_philgeps_budget.py -Raw | railway ssh -- python - --apply
"""
import sys
import time

sys.path.insert(0, "src")

import requests
from sqlalchemy import select, update

import config
import currency
import store
from connectors import philgeps
from schema import tenders

APPLY = "--apply" in sys.argv
floor = config.exclusions()["value_floor"]["eur"]
fx = currency.fetch_ecb_rates_or_fallback()

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    rows = c.execute(select(tenders.c.tenant_id, tenders.c.pub_number, tenders.c.tag_line).where(
        (tenders.c.source == "PhilGEPS") & (tenders.c.exclude_reason == "") & (tenders.c.value == ""))).fetchall()
print(f"surfaced PhilGEPS rows without a value: {len(rows)} (floor EUR {floor:,})")

updates = []
for r in rows:
    try:
        resp = requests.get(philgeps.DETAIL_URL.format(ref=r.pub_number),
                            headers={"User-Agent": philgeps.USER_AGENT}, timeout=30)
        resp.raise_for_status()
        cur, amount = philgeps.parse_approved_budget(resp.text)
    except Exception as e:
        print(f"  {r.pub_number}: fetch failed ({type(e).__name__})")
        cur, amount = None, None
    time.sleep(philgeps.REQUEST_DELAY_SECONDS)
    if not amount:
        print(f"  {r.pub_number}: no budget found, left as is")
        continue
    eur, rate_date = currency.to_eur(amount, cur, fx)
    excluded = eur is not None and eur < floor
    eur_label = f"EUR {eur:,.0f}" if eur is not None else "unconvertible"
    print(f"  {r.pub_number}: {cur} {float(amount):,.0f} = {eur_label}"
          f"{' -> below_value_floor' if excluded else ''} | {r.tag_line[:50]}")
    values = {"value": amount, "value_currency": cur,
              "value_eur": "" if eur is None else str(eur), "fx_rate_date": rate_date or ""}
    if excluded:
        values["exclude_reason"] = "below_value_floor"
    updates.append((r.tenant_id, r.pub_number, values))

print(f"rows to update: {len(updates)}, of which excluded: "
      f"{sum(1 for *_, v in updates if v.get('exclude_reason'))}")
if not APPLY:
    print("dry run — pass --apply to write")
    sys.exit(0)

with conn.begin() as c:
    for tenant_id, pub_number, values in updates:
        c.execute(update(tenders).where((tenders.c.tenant_id == tenant_id) & (tenders.c.source == "PhilGEPS")
                                        & (tenders.c.pub_number == pub_number)).values(**values))
print(f"rows updated: {len(updates)}")
