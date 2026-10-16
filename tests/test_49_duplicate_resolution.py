"""Duplicate-resolution follow-up (2026-08-26) to CR-008's plain same_source
"possible duplicate" label — closes the loop CR-008 P0 deliberately left
open: an ordering cue on linked pairs, reviewer actions to resolve a match
either way, and a Review Queue filter to view unique tenders only.

dedup.group_same_source_duplicates(records_by_pub, edges) -> unit coverage of
the ordering rule (pub_date, falling back to first_seen, "unknown" on a tie),
the canonical-selection cascade (deadline presence > newest > already-touched
> pub_number), and reconstructing group membership from the star-topology
pairwise edges run.py actually writes (kept<->candidate, never
candidate<->candidate).

store.py covers resolve_tender_duplicate/unresolve_tender_duplicate: pair-
level (not per-side), soft state (kept, not deleted), tender_history writes
on both pub_numbers, symmetric regardless of argument order.

The API section confirms api._attach_duplicates attaches duplicate_group
correctly (including excluding resolved edges and dismissed_final members
from the *active* group while leaving the raw `duplicates` entry intact),
and the two new endpoints (resolve/unresolve).

"Confirm as duplicate" is deliberately NOT re-tested here beyond one
regression: it's just patch_tender with reason_category='duplicate', an
enum value that already existed pre-this-CR (see test_23_dismiss_note.py /
test_36_dismissal_attribution.py for the general dismiss-flow coverage this
reuses rather than duplicates).
"""
import dedup
import store
import api
from conftest import TEST_TENANT_ID, make_identity


# ── dedup.group_same_source_duplicates — ordering + canonical unit tests ────

def _rec(pub_number, source="TED", pub_date="", first_seen="", deadline="2030-01-01",
         status="new", url=None):
    return {"pub_number": pub_number, "source": source, "pub_date": pub_date,
            "first_seen": first_seen, "deadline": deadline, "status": status,
            "url": url or f"http://example/{pub_number}"}


def test_two_member_group_orders_by_pub_date():
    a = _rec("A", pub_date="2026-06-01")
    b = _rec("B", pub_date="2026-06-15")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["order_confident"] is True
    ranks = {m["pub_number"]: m["rank"] for m in groups["A"]["members"]}
    assert ranks == {"A": 1, "B": 2}  # A published first


def test_falls_back_to_first_seen_when_pub_date_blank_on_either_side():
    # TED commonly ships a blank pub_date (confirmed in CR-008's live
    # investigation) — first_seen (always populated by store.upsert) is the
    # fallback, never guessed from anything else.
    a = _rec("A", pub_date="", first_seen="2026-06-01")
    b = _rec("B", pub_date="", first_seen="2026-06-15")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["order_confident"] is True
    ranks = {m["pub_number"]: m["rank"] for m in groups["A"]["members"]}
    assert ranks == {"A": 1, "B": 2}


def test_never_mixes_pub_date_on_one_side_with_first_seen_on_the_other():
    # A has a real pub_date; B doesn't. Comparing A's publish date against
    # B's discovery date would be comparing two different kinds of signal —
    # the whole group must report unknown instead of a misleading rank.
    a = _rec("A", pub_date="2026-01-01", first_seen="2026-01-01")
    b = _rec("B", pub_date="", first_seen="2026-06-01")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["order_confident"] is False
    assert all(m["rank"] is None for m in groups["A"]["members"])


def test_tied_dates_report_order_unknown_not_a_guess():
    a = _rec("A", pub_date="2026-06-01")
    b = _rec("B", pub_date="2026-06-01")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["order_confident"] is False
    assert groups["B"]["order_confident"] is False
    assert all(m["rank"] is None for m in groups["A"]["members"])


def test_three_way_star_topology_reconstructs_full_group():
    # run.py never writes a candidate<->candidate edge (see
    # find_duplicate_groups' docstring) — only kept<->each candidate. Group
    # membership/size must still come out as 3, not 2, from just those edges.
    kept = _rec("KEPT", pub_date="2026-06-20")
    c1 = _rec("C1", pub_date="2026-06-01")
    c2 = _rec("C2", pub_date="2026-06-10")
    groups = dedup.group_same_source_duplicates(
        {"KEPT": kept, "C1": c1, "C2": c2}, [("KEPT", "C1"), ("KEPT", "C2")])
    assert groups["KEPT"]["size"] == 3
    assert groups["C1"] is groups["KEPT"]  # one shared group object
    assert groups["C2"] is groups["KEPT"]
    ranks = {m["pub_number"]: m["rank"] for m in groups["KEPT"]["members"]}
    assert ranks == {"C1": 1, "C2": 2, "KEPT": 3}


