"""Step 57 - CR-012 S3: generic email-alert ingestion adapter.

email_alerts.py: sender recognition (SOURCE_DOMAINS) and the parser
registry — empty in production (see its own module docstring on why no
sender-specific parser ships in this build), exercised here via a
test-only fake parser registered through monkeypatch, never a real one.

store.py: email_alerts CRUD + last_email_alert_received_at (the "flag if
nothing arrives in N days" health check's own data source).

api.py: tenant resolution from the inbound address's plus-tag, the webhook
handler (called directly as a plain function per this repo's established
TestClient-free convention — see test_17_api.py's own docstring), the
email-alert health entries, and their inclusion in GET /api/health.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import api
import email_alerts
import store
from conftest import TEST_TENANT_ID


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


# -- email_alerts.py: sender recognition --------------------------------------

def test_recognize_source_exact_domain():
    assert email_alerts.recognize_source("noreply@tenders.gov.au") == "AusTender"
    assert email_alerts.recognize_source("alerts@gebiz.gov.sg") == "GeBIZ"
    assert email_alerts.recognize_source("notify@etimad.sa") == "Etimad"
    assert email_alerts.recognize_source("system@eperolehan.gov.my") == "ePerolehan"


def test_recognize_source_subdomain():
    assert email_alerts.recognize_source("noreply@notify.tenders.gov.au") == "AusTender"


def test_recognize_source_rejects_lookalike_domain():
    # a domain that merely CONTAINS the real one as a substring, not a true
    # suffix match, must not be recognized
    assert email_alerts.recognize_source("evil@fake-tenders.gov.au.evil.example") is None


def test_recognize_source_unknown_sender_is_none():
    assert email_alerts.recognize_source("someone@example.com") is None


def test_recognize_source_malformed_address_is_none():
    assert email_alerts.recognize_source("not-an-email") is None
    assert email_alerts.recognize_source("") is None
    assert email_alerts.recognize_source(None) is None


# -- email_alerts.py: parser registry -----------------------------------------

def test_parse_email_no_parser_registered_returns_empty_records():
    source, records, error = email_alerts.parse_email("noreply@tenders.gov.au", "Subject", "text", "<p>html</p>")
    assert source == "AusTender"
    assert records == []
    assert error is None


def test_parse_email_unrecognized_sender_returns_none_source():
    source, records, error = email_alerts.parse_email("someone@example.com", "Subject", "text", "html")
    assert source is None
    assert records == []
    assert error is None


def test_parse_email_with_registered_parser_returns_records(monkeypatch):
    def fake_parser(from_address, subject, text, html):
        return [{"source": "AusTender", "pub_number": "ABC-1", "tag_line": "Test tender"}]
    monkeypatch.setitem(email_alerts.PARSER_REGISTRY, "AusTender", fake_parser)
    source, records, error = email_alerts.parse_email("noreply@tenders.gov.au", "New opportunity", "text", "html")
    assert source == "AusTender"
    assert len(records) == 1
    assert records[0]["pub_number"] == "ABC-1"
    assert error is None


def test_parse_email_parser_exception_is_caught(monkeypatch):
    def broken_parser(from_address, subject, text, html):
        raise ValueError("malformed alert body")
    monkeypatch.setitem(email_alerts.PARSER_REGISTRY, "AusTender", broken_parser)
    source, records, error = email_alerts.parse_email("noreply@tenders.gov.au", "Subject", "text", "html")
    assert source == "AusTender"
    assert records == []
    assert "malformed alert body" in error


# -- store.py: email_alerts CRUD ----------------------------------------------

def test_create_and_list_email_alerts(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "noreply@tenders.gov.au",
                              "alerts+t1@parse.tender-izer.com", "New opportunity", "body text", "<p>body</p>",
                              "unparsed", None, 0)
    rows = store.list_email_alerts(conn, TEST_TENANT_ID)
    assert len(rows) == 1
    assert rows[0]["source"] == "AusTender"
    assert rows[0]["parse_status"] == "unparsed"


def test_list_email_alerts_newest_first(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "a@tenders.gov.au", "to", "First", "", "", "unparsed", None, 0)
    store.create_email_alert(conn, TEST_TENANT_ID, "GeBIZ", "a@gebiz.gov.sg", "to", "Second", "", "", "unparsed", None, 0)
    rows = store.list_email_alerts(conn, TEST_TENANT_ID)
    assert [r["subject"] for r in rows] == ["Second", "First"]


def test_list_email_alerts_is_tenant_scoped(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "a@tenders.gov.au", "to", "Mine", "", "", "unparsed", None, 0)
    store.create_email_alert(conn, 999, "AusTender", "a@tenders.gov.au", "to", "Theirs", "", "", "unparsed", None, 0)
    rows = store.list_email_alerts(conn, TEST_TENANT_ID)
    assert [r["subject"] for r in rows] == ["Mine"]


def test_last_email_alert_received_at_none_when_never(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    assert store.last_email_alert_received_at(conn, TEST_TENANT_ID, "AusTender") is None


def test_last_email_alert_received_at_returns_most_recent(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "a@tenders.gov.au", "to", "s", "", "", "unparsed", None, 0)
    last = store.last_email_alert_received_at(conn, TEST_TENANT_ID, "AusTender")
    assert last is not None
    assert last.startswith(datetime.now(timezone.utc).date().isoformat())


# -- api.py: tenant resolution from the inbound address -----------------------

def test_resolve_tenant_from_plain_address():
    assert api._resolve_tenant_from_inbound_address("alerts+t5@parse.tender-izer.com") == 5


def test_resolve_tenant_from_address_with_display_name():
    assert api._resolve_tenant_from_inbound_address('"Alerts" <alerts+t5@parse.tender-izer.com>') == 5


def test_resolve_tenant_from_multiple_comma_separated_addresses():
    addr = "other@example.com, alerts+t7@parse.tender-izer.com"
    assert api._resolve_tenant_from_inbound_address(addr) == 7


def test_resolve_tenant_no_match_returns_none():
    assert api._resolve_tenant_from_inbound_address("someone@example.com") is None
    assert api._resolve_tenant_from_inbound_address("") is None


# -- api.py: the webhook handler itself ---------------------------------------

def test_process_inbound_alert_unrecognized_sender_logs_as_unknown(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    result = api._process_inbound_email_alert(
        "someone@example.com", "alerts+t1@parse.tender-izer.com", "Subject", "text", "html")
    assert result == {"status": "received"}
    rows = store.list_email_alerts(conn, 1)
    assert len(rows) == 1
    assert rows[0]["source"] == "unknown"
    assert rows[0]["parse_status"] == "unparsed"
    assert rows[0]["records_created"] == 0


def test_process_inbound_alert_recognized_sender_no_parser_yet(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    api._process_inbound_email_alert(
        "noreply@tenders.gov.au", "alerts+t1@parse.tender-izer.com", "New opportunity", "text", "html")
    rows = store.list_email_alerts(conn, 1)
    assert rows[0]["source"] == "AusTender"
    assert rows[0]["parse_status"] == "unparsed"  # confirmed real, expected state — see module docstring


def test_process_inbound_alert_unroutable_to_address_still_logged(tmp_path, monkeypatch):
    # an unroutable `to` (no alerts+t<id> tag) must not be silently dropped —
    # still logged, with tenant_id left NULL rather than guessed.
    conn = _db(tmp_path, monkeypatch)
    api._process_inbound_email_alert(
        "noreply@tenders.gov.au", "nobody@parse.tender-izer.com", "Subject", "text", "html")
    with conn.connect() as c:
        from schema import email_alerts as email_alerts_table
        from sqlalchemy import select
        all_rows = c.execute(select(email_alerts_table.c.tenant_id, email_alerts_table.c.source)).fetchall()
    assert len(all_rows) == 1
    assert all_rows[0][0] is None
    assert all_rows[0][1] == "AusTender"


def test_process_inbound_alert_with_registered_parser_creates_tender(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.ensure_tenant(conn, TEST_TENANT_ID)
    monkeypatch.setattr(api.currency, "fetch_ecb_rates_or_fallback", lambda: {})

    def fake_parser(from_address, subject, text, html):
        return [{
            "source": "AusTender", "pub_number": "ABC-1", "tag_line": "Supply of tents",
            "description": "desc", "buyer": "Dept of X", "country": "AUS", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-10-01",
            "deadline": "2030-01-01", "cpv_codes": [], "matched_terms": [], "match_source": None,
            "url": "https://tenders.gov.au/x", "first_seen": None, "internal_identifier": None,
            "value": "", "value_currency": "", "language": "eng",
            "raw_award_winner": None, "raw_award_value": None, "raw_award_currency": None,
            "raw_award_detail": None,
        }]
    monkeypatch.setitem(email_alerts.PARSER_REGISTRY, "AusTender", fake_parser)

    result = api._process_inbound_email_alert(
        "noreply@tenders.gov.au", f"alerts+t{TEST_TENANT_ID}@parse.tender-izer.com",
        "New opportunity", "text", "html")
    assert result == {"status": "received"}

    records = store.all_records(conn, TEST_TENANT_ID)
    assert any(r["pub_number"] == "ABC-1" for r in records)

    rows = store.list_email_alerts(conn, TEST_TENANT_ID)
    assert rows[0]["parse_status"] == "parsed"
    assert rows[0]["records_created"] == 1


def test_inbound_webhook_rejects_wrong_secret(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setenv("EMAIL_ALERTS_WEBHOOK_SECRET", "correct-secret")

    class _FakeRequest:
        async def form(self):
            return {"from": "noreply@tenders.gov.au", "to": "alerts+t1@parse.tender-izer.com",
                     "subject": "s", "text": "t", "html": "h"}

    import pytest
    with pytest.raises(Exception) as exc:
        asyncio.run(api.inbound_email_alert("wrong-secret", _FakeRequest()))
    assert getattr(exc.value, "status_code", None) == 404


def test_inbound_webhook_accepts_correct_secret(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    monkeypatch.setenv("EMAIL_ALERTS_WEBHOOK_SECRET", "correct-secret")

    class _FakeRequest:
        async def form(self):
            return {"from": "noreply@tenders.gov.au", "to": "alerts+t1@parse.tender-izer.com",
                     "subject": "s", "text": "t", "html": "h"}

    result = asyncio.run(api.inbound_email_alert("correct-secret", _FakeRequest()))
    assert result == {"status": "received"}
    assert len(store.list_email_alerts(conn, 1)) == 1


# -- api.py: health -------------------------------------------------------------

def test_email_alert_health_paused_when_never_received(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    entries = api._email_alert_health(conn, TEST_TENANT_ID)
    names = {e["name"] for e in entries}
    assert names == {"AusTender", "GeBIZ", "Etimad", "ePerolehan"}
    assert all(e["status"] == "paused" for e in entries)


def test_email_alert_health_live_when_recent(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "a@tenders.gov.au", "to", "s", "", "",
                              "unparsed", None, 0)
    entries = api._email_alert_health(conn, TEST_TENANT_ID)
    austender = next(e for e in entries if e["name"] == "AusTender")
    assert austender["status"] == "live"


def test_email_alert_health_failing_when_silent_too_long(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.create_email_alert(conn, TEST_TENANT_ID, "AusTender", "a@tenders.gov.au", "to", "s", "", "",
                              "unparsed", None, 0)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=api.EMAIL_ALERT_SILENCE_DAYS + 1)).isoformat()
    with conn.begin() as c:
        from schema import email_alerts as email_alerts_table
        from sqlalchemy import update as sa_update
        c.execute(sa_update(email_alerts_table).where(email_alerts_table.c.tenant_id == TEST_TENANT_ID)
                  .values(received_at=old_ts))
    entries = api._email_alert_health(conn, TEST_TENANT_ID)
    austender = next(e for e in entries if e["name"] == "AusTender")
    assert austender["status"] == "failing"


def test_health_endpoint_includes_email_alert_sources(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    names = {e["name"] for e in result}
    assert {"AusTender", "GeBIZ", "Etimad", "ePerolehan"}.issubset(names)
