"""CR-011 §5: clear a freshly-provisioned tenant down to the "no structure
and no content" state the CR asks for Alpinter — no CPV config, no keywords.

Why this is a separate one-off rather than a code change: a tenant_id in
this system is auto-provisioned the moment a Clerk organization's member
first logs in (store.link_or_create_tenant_for_clerk_org /
create_tenant_for_clerk_user) — there is no "create a tenant directly"
admin endpoint, by design. _seed_tenant_config seeds that brand-new tenant
from config/cpv.yaml and config/keywords.yaml — the right default for a new
WoT-like client, confirmed live (see test_20_tenant_config.py's own
test_ensure_tenant_seeds_cpv_from_yaml_defaults). CR-011 §5 asks for ONE
specific org to start empty, not a change to what every future org
inherits by default, so this clears one tenant after the fact rather than
changing _seed_tenant_config itself.

Safe to run more than once (set_tenant_cpv/set_tenant_keywords overwrite,
not append) and safe to run before the org's admin has logged in at all —
it'll just find nothing to clear and say so, rather than erroring, since
ensure_tenant is idempotent.

Portals/vault rules/screening questions aren't touched here: every tenant
already starts with zero vault documents and zero screening questions (that
feature doesn't exist yet — see CR-011 §3), and CR-011 §4's new portals
(e-Procurement/Bekanntmachungsservice) already ship enabled:false for every
tenant by default, so there's nothing there to clear either — only CPV and
keywords actually get non-empty defaults seeded today (see store.
_seed_tenant_config).

Run from the project root, after Alpinter's admin has logged in at least
once (so the tenant_id actually exists):
    python scratch_empty_organization.py <tenant_id>
Find the tenant_id via the ops-gated GET /api/health endpoint's tenant
scoping, or by asking the admin to check Settings (not yet surfaced
anywhere more directly — this script prints what it found either way).
"""
import sys

sys.path.insert(0, "src")

import store


def empty_organization(tenant_id):
    conn = store.init_db("data/tenders.db")
    with conn.connect() as c:
        from sqlalchemy import select
        from schema import tenants
        exists = c.execute(select(tenants.c.id).where(tenants.c.id == tenant_id)).fetchone()
    if exists is None:
        print(f"tenant {tenant_id}: does not exist yet — the org's admin needs to log in "
              f"at least once first (auto-provisions the tenant row).")
        return

    before_cpv = store.get_tenant_cpv(conn, tenant_id)
    before_kw = store.get_tenant_keywords(conn, tenant_id)
    store.set_tenant_cpv(conn, tenant_id, [])
    store.set_tenant_keywords(conn, tenant_id, {"terms": {}, "distinctive": []})
    print(f"tenant {tenant_id}: cleared {len(before_cpv)} CPV codes and "
          f"{sum(len(v) for v in before_kw['terms'].values())} keyword terms "
          f"({len(before_kw['distinctive'])} distinctive).")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scratch_empty_organization.py <tenant_id>")
        sys.exit(1)
    empty_organization(int(sys.argv[1]))


if __name__ == "__main__":
    main()
