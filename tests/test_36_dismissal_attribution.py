"""Step 36 — CR-006: mandatory dismissal reason, attribution, Dismissed-tab
listing, and reinstatement.

Builds on test_23_dismiss_note.py's store-level dismissal_reason coverage;
this file covers the parts CR-006 actually added: the API rejecting an empty
reason (400), dismissed_by/dismissed_at being stamped server-side from the
authenticated identity (never client-suppliable), the Dismissed tab's list
query not losing past-deadline tenders, and reinstate leaving the dismissal
metadata in place as history.

CR-007 Phase A update: dismiss/reviewed are now personal per-account state
(store.tender_reviews), not the shared `tenders` row — see api.py's
_merge_personal_review_state. Assertions below read through the API's
merged view (api.get_tender/list_tenders) rather than raw store.all_records,
since that's now the only place personal dismissal metadata surfaces.
"""
import pytest
from fastapi import HTTPException

import store
import api
from conftest import (TEST_TENANT_ID, TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B,
                       TEST_CLERK_USER_ID_B, make_identity)


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


def _get(conn, pub_number, identity=None):
    """Reads through the API's merged view (personal review state overlaid
    on the shared tenders row) rather than raw store.all_records — see the
    module docstring's CR-007 Phase A note.
    """
    return api.get_tender(pub_number, include_excluded=True, identity=identity or make_identity())


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
    result = api.patch_tender(
        "PUB-1", api.StatusBody(status="dismissed", note="Out of scope", reason_category="other"),
        identity=make_identity())
    assert result["status"] == "dismissed"


def test_dismiss_without_a_reason_category_is_400(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Out of scope"),
                          identity=make_identity())
    assert exc.value.status_code == 400


# ── D3: attribution — server-stamped, not client-suppliable ─────────────────

def test_dismiss_stamps_dismissed_by_from_identity_not_the_client(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    identity = make_identity(account_name="reviewer@example.com")
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong CPV",
                                              reason_category="wrong_sector"), identity=identity)
    rec = _get(conn, "PUB-1", identity=identity)
    assert rec["dismissed_by"] == "reviewer@example.com"


def test_dismiss_stamps_dismissed_at(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong CPV",
                                              reason_category="wrong_sector"),
                      identity=make_identity())
    assert _get(conn, "PUB-1")["dismissed_at"]


def test_non_dismiss_transition_does_not_stamp_attribution(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())
    rec = _get(conn, "PUB-1")
    assert rec["dismissed_by"] is None
    assert rec["dismissed_at"] is None


# ── CR-007 Phase A: dismissal is personal, doesn't leak to a colleague ──────

def test_dismiss_is_personal_a_colleague_still_sees_it_as_new(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Not for us",
                                              reason_category="other"),
                      identity=make_identity())

    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    rec = _get(conn, "PUB-1", identity=colleague)
    assert rec["status"] == "new"
    assert rec["dismissed_by"] is None
    assert rec["dismissed_at"] is None

    # The dismisser's own view is unaffected by the colleague's read.
    assert _get(conn, "PUB-1")["status"] == "dismissed"


def test_shortlist_by_one_account_is_visible_to_a_colleague(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    assert _get(conn, "PUB-1", identity=colleague)["status"] == "shortlisted"


# ── D1: Dismissed-tab listing ────────────────────────────────────────────────

def test_dismissed_filter_returns_the_dismissal_metadata(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Wrong sector",
                                              reason_category="wrong_sector"),
                      identity=make_identity())
    results = api.list_tenders(status="dismissed", limit=100, offset=0,
                                identity=make_identity())["results"]
    assert len(results) == 1
    assert results[0]["dismissal_reason"] == "Wrong sector"
    assert results[0]["dismissal_reason_category"] == "wrong_sector"
    assert results[0]["dismissed_by"] == TEST_ACCOUNT_NAME
    assert results[0]["dismissed_at"]


def test_finally_dismissed_filter_still_includes_a_since_expired_deadline(tmp_path, monkeypatch):
    """A tender dismissed while still open, whose deadline has since passed,
    must not silently vanish from its own Dismissed tab — unlike every other
    view, which does hide expired deadlines by default (api.py's
    `if status != "dismissed_final"` guard around the expiry filter). CR-007
    B1: this exemption is now stage-2 ("dismissed_final") only.
    """
    conn = _seed(tmp_path, monkeypatch, deadline="2020-01-01T00:00:00+00:00")
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Missed it",
                                              reason_category="deadline_missed"),
                      identity=make_identity())
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed_final", note="Missed it",
                                              reason_category="deadline_missed"),
                      identity=make_identity())
    dismissed = api.list_tenders(status="dismissed_final", limit=100, offset=0,
                                  identity=make_identity())["results"]
    assert {r["pub_number"] for r in dismissed} == {"PUB-1"}

    # Sanity: the same expired-deadline tender is correctly hidden from a
    # non-dismissed_final-status view (the filter is status-gated, not removed).
    active = api.list_tenders(status="new", limit=100, offset=0, identity=make_identity())["results"]
    assert active == []


