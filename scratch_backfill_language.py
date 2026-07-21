"""One-off backfill: detect `language` + translate tag_line/description for
already-stored tenders that predate CR-001 R3 (DeepL translation) and were
never picked up by it.

store.upsert() is insert-only (never rewrites a record another run already
stored — see store.update_tagging's docstring), and the `language` column was
added in CR-001 R3 (commit 95cd77f), after which every pre-existing row was
left at its "" default. run.py's translation step then silently skips any
record with `not r.get("language")`, so those rows never get translated no
matter how many times the pipeline reruns — this affects every source
language equally, since the root cause has nothing to do with which language
a given notice is actually in.

Rather than adding a language-detection dependency, this reuses
translate.translate_and_detect(): DeepL's own /v2/translate response already
includes detected_source_language, so one call on tag_line both backfills
`language` and produces its translation; description is translated
separately (via the cache, so identical text across notices isn't billed
twice).

CR-005: originally scoped to status='shortlisted' only (the first pass, run
2026-07-16). That missed every other surfaced tender — including Review
Queue's status='new' rows, which is exactly where CR-005's screenshot found
a still-untranslated Polish notice. Broadened to match run.py's own ongoing
per-run translation filter exactly (`not exclude_reason` — the same test
that decides whether a record gets translated on every subsequent scrape),
so this backfill and the live pipeline now agree on scope instead of the
backfill being a narrower one-off. Still bounded (excluded/filtered-out
tenders are skipped, matching what the pipeline would do anyway) and still
idempotent: any row already carrying a `language` value is skipped.

Run from the project root:  python scratch_backfill_language.py
"""
import sys
from collections import Counter

sys.path.insert(0, "src")

import store
import translate
from schema import tenders


def backfill_tenant(conn, tenant_id):
    records = [r for r in store.all_records(conn, tenant_id)
               if not r.get("exclude_reason") and not r.get("language")]
    if not records:
        return None

    stats = Counter()
    for rec in records:
        tag_en, language, tag_status = translate.translate_and_detect(rec.get("tag_line"))
        if tag_status != "ok":
            stats["translate_unavailable"] += 1
            continue

        store.update_language(conn, tenant_id, rec["pub_number"], language or "eng")

        if (language or "eng") in ("eng", "en"):
            stats["already_english"] += 1
            continue

        desc_en, desc_status = translate.translate_cached(conn, rec.get("description"))
        status = "ok" if desc_status == "ok" else "unavailable"
        store.set_translation(conn, tenant_id, rec["pub_number"], tag_en or "", desc_en or "", status)
        stats[f"translated_{status}"] += 1

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