def test_unrelated_pair_not_in_the_records_map_is_skipped():
    # e.g. the counterpart belongs to a slice of records the caller didn't
    # pass in — group_same_source_duplicates must not KeyError.
    a = _rec("A")
    groups = dedup.group_same_source_duplicates({"A": a}, [("A", "GHOST")])
    assert groups == {}


# ── dedup._pick_canonical — the "unique only" representative ────────────────

def test_canonical_prefers_the_member_with_a_deadline():
    a = _rec("A", pub_date="2026-06-15", deadline="")   # newer but no deadline
    b = _rec("B", pub_date="2026-06-01", deadline="2030-01-01")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["canonical_pub_number"] == "B"


def test_canonical_picks_newest_when_order_confident():
    a = _rec("A", pub_date="2026-06-01")
    b = _rec("B", pub_date="2026-06-15")
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["canonical_pub_number"] == "B"


def test_canonical_falls_back_to_already_touched_when_order_unknown():
    a = _rec("A", pub_date="2026-06-01", first_seen="2026-06-01", status="new")
    b = _rec("B", pub_date="", first_seen="2026-06-01", status="shortlisted")
    # order unknown: A has a real pub_date, B doesn't (never cross-compared)
    groups = dedup.group_same_source_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["order_confident"] is False
    assert groups["A"]["canonical_pub_number"] == "B"


def test_canonical_deterministic_pub_number_tiebreak_never_row_order():
    # Nothing to distinguish them at all — must be stable regardless of dict
    # insertion order (the exact non-determinism CR-008 P0 was filed over).
    a = _rec("ZZZ", pub_date="", first_seen="", deadline="", status="new")
    b = _rec("AAA", pub_date="", first_seen="", deadline="", status="new")
    forward = dedup.group_same_source_duplicates({"ZZZ": a, "AAA": b}, [("ZZZ", "AAA")])
    backward = dedup.group_same_source_duplicates({"AAA": b, "ZZZ": a}, [("AAA", "ZZZ")])
    assert forward["ZZZ"]["canonical_pub_number"] == "AAA"
    assert backward["ZZZ"]["canonical_pub_number"] == "AAA"


# ── store.resolve_tender_duplicate / unresolve_tender_duplicate ─────────────

def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def test_resolve_sets_resolved_by_and_at(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "reviewer@example.com")
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert len(dupes) == 1
    assert dupes[0]["resolved_by"] == "reviewer@example.com"
    assert dupes[0]["resolved_at"]


def test_resolve_is_symmetric_regardless_of_argument_order(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "B", "A", "reviewer@example.com")
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert dupes[0]["resolved_by"] == "reviewer@example.com"


def test_resolve_unknown_pair_raises(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    try:
        store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "reviewer@example.com")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_resolve_writes_tender_history_on_both_pub_numbers(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "reviewer@example.com")
    hist_a = store.get_tender_history(conn, TEST_TENANT_ID, "A")
    hist_b = store.get_tender_history(conn, TEST_TENANT_ID, "B")
    assert len(hist_a) == 1 and hist_a[0]["field"] == "duplicate_resolution"
    assert len(hist_b) == 1 and hist_b[0]["field"] == "duplicate_resolution"


def test_unresolve_clears_resolution_and_is_reversible(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "reviewer@example.com")
    store.unresolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B")
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert dupes[0]["resolved_by"] is None
    assert dupes[0]["resolved_at"] is None
    hist_a = store.get_tender_history(conn, TEST_TENANT_ID, "A")
    assert len(hist_a) == 2  # resolve, then unresolve


