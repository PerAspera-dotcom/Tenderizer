"""Notifications & workflow — daily digest (digest.build_daily_digest) and
the scheduled job that sends it (api._run_daily_digest). Same direct-call
pattern as the rest of the suite; digest.py has no side effects of its own
(pure string-building), so the store-layer tests here don't need any
SMTP/alerts mocking — that only matters for the api.py-layer tests below.
"""
from datetime import date, timedelta

import store
import digest
import api
from conftest import TEST_TENANT_ID

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _tender(pub_number, status="new", first_seen=TODAY, deadline=None):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline or "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": first_seen, "status": status}


def _in_days(n):
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) + timedelta(days=n)).date().isoformat()


# ── digest.build_daily_digest ────────────────────────────────────────────────

def test_build_daily_digest_returns_none_with_nothing_to_report(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY) is None


def test_build_daily_digest_includes_tender_first_seen_today(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1", first_seen=TODAY))
    body = digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY)
    assert body is not None
    assert "P-1" in body and "New matches today" in body


def test_build_daily_digest_excludes_tender_first_seen_earlier(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-OLD", first_seen=YESTERDAY))
    assert digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY) is None


def test_build_daily_digest_includes_pipeline_entry_closing_soon(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1", status="shortlisted", first_seen=YESTERDAY,
                                                deadline=_in_days(5) + "T00:00:00+00:00"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    body = digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY)
    assert body is not None
    assert "Closing soon" in body and "P-1" in body


def test_build_daily_digest_excludes_entry_more_than_14_days_out(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-FAR", status="shortlisted", first_seen=YESTERDAY,
                                                deadline=_in_days(20) + "T00:00:00+00:00"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-FAR")
    assert digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY) is None


def test_build_daily_digest_excludes_submitted_entries(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-SUB", status="shortlisted", first_seen=YESTERDAY,
                                                deadline=_in_days(3) + "T00:00:00+00:00"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-SUB")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-SUB", {"submission_status": "submitted"})
    assert digest.build_daily_digest(conn, TEST_TENANT_ID, today=TODAY) is None


# ── api._run_daily_digest ─────────────────────────────────────────────────────

def _seed_provisioned(tmp_path, monkeypatch, notify_on_complete=True, notify_email="a@b.com"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    tenant_id = store.create_tenant_for_clerk_user(conn, "user_1")
    store.set_tenant_settings(conn, tenant_id,
                               {"notify_on_complete": notify_on_complete, "notify_email": notify_email})
    return conn, tenant_id


def test_run_daily_digest_sends_for_qualifying_tenant(tmp_path, monkeypatch):
    conn, tenant_id = _seed_provisioned(tmp_path, monkeypatch)
    store.upsert(conn, tenant_id, _tender("P-1", first_seen=TODAY))
    import alerts
    captured = []
    monkeypatch.setattr(alerts, "send_tenant_email", lambda to_addr, subject, body: captured.append((to_addr, subject)))

    api._run_daily_digest()

    assert captured == [("a@b.com", "Tenderizer — daily digest")]


def test_run_daily_digest_skips_tenant_with_notify_off(tmp_path, monkeypatch):
    conn, tenant_id = _seed_provisioned(tmp_path, monkeypatch, notify_on_complete=False)
    store.upsert(conn, tenant_id, _tender("P-1", first_seen=TODAY))
    import alerts
    captured = []
    monkeypatch.setattr(alerts, "send_tenant_email", lambda *a, **kw: captured.append(a))

    api._run_daily_digest()

    assert captured == []


def test_run_daily_digest_skips_tenant_without_notify_email(tmp_path, monkeypatch):
    conn, tenant_id = _seed_provisioned(tmp_path, monkeypatch, notify_email="")
    store.upsert(conn, tenant_id, _tender("P-1", first_seen=TODAY))
    import alerts
    captured = []
    monkeypatch.setattr(alerts, "send_tenant_email", lambda *a, **kw: captured.append(a))

    api._run_daily_digest()

    assert captured == []


def test_run_daily_digest_skips_empty_digest(tmp_path, monkeypatch):
    conn, tenant_id = _seed_provisioned(tmp_path, monkeypatch)  # nothing to report
    import alerts
    captured = []
    monkeypatch.setattr(alerts, "send_tenant_email", lambda *a, **kw: captured.append(a))

    api._run_daily_digest()

    assert captured == []


def test_run_daily_digest_one_tenant_failure_does_not_stop_others(tmp_path, monkeypatch):
    conn, tenant_id = _seed_provisioned(tmp_path, monkeypatch)
    store.upsert(conn, tenant_id, _tender("P-1", first_seen=TODAY))
    other_id = store.create_tenant_for_clerk_user(conn, "user_2")
    store.set_tenant_settings(conn, other_id, {"notify_on_complete": True, "notify_email": "c@d.com"})
    store.upsert(conn, other_id, _tender("P-2", first_seen=TODAY))

    import digest as digest_mod
    calls = []

    def _flaky_build(conn, tid, today=None):
        calls.append(tid)
        if tid == tenant_id:
            raise RuntimeError("boom")
        return "some digest body"

    monkeypatch.setattr(digest_mod, "build_daily_digest", _flaky_build)
    import alerts
    captured = []
    monkeypatch.setattr(alerts, "send_tenant_email", lambda to_addr, subject, body: captured.append(to_addr))

    api._run_daily_digest()

    assert set(calls) == {tenant_id, other_id}
    assert captured == ["c@d.com"]
