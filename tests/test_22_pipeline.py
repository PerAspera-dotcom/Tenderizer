"""Portal workflow store (TENDERIZER_HANDOFF.md §5.4) — pipeline/followup.

Found while auditing CR-001/§5 against the actual code: store.py's
ensure_pipeline_entry/set_pipeline_entry/get_pipeline_entries/
get_followup_entries and the /api/pipeline + /api/followup routes were fully
implemented and wired into the frontend, but had no test coverage at all —
the one CR-001 process gap ("extend or add a unit test for the new
behaviour... do not batch") this repo's own audit found. No behaviour
changed here, only tests added.
"""
from fastapi import BackgroundTasks

import store
import api
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


def _patch_pipeline(pub_number, body, tenant_id=TEST_TENANT_ID):
    """api.patch_pipeline now takes a BackgroundTasks (owner-handoff email) —
    tests don't care about the queued task itself, just supply a fresh one.
    """
    return api.patch_pipeline(pub_number, body, BackgroundTasks(), tenant_id=tenant_id)


def _tender(pub_number, status="shortlisted"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": status}


# ── store.py: ensure_pipeline_entry / set_pipeline_entry ────────────────────

def test_ensure_pipeline_entry_creates_default_row(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")

    entries = store.get_pipeline_entries(conn, TEST_TENANT_ID)
    assert len(entries) == 1
    assert entries[0]["submission_status"] == "not_started"


def test_ensure_pipeline_entry_is_idempotent(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"notes": "keep me"})
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")  # must not reset the row

    entries = store.get_pipeline_entries(conn, TEST_TENANT_ID)
    assert entries[0]["notes"] == "keep me"


def test_get_pipeline_entries_defaults_submission_status_without_a_pipeline_row(tmp_path):
    """No ensure_pipeline_entry call at all — the outerjoin + coalesce in
    get_pipeline_entries must still surface the shortlisted tender."""
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))

    entries = store.get_pipeline_entries(conn, TEST_TENANT_ID)
    assert len(entries) == 1
    assert entries[0]["submission_status"] == "not_started"
    assert entries[0]["deadline_override"] is None


def test_set_pipeline_entry_updates_only_valid_fields(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1",
                              {"submission_status": "submitted", "notes": "hello",
                               "bogus_field": "should be ignored"})

    followup = store.get_followup_entries(conn, TEST_TENANT_ID)
    assert len(followup) == 1
    assert followup[0]["notes"] == "hello"
    assert followup[0]["submission_status"] == "submitted"


def test_set_pipeline_entry_returns_changed_fields(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"})

    changed = store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Bob", "notes": "hi"})
    assert changed == {"owner": ("Alice", "Bob"), "notes": (None, "hi")}


def test_set_pipeline_entry_returns_empty_dict_when_nothing_changes(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"})

    assert store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"}) == {}
    assert store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"bogus": "x"}) == {}


def test_set_pipeline_entry_noop_when_no_valid_fields(tmp_path):
    """Guards against a caller with only-invalid keys touching a row that
    doesn't exist yet — must not raise, and must not create a row."""
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"bogus": "x"})

    entries = store.get_pipeline_entries(conn, TEST_TENANT_ID)
    assert entries[0]["submission_status"] == "not_started"  # untouched, no crash


# ── store.py: get_pipeline_entries / get_followup_entries scoping ──────────

def test_get_pipeline_entries_only_returns_shortlisted(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("SHORTLISTED-1", status="shortlisted"))
    store.upsert(conn, TEST_TENANT_ID, _tender("NEW-1", status="new"))
    store.upsert(conn, TEST_TENANT_ID, _tender("DISMISSED-1", status="dismissed"))

    pub_numbers = {r["pub_number"] for r in store.get_pipeline_entries(conn, TEST_TENANT_ID)}
    assert pub_numbers == {"SHORTLISTED-1"}


def test_get_followup_entries_only_returns_submitted(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("NOT-STARTED-1"))
    store.upsert(conn, TEST_TENANT_ID, _tender("DRAFTING-1"))
    store.upsert(conn, TEST_TENANT_ID, _tender("SUBMITTED-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "NOT-STARTED-1")
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "DRAFTING-1")
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "SUBMITTED-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "DRAFTING-1", {"submission_status": "drafting"})
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "SUBMITTED-1", {"submission_status": "submitted"})

    pub_numbers = {r["pub_number"] for r in store.get_followup_entries(conn, TEST_TENANT_ID)}
    assert pub_numbers == {"SUBMITTED-1"}


def test_pipeline_entries_are_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, TEST_TENANT_ID, _tender("MINE-1"))
    store.upsert(conn, OTHER_TENANT_ID, _tender("THEIRS-1"))

    pub_numbers = {r["pub_number"] for r in store.get_pipeline_entries(conn, TEST_TENANT_ID)}
    assert pub_numbers == {"MINE-1"}


