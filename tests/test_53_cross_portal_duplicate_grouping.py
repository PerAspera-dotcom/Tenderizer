"""CR-011 §2 — cross-portal duplicate visibility + triage propagation.

dedup.group_cross_portal_duplicates(records_by_pub, edges) -> unit coverage
of canonical selection (national portal preferred over TED, falling back to
_pick_canonical's existing cascade), reusing the same graph-walk
group_same_source_duplicates already has unit coverage for in
test_49_duplicate_resolution.py (not re-tested here beyond one shared-walk
regression).

api._attach_duplicates' new `cross_portal_group` field, and api.patch_tender's
new "triage once, apply to both" propagation (shortlist/dismiss on one member
of an unresolved duplicate link — same_source OR cross-portal — applies to
every other member), are the API-level focus.
"""
import dedup
import store
import api
from conftest import TEST_TENANT_ID, make_identity


# ── dedup.group_cross_portal_duplicates — canonical selection ──────────────

def _rec(pub_number, source="TED", pub_date="", first_seen="", deadline="2030-01-01",
         status="new", url=None):
    return {"pub_number": pub_number, "source": source, "pub_date": pub_date,
            "first_seen": first_seen, "deadline": deadline, "status": status,
            "url": url or f"http://example/{pub_number}"}


def test_national_portal_preferred_over_ted():
    ted = _rec("TED-1", source="TED", pub_date="2026-06-20")  # newer, but still not canonical
    boamp = _rec("BOAMP-1", source="BOAMP", pub_date="2026-06-01")
    groups = dedup.group_cross_portal_duplicates({"TED-1": ted, "BOAMP-1": boamp}, [("TED-1", "BOAMP-1")])
    assert groups["TED-1"]["canonical_pub_number"] == "BOAMP-1"
    assert groups["TED-1"] == groups["BOAMP-1"]  # shared by reference, both sides agree


def test_newer_national_portal_source_still_wins_over_older_ted():
    # national-over-TED is absolute, not just a tie-break against pub_date
    ted = _rec("TED-1", source="TED", pub_date="2026-12-01")
    de = _rec("DE-1", source="Bekanntmachungsservice", pub_date="2026-01-01")
    groups = dedup.group_cross_portal_duplicates({"TED-1": ted, "DE-1": de}, [("TED-1", "DE-1")])
    assert groups["TED-1"]["canonical_pub_number"] == "DE-1"


def test_falls_back_to_pick_canonical_cascade_when_no_national_member():
    # shouldn't occur via find_cross_portal_duplicates (only pairs different
    # sources), but a 3-node chain formed by two separate pairs could still
    # leave an all-non-national component in principle — never crash, just
    # fall back to the same determinism cascade same_source groups use.
    a = _rec("A", source="TED", pub_date="2026-06-01")
    b = _rec("B", source="TED", pub_date="2026-06-15")
    groups = dedup.group_cross_portal_duplicates({"A": a, "B": b}, [("A", "B")])
    assert groups["A"]["canonical_pub_number"] == "B"  # newest, same cascade as _pick_canonical


def test_multiple_national_members_use_pick_canonical_among_them():
    ted = _rec("TED-1", source="TED", pub_date="2026-06-01")
    boamp = _rec("BOAMP-1", source="BOAMP", pub_date="2026-06-01")
    de = _rec("DE-1", source="Bekanntmachungsservice", pub_date="2026-06-15")
    groups = dedup.group_cross_portal_duplicates(
        {"TED-1": ted, "BOAMP-1": boamp, "DE-1": de},
        [("TED-1", "BOAMP-1"), ("TED-1", "DE-1")],
    )
    assert groups["TED-1"]["size"] == 3
    assert groups["TED-1"]["canonical_pub_number"] == "DE-1"  # newest among the two national members


# ── API: _attach_duplicates' cross_portal_group field ───────────────────────

def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def _tender(pub_number, source="TED", pub_date="2026-06-01", deadline="2030-01-01",
            buyer="Ministry X", status="new"):
    return {"source": source, "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": buyer, "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": pub_date,
            "deadline": deadline, "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv",
            "url": f"http://{source.lower()}.example/{pub_number}",
            "first_seen": None, "exclude_reason": "", "status": status}


