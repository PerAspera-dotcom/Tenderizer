"""Step 36 — CR-006: mandatory dismissal reason, attribution, Dismissed-tab
listing, and reinstatement.

Builds on test_23_dismiss_note.py's store-level dismissal_reason coverage;
this file covers the parts CR-006 actually added: the API rejecting an empty
reason (400), dismissed_by/dismissed_at being stamped server-side from the
authenticated identity (never client-suppliable), the Dismissed tab's list
query not losing past-deadline tenders, and reinstate leaving the dismissal
metadata in place as history.
"""
import pytest
from fastapi import HTTPException

import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME, make_identity


def _rec(pub_number, deadline="2030-01-01T00:00:00+00:00"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "exclude_reason": ""}


def _seed(tmp_path, monkeypatch, pub_number="PUB-1", deadline="2030-01-01T00:00:00+00:00"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec(pub_number, deadline))
    return conn


def _get(conn, pub_number):
    return next(r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == pub_number)


# ── D2: mandatory reason, server-side ────────────────────────────────────────

def test_dismiss_with_no_note_is_400(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="dismissed"), identity=make_identity())
    assert exc.value.status_code == 400


def test_dismiss_with_blank_note_is_400(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="   "),
                          identity=make_identity())
    assert exc.value.status_code == 400


def test_dismiss_with_blank_note_does_not_change_status(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note=""),
                          identity=make_identity())
    assert _get(conn, "PUB-1")["status"] == "new"


def test_dismiss_with_a_real_reason_succeeds(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Out of scope"),
                               identity=make_identity())
    assert result["status"] == "dismissed"


# ── D3: attribution — server-stamped, not client-suppliable ─────────────────

def test_dismiss_stamps_dismissed_by_from_identity_not_the_client(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    identity = make_identity(account_name="reviewer@example.com")
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong CPV"), identity=identity)
    rec = _get(conn, "PUB-1")
    assert rec["dismissed_by"] == "reviewer@example.com"


def test_dismiss_stamps_dismissed_at(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong CPV"),
                      identity=make_identity())
    assert _get(conn, "PUB-1")["dismissed_at"]


def test_non_dismiss_transition_does_not_stamp_attribution(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())
    rec = _get(conn, "PUB-1")
    assert rec["dismissed_by"] is None
    assert rec["dismissed_at"] is None


# ── D1: Dismissed-tab listing ────────────────────────────────────────────────

def test_dismissed_filter_returns_the_dismissal_metadata(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong sector"),
                      identity=make_identity())
    results = api.list_tenders(status="dismissed", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]
    assert len(results) == 1
    assert results[0]["dismissal_reason"] == "Wrong sector"
    assert results[0]["dismissed_by"] == TEST_ACCOUNT_NAME
    assert results[0]["dismissed_at"]


def test_dismissed_filter_still_includes_a_since_expired_deadline(tmp_path, monkeypatch):
    """A tender dismissed while still open, whose deadline has since passed,
    must not silently vanish from its own Dismissed tab — unlike every other
    view, which does hide expired deadlines by default (api.py's
    `if status != "dismissed"` guard around the expiry filter).
    """
    conn = _seed(tmp_path, monkeypatch, deadline="2020-01-01T00:00:00+00:00")
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Missed it"),
                      identity=make_identity())
    dismissed = api.list_tenders(status="dismissed", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]
    assert {r["pub_number"] for r in dismissed} == {"PUB-1"}

    # Sanity: the same expired-deadline tender is correctly hidden from a
    # non-dismissed-status view (the filter is status-gated, not removed).
    active = api.list_tenders(status="new", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]
    assert active == []


# ── D4: reinstate reuses set_status and keeps dismissal metadata as history ──

def test_reinstate_leaves_the_dismissed_tab_and_reappears_elsewhere(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed"),
                      identity=make_identity())
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    dismissed = api.list_tenders(status="dismissed", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]
    assert dismissed == []
    shortlisted = api.list_tenders(status="shortlisted", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]
    assert {r["pub_number"] for r in shortlisted} == {"PUB-1"}


def test_reinstate_keeps_the_prior_dismissal_metadata_as_history(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed"),
                      identity=make_identity())
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    rec = _get(conn, "PUB-1")
    assert rec["status"] == "shortlisted"
    assert rec["dismissal_reason"] == "Second look needed"
    assert rec["dismissed_by"] == TEST_ACCOUNT_NAME
    assert rec["dismissed_at"]