def test_followup_entries_are_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, TEST_TENANT_ID, _tender("MINE-1"))
    store.upsert(conn, OTHER_TENANT_ID, _tender("THEIRS-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "MINE-1")
    store.ensure_pipeline_entry(conn, OTHER_TENANT_ID, "THEIRS-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "MINE-1", {"submission_status": "submitted"})
    store.set_pipeline_entry(conn, OTHER_TENANT_ID, "THEIRS-1", {"submission_status": "submitted"})

    pub_numbers = {r["pub_number"] for r in store.get_followup_entries(conn, TEST_TENANT_ID)}
    assert pub_numbers == {"MINE-1"}


# ── api.py: /api/pipeline, /api/followup ────────────────────────────────────
# Route handlers called directly, same pattern as test_17_api.py — this repo
# has no httpx dependency for FastAPI's TestClient.

def _seed_api(tmp_path, monkeypatch, tenant_id=TEST_TENANT_ID, pub_number="P-1", status="shortlisted"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, tenant_id)
    store.upsert(conn, tenant_id, _tender(pub_number, status=status))
    return conn


def test_get_pipeline_returns_shortlisted_only(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("NEW-1", status="new"))

    result = api.get_pipeline(tenant_id=TEST_TENANT_ID)
    assert {r["pub_number"] for r in result} == {"P-1"}


def test_patch_pipeline_creates_entry_and_updates_fields(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)

    result = _patch_pipeline("P-1", api.PipelinePatch(notes="on track", owner="Alice"),
                                 tenant_id=TEST_TENANT_ID)
    assert result["notes"] == "on track"

    entries = api.get_pipeline(tenant_id=TEST_TENANT_ID)
    assert entries[0]["notes"] == "on track"


def test_patch_pipeline_rejects_invalid_submission_status(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    try:
        _patch_pipeline("P-1", api.PipelinePatch(submission_status="bogus"),
                            tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


def test_patch_pipeline_only_affects_calling_tenant(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))  # same pub_number, other tenant

    _patch_pipeline("P-1", api.PipelinePatch(notes="mine"), tenant_id=TEST_TENANT_ID)

    assert api.get_pipeline(tenant_id=TEST_TENANT_ID)[0]["notes"] == "mine"
    assert api.get_pipeline(tenant_id=OTHER_TENANT_ID)[0]["notes"] is None


# ── Notifications: owner-handoff email queuing ───────────────────────────────

def test_patch_pipeline_queues_handoff_task_on_owner_change(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.set_tenant_settings(conn, TEST_TENANT_ID,
                               {"notify_on_complete": True, "notify_email": "a@b.com"})
    background = BackgroundTasks()
    api.patch_pipeline("P-1", api.PipelinePatch(owner="Alice"), background, tenant_id=TEST_TENANT_ID)
    assert len(background.tasks) == 1


def test_patch_pipeline_does_not_queue_handoff_task_without_owner_change(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    background = BackgroundTasks()
    api.patch_pipeline("P-1", api.PipelinePatch(notes="hello"), background, tenant_id=TEST_TENANT_ID)
    assert len(background.tasks) == 0


def test_send_owner_handoff_email_noop_when_notify_off(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.set_tenant_settings(conn, TEST_TENANT_ID,
                               {"notify_on_complete": False, "notify_email": "a@b.com"})
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "t.db"))
    captured = []
    import alerts
    monkeypatch.setattr(alerts, "send_tenant_email", lambda *a, **kw: captured.append(a))
    api._send_owner_handoff_email(TEST_TENANT_ID, "P-1", None, "Alice")
    assert captured == []


def test_send_owner_handoff_email_sends_when_notify_on(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.set_tenant_settings(conn, TEST_TENANT_ID,
                               {"notify_on_complete": True, "notify_email": "a@b.com"})
    captured = []
    import alerts
    monkeypatch.setattr(alerts, "send_tenant_email", lambda to_addr, subject, body: captured.append((to_addr, body)))
    api._send_owner_handoff_email(TEST_TENANT_ID, "P-1", "Alice", "Bob")
    assert len(captured) == 1
    to_addr, body = captured[0]
    assert to_addr == "a@b.com"
    assert "Alice" in body and "Bob" in body and "P-1" in body


def test_get_followup_returns_submitted_only(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    _patch_pipeline("P-1", api.PipelinePatch(submission_status="submitted"),
                        tenant_id=TEST_TENANT_ID)

    result = api.get_followup(tenant_id=TEST_TENANT_ID)
    assert {r["pub_number"] for r in result} == {"P-1"}


def test_patch_followup_updates_outcome(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    _patch_pipeline("P-1", api.PipelinePatch(submission_status="submitted"),
                        tenant_id=TEST_TENANT_ID)

    result = api.patch_followup("P-1", api.FollowupPatch(outcome="won"), tenant_id=TEST_TENANT_ID)
    assert result["outcome"] == "won"
    assert api.get_followup(tenant_id=TEST_TENANT_ID)[0]["outcome"] == "won"


def test_patch_followup_rejects_invalid_outcome(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    try:
        api.patch_followup("P-1", api.FollowupPatch(outcome="bogus"), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


# ── CR-002 D: shortlist -> pipeline round trip, verified end-to-end ─────────
# "Shortlist in Review Queue -> gone from queue's `new` list -> appears in
# Pipeline" exercised through the exact calls the frontend makes: patch_tender
# (Review Queue's Shortlist button) -> list_tenders(status="new") (the
# sidebar badge / Review Queue's new count) -> get_pipeline (Portal Home's
# Accepted Tenders + Pipeline & Deadlines). No gap found; this closes the
# "confirm it actually works" ask without changing behaviour.

def test_shortlist_round_trip_end_to_end(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch, pub_number="ROUNDTRIP-1", status="new")

    # Before: counted as `new`, not yet in the pipeline.
    assert "ROUNDTRIP-1" in {r["pub_number"] for r in api.list_tenders(
        status="new", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]}
    assert api.get_pipeline(tenant_id=TEST_TENANT_ID) == []

    # The Review Queue's Shortlist action.
    api.patch_tender("ROUNDTRIP-1", api.StatusBody(status="shortlisted"), tenant_id=TEST_TENANT_ID)

    # After: gone from the `new` list/badge count...
    assert "ROUNDTRIP-1" not in {r["pub_number"] for r in api.list_tenders(
        status="new", limit=100, offset=0, tenant_id=TEST_TENANT_ID)["results"]}
    # ...and present in the pipeline (Portal Home's Accepted Tenders / Pipeline & Deadlines).
    pipeline = api.get_pipeline(tenant_id=TEST_TENANT_ID)
    assert {r["pub_number"] for r in pipeline} == {"ROUNDTRIP-1"}
    assert pipeline[0]["submission_status"] == "not_started"


# ── Tenancy hardening: pipeline_history audit trail ─────────────────────────

def test_set_pipeline_entry_logs_only_changed_fields(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice", "notes": "first"})
    history = store.get_pipeline_history(conn, TEST_TENANT_ID, "P-1")
    assert {h["field"] for h in history} == {"owner", "notes"}
    for h in history:
        assert h["old_value"] is None
        assert h["changed_at"]


def test_set_pipeline_entry_reapplying_same_value_logs_nothing(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"})

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"})  # no-op re-PATCH

    history = store.get_pipeline_history(conn, TEST_TENANT_ID, "P-1")
    assert len(history) == 1  # only the first, real change


def test_pipeline_history_records_old_and_new_values_newest_first(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"submission_status": "drafting"})
    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"submission_status": "submitted"})

    history = [h for h in store.get_pipeline_history(conn, TEST_TENANT_ID, "P-1")
               if h["field"] == "submission_status"]
    assert len(history) == 2
    assert history[0]["old_value"] == "drafting" and history[0]["new_value"] == "submitted"
    assert history[1]["old_value"] == "not_started" and history[1]["new_value"] == "drafting"


def test_pipeline_history_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, "P-1")
    store.ensure_pipeline_entry(conn, OTHER_TENANT_ID, "P-1")

    store.set_pipeline_entry(conn, TEST_TENANT_ID, "P-1", {"owner": "Alice"})
    store.set_pipeline_entry(conn, OTHER_TENANT_ID, "P-1", {"owner": "Bob"})

    assert [h["new_value"] for h in store.get_pipeline_history(conn, TEST_TENANT_ID, "P-1")] == ["Alice"]
    assert [h["new_value"] for h in store.get_pipeline_history(conn, OTHER_TENANT_ID, "P-1")] == ["Bob"]


def test_record_pipeline_change_appends_directly(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.record_pipeline_change(conn, TEST_TENANT_ID, "P-1", "notes", None, "hello")
    history = store.get_pipeline_history(conn, TEST_TENANT_ID, "P-1")
    assert history == [{"field": "notes", "old_value": None, "new_value": "hello",
                         "changed_at": history[0]["changed_at"]}]


def test_get_pipeline_history_endpoint_round_trips(tmp_path, monkeypatch):
    _seed_api(tmp_path, monkeypatch)
    _patch_pipeline("P-1", api.PipelinePatch(owner="Alice"), tenant_id=TEST_TENANT_ID)
    _patch_pipeline("P-1", api.PipelinePatch(owner="Bob"), tenant_id=TEST_TENANT_ID)

    result = api.get_pipeline_history("P-1", tenant_id=TEST_TENANT_ID)
    assert result["pub_number"] == "P-1"
    owner_changes = [h for h in result["history"] if h["field"] == "owner"]
    assert len(owner_changes) == 2
    assert owner_changes[0]["new_value"] == "Bob"  # newest first


def test_patch_followup_only_affects_calling_tenant(tmp_path, monkeypatch):
    conn = _seed_api(tmp_path, monkeypatch)
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))
    _patch_pipeline("P-1", api.PipelinePatch(submission_status="submitted"), tenant_id=TEST_TENANT_ID)
    _patch_pipeline("P-1", api.PipelinePatch(submission_status="submitted"), tenant_id=OTHER_TENANT_ID)

    api.patch_followup("P-1", api.FollowupPatch(outcome="won"), tenant_id=TEST_TENANT_ID)

    assert api.get_followup(tenant_id=TEST_TENANT_ID)[0]["outcome"] == "won"
    assert api.get_followup(tenant_id=OTHER_TENANT_ID)[0]["outcome"] == "pending"
