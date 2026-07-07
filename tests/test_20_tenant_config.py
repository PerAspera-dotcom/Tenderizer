"""Step 20 — per-tenant config (phase2/3 step 5): CPV set, keywords, and
enabled portals are now DB rows per tenant, seeded from config/*.yaml
defaults the first time a tenant is ensured, and independently customisable
(and isolated) after that. The YAML files are the *default* content now, not
the live config — config.cpv_codes()/keywords()/portals() are still exercised
directly elsewhere (test_13/test_14) as tests of that default content.
"""
import config
import store


def test_ensure_tenant_seeds_cpv_from_yaml_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert set(store.get_tenant_cpv(conn, 1)) == set(config.cpv_codes())


def test_ensure_tenant_seeds_keywords_from_yaml_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    kw = config._load("keywords.yaml")
    stored = store.get_tenant_keywords(conn, 1)
    assert stored["terms"] == kw["terms"]
    assert stored["distinctive"] == kw["distinctive"]


def test_ensure_tenant_seeds_portals_from_yaml_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    seeded = {p["name"]: p["enabled"] for p in store.get_tenant_portals(conn, 1)}
    assert seeded == {p["name"]: p["enabled"] for p in config.portals()}


def test_two_tenants_have_independent_cpv_sets(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, 2)
    store.set_tenant_cpv(conn, 2, ["12345678"])
    assert store.get_tenant_cpv(conn, 2) == ["12345678"]
    assert set(store.get_tenant_cpv(conn, 1)) == set(config.cpv_codes())  # tenant 1 untouched


def test_set_tenant_cpv_overwrites_not_merges(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_tenant_cpv(conn, 1, ["11111111"])
    assert store.get_tenant_cpv(conn, 1) == ["11111111"]


def test_ensure_tenant_does_not_clobber_customised_config(tmp_path):
    # init_db() calls ensure_tenant() unconditionally on every call — a
    # customised tenant must survive that, not get reset to the YAML default.
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_tenant_cpv(conn, 1, ["99999999"])
    store.ensure_tenant(conn, 1)
    assert store.get_tenant_cpv(conn, 1) == ["99999999"]


def test_ensure_tenant_does_not_reset_a_deliberately_emptied_cpv_set(tmp_path):
    # Regression: an empty tenant_cpv (0 rows) must not look like "never
    # seeded" — that would silently reset a tenant who cleared their CPV set
    # back to the YAML defaults on every subsequent ensure_tenant()/init_db().
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_tenant_cpv(conn, 1, [])
    store.ensure_tenant(conn, 1)
    assert store.get_tenant_cpv(conn, 1) == []


def test_set_tenant_keywords_merges_only_provided_keys(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    before = store.get_tenant_keywords(conn, 1)
    store.set_tenant_keywords(conn, 1, {"distinctive": ["widget"]})
    after = store.get_tenant_keywords(conn, 1)
    assert after["distinctive"] == ["widget"]
    assert after["terms"] == before["terms"]  # untouched


def test_two_tenants_have_independent_enabled_portals(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, 2)
    store.set_tenant_portal_enabled(conn, 2, "BOAMP", False)
    assert store.get_enabled_portal_names(conn, 2) == {"TED"}
    assert store.get_enabled_portal_names(conn, 1) == {"TED", "BOAMP"}  # tenant 1 untouched


def test_ensure_tenant_seeds_settings_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert store.get_tenant_settings(conn, 1) == {
        "run_frequency": "daily", "run_window_start": "02:00", "run_window_end": "06:00",
        "notify_on_complete": False, "notify_email": "",
    }


def test_set_tenant_settings_merges_only_provided_keys(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_tenant_settings(conn, 1, {"notify_on_complete": True, "notify_email": "a@b.com"})
    after = store.get_tenant_settings(conn, 1)
    assert after["notify_on_complete"] is True
    assert after["notify_email"] == "a@b.com"
    assert after["run_frequency"] == "daily"  # untouched
    assert after["run_window_start"] == "02:00"  # untouched


def test_two_tenants_have_independent_settings(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, 2)
    store.set_tenant_settings(conn, 2, {"run_frequency": "weekly"})
    assert store.get_tenant_settings(conn, 2)["run_frequency"] == "weekly"
    assert store.get_tenant_settings(conn, 1)["run_frequency"] == "daily"  # untouched


def test_ensure_tenant_does_not_clobber_customised_settings(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_tenant_settings(conn, 1, {"run_frequency": "paused"})
    store.ensure_tenant(conn, 1)
    assert store.get_tenant_settings(conn, 1)["run_frequency"] == "paused"
