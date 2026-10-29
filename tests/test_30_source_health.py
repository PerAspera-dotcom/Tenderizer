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


def test_run_pipeline_surfaces_a_partial_fetch_warning(tmp_path, raw_ted_supply):
    class Partial(list):
        warning = "1 day skipped: 2026-10-19"

    db = str(tmp_path / "t.db")
    src = {"name": "TED", "fetch": lambda: Partial([raw_ted_supply]),
           "normalize": __import__("normalize").normalize_ted}
    health = run.run_pipeline([src], db, str(tmp_path / "r.xlsx"),
                               tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    assert health["TED"] == "ok (1; 1 day skipped: 2026-10-19)"
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "TED")
    assert summary["last_result"] == "ok (1 new; 1 day skipped: 2026-10-19)"
    assert summary["streak_ok_days"] == 1  # a partial fetch is still a working source
    assert summary["consecutive_failures"] == 0


# ── CR-011 §1: DeepL's own health, recorded every run via translate.check_health ──

def test_run_pipeline_records_deepl_health_ok(tmp_path, monkeypatch, raw_ted_supply):
    monkeypatch.setattr("translate.check_health", lambda: ("ok", ""))
    db = str(tmp_path / "t.db")
    run.run_pipeline([_src_ok(raw_ted_supply)], db, str(tmp_path / "r.xlsx"),
                      tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "DeepL")
    assert summary["last_result"].startswith("ok")


def test_run_pipeline_records_deepl_health_failing(tmp_path, monkeypatch, raw_ted_supply):
    monkeypatch.setattr("translate.check_health", lambda: ("failing", "DeepL auth rejected (401)"))
    db = str(tmp_path / "t.db")
    run.run_pipeline([_src_ok(raw_ted_supply)], db, str(tmp_path / "r.xlsx"),
                      tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "DeepL")
    assert summary["consecutive_failures"] == 1
    assert "auth rejected" in summary["last_result"]


def test_run_pipeline_records_deepl_health_runs_even_with_nothing_to_translate(tmp_path, monkeypatch):
    # confirmed deliberate: check_health() pings regardless of whether any
    # record needed translation this run (see translate.check_health's
    # docstring) — an empty source list must still record DeepL's own status.
    monkeypatch.setattr("translate.check_health", lambda: ("unavailable", "DEEPL_API_KEY is not set"))
    db = str(tmp_path / "t.db")
    run.run_pipeline([], db, str(tmp_path / "r.xlsx"), tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES)
    conn = store.init_db(db)
    summary = store.get_source_health(conn, TEST_TENANT_ID, "DeepL")
    assert summary["last_result"] is not None


# ── CR-011 §1: /api/health surfaces DeepL alongside the Scout portals ──────

def test_get_health_includes_deepl_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    store.ensure_tenant(store.init_db(str(tmp_path / "t.db")), TEST_TENANT_ID)
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    deepl = next(p for p in result if p["name"] == "DeepL")
    assert deepl["region"] == "Translation"
    assert deepl["status"] == "paused"  # never run yet in this fresh DB


def test_get_health_deepl_status_live_after_ok_run(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, TEST_TENANT_ID)
    store.record_source_health(conn, TEST_TENANT_ID, "DeepL", "2026-10-16", "ok", notices_pulled=3)
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    deepl = next(p for p in result if p["name"] == "DeepL")
    assert deepl["status"] == "live"


def test_get_health_deepl_status_failing_surfaces_detail(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, TEST_TENANT_ID)
    store.record_source_health(conn, TEST_TENANT_ID, "DeepL", "2026-10-16", "failed",
                                error_detail="DeepL quota exceeded (456)")
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    deepl = next(p for p in result if p["name"] == "DeepL")
    assert deepl["status"] == "failing"
    assert deepl["detail"] == "DeepL quota exceeded (456)"


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


def test_get_health_portal_never_run_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    store.ensure_tenant(store.init_db(str(tmp_path / "t.db")), TEST_TENANT_ID)
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    ted = next(p for p in result if p["name"] == "TED")
    assert ted["status"] == "pending"


