"""CR-007 Phase D — collaboration presence (D1) and the deadline window
becoming a configurable, non-hard-excluding setting (D2 — see test_12_
filters.py for the filters.py-side coverage of that removal).
"""
from datetime import datetime, timedelta, timezone

import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME_B, TEST_CLERK_USER_ID_B, make_identity


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def _tender(pub_number):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "exclude_reason": ""}


# ── D2: scout settings (deadline window) ─────────────────────────────────────

def test_scout_settings_default_and_round_trip(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    assert store.get_scout_settings(conn, TEST_TENANT_ID) == {"deadline_floor_hours": 72}
    store.set_scout_settings(conn, TEST_TENANT_ID, {"deadline_floor_hours": 24})
    assert store.get_scout_settings(conn, TEST_TENANT_ID) == {"deadline_floor_hours": 24}


def test_scout_settings_endpoint_round_trips(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    assert api.get_scout_settings_config(tenant_id=TEST_TENANT_ID) == {"deadline_floor_hours": 72}
    api.put_scout_settings_config(api.ScoutSettingsBody(deadline_floor_hours=12),
                                   tenant_id=TEST_TENANT_ID)
    assert api.get_scout_settings_config(tenant_id=TEST_TENANT_ID) == {"deadline_floor_hours": 12}


def test_a_near_deadline_tender_is_returned_by_list_tenders(tmp_path, monkeypatch):
    """The actual D2 acceptance line: a tender due imminently is no longer
    hard-excluded, so it's a normal list_tenders result now.
    """
    conn = _db(tmp_path, monkeypatch)
    # +25h rather than +1h: comfortably past any local-vs-UTC "today" boundary
    # ambiguity (api.list_tenders' expiry filter compares against date.today(),
    # local time, against a UTC deadline string) while still well under the
    # old 72h floor — the point being tested is "not excluded", not the exact
    # margin.
    soon = (datetime.now(timezone.utc) + timedelta(hours=25)).isoformat()
    rec = _tender("PUB-SOON")
    rec["deadline"] = soon
    rec["exclude_reason"] = ""  # run.py's own filters.apply_filters call would leave this empty now
    store.upsert(conn, TEST_TENANT_ID, rec)

    results = api.list_tenders(limit=100, offset=0, identity=make_identity())["results"]
    assert "PUB-SOON" in {r["pub_number"] for r in results}


# ── D1: presence store-level windowing ───────────────────────────────────────

def test_upsert_tender_presence_then_visible_within_the_window(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_presence(conn, TEST_TENANT_ID, "PUB-1", "user_a", "a@example.com")
    viewers = store.get_tender_viewers(conn, TEST_TENANT_ID, since_iso="2000-01-01T00:00:00+00:00")
    assert len(viewers) == 1
    assert viewers[0]["pub_number"] == "PUB-1"
    assert viewers[0]["account_name"] == "a@example.com"


def test_get_tender_viewers_excludes_rows_older_than_since(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_presence(conn, TEST_TENANT_ID, "PUB-1", "user_a", "a@example.com")
    future_cutoff = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert store.get_tender_viewers(conn, TEST_TENANT_ID, since_iso=future_cutoff) == []


def test_upsert_tender_presence_is_idempotent_per_account(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_presence(conn, TEST_TENANT_ID, "PUB-1", "user_a", "a@example.com")
    store.upsert_tender_presence(conn, TEST_TENANT_ID, "PUB-1", "user_a", "a@example.com")
    viewers = store.get_tender_viewers(conn, TEST_TENANT_ID, since_iso="2000-01-01T00:00:00+00:00")
    assert len(viewers) == 1


# ── D1: API-level presence — symmetric, self-excluded ────────────────────────

def test_presence_shows_a_colleague_but_never_yourself(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))

    user_a = make_identity()
    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)

    api.post_tender_presence("PUB-1", identity=colleague)

    seen_by_a = api.get_tender("PUB-1", identity=user_a)
    assert len(seen_by_a["viewers"]) == 1
    assert seen_by_a["viewers"][0]["account_name"] == TEST_ACCOUNT_NAME_B

    seen_by_colleague = api.get_tender("PUB-1", identity=colleague)
    assert seen_by_colleague["viewers"] == []  # never lists yourself


def test_tender_with_no_recent_presence_has_an_empty_viewers_list(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))
    rec = api.get_tender("PUB-1", identity=make_identity())
    assert rec["viewers"] == []


def test_presence_does_not_leak_across_different_tenders(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-2"))
    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    api.post_tender_presence("PUB-1", identity=colleague)

    rec_1 = api.get_tender("PUB-1", identity=make_identity())
    rec_2 = api.get_tender("PUB-2", identity=make_identity())
    assert len(rec_1["viewers"]) == 1
    assert rec_2["viewers"] == []