def test_unresolve_unknown_pair_raises(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    try:
        store.unresolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B")
        assert False, "expected ValueError"
    except ValueError:
        pass


# ── API: _attach_duplicates' duplicate_group + resolve/unresolve endpoints ──

def _tender(pub_number, source="TED", pub_date="2026-06-01", deadline="2030-01-01",
            buyer="Ministry X", status="new"):
    return {"source": source, "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": buyer, "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": pub_date,
            "deadline": deadline, "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv",
            "url": f"http://{source.lower()}.example/{pub_number}",
            "first_seen": None, "exclude_reason": "", "status": status}


def test_duplicate_group_attached_with_ordering_and_canonical(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B", pub_date="2026-06-15"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")

    identity = make_identity()
    rec_a = api.get_tender("A", identity=identity)
    rec_b = api.get_tender("B", identity=identity)

    assert rec_a["duplicate_group"]["size"] == 2
    assert rec_a["duplicate_group"]["canonical_pub_number"] == "B"  # newest
    assert rec_a["duplicate_group"] == rec_b["duplicate_group"]
    assert rec_a["duplicates"][0]["resolved_by"] is None


def test_tender_without_a_same_source_match_has_null_duplicate_group(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A"))
    rec = api.get_tender("A", identity=make_identity())
    assert rec["duplicate_group"] is None


def test_cross_portal_match_never_gets_a_same_source_duplicate_group(tmp_path, monkeypatch):
    # 'reference'/'similarity' matches are cross-portal (CR-007 Phase C) —
    # this follow-up's own duplicate_group field is scoped to same_source
    # only. CR-011 §2 adds the cross-portal equivalent as a SEPARATE field
    # (cross_portal_group, tested below) rather than reusing this one.
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")
    rec = api.get_tender("TED-1", identity=make_identity())
    assert rec["duplicate_group"] is None
    assert rec["duplicates"][0]["match_type"] == "reference"


def test_resolved_edge_excluded_from_active_group_but_kept_in_duplicates(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B", pub_date="2026-06-15"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "reviewer@example.com")

    rec_a = api.get_tender("A", identity=make_identity())
    assert rec_a["duplicate_group"] is None  # no longer an active group
    assert len(rec_a["duplicates"]) == 1
    assert rec_a["duplicates"][0]["resolved_by"] == "reviewer@example.com"
    assert rec_a["duplicates"][0]["resolved_at"]


def test_dismissed_final_member_excluded_from_active_group(tmp_path, monkeypatch):
    # A three-way group where one member has already been fully dismissed
    # (e.g. via "Confirm as duplicate") shrinks to an active group of the
    # remaining two, not three — the dismissed one is already resolved.
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("KEPT", pub_date="2026-06-20"))
    store.upsert(conn, TEST_TENANT_ID, _tender("C1", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("C2", pub_date="2026-06-10"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "KEPT", "C1", "same_source")
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "KEPT", "C2", "same_source")
    store.set_status(conn, TEST_TENANT_ID, "C1", "dismissed_final",
                      dismissal_reason="Duplicate of KEPT", dismissed_by="reviewer@example.com",
                      dismissed_at="2026-06-21T00:00:00+00:00", reason_category="duplicate")

    rec_kept = api.get_tender("KEPT", identity=make_identity())
    assert rec_kept["duplicate_group"]["size"] == 2
    assert {m["pub_number"] for m in rec_kept["duplicate_group"]["members"]} == {"KEPT", "C2"}
    # C1's own pairing with KEPT is still visible on C1 itself (Dismissed tab).
    rec_c1 = api.get_tender("C1", identity=make_identity(), include_excluded=True)
    assert rec_c1["duplicates"][0]["pub_number"] == "KEPT"
    assert rec_c1["duplicate_group"] is None


def test_resolve_duplicate_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")

    result = api.resolve_duplicate("A", "B", identity=make_identity())
    assert result["resolved"] is True
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert dupes[0]["resolved_by"] == "tester@example.com"


def test_resolve_duplicate_endpoint_404_for_unknown_pair(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B"))
    try:
        api.resolve_duplicate("A", "B", identity=make_identity())
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_unresolve_duplicate_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    api.resolve_duplicate("A", "B", identity=make_identity())

    result = api.unresolve_duplicate("A", "B", identity=make_identity())
    assert result["resolved"] is False
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert dupes[0]["resolved_by"] is None


# ── "Confirm as duplicate" reuses the existing dismiss flow — regression ────

def test_confirm_as_duplicate_is_just_a_dismiss_with_the_duplicate_category(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B", pub_date="2026-06-15"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")

    body = api.StatusBody(status="dismissed_final", note="Duplicate of B",
                           reason_category="duplicate")
    api.patch_tender("A", body, identity=make_identity())

    rec_a = api.get_tender("A", identity=make_identity(), include_excluded=True)
    assert rec_a["status"] == "dismissed_final"
    assert rec_a["dismissal_reason_category"] == "duplicate"
    assert rec_a["dismissed_by"] == "tester@example.com"
    # The duplicate link itself is untouched by a dismiss — still there,
    # still visible on the dismissed record.
    assert rec_a["duplicates"][0]["pub_number"] == "B"
    assert rec_a["duplicates"][0]["resolved_by"] is None