def test_get_health_portal_disabled_for_tenant_is_paused(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, TEST_TENANT_ID)
    store.set_tenant_portal_enabled(conn, TEST_TENANT_ID, "e-Procurement", False)
    store.record_source_health(conn, TEST_TENANT_ID, "e-Procurement", "2026-10-16", "ok", notices_pulled=1)
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    ep = next(p for p in result if p["name"] == "e-Procurement")
    assert ep["status"] == "paused"
    assert ep["detail"] == "Switched off for this organization"


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


# CR-004/5 UX pass: Dashboard's "Next run in" display was hardcoded to None
# before the scheduler existed for real; now it's a real countdown to the
# daily scrape's own 02:00 UTC cron (api.DAILY_SCRAPE_HOUR_UTC).

def test_next_scheduled_run_same_day_when_before_cron_hour():
    now = datetime(2026, 7, 21, 0, 30, tzinfo=timezone.utc)  # before DAILY_SCRAPE_HOUR_UTC (2am)
    next_run = api._next_scheduled_run(now)
    assert next_run == datetime(2026, 7, 21, api.DAILY_SCRAPE_HOUR_UTC, 0, tzinfo=timezone.utc)


def test_next_scheduled_run_rolls_to_tomorrow_when_after_cron_hour():
    now = datetime(2026, 7, 21, 23, 0, tzinfo=timezone.utc)
    next_run = api._next_scheduled_run(now)
    assert next_run == datetime(2026, 7, 22, api.DAILY_SCRAPE_HOUR_UTC, 0, tzinfo=timezone.utc)


def test_next_scheduled_run_exactly_at_cron_hour_rolls_to_tomorrow():
    now = datetime(2026, 7, 21, api.DAILY_SCRAPE_HOUR_UTC, 0, tzinfo=timezone.utc)
    next_run = api._next_scheduled_run(now)
    assert next_run == datetime(2026, 7, 22, api.DAILY_SCRAPE_HOUR_UTC, 0, tzinfo=timezone.utc)


def test_get_stats_returns_real_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    store.ensure_tenant(store.init_db(str(tmp_path / "t.db")), TEST_TENANT_ID)
    stats = api.get_stats(tenant_id=TEST_TENANT_ID)
    assert stats["next_run"] is not None
    datetime.fromisoformat(stats["next_run"])  # must be a real, parseable ISO timestamp


def test_portals_active_is_computed_not_hardcoded(tmp_path, monkeypatch):
    # found stale 2026-10-16 (still read "2/4" after Bekanntmachungsservice's
    # go-live went into _PORTAL_META but this KPI wasn't updated alongside
    # it) -- now derived from _PORTAL_META itself so a future status change
    # can't silently leave this behind again.
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    store.ensure_tenant(store.init_db(str(tmp_path / "t.db")), TEST_TENANT_ID)
    stats = api.get_stats(tenant_id=TEST_TENANT_ID)
    live = sum(1 for p in api._PORTAL_META if p["status"] == "live")
    assert stats["portals_active"] == f"{live}/{len(api._PORTAL_META)}"
    # TED, BOAMP, Bekanntmachungsservice, and all five CR-011 §4 sources
    # (Find a Tender, Contracts Finder, CanadaBuys, TenderNed, PhilGEPS) live,
    # plus e-Procurement since BOSA's go-ahead (2026-10-28).
    assert stats["portals_active"] == "9/9"


def test_every_portal_health_entry_has_a_description():
    # each Portal Health card shows a one-line descriptor; a portal added to
    # _PORTAL_META or email_alerts.SOURCE_DOMAINS needs one too
    import api, email_alerts
    names = [p["name"] for p in api._PORTAL_META] + ["DeepL"] + list(email_alerts.SOURCE_DOMAINS.values())
    missing = [n for n in names if not api._PORTAL_DESCRIPTIONS.get(n)]
    assert missing == []
    assert all(len(d) <= 90 for d in api._PORTAL_DESCRIPTIONS.values())
