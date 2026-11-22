"""CR-017 §1.4 — the Review Queue's in-place refresh after an action.

After a status change, a duplicate resolve/unresolve or a relevance
correction, the queue re-reads only the affected rows:
GET /api/tenders?pub=X (and /api/tenders/expired?pub=X) returns X and every
tender duplicate-linked to it, plus `scope`, the pub_numbers considered.
The client replaces every row in `scope` with the fresh one, or drops it.

That is only safe if (1) each refreshed row is identical to the same row in
a full reload, and (2) `scope` covers every row whose content the action
could have changed. Both are checked here after real actions — including a
decision that propagates across a duplicate group, where getting it wrong
leaves rows on screen that look decided but aren't.

Uses test_67's fixture (pinned date; CR017_PG_URL runs it on Postgres).
"""
import random

from sqlalchemy import insert

import api
import store
from conftest import TEST_TENANT_ID, make_identity
from schema import tender_duplicates
from test_67_queue_sql_filter import _PARAMS, _build, cheap_scores, conn, template_db  # noqa: F401  (fixtures)


def _full(identity):
    return {r["pub_number"]: r for r in api.list_tenders(identity=identity, limit=1000, offset=0, **_PARAMS)["results"]}


def _full_expired(identity):
    return {r["pub_number"]: r for r in api.list_expired_undecided(identity=identity, limit=1000, offset=0)["results"]}


def _refresh(identity, pubs):
    return api.list_tenders(identity=identity, limit=1000, offset=0, pub=list(pubs), **_PARAMS)


def _refresh_expired(identity, pubs):
    return api.list_expired_undecided(identity=identity, limit=1000, offset=0, pub=list(pubs))


def _apply(screen, fresh):
    """What ReviewQueue does with a refresh: drop every row in scope, add
    the fresh rows back (queueRows.mergeRefreshed)."""
    scope = set(fresh["scope"])
    out = {p: r for p, r in screen.items() if p not in scope}
    out.update({r["pub_number"]: r for r in fresh["results"]})
    return out


# Accepted 2026-11-22: a shortlist or final dismiss changes the relevance
# training pools, so every other undecided row's score shifts slightly. An
# in-place refresh doesn't re-read those rows; they catch up on the next
# full load. Re-scoring mid-session would reorder nothing in the default
# (deadline) view but would change numbers under the reviewer between
# clicks — the user chose stale-until-reload. These two fields, on rows
# outside the refresh scope, are the only difference allowed.
RELEVANCE_DRIFT = ("relevance_score", "relevance_reasoning")


def _assert_screen_matches_reload(identity, screen, scope):
    full = _full(identity)
    assert screen.keys() == full.keys()
    for p, row in full.items():
        if p in scope:
            assert screen[p] == row, p
        else:
            strip = lambda r: {k: v for k, v in r.items() if k not in RELEVANCE_DRIFT}
            assert strip(screen[p]) == strip(row), p


def _act_and_refresh(identity, screen, pubs):
    fresh = _refresh(identity, pubs)
    _assert_screen_matches_reload(identity, _apply(screen, fresh), set(fresh["scope"]))
    _assert_consistent(identity, pubs)
    return _apply(screen, fresh)


def _assert_consistent(identity, pubs):
    """A refresh of `pubs` returns exactly the full-list rows inside its
    scope, field for field — for both the queue and the expired view."""
    for full, refresh in ((_full, _refresh), (_full_expired, _refresh_expired)):
        everything = full(identity)
        got = refresh(identity, pubs)
        scope = set(got["scope"])
        assert set(pubs) <= scope
        assert {r["pub_number"]: r for r in got["results"]} == \
               {p: r for p, r in everything.items() if p in scope}


def test_refreshed_rows_equal_full_list_rows(conn, cheap_scores):
    pubs = _build(conn)
    identity = make_identity()
    rng = random.Random(4)
    for sample in [[p] for p in rng.sample(pubs, 25)] + [rng.sample(pubs, 3) for _ in range(5)]:
        _assert_consistent(identity, sample)


def test_scope_includes_the_whole_duplicate_component(conn, cheap_scores):
    _build(conn)
    identity = make_identity()
    full = _full(identity)
    grouped = [r for r in full.values() if r["duplicate_group"] or r["cross_portal_group"]]
    assert grouped
    for r in grouped[:20]:
        scope = set(_refresh(identity, [r["pub_number"]])["scope"])
        for g in (r["duplicate_group"], r["cross_portal_group"]):
            if g:
                assert {m["pub_number"] for m in g["members"]} <= scope
        assert {d["pub_number"] for d in r["duplicates"]} <= scope


def test_unknown_pub_number_returns_nothing_but_names_it_in_scope(conn, cheap_scores):
    _build(conn)
    got = _refresh(make_identity(), ["NOPE-1"])
    assert got["results"] == [] and got["scope"] == ["NOPE-1"]


def test_pub_is_tenant_scoped(conn, cheap_scores):
    pubs = _build(conn)
    store.ensure_tenant(conn, 2)
    assert _refresh(make_identity(tenant_id=2), pubs[:50])["results"] == []


# ── after real actions ───────────────────────────────────────────────────────

