"""CR-011 §4 go-live: flip the five new sources on for every tenant that
already existed before today's portals.yaml flip.

Why this is a separate one-off rather than trusting the config flip alone:
store._sync_new_portals only INSERTS a tenant_portals row for a portal name
a tenant has never seen before, using config/portals.yaml's CURRENT default
at the moment it runs — it never UPDATES a row that already exists (by
design: it must never silently overwrite a tenant's own enable/disable
choice on a portal they've already configured). Find a Tender/Contracts
Finder/CanadaBuys/TenderNed/PhilGEPS were added to portals.yaml several
hours before business green-lit go-live and flipped `enabled: false` to
`true` — if any tenant's scheduled pipeline run already executed in that
window, that tenant now has real `enabled: false` rows for these five
names, and flipping the YAML default alone will never reach them. This
script corrects exactly that gap, directly, for every tenant that exists.

Safe to run more than once (set_tenant_portal_enabled overwrites, not
appends) and safe to run even if no tenant was ever affected (an UPDATE
matching zero rows is a no-op, not an error).

Run from the project root (against whatever DATABASE_URL is set in the
environment — this is meant to be run on Railway against production, the
same way scratch_empty_organization.py is):
    python scratch_enable_new_sources.py
"""
import sys

sys.path.insert(0, "src")

import store

NEW_SOURCES = ["Find a Tender", "Contracts Finder", "CanadaBuys", "TenderNed", "PhilGEPS"]


def enable_for_all_tenants():
    conn = store.init_db("data/tenders.db")
    tenant_ids = store.list_provisioned_tenant_ids(conn)
    if not tenant_ids:
        print("No provisioned tenants found.")
        return
    for tenant_id in tenant_ids:
        before = {p["name"]: p["enabled"] for p in store.get_tenant_portals(conn, tenant_id)}
        changed = []
        for name in NEW_SOURCES:
            if name in before and not before[name]:
                store.set_tenant_portal_enabled(conn, tenant_id, name, True)
                changed.append(name)
        if changed:
            print(f"tenant {tenant_id}: enabled {changed}")
        else:
            print(f"tenant {tenant_id}: nothing to fix (either already enabled, "
                  f"or no row existed yet — config/portals.yaml's own enabled:true "
                  f"default will seed it correctly on this tenant's next pipeline run).")


if __name__ == "__main__":
    enable_for_all_tenants()
