"""One-off (2026-10-18): Find a Tender's `stages=tender` filter returned only
~1/6 of new tender notices, so the nightly runs missed most UK above-
threshold tenders. connectors/find_a_tender now fetches unfiltered and keeps
releases by tag, but with a 7-day look-back (rate limit). This pulls the
full 30 days once — one unfiltered fetch shared by every tenant — and feeds
each tenant's CPV-or-distinctive-keyword matches through run.process_record,
exactly as the nightly run would. Already-stored notices are skipped by
store.upsert (insert-only).

Dry run (lists matches, writes nothing) unless --apply is passed.
Takes ~15 min: ~130 requests at the API's 12-a-minute limit.

Run on Railway:
    Get-Content scratch_backfill_find_a_tender.py -Raw | railway ssh -- python - --apply
"""
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, "src")

import config
import currency
import match
import normalize
import run
import store
from connectors import find_a_tender

APPLY = "--apply" in sys.argv
DAYS = 30

raws = find_a_tender.fetch([], [], date.today() - timedelta(days=DAYS), max_records=100000,
                           max_lookback_days=DAYS)
print(f"Find a Tender tender/planning releases, last {DAYS} days: {len(raws)}", flush=True)

conn = store.db.get_engine(store.db.configured_url())
now = datetime.now(timezone.utc)
fx = currency.fetch_ecb_rates_or_fallback()
for tenant_id in store.list_provisioned_tenant_ids(conn):
    kw = store.get_tenant_keywords(conn, tenant_id)
    cpv = set(store.get_tenant_cpv(conn, tenant_id))
    full = [w for lang in kw["terms"].values() for w in lang]
    exclusions = dict(config.exclusions())
    exclusions["_distinctive_keywords"] = kw["distinctive"]
    hits = [r for r in raws if cpv & set(r["cpvCodes"])
            or match.match_relevance(f"{r['title']} {r['description']}", kw["distinctive"])]
    print(f"tenant {tenant_id}: {len(hits)} match CPV or a distinctive keyword")
    stored = 0
    for raw in hits:
        print(f"  {raw['id']} [{raw['formType']}] closes {raw['tenderPeriodEndDate'][:10] or '-'} | {raw['title'][:80]}")
        if APPLY:
            rec = normalize.normalize_find_a_tender(raw)
            if run.process_record(conn, tenant_id, rec, full, cpv, exclusions, now, fx, now.date().isoformat()):
                stored += 1
    if APPLY:
        print(f"  processed (stored unless already present): {stored}")
if not APPLY:
    print("dry run — pass --apply to write")
