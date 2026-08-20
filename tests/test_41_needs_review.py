"""CR-007 Phase B (B2) — "Needs further review" state.

A third org-shared appraisal state alongside new/reviewed/dismissed,
carrying a mandatory comment (same required-field pattern as CR-006's
dismiss reason) and, post-CR-007, an optional colleague assignment. Follows
test_36_dismissal_attribution.py's conventions.
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

import alerts
import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME_B, TEST_CLERK_USER_ID_B, make_identity


def _rec(pub_number, deadline="2030-01-01T00:00:00+00:00"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "exclude_reason": ""}


def _seed(tmp_path, monkeypatch, pub_number="PUB-1"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _rec(pub_number))
    return conn


def test_needs_review_without_a_comment_is_400(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="needs_review"), identity=make_identity())
    assert exc.value.status_code == 400


def test_needs_review_with_a_comment_succeeds_and_is_visible_to_the_whole_org(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Check budget with finance"),
                      identity=make_identity())

    rec = api.get_tender("PUB-1", identity=make_identity())
    assert rec["status"] == "needs_review"
    assert rec["dismissal_reason"] == "Check budget with finance"

    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    colleague_rec = api.get_tender("PUB-1", identity=colleague)
    assert colleague_rec["status"] == "needs_review"
    assert colleague_rec["dismissal_reason"] == "Check budget with finance"


def test_needs_review_does_not_require_a_reason_category(tmp_path, monkeypatch):
    """Unlike a dismiss, "needs review" has no B3 category — it's a parking
    comment, not a negative training signal.
    """
    _seed(tmp_path, monkeypatch)
    result = api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Ask the client"),
                               identity=make_identity())
    assert result["status"] == "needs_review"


def test_needs_review_shows_under_its_own_filter_not_the_plain_new_list(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch, "PUB-1")
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-2"))

    api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Ask the client"),
                      identity=make_identity())

    needs_review = api.list_tenders(status="needs_review", limit=100, offset=0,
                                     identity=make_identity())["results"]
    assert {r["pub_number"] for r in needs_review} == {"PUB-1"}

    plain_new = api.list_tenders(status="new", limit=100, offset=0, identity=make_identity())["results"]
    assert {r["pub_number"] for r in plain_new} == {"PUB-2"}


# Post-CR-007: optional colleague assignment + ping on a needs_review parking.

def test_needs_review_with_an_assignee_persists_and_pings_them(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(alerts, "send_tenant_email",
                         lambda to_addr, subject, body: captured.update(to=to_addr, subject=subject, body=body))
    background = BackgroundTasks()

    api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Check budget with finance",
                                              assigned_to="colleague@example.com"),
                      background, identity=make_identity())
    assert len(background.tasks) == 1
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)

    rec = api.get_tender("PUB-1", identity=make_identity())
    assert rec["status"] == "needs_review"
    assert rec["assigned_to"] == "colleague@example.com"
    assert captured["to"] == "colleague@example.com"
    assert "Check budget with finance" in captured["body"]
    assert "PUB-1" in captured["body"] or "Tent supply" in captured["body"]


def test_needs_review_without_an_assignee_does_not_ping(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Ask the client"),
                      background, identity=make_identity())
    assert len(background.tasks) == 0
    assert api.get_tender("PUB-1", identity=make_identity())["assigned_to"] is None


def test_assigned_to_is_ignored_for_a_non_needs_review_status(tmp_path, monkeypatch):
    """assigned_to is only meaningful alongside a needs_review parking — sent
    on any other status, it's silently dropped rather than 422ing, same
    tolerant-ignore behavior as `note` on a non-required status.
    """
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    api.patch_tender("PUB-1", api.StatusBody(status="reviewed", assigned_to="colleague@example.com"),
                      background, identity=make_identity())
    assert len(background.tasks) == 0
    assert api.get_tender("PUB-1", identity=make_identity())["assigned_to"] is None


def test_needs_review_rejects_an_invalid_assignee_email(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Ask the client",
                                                  assigned_to="not-an-email"),
                          BackgroundTasks(), identity=make_identity())
    assert exc.value.status_code == 422


def test_needs_review_assignment_is_visible_to_the_whole_org(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="needs_review", note="Ask the client",
                                              assigned_to="colleague@example.com"),
                      BackgroundTasks(), identity=make_identity())

    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    rec = api.get_tender("PUB-1", identity=colleague)
    assert rec["status"] == "needs_review"
    assert rec["assigned_to"] == "colleague@example.com"
