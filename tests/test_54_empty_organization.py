"""CR-011 §5 — a brand-new, deliberately empty organization ("Alpinter").

Two things this CR actually asks for that are backend-testable from here:
  1. "Empty-state screens must render cleanly (no errors on zero CPV codes /
     zero sources configured)" — every read endpoint a fresh org's first
     page load would hit, called against a tenant with zero of everything
     (CPV codes, keywords, enabled portals, tenders, vault docs), confirming
     none of them raise or return a shape the frontend can't handle.
  2. "Must be fully isolated from WoT" — a second tenant's data never leaks
     into the empty org's own reads. The broader Vault/Composer filesystem-
     isolation mechanism already has dedicated coverage in
     test_37_tenant_path_isolation.py; this file only adds the specific
     "two tenants, one of them empty" angle that file doesn't cover.

What this file does NOT cover, and can't from here: actually creating the
"Alpinter" Clerk organization itself. A tenant_id in this system is
auto-provisioned the moment a Clerk org's member first logs in (store.
link_or_create_tenant_for_clerk_org) — there is no "create a tenant
directly" admin endpoint, by design (see that function's own docstring).
Creating the org is a Clerk-dashboard action outside this codebase.

Real finding from reading _seed_tenant_config, confirmed by the tests
below: a brand-new tenant does NOT start structurally empty today — it
auto-seeds WoT's own CPV codes and keyword library from the shared
config/cpv.yaml and config/keywords.yaml (see test_ensure_tenant_seeds_
cpv_from_yaml_defaults in test_20_tenant_config.py). That's the right
default for a new WoT-like client, but it's the opposite of what CR-011 §5
asks for Alpinter specifically. Deliberately NOT changing _seed_tenant_config
itself here — that would change the onboarding default for every FUTURE
org, which isn't what was asked; the clear-to-empty step for Alpinter
specifically is scratch_empty_organization.py (one-off, matching the
scratch_*.py convention), run once against the real tenant_id after it's
provisioned.
"""
import store
import api
from conftest import TEST_TENANT_ID

ALPINTER_TENANT_ID = 2  # same style as test_20/test_49's "a second tenant"


def _empty_tenant(conn, tenant_id):
    """Simulates what scratch_empty_organization.py does against a real
    freshly-provisioned tenant: seed normally (ensure_tenant's only path),
    then clear CPV/keywords down to nothing — the same "deliberately
    emptied" state test_20_tenant_config.py's own
    test_ensure_tenant_does_not_reset_a_deliberately_emptied_cpv_set already
    proves the system treats correctly (never silently reset back to the
    YAML defaults on a later ensure_tenant call).
    """
    store.ensure_tenant(conn, tenant_id)
    store.set_tenant_cpv(conn, tenant_id, [])
    store.set_tenant_keywords(conn, tenant_id, {"terms": {}, "distinctive": []})
    return conn


def test_seeding_is_not_empty_by_default(tmp_path, monkeypatch):
    # confirms the finding above, so this file fails loudly if a future
    # change to _seed_tenant_config ever makes this assumption stale.
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, ALPINTER_TENANT_ID)
    assert store.get_tenant_cpv(conn, ALPINTER_TENANT_ID) != []
    assert store.get_tenant_keywords(conn, ALPINTER_TENANT_ID)["terms"] != {}


# ── empty-state: every relevant read endpoint, zero of everything ──────────

def test_stats_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    stats = api.get_stats(tenant_id=ALPINTER_TENANT_ID)
    assert stats["notices_scanned"] == 0
    assert stats["by_match"] == {"cpv": 0, "both": 0, "keyword": 0, "none": 0}
    assert stats["by_category"] == {"Supply": 0, "Services": 0, "Works": 0, "Training": 0, "Other": 0}
    assert stats["past_tenders"] == 0


def test_health_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    result = api.get_health(tenant_id=ALPINTER_TENANT_ID)
    assert isinstance(result, list) and len(result) > 0  # TED/BOAMP/etc. + DeepL, all "no history yet"
    deepl = next(p for p in result if p["name"] == "DeepL")
    assert deepl["status"] == "paused"  # never run — see api._deepl_status_label


def test_list_tenders_on_empty_tenant(tmp_path, monkeypatch):
    from conftest import make_identity
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    identity = make_identity(tenant_id=ALPINTER_TENANT_ID)
    result = api.list_tenders(limit=100, offset=0, identity=identity)
    assert result == {"total": 0, "results": []}


def test_scout_settings_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    settings = api.get_scout_settings_config(tenant_id=ALPINTER_TENANT_ID)
    assert isinstance(settings, dict)  # defaults, no crash — zero portals enabled is a valid state


def test_vault_docs_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    body = api.get_vault_docs(q=None, tenant_id=ALPINTER_TENANT_ID)
    assert body == {"total": 0, "processing": 0, "results": []}


def test_vault_tags_and_rules_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    assert api.get_vault_tags(tenant_id=ALPINTER_TENANT_ID) == {"tags": []}
    assert api.get_vault_rules_config(tenant_id=ALPINTER_TENANT_ID) == {"hints": []}


def test_vault_settings_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    settings = api.get_vault_settings_config(tenant_id=ALPINTER_TENANT_ID)
    assert isinstance(settings, dict)


def test_composer_settings_and_style_on_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    settings = api.get_composer_settings_config(tenant_id=ALPINTER_TENANT_ID)
    assert "model" in settings
    style = api.get_composer_style(tenant_id=ALPINTER_TENANT_ID)
    assert isinstance(style, dict)


def test_default_sources_is_empty_when_every_portal_disabled(tmp_path, monkeypatch):
    # CR-011 §5: "no pipeline" — a scheduled/manual run against this tenant
    # must be a safe no-op, not an error, if every portal has been disabled.
    import run
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = _empty_tenant(store.init_db(str(tmp_path / "t.db")), ALPINTER_TENANT_ID)
    for portal in store.get_tenant_portals(conn, ALPINTER_TENANT_ID):
        store.set_tenant_portal_enabled(conn, ALPINTER_TENANT_ID, portal["name"], False)
    sources = run._default_sources(conn, ALPINTER_TENANT_ID, since=None)
    assert sources == []


# ── isolation: WoT's data never reaches the empty org's reads ──────────────

def _wot_tender(pub_number):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv",
            "url": "http://ted.example/1", "first_seen": None, "exclude_reason": ""}


def test_wot_tenders_do_not_appear_for_the_empty_org(tmp_path, monkeypatch):
    from conftest import make_identity
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = store.init_db(str(tmp_path / "t.db"))  # seeds TEST_TENANT_ID ("WoT") normally
    store.upsert(conn, TEST_TENANT_ID, _wot_tender("WOT-1"))
    _empty_tenant(conn, ALPINTER_TENANT_ID)

    wot_result = api.list_tenders(limit=100, offset=0, identity=make_identity(tenant_id=TEST_TENANT_ID))
    alpinter_result = api.list_tenders(limit=100, offset=0, identity=make_identity(tenant_id=ALPINTER_TENANT_ID))
    assert wot_result["total"] == 1
    assert alpinter_result["total"] == 0
    assert alpinter_result["results"] == []


def test_wot_cpv_and_keywords_do_not_leak_into_the_empty_org(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = store.init_db(str(tmp_path / "t.db"))
    _empty_tenant(conn, ALPINTER_TENANT_ID)
    assert store.get_tenant_cpv(conn, TEST_TENANT_ID) != []  # WoT keeps its own defaults
    assert store.get_tenant_cpv(conn, ALPINTER_TENANT_ID) == []  # Alpinter stays empty
