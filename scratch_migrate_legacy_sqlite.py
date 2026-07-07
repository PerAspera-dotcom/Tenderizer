"""One-off: migrate the pre-multi-tenancy local data/tenders.db (SQLite) into
whichever DATABASE_URL is configured (the consolidated Postgres for the prod
deploy). This file predates tenant_id entirely (no tenants/tenant_cpv/etc.
tables, no CR-001 columns like exclude_reason/value*/supersedes/language/
tag_line_en/description_en/translation_status) - it's raw Phase 1 engine
dev/test data from before the multi-tenancy and CR-001 work, last touched
2026-07-01. Assigned to DEFAULT_TENANT_ID (1), which the real customer
(tenant 2) never sees or logs into - preserved per "don't lose history"
without polluting their live Review Queue with un-filtered, pre-CR-001 rows.

Reuses store.upsert(), which recomputes hash from source|pub_number (see
normalize.record_hash) rather than trusting the old file's hash column, and
already defaults every column this old schema doesn't have (exclude_reason,
value*, supersedes, language, tag_line_en, description_en,
translation_status) to '' / '[]' via its existing _EMPTY_DEFAULT/_JSON
handling - no new mapping logic needed here. upsert() is also insert-only
and hash-deduped, so re-running this script is a safe no-op the second time.

Run from the project root, with DATABASE_URL pointed at the target Postgres:
  python scratch_migrate_legacy_sqlite.py
"""
import sqlite3
import sys

sys.path.insert(0, "src")

import store
from schema import DEFAULT_TENANT_ID

LEGACY_SQLITE_PATH = "data/tenders.db"

# Columns actually present in the pre-multi-tenancy file.
LEGACY_COLUMNS = ["hash", "source", "pub_number", "tag_line", "description",
                  "buyer", "country", "place", "category", "procedure",
                  "pub_date", "deadline", "cpv_codes", "matched_terms",
                  "match_source", "url", "first_seen", "status"]


def main():
    legacy = sqlite3.connect(LEGACY_SQLITE_PATH)
    legacy.text_factory = str
    cur = legacy.cursor()
    cur.execute(f"SELECT {', '.join(LEGACY_COLUMNS)} FROM tenders")
    rows = cur.fetchall()
    legacy.close()

    conn = store.init_db(LEGACY_SQLITE_PATH)  # DATABASE_URL (if set) wins over this path
    store.ensure_tenant(conn, DEFAULT_TENANT_ID)

    migrated = 0
    for row in rows:
        rec = dict(zip(LEGACY_COLUMNS, row))
        import json
        rec["cpv_codes"] = json.loads(rec["cpv_codes"] or "[]")
        rec["matched_terms"] = json.loads(rec["matched_terms"] or "[]")
        if store.upsert(conn, DEFAULT_TENANT_ID, rec):
            migrated += 1

    print(f"legacy rows read: {len(rows)}, newly inserted under tenant "
          f"{DEFAULT_TENANT_ID}: {migrated}, already present (skipped): {len(rows) - migrated}")


if __name__ == "__main__":
    main()
