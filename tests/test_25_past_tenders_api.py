"""Step 25 — CR-002 A1/B1/B2: past_tender storage + API routing.

B1: past_tender notices never surface in the default /api/tenders view
(Tender Feed / Review Queue) — notice_type=past_tender is the explicit
opt-in for the Past Tenders page. B2: dashboard KPIs (/api/stats) count
active tenders only.
"""
import store
import api
from conftest import TEST_TENANT_ID


def _rec(pub_number, notice_type="tender", deadline="2030-01-01T00:00:00+00:00",
         awarded_to=None, awarded_value=None, awarded_currency=None, first_seen=None):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": ["39522530"], "matched_terms": ["tent"],
            "match_source": "cpv", "url": "http://x", "first_seen": first_seen,
            "exclude_reason": "", "notice_type": notice_type,
            "awarded_to": awarded_to, "awarded_value": awarded_value,
            "awarded_currency": awarded_currency}


def _seed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec("ACTIVE-1"))
    store.upsert(conn, TEST_TENANT_ID, _rec(
        "PAST-1", notice_type="past_tender", deadline="",
        awarded_to="Acme Shelters Ltd", awarded_value="350000", awarded_currency="EUR"))
    return conn


def test_upsert_defaults_notice_type_to_tender_when_absent(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    conn = store.init_db(db_path)
    rec = _rec("NO-TYPE-1")
    del rec["notice_type"]
    store.upsert(conn, TEST_TENANT_ID, rec)
    stored = next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == "NO-TYPE-1")
    assert stored["notice_type"] == "tender"


def test_upsert_stores_award_fields(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    stored = next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == "PAST-1")
    assert stored["notice_type"] == "past_tender"
    assert stored["awarded_to"] == "Acme Shelters Ltd"
    assert stored["awarded_value"] == "350000"
    assert stored["awarded_currency"] == "EUR"


def test_upsert_award_fields_null_when_absent(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec("ACTIVE-2"))
    stored = next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == "ACTIVE-2")
    assert stored["awarded_to"] is None
    assert stored["awarded_value"] is None
    assert stored["awarded_currency"] is None


def test_list_tenders_hides_past_tender_by_default(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(limit=100, offset=0, tenant_id=TEST_TENANT_ID)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"ACTIVE-1"}


def test_list_tenders_notice_type_past_tender_returns_only_those(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.list_tenders(notice_type="past_tender", limit=100, offset=0, tenant_id=TEST_TENANT_ID)
    pub_numbers = {r["pub_number"] for r in result["results"]}
    assert pub_numbers == {"PAST-1"}
    assert result["results"][0]["awarded_to"] == "Acme Shelters Ltd"


def test_stats_excludes_past_tenders_from_active_counts(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    stats = api.get_stats(tenant_id=TEST_TENANT_ID)
    assert stats["past_tenders"] == 1
    # by_match should only reflect ACTIVE-1 (cpv), not PAST-1
    assert stats["by_match"]["cpv"] == 1
    assert sum(stats["by_match"].values()) == 1


def test_stats_new_today_excludes_past_tenders(tmp_path, monkeypatch):
    import datetime
    today = datetime.date.today().isoformat()
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec("ACTIVE-3", first_seen=today))
    store.upsert(conn, TEST_TENANT_ID, _rec(
        "PAST-2", notice_type="past_tender", deadline="", first_seen=today))
    stats = api.get_stats(tenant_id=TEST_TENANT_ID)
    assert stats["new_today"] == 1


# ── update_classification: the notice_type backfill escape hatch ───────────
# (scratch_backfill_notice_type.py) for rows ingested before classification
# existed, stuck at notice_type's column default regardless of their actual
# deadline field.

def test_update_classification_reclassifies_a_stale_row(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    # Simulates a pre-CR-002 row: empty deadline, but notice_type stuck at
    # the column default "tender" since classify() never ran for it.
    store.upsert(conn, TEST_TENANT_ID, _rec("STALE-1", deadline=""))
    assert _get(conn, "STALE-1")["notice_type"] == "tender"

    store.update_classification(conn, TEST_TENANT_ID, "STALE-1", "past_tender",
                                 "Acme Shelters Ltd", "350000", "EUR")
    stored = _get(conn, "STALE-1")
    assert stored["notice_type"] == "past_tender"
    assert stored["awarded_to"] == "Acme Shelters Ltd"
    assert stored["awarded_value"] == "350000"
    assert stored["awarded_currency"] == "EUR"


def test_update_classification_clears_award_fields_back_to_null(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec(
        "STALE-2", notice_type="past_tender", deadline="",
        awarded_to="Old Corp", awarded_value="1", awarded_currency="EUR"))

    store.update_classification(conn, TEST_TENANT_ID, "STALE-2", "tender", None, None, None)
    stored = _get(conn, "STALE-2")
    assert stored["notice_type"] == "tender"
    assert stored["awarded_to"] is None
    assert stored["awarded_value"] is None
    assert stored["awarded_currency"] is None


def _get(conn, pub_number):
    return next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == pub_number)
