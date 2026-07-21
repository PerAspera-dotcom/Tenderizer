"""CR-004 F4 — source_health logging (store.record_source_health /
store.get_source_health) and run.run_pipeline's integration with it.
"""
from datetime import datetime, timezone

import api
import store
import run
from conftest import TEST_TENANT_ID

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0, "SEK": 11.23}}


def _src_ok(raw_ted_supply):
    return {"name": "TED", "fetch": lambda: [raw_ted_supply],
            "normalize": __import__("normalize").normalize_ted}


def _src_boom():
    def boom(): raise RuntimeError("portal down")
    return {"name": "BROKEN", "fetch": boom, "normalize": lambda r: r}


def test_record_source_health_ok_then_failed_resets_streak(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-01", "ok", notices_pulled=5)
    row2 = store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-02", "ok", notices_pulled=3)
    assert row2["streak_ok_days"] == 2
    row3 = store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-03", "failed",
                                       error_detail="timeout")
    assert row3["streak_ok_days"] == 0
    row4 = store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-04", "ok", notices_pulled=1)
    assert row4["streak_ok_days"] == 1  # streak restarts from the failure, not from the original run


def test_get_source_health_no_history_is_all_zero(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    summary = store.get_source_health(conn, TEST_TENANT_ID, "TED")
    assert summary == {"source": "TED", "last_result": None, "streak_ok_days": 0,
                        "failures_7d": 0, "last_failure": None, "consecutive_failures": 0}


def test_get_source_health_consecutive_failures_and_failures_7d(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-01", "ok", notices_pulled=5)
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-02", "failed", error_detail="a")
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-03", "failed", error_detail="b")
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "TED", days=7, now=now)
    assert summary["consecutive_failures"] == 2
    assert summary["failures_7d"] == 2
    assert summary["last_failure"] == "2026-07-03"
    assert summary["last_result"] == "error: b"
    assert summary["streak_ok_days"] == 0


def test_failures_7d_excludes_older_than_window(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-06-01", "failed", error_detail="old")
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-04", "ok", notices_pulled=1)
    now = datetime(2026, 7, 5, tzinfo=timezone.utc)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "TED", days=7, now=now)
    assert summary["failures_7d"] == 0  # the June failure is outside the 7-day window


def test_run_pipeline_records_source_health_on_success(tmp_path, raw_ted_supply):
    db = str(tmp_path / "t.db")
    run.run_pipeline([_src_ok(raw_ted_supply)], db, str(tmp_path / "r.xlsx"),
                      tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "TED")
    assert summary["streak_ok_days"] == 1
    assert summary["last_result"] == "ok (1 new)"


def test_run_pipeline_records_source_health_on_failure(tmp_path):
    db = str(tmp_path / "t.db")
    run.run_pipeline([_src_boom()], db, str(tmp_path / "r.xlsx"),
                      tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "BROKEN")
    assert summary["consecutive_failures"] == 1
    assert "portal down" in summary["last_result"]


def test_consecutive_failures_triggers_alert_at_threshold(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("alerts.send_alert", lambda subject, message: calls.append((subject, message)))
    db = str(tmp_path / "t.db")
    for _ in range(2):
        run.run_pipeline([_src_boom()], db, str(tmp_path / "r.xlsx"),
                          tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    assert calls == []  # below the default threshold (3) — no alert yet
    run.run_pipeline([_src_boom()], db, str(tmp_path / "r.xlsx"),
                      tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    assert len(calls) == 1
    assert "3" in calls[0][0]


def test_get_health_api_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, TEST_TENANT_ID)
    store.record_source_health(conn, TEST_TENANT_ID, "TED", "2026-07-01", "ok", notices_pulled=5)
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    ted = next(p for p in result if p["name"] == "TED")
    assert ted["streak_ok_days"] == 1
    assert ted["failures_7d"] == 0
    assert ted["consecutive_failures"] == 0
    assert ted["last_result"] == "ok (5 new)"