def test_cross_portal_group_attached_national_is_canonical(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    identity = make_identity()
    rec_ted = api.get_tender("TED-1", identity=identity)
    rec_boamp = api.get_tender("BOAMP-1", identity=identity)

    assert rec_ted["cross_portal_group"]["size"] == 2
    assert rec_ted["cross_portal_group"]["canonical_pub_number"] == "BOAMP-1"
    assert rec_ted["cross_portal_group"] == rec_boamp["cross_portal_group"]
    assert rec_ted["duplicate_group"] is None  # still separate from the same_source field


def test_resolved_cross_portal_edge_excluded_from_active_group(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reviewer@example.com")

    rec = api.get_tender("TED-1", identity=make_identity())
    assert rec["cross_portal_group"] is None
    assert rec["duplicates"][0]["resolved_by"] == "reviewer@example.com"  # verdict still visible


def test_tender_without_a_cross_portal_match_has_null_group(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1"))
    rec = api.get_tender("TED-1", identity=make_identity())
    assert rec["cross_portal_group"] is None


# ── API: patch_tender's "triage once, apply to both" propagation ───────────

def test_dismissing_primary_propagates_to_cross_portal_duplicate(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    from api import StatusBody
    body = StatusBody(status="dismissed", note="Out of scope", reason_category="other")
    api.patch_tender("BOAMP-1", body, identity=make_identity())

    rec_ted = api.get_tender("TED-1", identity=make_identity())
    assert rec_ted["status"] == "dismissed"
    assert "Linked duplicate of BOAMP-1" in rec_ted["dismissal_reason"]
    assert rec_ted["dismissal_reason"].endswith("Out of scope")
    assert rec_ted["dismissal_reason_category"] == "other"


def test_propagation_is_bidirectional(tmp_path, monkeypatch):
    # acting on EITHER side of the pair propagates — the link is symmetric
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    from api import StatusBody
    body = StatusBody(status="shortlisted")
    api.patch_tender("TED-1", body, identity=make_identity())  # the non-canonical side this time

    rec_boamp = api.get_tender("BOAMP-1", identity=make_identity())
    assert rec_boamp["status"] == "shortlisted"


def test_shortlist_propagation_needs_no_note(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    from api import StatusBody
    api.patch_tender("BOAMP-1", StatusBody(status="shortlisted"), identity=make_identity())

    rec_ted = api.get_tender("TED-1", identity=make_identity())
    assert rec_ted["status"] == "shortlisted"
    assert not rec_ted["dismissal_reason"]


def test_same_source_duplicate_also_propagates(tmp_path, monkeypatch):
    # CR-011 §2: applied generically, not only to the cross-portal case the
    # CR's own example is about.
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B", pub_date="2026-06-15"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")

    from api import StatusBody
    body = StatusBody(status="dismissed", note="Superseded", reason_category="duplicate")
    api.patch_tender("A", body, identity=make_identity())

    rec_b = api.get_tender("B", identity=make_identity())
    assert rec_b["status"] == "dismissed"


def test_propagation_reaches_every_member_of_a_multi_member_group(tmp_path, monkeypatch):
    # star topology (KEPT paired with each candidate) -- propagating from a
    # leaf must still reach every OTHER member via the full component walk,
    # not just KEPT (its only direct edge).
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("KEPT", pub_date="2026-06-20"))
    store.upsert(conn, TEST_TENANT_ID, _tender("C1", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("C2", pub_date="2026-06-10"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "KEPT", "C1", "same_source")
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "KEPT", "C2", "same_source")

    from api import StatusBody
    api.patch_tender("C1", StatusBody(status="shortlisted"), identity=make_identity())

    identity = make_identity()
    assert api.get_tender("KEPT", identity=identity)["status"] == "shortlisted"
    assert api.get_tender("C2", identity=identity)["status"] == "shortlisted"


def test_needs_review_does_not_propagate(tmp_path, monkeypatch):
    # a delegation action, not a relevance verdict -- see _PROPAGATE_TO_
    # DUPLICATES' own docstring for why this is deliberately excluded.
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    from api import StatusBody
    body = StatusBody(status="needs_review", note="Check budget line")
    api.patch_tender("BOAMP-1", body, identity=make_identity())

    rec_ted = api.get_tender("TED-1", identity=make_identity())
    assert rec_ted["status"] == "new"  # untouched


def test_resolved_duplicate_does_not_propagate(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", source="TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", source="BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")
    store.resolve_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reviewer@example.com")

    from api import StatusBody
    body = StatusBody(status="dismissed", note="Out of scope", reason_category="other")
    api.patch_tender("BOAMP-1", body, identity=make_identity())

    rec_ted = api.get_tender("TED-1", identity=make_identity())
    assert rec_ted["status"] == "new"  # the unlinked false positive stays untouched


def test_dismissing_a_tender_with_no_duplicate_link_is_a_plain_no_propagation(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("SOLO"))
    from api import StatusBody
    body = StatusBody(status="dismissed", note="Not relevant", reason_category="other")
    result = api.patch_tender("SOLO", body, identity=make_identity())
    assert result["status"] == "dismissed"  # no crash, no counterpart to touch
