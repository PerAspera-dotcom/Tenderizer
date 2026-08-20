"""Tenancy hardening: tender_history — the pipeline_history pattern (see
test_22_pipeline.py's "Tenancy hardening" section) extended to Scout tenders.
Every status transition previously had zero history unless it happened to be
a dismiss (which only ever populated dismissed_by/dismissed_at/dismissal_
reason — see test_36_dismissal_attribution.py). This file confirms the new
event log is additive to that CR-006 behaviour, not a replacement of it.

Post-CR-007 update: every status transition (not just new<->shortlisted)
writes the shared `tenders` row via store.set_status now — client feedback
reversed CR-007 Phase A's personal-per-account split (see schema.py's
tender_reviews retirement comment) — so "reviewed"/"dismissed"/etc. log here
too. See test_36_dismissal_attribution.py for that layer's own coverage.
"""
import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME, make_identity

OTHER_TENANT_ID = 999


def _tender(pub_number, status="new"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": status}


def _seed(tmp_path, monkeypatch, pub_number="P-1", status="new"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _tender(pub_number, status=status))
    return conn


def test_status_transition_is_logged_with_old_and_new_value(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))

    store.set_status(conn, TEST_TENANT_ID, "P-1", "reviewed")

    history = store.get_tender_history(conn, TEST_TENANT_ID, "P-1")
    assert len(history) == 1
    assert history[0]["field"] == "status"
    assert history[0]["old_value"] == "new"
    assert history[0]["new_value"] == "reviewed"
    assert history[0]["changed_at"]


def test_no_op_status_set_is_not_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1", status="reviewed"))

    store.set_status(conn, TEST_TENANT_ID, "P-1", "reviewed")  # already reviewed

    assert store.get_tender_history(conn, TEST_TENANT_ID, "P-1") == []


def test_multiple_transitions_are_ordered_newest_first(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))

    store.set_status(conn, TEST_TENANT_ID, "P-1", "reviewed")
    store.set_status(conn, TEST_TENANT_ID, "P-1", "shortlisted")
    store.set_status(conn, TEST_TENANT_ID, "P-1", "dismissed")

    history = store.get_tender_history(conn, TEST_TENANT_ID, "P-1")
    assert len(history) == 3
    assert [h["new_value"] for h in history] == ["dismissed", "shortlisted", "reviewed"]
    assert [h["old_value"] for h in history] == ["shortlisted", "reviewed", "new"]


def test_tender_history_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))

    store.set_status(conn, TEST_TENANT_ID, "P-1", "reviewed")
    store.set_status(conn, OTHER_TENANT_ID, "P-1", "shortlisted")

    assert [h["new_value"] for h in store.get_tender_history(conn, TEST_TENANT_ID, "P-1")] == ["reviewed"]
    assert [h["new_value"] for h in store.get_tender_history(conn, OTHER_TENANT_ID, "P-1")] == ["shortlisted"]


def test_dismiss_via_api_is_shared_and_logged_in_the_tender_history(tmp_path, monkeypatch):
    """Post-CR-007: dismiss writes the shared `tenders` row directly (see
    test_36_dismissal_attribution.py for that layer's own coverage), and
    like any other status change, that transition is logged here too.
    """
    conn = _seed(tmp_path, monkeypatch)

    api.patch_tender("P-1", api.StatusBody(status="dismissed", note="Second look needed",
                                            reason_category="other"),
                      identity=make_identity())

    history = store.get_tender_history(conn, TEST_TENANT_ID, "P-1")
    assert len(history) == 1
    assert history[0]["old_value"] == "new"
    assert history[0]["new_value"] == "dismissed"
    rec = next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == "P-1")
    assert rec["status"] == "dismissed"
    assert rec["dismissal_reason"] == "Second look needed"
    assert rec["dismissed_by"] == TEST_ACCOUNT_NAME


def test_get_tender_history_endpoint_round_trips(tmp_path, monkeypatch):
    """Post-CR-007: "reviewed" now writes the shared row too, so both
    transitions are logged (newest first).
    """
    _seed(tmp_path, monkeypatch)

    api.patch_tender("P-1", api.StatusBody(status="reviewed"), identity=make_identity())
    api.patch_tender("P-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    result = api.get_tender_history("P-1", tenant_id=TEST_TENANT_ID)
    assert result["pub_number"] == "P-1"
    assert len(result["history"]) == 2
    assert result["history"][0]["old_value"] == "reviewed"
    assert result["history"][0]["new_value"] == "shortlisted"
    assert result["history"][1]["old_value"] == "new"
    assert result["history"][1]["new_value"] == "reviewed"
