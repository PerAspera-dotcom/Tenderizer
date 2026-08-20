"""CR-007 Phase A — org-shared workspace.

Covers resolving/self-healing a tenant from a Clerk organization claim
(store.link_or_create_tenant_for_clerk_org / api._resolve_tenant_id /
api._org_id_from_claims). Every Review Queue action is org-shared post-CR-007
(see schema.py's tender_reviews retirement comment; test_36_dismissal_
attribution.py has that layer's dedicated coverage) — this file's remaining
job is two different orgs staying as fully isolated as two different
pre-Phase-A tenants always were.
"""
import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME_B, TEST_CLERK_USER_ID_B, make_identity


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


# ── store.link_or_create_tenant_for_clerk_org ────────────────────────────────

def test_fresh_org_gets_a_new_tenant(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    tenant_id = store.link_or_create_tenant_for_clerk_org(conn, "org_abc", "user_1", "a@example.com")
    assert tenant_id is not None
    assert store.get_tenant_id_by_clerk_org_id(conn, "org_abc") == tenant_id


def test_resolving_the_same_org_twice_is_idempotent(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    first = store.link_or_create_tenant_for_clerk_org(conn, "org_abc", "user_1")
    second = store.link_or_create_tenant_for_clerk_org(conn, "org_abc", "user_1")
    assert first == second


def test_a_second_colleague_in_the_same_org_resolves_to_the_same_tenant(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    owner_tenant = store.link_or_create_tenant_for_clerk_org(conn, "org_abc", "user_owner")
    colleague_tenant = store.link_or_create_tenant_for_clerk_org(conn, "org_abc", "user_colleague")
    assert owner_tenant == colleague_tenant


def test_a_legacy_solo_tenant_is_linked_onto_its_owners_first_org(tmp_path, monkeypatch):
    """The self-healing migration path: a pre-Phase-A tenant (1 Clerk user =
    1 tenant, no org) keeps its id and all its data once its owner creates
    or joins an org — nothing is copied or re-keyed.
    """
    conn = _db(tmp_path, monkeypatch)
    legacy_tenant_id = store.create_tenant_for_clerk_user(conn, "user_legacy", "legacy@example.com")
    store.upsert(conn, legacy_tenant_id, {
        "source": "TED", "pub_number": "LEGACY-1", "tag_line": "Old data", "description": "",
        "buyer": "Ministry X", "country": "SWE", "place": "", "category": "Supply",
        "procedure": "open", "pub_date": "2026-01-01", "deadline": "2030-01-01T00:00:00+00:00",
        "cpv_codes": [], "matched_terms": [], "match_source": None, "url": "http://x",
        "first_seen": None, "exclude_reason": "",
    })

    linked_tenant_id = store.link_or_create_tenant_for_clerk_org(conn, "org_new", "user_legacy")

    assert linked_tenant_id == legacy_tenant_id
    assert {r["pub_number"] for r in store.all_records(conn, linked_tenant_id)} == {"LEGACY-1"}


def test_a_user_creating_a_second_org_does_not_reassign_their_first(tmp_path, monkeypatch):
    """A person can create/own multiple orgs — only their one pre-Phase-A
    legacy tenant (if any) gets auto-linked, to whichever org they activate
    first; every other org they create gets its own fresh tenant.
    """
    conn = _db(tmp_path, monkeypatch)
    legacy_tenant_id = store.create_tenant_for_clerk_user(conn, "user_multi")
    first_org_tenant = store.link_or_create_tenant_for_clerk_org(conn, "org_one", "user_multi")
    assert first_org_tenant == legacy_tenant_id

    second_org_tenant = store.link_or_create_tenant_for_clerk_org(conn, "org_two", "user_multi")
    assert second_org_tenant != legacy_tenant_id
    assert second_org_tenant != first_org_tenant


# ── api._org_id_from_claims / _resolve_tenant_id ─────────────────────────────

def test_org_id_read_from_top_level_claim():
    assert api._org_id_from_claims({"org_id": "org_x"}) == "org_x"


def test_org_id_read_from_nested_o_claim():
    assert api._org_id_from_claims({"o": {"id": "org_x", "rol": "admin"}}) == "org_x"


def test_org_id_absent_when_no_active_org():
    assert api._org_id_from_claims({"sub": "user_1"}) is None


def test_resolve_tenant_id_two_users_same_org_claim_share_a_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    tenant_a = api._resolve_tenant_id({"sub": "user_a", "org_id": "org_shared", "email": "a@x.com"})
    tenant_b = api._resolve_tenant_id({"sub": "user_b", "org_id": "org_shared", "email": "b@x.com"})
    assert tenant_a == tenant_b


def test_resolve_tenant_id_different_orgs_get_different_tenants(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    tenant_a = api._resolve_tenant_id({"sub": "user_a", "org_id": "org_one"})
    tenant_b = api._resolve_tenant_id({"sub": "user_b", "org_id": "org_two"})
    assert tenant_a != tenant_b


def test_resolve_tenant_id_falls_back_to_per_user_when_no_org_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    tenant_id = api._resolve_tenant_id({"sub": "user_solo"})
    assert tenant_id == api._resolve_tenant_id({"sub": "user_solo"})


# ── Cross-org isolation — two different orgs stay as isolated as two
# different pre-Phase-A tenants always were ──────────────────────────────────

def test_two_orgs_tenders_and_pipeline_are_fully_isolated(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    org_a = store.link_or_create_tenant_for_clerk_org(conn, "org_a", "user_a")
    org_b = store.link_or_create_tenant_for_clerk_org(conn, "org_b", "user_b")

    store.upsert(conn, org_a, {
        "source": "TED", "pub_number": "ORG-A-1", "tag_line": "Org A tender", "description": "",
        "buyer": "Ministry A", "country": "SWE", "place": "", "category": "Supply",
        "procedure": "open", "pub_date": "2026-01-01", "deadline": "2030-01-01T00:00:00+00:00",
        "cpv_codes": [], "matched_terms": [], "match_source": None, "url": "http://x",
        "first_seen": None, "exclude_reason": "",
    })

    assert {r["pub_number"] for r in store.all_records(conn, org_a)} == {"ORG-A-1"}
    assert {r["pub_number"] for r in store.all_records(conn, org_b)} == set()
    assert store.get_pipeline_entries(conn, org_b) == []


# ── Every review action rolls up to the org, shortlist included (see also
# test_36_dismissal_attribution.py's dedicated coverage) ────────────────────

def test_org_shared_overview_acceptance_criterion(tmp_path, monkeypatch):
    """The CR-007 Phase A global acceptance line: user A shortlists a tender
    -> user B (same org) sees it in the shared Overview/pipeline.
    """
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, {
        "source": "TED", "pub_number": "ACC-1", "tag_line": "Shared tender", "description": "",
        "buyer": "Ministry X", "country": "SWE", "place": "", "category": "Supply",
        "procedure": "open", "pub_date": "2026-01-01", "deadline": "2030-01-01T00:00:00+00:00",
        "cpv_codes": [], "matched_terms": [], "match_source": None, "url": "http://x",
        "first_seen": None, "exclude_reason": "",
    })

    user_a = make_identity()
    user_b = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)

    api.patch_tender("ACC-1", api.StatusBody(status="shortlisted"), identity=user_a)

    seen_by_b = api.get_tender("ACC-1", identity=user_b)
    assert seen_by_b["status"] == "shortlisted"
    # Shortlisting auto-creates a pipeline entry, and B's read hits the same
    # shared tenant_id as A's write — the actual "shared Overview/pipeline"
    # acceptance criterion.
    pipeline_for_b = api.get_pipeline(tenant_id=user_b.tenant_id)
    assert {e["pub_number"] for e in pipeline_for_b} == {"ACC-1"}
