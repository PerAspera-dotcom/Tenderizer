"""CR-017 §1.3 — the expired-undecided view (GET /api/tenders/expired).

Definition (agreed 2026-11-22): status new or needs_review, visible (a
manual tender's exclude_reason doesn't hide it), not a past_tender notice,
deadline before today. Soft-dismissed is excluded — someone decided it.

The date is pinned (api._today), never date.today(). Set CR017_PG_URL (see
test_67) to run this module against Postgres.
"""
import os
from urllib.parse import urlparse

import pytest
from sqlalchemy import update

import api
import store
from conftest import TEST_TENANT_ID, make_identity
from schema import metadata, tenders

TODAY = "2026-07-15"
PG_URL = os.getenv("CR017_PG_URL")
if PG_URL:
    _u = urlparse(PG_URL)
    assert _u.hostname in ("localhost", "127.0.0.1") and "test" in _u.path, \
        "CR017_PG_URL must be a local database whose name contains 'test' — its schema is dropped"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_today", lambda: TODAY)
    if PG_URL:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        engine = store.init_db(str(tmp_path / "unused.db"))
        metadata.drop_all(engine)
        engine = store.init_db(str(tmp_path / "unused.db"))
        assert engine.dialect.name == "postgresql"
    else:
        db_path = str(tmp_path / "t.db")
        monkeypatch.setattr(api, "DB_PATH", db_path)
        engine = store.init_db(db_path)
    store.ensure_tenant(engine, TEST_TENANT_ID)
    return engine


def _add(conn, pub, tenant_id=TEST_TENANT_ID, **over):
    r = {"source": "TED", "pub_number": pub, "tag_line": "Tentes", "description": "", "buyer": "Ville",
         "country": "BE", "category": "Supply", "deadline": "2026-07-01", "cpv_codes": ["39522530"],
         "matched_terms": [], "match_source": "cpv", "url": "", "status": "new", "exclude_reason": "",
         "notice_type": "tender", "entry_method": "scraped"}
    r.update(over)
    store.upsert(conn, tenant_id, r)


def _expired(tenant_id=TEST_TENANT_ID):
    return api.list_expired_undecided(identity=make_identity(tenant_id=tenant_id), limit=1000, offset=0)


def _queue():
    return api.list_tenders(identity=make_identity(), limit=1000, offset=0, source=None, category=None,
                            match_source=None, country=None, q=None, status=None, has_deadline=None,
                            notice_type=None, include_excluded=False, entry_method=None)


INCLUDED = {
    "new-expired": {},
    "needs-review-expired": {"status": "needs_review"},
    "blank-status-expired": {"status": ""},                     # read as "new", as everywhere else
    "yesterday-late": {"deadline": "2026-07-14T23:59:00"},
    "manual-excluded-expired": {"entry_method": "manual", "source": "Manual",
                                "exclude_reason": "F3: value below floor"},
}
EXCLUDED = {
    "soft-dismissed-expired": {"status": "dismissed", "dismissal_reason": "no", "reason_category": "other"},
    "dismissed-final-expired": {"status": "dismissed_final", "dismissal_reason": "no", "reason_category": "other"},
    "shortlisted-expired": {"status": "shortlisted"},
    "reviewed-expired": {"status": "reviewed"},
    "due-today": {"deadline": TODAY},
    "due-today-morning": {"deadline": f"{TODAY}T00:01:00"},
    "live": {"deadline": "2026-08-01"},
    "no-deadline": {"deadline": ""},
    "scraped-excluded-expired": {"exclude_reason": "F3: value below floor"},
    "past-tender-expired": {"notice_type": "past_tender"},
    # byte order, as the default list: "2026/07/01" sorts after "2026-07-15",
    # so the default list keeps it and this view must not also claim it
    "malformed-deadline": {"deadline": "2026/07/01"},
}


def test_returns_exactly_the_expired_undecided(conn):
    for pub, over in {**INCLUDED, **EXCLUDED}.items():
        _add(conn, pub, **over)
    _add(conn, "deleted-expired", entry_method="manual", source="Manual")
    with conn.begin() as c:
        c.execute(update(tenders).where(tenders.c.pub_number == "deleted-expired")
                  .values(deleted_at="2026-07-10T00:00:00"))
    got = _expired()
    assert {r["pub_number"] for r in got["results"]} == set(INCLUDED)
    assert got["total"] == len(INCLUDED)


def test_manual_tender_with_exclude_reason_appears_flagged(conn):
    """§5: never verified end to end before — the exclude_reason travels
    with the row so the UI can show "would be excluded"."""
    _add(conn, "m", entry_method="manual", source="Manual", exclude_reason="F3: value below floor")
    _add(conn, "m-live", entry_method="manual", source="Manual", exclude_reason="F3: value below floor",
         deadline="2026-08-01")
    (row,) = _expired()["results"]
    assert row["pub_number"] == "m" and row["exclude_reason"] == "F3: value below floor"
    assert row["entry_method"] == "manual"
    (live,) = [r for r in _queue()["results"] if r["pub_number"] == "m-live"]
    assert live["exclude_reason"] == "F3: value below floor"


def test_most_recently_expired_first(conn):
    for pub, deadline in [("a", "2026-06-01"), ("b", "2026-07-14"), ("c", "2026-07-02T10:00:00")]:
        _add(conn, pub, deadline=deadline)
    assert [r["pub_number"] for r in _expired()["results"]] == ["b", "c", "a"]


def test_rows_are_enriched_like_the_queue(conn):
    _add(conn, "x")
    (row,) = _expired()["results"]
    for key in ("relevance_score", "duplicates", "duplicate_group", "cross_portal_group",
                "screening_answers", "viewers", "forwarded_to", "dismissal_reason_category"):
        assert key in row, key


def test_never_overlaps_the_default_queue_and_covers_every_undecided_row(conn):
    """Every visible, non-past, undecided tender is in exactly one place:
    the default queue (live or no deadline) or this view (expired)."""
    for pub, over in {**INCLUDED, **EXCLUDED}.items():
        _add(conn, pub, **over)
    expired = {r["pub_number"] for r in _expired()["results"]}
    queued_undecided = {r["pub_number"] for r in _queue()["results"]
                        if (r["status"] or "new") in ("new", "needs_review")}
    assert not expired & queued_undecided
    undecided_visible = {p for p, o in {**INCLUDED, **EXCLUDED}.items()
                         if (o.get("status", "new") or "new") in ("new", "needs_review")
                         and o.get("notice_type") != "past_tender"
                         and not (o.get("exclude_reason") and o.get("entry_method") != "manual")}
    assert expired | queued_undecided == undecided_visible


def test_tenant_scoped(conn):
    _add(conn, "mine")
    store.ensure_tenant(conn, 2)
    _add(conn, "theirs", tenant_id=2)
    assert {r["pub_number"] for r in _expired()["results"]} == {"mine"}
    assert {r["pub_number"] for r in _expired(tenant_id=2)["results"]} == {"theirs"}


def test_pagination_reports_the_full_total(conn):
    for i in range(5):
        _add(conn, f"p{i}", deadline=f"2026-07-0{i + 1}")
    page = api.list_expired_undecided(identity=make_identity(), limit=2, offset=2)
    assert page["total"] == 5 and len(page["results"]) == 2
