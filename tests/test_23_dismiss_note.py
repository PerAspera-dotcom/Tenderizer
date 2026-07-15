"""Step 23 — CR-002 C2: optional note on dismiss.

dismiss_note is additive (schema.py) and NULL by default (store.upsert's
_NULL_DEFAULT) — "no note" must stay distinguishable from "", and a note is
only ever written alongside the dismiss action itself (store.set_status),
never as a standalone field.
"""
import store
import api
from conftest import TEST_TENANT_ID


def _rec(pub_number):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "exclude_reason": ""}


def _seed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-1"))
    return conn


def _get(conn, pub_number):
    return next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == pub_number)


def test_fresh_ingest_has_no_dismiss_note(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    assert _get(conn, "PUB-1")["dismiss_note"] is None


def test_set_status_dismissed_with_note_persists_it(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed", dismiss_note="Wrong sector, mislabelled CPV")
    rec = _get(conn, "PUB-1")
    assert rec["status"] == "dismissed"
    assert rec["dismiss_note"] == "Wrong sector, mislabelled CPV"


def test_set_status_dismissed_without_note_leaves_it_null(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed")
    assert _get(conn, "PUB-1")["dismiss_note"] is None


def test_set_status_none_note_does_not_clear_an_existing_note(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed", dismiss_note="Duplicate of another notice")
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "shortlisted")  # no note arg -> untouched
    assert _get(conn, "PUB-1")["dismiss_note"] == "Duplicate of another notice"


def test_api_patch_dismissed_with_note(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Out of scope"),
                               tenant_id=TEST_TENANT_ID)
    assert result["status"] == "dismissed"
    rec = api.get_tender("PUB-1", include_excluded=True, tenant_id=TEST_TENANT_ID)
    assert rec["dismiss_note"] == "Out of scope"


def test_api_patch_note_ignored_on_non_dismiss_status(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="reviewed", note="should not be stored"),
                      tenant_id=TEST_TENANT_ID)
    rec = api.get_tender("PUB-1", include_excluded=True, tenant_id=TEST_TENANT_ID)
    assert rec["dismiss_note"] is None