def test_soft_dismissed_stays_subject_to_the_expiry_filter(tmp_path, monkeypatch):
    """CR-007 B1: unlike the final Dismissed tab, a stage-1 ("dismissed")
    tender is still an active Review Queue item, so an expired deadline
    hides it same as any other undecided tender.
    """
    _seed(tmp_path, monkeypatch, deadline="2020-01-01T00:00:00+00:00")
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Missed it",
                                              reason_category="deadline_missed"),
                      identity=make_identity())
    soft_dismissed = api.list_tenders(status="dismissed", limit=100, offset=0,
                                       identity=make_identity())["results"]
    assert soft_dismissed == []


# ── D4: reinstate reuses set_status; CR-007 Phase A: shortlisting supersedes
# the shared view's dismissal metadata (dismissal was always personal) ──────

def test_reinstate_leaves_the_dismissed_tab_and_reappears_elsewhere(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed",
                                              reason_category="other"),
                      identity=make_identity())
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    dismissed = api.list_tenders(status="dismissed", limit=100, offset=0,
                                  identity=make_identity())["results"]
    assert dismissed == []
    shortlisted = api.list_tenders(status="shortlisted", limit=100, offset=0,
                                    identity=make_identity())["results"]
    assert {r["pub_number"] for r in shortlisted} == {"PUB-1"}


def test_shortlisting_a_dismissed_tender_supersedes_the_shared_view(tmp_path, monkeypatch):
    """CR-007 Phase A change from CR-006's original behavior: dismissal is
    now personal (tender_reviews), so once shortlisted (the org's shared
    decision), the shared view no longer carries the dismisser's prior
    personal reason — nothing was ever written to the shared `tenders` row
    for a dismiss. This is a deliberate scope choice (see CR-007 Phase A
    plan's "Explicitly out of scope" note), not a regression: the personal
    record itself isn't deleted, just not surfaced once the org has decided
    (see the next test).
    """
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed",
                                              reason_category="other"),
                      identity=make_identity())
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    rec = _get(conn, "PUB-1")
    assert rec["status"] == "shortlisted"
    assert rec["dismissal_reason"] is None
    assert rec["dismissed_by"] is None
    assert rec["dismissed_at"] is None


def test_the_prior_personal_dismissal_is_still_queryable_after_shortlisting(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    identity = make_identity()
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed",
                                              reason_category="other"),
                      identity=identity)
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=identity)

    reviews = store.get_tender_reviews_for_account(conn, TEST_TENANT_ID, identity.clerk_user_id)
    assert reviews["PUB-1"]["status"] == "dismissed"
    assert reviews["PUB-1"]["reason"] == "Second look needed"


# ── CR-007 Phase B (B1): two-stage dismissal ─────────────────────────────────

def test_first_dismiss_greys_out_but_stays_in_the_queue(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="Second look needed",
                                              reason_category="other"),
                      identity=make_identity())

    rec = _get(conn, "PUB-1")
    assert rec["status"] == "dismissed"
    # Not yet on the final Dismissed tab...
    assert api.list_tenders(status="dismissed_final", limit=100, offset=0,
                             identity=make_identity())["results"] == []
    # ...but still returned by an unfiltered/soft-dismissed query, i.e. still
    # "in the queue" rather than removed.
    assert api.list_tenders(status="dismissed", limit=100, offset=0,
                             identity=make_identity())["results"][0]["pub_number"] == "PUB-1"


def test_second_dismiss_moves_it_to_the_dismissed_tab(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    identity = make_identity()
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="First pass",
                                              reason_category="other"), identity=identity)
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed_final", note="Confirmed",
                                              reason_category="other"), identity=identity)

    rec = _get(conn, "PUB-1")
    assert rec["status"] == "dismissed_final"
    assert api.list_tenders(status="dismissed", limit=100, offset=0,
                             identity=identity)["results"] == []
    final = api.list_tenders(status="dismissed_final", limit=100, offset=0, identity=identity)["results"]
    assert {r["pub_number"] for r in final} == {"PUB-1"}


def test_dismissed_final_without_a_note_is_400(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    identity = make_identity()
    api.patch_tender("PUB-1", api.StatusBody(status="dismissed", note="First pass",
                                              reason_category="other"), identity=identity)
    with pytest.raises(HTTPException) as exc:
        api.patch_tender("PUB-1", api.StatusBody(status="dismissed_final"), identity=identity)
    assert exc.value.status_code == 400
