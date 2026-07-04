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
import store
import api


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
    store.upsert(conn, _rec("KEPT-1"))
    store.upsert(conn, _rec("EXCLUDED-1", exclude_reason="rental"))
    return conn


def test_list_tenders_hides_excluded_by_default(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(limit=100, offset=0)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"KEPT-1"}


def test_list_tenders_include_excluded_shows_everything(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(include_excluded=True, limit=100, offset=0)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"KEPT-1", "EXCLUDED-1"}


def test_get_tender_404s_on_excluded_by_default(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert api.get_tender("KEPT-1")["pub_number"] == "KEPT-1"
    try:
        api.get_tender("EXCLUDED-1")
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_get_tender_include_excluded_returns_the_record(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = api.get_tender("EXCLUDED-1", include_excluded=True)
    assert r["pub_number"] == "EXCLUDED-1"
    assert r["exclude_reason"] == "rental"


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
