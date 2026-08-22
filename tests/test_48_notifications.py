"""CR-008 W1/W2 — in-app notification feed for forwards + needs_review
assignee pings, and the row-level metadata (forwarded_to/last_notification_at)
that feeds off it. Follows test_47_forward_tender.py's conventions.
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, make_identity


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def _tender(pub_number="P-1"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": "new"}


# ── store.py primitives ──────────────────────────────────────────────────────

def test_create_and_list_notifications_for_recipient(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, "please look", "new")
    notes = store.get_notifications_for_recipient(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B)
    assert len(notes) == 1
    assert notes[0]["pub_number"] == "P-1"
    assert notes[0]["from_account_name"] == TEST_ACCOUNT_NAME
    assert notes[0]["message"] == "please look"
    assert notes[0]["status_at_send"] == "new"
    assert notes[0]["read_at"] is None


def test_notifications_are_scoped_to_their_recipient(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    assert store.get_notifications_for_recipient(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME) == []


def test_unread_count(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-2", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    assert store.get_unread_notification_count(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B) == 2


def test_mark_notification_read_clears_it_from_unread_count(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    [note] = store.get_notifications_for_recipient(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B)

    store.mark_notification_read(conn, TEST_TENANT_ID, note["id"], TEST_ACCOUNT_NAME_B)

    assert store.get_unread_notification_count(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B) == 0
    [note] = store.get_notifications_for_recipient(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B)
    assert note["read_at"] is not None


def test_mark_notification_read_is_a_noop_for_the_wrong_recipient(tmp_path, monkeypatch):
    # Ownership check: TEST_ACCOUNT_NAME didn't receive this notification,
    # so their attempt to mark it read must not affect the real recipient's.
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    [note] = store.get_notifications_for_recipient(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B)

    store.mark_notification_read(conn, TEST_TENANT_ID, note["id"], TEST_ACCOUNT_NAME)

    assert store.get_unread_notification_count(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME_B) == 1


def test_last_notification_times_and_forwarded_to(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, None, "new")
    times = store.get_last_notification_times(conn, TEST_TENANT_ID)
    forwarded = store.get_last_forwarded_to(conn, TEST_TENANT_ID)
    assert "P-1" in times
    assert forwarded["P-1"] == TEST_ACCOUNT_NAME_B


def test_forwarded_to_reflects_the_most_recent_forward(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, "first@example.com", None, "new")
    store.create_tender_notification(conn, TEST_TENANT_ID, "P-1", "forward",
                                      TEST_ACCOUNT_NAME, "second@example.com", None, "new")
    assert store.get_last_forwarded_to(conn, TEST_TENANT_ID)["P-1"] == "second@example.com"


# ── api.py wiring ────────────────────────────────────────────────────────────

def test_forward_endpoint_writes_an_in_app_notification(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    api.forward_tender("P-1", api.ForwardBody(to_email=TEST_ACCOUNT_NAME_B, message="hi"),
                        BackgroundTasks(), identity=make_identity())

    recipient = make_identity(account_name=TEST_ACCOUNT_NAME_B)
    result = api.get_notifications(identity=recipient)
    assert result["unread_count"] == 1
    assert result["notifications"][0]["pub_number"] == "P-1"
    assert result["notifications"][0]["kind"] == "forward"
    assert result["notifications"][0]["message"] == "hi"


def test_needs_review_ping_writes_an_in_app_notification(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    api.patch_tender("P-1", api.StatusBody(status="needs_review", note="check this",
                                            assigned_to=TEST_ACCOUNT_NAME_B),
                      BackgroundTasks(), identity=make_identity())

    recipient = make_identity(account_name=TEST_ACCOUNT_NAME_B)
    result = api.get_notifications(identity=recipient)
    assert result["unread_count"] == 1
    assert result["notifications"][0]["kind"] == "needs_review_ping"
    assert result["notifications"][0]["status_at_send"] == "needs_review"


def test_needs_review_ping_notification_is_written_even_without_background_tasks(tmp_path, monkeypatch):
    # patch_tender's `background` param defaults to None for direct-call
    # test sites (see its own docstring) — the DB write must not depend on it.
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    api.patch_tender("P-1", api.StatusBody(status="needs_review", note="check this",
                                            assigned_to=TEST_ACCOUNT_NAME_B),
                      identity=make_identity())

    recipient = make_identity(account_name=TEST_ACCOUNT_NAME_B)
    assert api.get_notifications(identity=recipient)["unread_count"] == 1


def test_mark_notification_read_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    api.forward_tender("P-1", api.ForwardBody(to_email=TEST_ACCOUNT_NAME_B),
                        BackgroundTasks(), identity=make_identity())

    recipient = make_identity(account_name=TEST_ACCOUNT_NAME_B)
    note_id = api.get_notifications(identity=recipient)["notifications"][0]["id"]
    api.mark_notification_read(note_id, identity=recipient)

    assert api.get_notifications(identity=recipient)["unread_count"] == 0


def test_list_and_get_tender_expose_forwarded_to_and_last_notification_at(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    api.forward_tender("P-1", api.ForwardBody(to_email=TEST_ACCOUNT_NAME_B),
                        BackgroundTasks(), identity=make_identity())

    listed = api.list_tenders(limit=100, offset=0, identity=make_identity())["results"]
    row = next(r for r in listed if r["pub_number"] == "P-1")
    assert row["forwarded_to"] == TEST_ACCOUNT_NAME_B
    assert row["last_notification_at"] is not None

    detail = api.get_tender("P-1", identity=make_identity())
    assert detail["forwarded_to"] == TEST_ACCOUNT_NAME_B
    assert detail["last_notification_at"] is not None


def test_tender_never_forwarded_has_no_notification_metadata(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    row = api.get_tender("P-1", identity=make_identity())
    assert row["forwarded_to"] is None
    assert row["last_notification_at"] is None


# ── CR-008 W5: CPV labels ────────────────────────────────────────────────────

def test_cpv_labels_endpoint_returns_known_codes():
    result = api.get_cpv_labels(codes="39522530")
    assert "39522530" in result
    assert result["39522530"]["en"]


def test_cpv_labels_endpoint_omits_unknown_codes():
    result = api.get_cpv_labels(codes="39522530,00000000")
    assert "39522530" in result
    assert "00000000" not in result
