"""CR-007 Phase G (G1) — account-to-account forward/reminder. Rides the
existing alerts.send_tenant_email primitive (test_32_alerts.py / the
owner-handoff email in test_22_pipeline.py), not a new notification
subsystem, per the CR's own instruction.
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

import alerts
import store
import api
from conftest import TEST_TENANT_ID, make_identity


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


def test_send_forward_email_includes_sender_tender_and_message(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    captured = {}
    monkeypatch.setattr(alerts, "send_tenant_email",
                         lambda to_addr, subject, body: captured.update(to=to_addr, subject=subject, body=body))

    api._send_forward_email(TEST_TENANT_ID, "P-1", "colleague@example.com",
                             "please take a look", "reviewer@example.com")

    assert captured["to"] == "colleague@example.com"
    assert "reviewer@example.com" in captured["subject"]
    assert "Tent supply" in captured["body"]
    assert "P-1" in captured["body"]
    assert "please take a look" in captured["body"]


def test_send_forward_email_without_a_message_still_sends(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    captured = {}
    monkeypatch.setattr(alerts, "send_tenant_email",
                         lambda to_addr, subject, body: captured.update(to=to_addr, body=body))

    api._send_forward_email(TEST_TENANT_ID, "P-1", "colleague@example.com", None, "reviewer@example.com")
    assert captured["to"] == "colleague@example.com"
    assert "Tent supply" in captured["body"]


def test_send_forward_email_falls_back_to_pub_number_for_an_unknown_tender(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(alerts, "send_tenant_email",
                         lambda to_addr, subject, body: captured.update(body=body))
    api._send_forward_email(TEST_TENANT_ID, "GONE-1", "colleague@example.com", None, "reviewer@example.com")
    assert "GONE-1" in captured["body"]


def test_forward_endpoint_enqueues_the_background_email(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    background = BackgroundTasks()

    result = api.forward_tender("P-1", api.ForwardBody(to_email="colleague@example.com", message="hi"),
                                 background, identity=make_identity())
    assert result == {"sent": True}
    assert len(background.tasks) == 1


def test_forward_endpoint_rejects_an_invalid_email(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    with pytest.raises(HTTPException) as exc:
        api.forward_tender("P-1", api.ForwardBody(to_email="not-an-email"),
                            BackgroundTasks(), identity=make_identity())
    assert exc.value.status_code == 422


def test_forward_endpoint_404s_for_an_unknown_tender(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.forward_tender("NOPE", api.ForwardBody(to_email="colleague@example.com"),
                            BackgroundTasks(), identity=make_identity())
    assert exc.value.status_code == 404


def test_forward_endpoint_403s_for_another_tenants_tender(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    other_conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(other_conn, 999, _tender("OTHER-1"))
    with pytest.raises(HTTPException) as exc:
        api.forward_tender("OTHER-1", api.ForwardBody(to_email="colleague@example.com"),
                            BackgroundTasks(), identity=make_identity())
    assert exc.value.status_code == 403
