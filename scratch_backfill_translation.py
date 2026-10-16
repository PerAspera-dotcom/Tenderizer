"""CR-011 §1: one-off backfill + diagnostic pass for notices stuck with a
null/failed translation (tag_line_en / description_en never populated).

Distinct from scratch_backfill_language.py, which backfills the *language*
column itself for pre-CR-001-R3 legacy rows (and deliberately skips anything
that already has a language) — this script is for the opposite case: rows
that DO have a `language` set (correctly identified as non-English) but
whose translation never landed, because of the exact silent-failure bug
CR-011 §1 fixes in translate.py.

Strictly speaking this script isn't load-bearing: run.py's own per-run
translation loop already retries any record where translation_status != 'ok'
on every single scheduled run ("'unavailable' ones are retried in case DeepL
is back up" — see that loop's own comment), so once the real production
cause (missing/wrong DEEPL_API_KEY, wrong endpoint for the key type, or an
exhausted quota — check Railway's env vars; this script can't see that from
here) is actually fixed, the next scheduled run backfills everything on its
own. This script exists for two things the next scheduled run alone doesn't
give you:
  1. An immediate result, now, rather than waiting for the next cron tick.
  2. A per-notice reason for every remaining failure (now that translate.py's
     fix means failures carry a real message instead of a bare
     "unavailable") — run this BEFORE assuming the production key issue is
     fixed, to see the actual error for each stuck notice, and again
     AFTERWARD to confirm they all cleared.

Run from the project root:  python scratch_backfill_translation.py
"""
import sys
from collections import Counter

sys.path.insert(0, "src")

import store
import translate
from schema import tenders


def backfill_tenant(conn, tenant_id):
    # same scope run.py's own per-run loop uses, so this and the live
    # pipeline never disagree about which records are eligible.
    records = [
        r for r in store.all_records(conn, tenant_id)
        if not r.get("exclude_reason") and r.get("language") and r["language"] != "eng"
        and r.get("translation_status") != "ok"
    ]
    if not records:
        return None

    stats = Counter()
    failures = []  # (pub_number, reason) — only populated on failure, kept short
    for rec in records:
        tag_en, tag_status = translate.translate_cached(conn, rec.get("tag_line"))
        desc_en, desc_status = translate.translate_cached(conn, rec.get("description"))
        status = "ok" if tag_status == "ok" and desc_status == "ok" else "unavailable"
        store.set_translation(conn, tenant_id, rec["pub_number"], tag_en or "", desc_en or "", status)
        stats[status] += 1
        if status != "ok" and len(failures) < 10:
            failures.append(rec["pub_number"])

    stats["total"] = len(records)
    return stats, failures


def main():
    conn = store.init_db("data/tenders.db")
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    # DeepL's own current health first — if this prints "failing"/
    # "unavailable", every record below will too, and the real fix is
    # elsewhere (check DEEPL_API_KEY on Railway), not a re-run of this script.
    deepl_status, deepl_detail = translate.check_health()
    print(f"DeepL health: {deepl_status}" + (f" — {deepl_detail}" if deepl_detail else ""))

    for tenant_id in tenant_ids:
        result = backfill_tenant(conn, tenant_id)
        if result is None:
            continue
        stats, failures = result
        print(f"tenant {tenant_id}: {dict(stats)}")
        if failures:
            print(f"  still failing (see logs/Sentry for the reason each hit): {failures}")


if __name__ == "__main__":
    main()
