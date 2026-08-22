"""CR-008 P1 retroactive fix: re-checks TED tenders that were wrongly
stamped `language='eng'` by the pre-fix normalize.normalize_ted, which
derived the whole record's language from notice-title alone (see
normalize._record_language's docstring). TED commonly gives an English
title (buyers add one for visibility) while description-proc has no 'eng'
key at all -- those records skipped run.py's translation step entirely
(`language == 'eng'` short-circuits it), so description_en was never
populated and the reviewer saw an untranslated body with no indication it
needed translation.

The stored `language` column can't be re-derived from the raw per-language
dicts (only the already-picked tag_line/description text is persisted, not
TED's original multilingual payload) -- so this uses DeepL's own detection
(translate.translate_and_detect) on the stored `description`, the same
technique scratch_backfill_language.py uses for blank `language`. Unlike
that script, this one deliberately re-checks rows that already have
`language='eng'` set, since that's exactly the value the bug produces.

Scope: every non-excluded TED tender, any status (matches the CR's "all TED
tenders currently in the Review Queue" -- not just status='new'; a
shortlisted/needs_review/dismissed tender is still worth fixing since its
description is still visible wherever it's shown).

Idempotent: a record whose description really is English is left alone
(language stays 'eng', nothing re-translated); a record already correctly
translated is skipped via the same `translation_status == 'ok'` check
run.py's own translation step uses, so re-running this costs nothing.

Multi-tenant note: unlike translate_cached (backed by store's tenant-agnostic
`translations` table, see schema.py), translate.translate_and_detect makes an
uncached DeepL call every time. When several tenants track the same public
TED notice (common -- confirmed live, e.g. tenant 2/5/8/10 all carrying
563438-2026), calling it once per (tenant, record) instead of once per
distinct description text burns real DeepL quota on the exact same text
repeatedly. `_cache` below (content-hash -> detection result) is shared
across every tenant processed in one `main()` run to avoid that.

Run from the project root:  python scratch_backfill_ted_description_language.py
Point DATABASE_URL at the target DB first (see store.init_db) -- unset,
this defaults to the local SQLite dev DB.
"""
import sys
from collections import Counter

sys.path.insert(0, "src")

import store
import translate
from schema import tenders


def _detect_cached(text, cache):
    """translate.translate_and_detect, deduped within this run by content
    hash — see the module docstring's multi-tenant note.
    """
    h = translate.content_hash(text)
    if h in cache:
        return cache[h]
    result = translate.translate_and_detect(text)
    cache[h] = result
    return result


def backfill_tenant(conn, tenant_id, cache=None):
    if cache is None:
        cache = {}
    records = [r for r in store.all_records(conn, tenant_id)
               if r["source"] == "TED"
               and not r.get("exclude_reason")
               and r.get("language") == "eng"
               and r.get("translation_status") != "ok"]
    if not records:
        return None

    stats = Counter()
    for rec in records:
        desc_en, detected_lang, desc_status = _detect_cached(rec.get("description"), cache)
        if desc_status != "ok":
            stats["translate_unavailable"] += 1
            continue

        if not detected_lang or detected_lang == "eng":
            stats["confirmed_english"] += 1
            continue

        store.update_language(conn, tenant_id, rec["pub_number"], detected_lang)
        tag_en, tag_status = translate.translate_cached(conn, rec.get("tag_line"))
        status = "ok" if tag_status == "ok" else "unavailable"
        store.set_translation(conn, tenant_id, rec["pub_number"], tag_en or "", desc_en or "", status)
        stats[f"fixed_{status}"] += 1

    stats["total_checked"] = len(records)
    return stats


def main():
    conn = store.init_db("data/tenders.db")
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    cache = {}
    for tenant_id in tenant_ids:
        stats = backfill_tenant(conn, tenant_id, cache)
        if stats is None:
            continue
        print(f"tenant {tenant_id}: {dict(stats)}")
    print(f"\ndistinct descriptions checked against DeepL: {len(cache)}")


if __name__ == "__main__":
    main()
