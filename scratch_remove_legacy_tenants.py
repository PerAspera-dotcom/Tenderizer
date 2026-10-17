"""One-off (client decision 2026-10-17): the only tenants are WoT
(org_3I0hyvv0UV39gdtMDnX1BtRqjt5) and Alpinter (org_3KmRXdIzeizTq5T8clVzgbT7Fn2,
provisioned on its first login). Removes every other tenant's data:

  - per-user tenants left over from before Clerk organizations (no
    clerk_org_id): their data AND their tenants row;
  - DEFAULT_TENANT_ID (1), the pre-multi-tenancy seed: its data only — the
    row itself is recreated by store.init_db on every request, so deleting
    it would just bring it back empty.

Shared, non-tenant tables (e.g. the translations cache) are untouched.
Refuses to run if a tenant to remove is linked to any Clerk org. Dry run
(counts only) unless --apply is passed. Take a database backup first.

Run on Railway:
    Get-Content scratch_remove_legacy_tenants.py -Raw | railway ssh -- python - --apply
"""
import sys

sys.path.insert(0, "src")

from sqlalchemy import delete, func, select

import store
from schema import DEFAULT_TENANT_ID, metadata, tenants

KEEP_ORGS = {"org_3I0hyvv0UV39gdtMDnX1BtRqjt5", "org_3KmRXdIzeizTq5T8clVzgbT7Fn2"}
APPLY = "--apply" in sys.argv

conn = store.db.get_engine(store.db.configured_url())
with conn.connect() as c:
    rows = c.execute(select(tenants.c.id, tenants.c.clerk_org_id)).fetchall()

unknown_orgs = [(i, o) for i, o in rows if o and o not in KEEP_ORGS]
if unknown_orgs:
    sys.exit(f"refusing: tenants linked to an org not in KEEP_ORGS: {unknown_orgs}")
remove_rows = [i for i, o in rows if not o and i != DEFAULT_TENANT_ID]
clear_data = remove_rows + [DEFAULT_TENANT_ID]
kept = [(i, o) for i, o in rows if o]
print(f"keeping: {kept}")
print(f"removing tenants (data + row): {remove_rows}")
print(f"clearing data only: {[DEFAULT_TENANT_ID]}")

scoped = [t for t in reversed(metadata.sorted_tables) if t.name != "tenants" and "tenant_id" in t.c]
with (conn.begin() if APPLY else conn.connect()) as c:
    total = 0
    for t in scoped:
        n = c.execute(select(func.count()).select_from(t).where(t.c.tenant_id.in_(clear_data))).scalar()
        if n:
            print(f"  {t.name}: {n}")
            total += n
            if APPLY:
                c.execute(delete(t).where(t.c.tenant_id.in_(clear_data)))
    if APPLY and remove_rows:
        c.execute(delete(tenants).where(tenants.c.id.in_(remove_rows)))
    print(f"rows {'deleted' if APPLY else 'that would be deleted'}: {total} (+ {len(remove_rows)} tenants rows)")
if not APPLY:
    print("dry run — pass --apply to delete")
