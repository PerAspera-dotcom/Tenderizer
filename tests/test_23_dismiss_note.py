"""Step 23 — CR-002 C2's dismissal note, since CR-006 renamed to dismissal_reason.

dismissal_reason is additive (schema.py) and NULL by default (store.upsert's
_NULL_DEFAULT) — "no reason" must stay distinguishable from "", and a reason
is only ever written alongside the dismiss action itself (store.set_status),
never as a standalone field. CR-006 makes the reason mandatory through the
API layer — see test_36_dismissal_attribution.py for that, plus attribution
and reinstate coverage. This file stays focused on the store-level
read/write/persist-across-transitions behavior, mirroring its original scope.
"""
import store
import api
from conftest import TEST_TENANT_ID, make_identity


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


def test_fresh_ingest_has_no_dismissal_reason(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    assert _get(conn, "PUB-1")["dismissal_reason"] is None


def test_set_status_dismissed_with_reason_persists_it(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed",
                      dismissal_reason="Wrong sector, mislabelled CPV")
    rec = _get(conn, "PUB-1")
    assert rec["status"] == "dismissed"
    assert rec["dismissal_reason"] == "Wrong sector, mislabelled CPV"


def test_set_status_dismissed_without_reason_leaves_it_null(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed")
    assert _get(conn, "PUB-1")["dismissal_reason"] is None


def test_set_status_none_reason_does_not_clear_an_existing_reason(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "dismissed",
                      dismissal_reason="Duplicate of another notice")
    store.set_status(conn, TEST_TENANT_ID, "PUB-1", "shortlisted")  # no reason arg -> untouched
    assert _get(conn, "PUB-1")["dismissal_reason"] == "Duplicate of another notice"


def test_api_patch_dismissed_with_reason(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.patch_tender(
        "PUB-1", api.StatusBody(status="dismissed", note="Out of scope", reason_category="other"),
        identity=make_identity())
    assert result["status"] == "dismissed"
    rec = api.get_tender("PUB-1", include_excluded=True, identity=make_identity())
    assert rec["dismissal_reason"] == "Out of scope"


def test_api_patch_note_ignored_on_non_dismiss_status(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="reviewed", note="should not be stored"),
                      identity=make_identity())
    rec = api.get_tender("PUB-1", include_excluded=True, identity=make_identity())
    assert rec["dismissal_reason"] is None
