"""Step 17 — API exclude_reason filtering (CR-001 follow-up, found while
verifying R1).

api.py's route handlers are plain functions — called directly here rather
than through FastAPI's TestClient, since this repo has no httpx dependency
and none of the existing tests need one either. DB_PATH is monkeypatched to
an isolated tmp DB so this never touches the real data/tenders.db.

Before this fix, GET /api/tenders and GET /api/tenders/{pub_number} never
filtered on exclude_reason at all — every F1-F8/D-DUP exclusion was cosmetic
in the .xlsx report only, and still fully visible in the live Review Queue /
Tender Feed via the API.
"""
import config
import store
import api
from conftest import TEST_TENANT_ID


def _rec(pub_number, exclude_reason="", deadline="2030-01-01T00:00:00+00:00"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": ["39522530"], "matched_terms": ["tent"],
            "match_source": "cpv", "url": "http://x", "first_seen": None,
            "exclude_reason": exclude_reason}


def _seed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec("KEPT-1"))
    store.upsert(conn, TEST_TENANT_ID, _rec("EXCLUDED-1", exclude_reason="rental"))
    return conn


# route handlers depend on tenant_id via Depends(get_current_tenant_id); calling
# them directly (not through FastAPI's DI) means that default isn't resolved, so
# every call below passes tenant_id explicitly (same reason limit/offset need
# explicit values — see the Query(...) note in the R2 commit).

def test_list_tenders_hides_excluded_by_default(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(limit=100, offset=0, tenant_id=TEST_TENANT_ID)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"KEPT-1"}


def test_list_tenders_include_excluded_shows_everything(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(include_excluded=True, limit=100, offset=0, tenant_id=TEST_TENANT_ID)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"KEPT-1", "EXCLUDED-1"}


def test_get_tender_404s_on_excluded_by_default(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert api.get_tender("KEPT-1", tenant_id=TEST_TENANT_ID)["pub_number"] == "KEPT-1"
    try:
        api.get_tender("EXCLUDED-1", tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_get_tender_include_excluded_returns_the_record(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = api.get_tender("EXCLUDED-1", include_excluded=True, tenant_id=TEST_TENANT_ID)
    assert r["pub_number"] == "EXCLUDED-1"
    assert r["exclude_reason"] == "rental"


def test_tenant_isolation_at_the_api_layer(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    other_conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(other_conn, 999, _rec("OTHER-TENANT-1"))

    result = api.list_tenders(limit=100, offset=0, tenant_id=TEST_TENANT_ID)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert "OTHER-TENANT-1" not in pub_numbers


# ── Cross-tenant access (phase 2/3 step 6) — 403, not 404 ───────────────────

def test_get_tender_belonging_to_another_tenant_is_403(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    other_conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(other_conn, 999, _rec("OTHER-TENANT-1"))

    try:
        api.get_tender("OTHER-TENANT-1", tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_get_tender_that_truly_does_not_exist_is_still_404(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    try:
        api.get_tender("NEVER-EXISTED", tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_patch_tender_belonging_to_another_tenant_is_403(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    other_conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(other_conn, 999, _rec("OTHER-TENANT-1"))

    try:
        api.patch_tender("OTHER-TENANT-1", api.StatusBody(status="reviewed"), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


# ── CORS lockdown (phase 2/3 prep) ───────────────────────────────────────────

def test_parse_allowed_origins_splits_and_trims():
    assert api.parse_allowed_origins("http://a.com, http://b.com") == \
        ["http://a.com", "http://b.com"]


def test_parse_allowed_origins_drops_empties():
    assert api.parse_allowed_origins("http://a.com,,  ,http://b.com") == \
        ["http://a.com", "http://b.com"]


def test_parse_allowed_origins_defaults_when_unset():
    assert api.parse_allowed_origins(None) == ["http://localhost:5173"]


def test_parse_allowed_origins_single_value():
    assert api.parse_allowed_origins("https://app.tenderizer.example") == \
        ["https://app.tenderizer.example"]


# ── Per-tenant config (phase 2/3 step 5) ────────────────────────────────────

def test_get_cpv_config_reflects_tenant_customization(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.set_tenant_cpv(conn, TEST_TENANT_ID, ["39522530"])

    result = api.get_cpv_config(tenant_id=TEST_TENANT_ID)
    assert {r["code"] for r in result} == {"39522530"}


def test_put_cpv_config_only_affects_calling_tenant(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, 999)

    api.put_cpv_config(api.CpvBody(codes=["39522530"]), tenant_id=TEST_TENANT_ID)

    assert store.get_tenant_cpv(conn, TEST_TENANT_ID) == ["39522530"]
    assert set(store.get_tenant_cpv(conn, 999)) == set(config.cpv_codes())  # untouched


def test_keywords_config_round_trips_per_tenant(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, 999)

    api.put_keywords_config(api.KeywordsBody(distinctive=["widget"]), tenant_id=TEST_TENANT_ID)

    assert api.get_keywords_config(tenant_id=TEST_TENANT_ID)["distinctive"] == ["widget"]
    assert api.get_keywords_config(tenant_id=999)["distinctive"] != ["widget"]  # untouched