def _group(conn, *pubs, match_type="same_source"):
    """A live, undecided group: `pubs[0]` linked to each of the others."""
    for i, pub in enumerate(pubs):
        store.upsert(conn, TEST_TENANT_ID, {
            "source": "TED", "pub_number": pub, "tag_line": "Tentes", "description": "", "buyer": "Ville",
            "country": "BE", "category": "Supply", "deadline": f"2026-09-0{i + 1}", "cpv_codes": ["39522530"],
            "matched_terms": [], "match_source": "cpv", "url": "", "status": "new", "exclude_reason": "",
            "notice_type": "tender", "entry_method": "scraped"})
    with conn.begin() as c:
        for other in pubs[1:]:
            c.execute(insert(tender_duplicates).values(
                tenant_id=TEST_TENANT_ID, pub_number_a=pubs[0], pub_number_b=other, match_type=match_type,
                similarity=None, detected_at="2026-07-01"))


def _status(pub, status, **kw):
    api.patch_tender(pub, api.StatusBody(status=status, **kw), identity=make_identity())


def test_shortlist_propagates_and_refresh_shows_every_member_decided(conn, cheap_scores):
    _build(conn)
    _group(conn, "G-A", "G-B", "G-C")
    identity = make_identity()
    screen = _full(identity)
    assert all(screen[p]["status"] == "new" for p in ("G-A", "G-B", "G-C"))

    _status("G-B", "shortlisted")   # a leaf of the star: propagation must reach G-C via G-A
    screen = _act_and_refresh(identity, screen, ["G-B"])
    assert all(screen[p]["status"] == "shortlisted" for p in ("G-A", "G-B", "G-C"))


def test_final_dismiss_propagates_and_refresh_shows_the_whole_group_gone(conn, cheap_scores):
    _build(conn)
    _group(conn, "H-A", "H-B", "H-C", match_type="reference")
    identity = make_identity()
    screen = _full(identity)
    assert screen["H-A"]["cross_portal_group"]["size"] == 3

    _status("H-C", "dismissed", note="dup", reason_category="duplicate")
    screen = _act_and_refresh(identity, screen, ["H-C"])
    assert {screen[p]["status"] for p in ("H-A", "H-B", "H-C")} == {"dismissed"}

    _status("H-C", "new")           # "Reset to new" — doesn't propagate
    screen = _act_and_refresh(identity, screen, ["H-C"])

    # "Dismiss permanently" propagates to the whole group (fixed in CR-017,
    # see test_53), and every member leaves its group
    _status("H-C", "dismissed_final", note="dup", reason_category="duplicate")
    screen = _act_and_refresh(identity, screen, ["H-C"])
    assert all(screen[p]["status"] == "dismissed_final" for p in ("H-A", "H-B", "H-C"))
    assert all(screen[p]["cross_portal_group"] is None for p in ("H-A", "H-B", "H-C"))


def test_a_member_leaving_regroups_the_rest_in_place(conn, cheap_scores):
    """A member reaching dismissed_final without propagation (written
    directly, as any path other than patch_tender would) leaves the other
    two as a pair: their group size drops 3 -> 2, and the refresh must
    re-read them although only H-C was touched."""
    _build(conn)
    _group(conn, "H-A", "H-B", "H-C", match_type="reference")
    identity = make_identity()
    screen = _full(identity)
    store.set_status(conn, TEST_TENANT_ID, "H-C", "dismissed_final", dismissal_reason="gone",
                     dismissed_by="x", dismissed_at="2026-07-10", reason_category="other")
    screen = _act_and_refresh(identity, screen, ["H-C"])
    assert screen["H-C"]["cross_portal_group"] is None
    assert screen["H-A"]["cross_portal_group"]["size"] == 2
    assert screen["H-B"]["cross_portal_group"]["size"] == 2


def test_resolve_and_unresolve_regroup_in_place(conn, cheap_scores):
    _build(conn)
    _group(conn, "R-A", "R-B", "R-C")
    identity = make_identity()
    screen = _full(identity)
    assert screen["R-B"]["duplicate_group"]["size"] == 3

    api.resolve_duplicate("R-A", "R-B", identity=identity)
    screen = _act_and_refresh(identity, screen, ["R-A", "R-B"])
    assert screen["R-B"]["duplicate_group"] is None
    assert screen["R-C"]["duplicate_group"]["size"] == 2

    api.unresolve_duplicate("R-A", "R-B", identity=identity)
    screen = _act_and_refresh(identity, screen, ["R-A", "R-B"])
    assert screen["R-C"]["duplicate_group"]["size"] == 3


def test_relevance_correction_refreshes_in_place(conn, cheap_scores):
    _build(conn)
    _group(conn, "S-A")
    identity = make_identity()
    screen = _full(identity)
    api.patch_tender_relevance("S-A", api.RelevanceBody(score=12, note="no"), identity=identity)
    screen = _act_and_refresh(identity, screen, ["S-A"])
    assert (screen["S-A"]["relevance_score"], screen["S-A"]["relevance_corrected"]) == (12, True)


def test_dismissing_an_expired_tender_moves_it_out_of_the_expired_view(conn, cheap_scores):
    _build(conn)
    store.upsert(conn, TEST_TENANT_ID, {
        "source": "TED", "pub_number": "E-1", "tag_line": "Tentes", "description": "", "buyer": "Ville",
        "country": "BE", "category": "Supply", "deadline": "2026-07-01", "cpv_codes": ["39522530"],
        "matched_terms": [], "match_source": "cpv", "url": "", "status": "new", "exclude_reason": "",
        "notice_type": "tender", "entry_method": "scraped"})
    identity = make_identity()
    expired = _full_expired(identity)
    assert "E-1" in expired
    _status("E-1", "dismissed", note="too late", reason_category="deadline_missed")
    expired = _apply(expired, _refresh_expired(identity, ["E-1"]))
    assert "E-1" not in expired and expired == _full_expired(identity)
