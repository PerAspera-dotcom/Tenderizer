"""Step 16 — cross-record dedup pass (CR-001 D-DUP, highest risk in the CR).
  dedup.find_duplicate_groups(records) -> list[list[record]]
      each group sorted newest pub_date first (index 0 = kept); len >= 2.
  Match rule: same buyer + deadline within 7 days + title similarity >= 0.9.
  This is the CR's own example: two "Romania – Tents – Corturi și Generatoare"
  rows, both due 10 Jul, republished under different pub_numbers.
"""
import dedup


def _rec(pub_number, buyer="Ministry X", tag_line="Romania - Tents - Corturi si Generatoare",
         deadline="2026-07-10T12:00:00+00:00", pub_date="2026-06-01", exclude_reason=""):
    return {"pub_number": pub_number, "buyer": buyer, "tag_line": tag_line,
            "deadline": deadline, "pub_date": pub_date, "exclude_reason": exclude_reason,
            "supersedes": []}


def test_identical_republish_collapses_to_one_group():
    a = _rec("RO-1", pub_date="2026-06-01")
    b = _rec("RO-2", pub_date="2026-06-15")   # republished two weeks later
    groups = dedup.find_duplicate_groups([a, b])
    assert len(groups) == 1
    assert {r["pub_number"] for r in groups[0]} == {"RO-1", "RO-2"}


def test_kept_record_is_the_most_recently_published():
    a = _rec("RO-1", pub_date="2026-06-01")
    b = _rec("RO-2", pub_date="2026-06-15")
    kept, superseded = dedup.find_duplicate_groups([a, b])[0]
    assert kept["pub_number"] == "RO-2"
    assert superseded["pub_number"] == "RO-1"


def test_fuzzy_title_variation_still_matches():
    # A real republish often has minor wording drift, not byte-identical titles.
    a = _rec("RO-1", tag_line="Romania - Tents - Corturi si Generatoare")
    b = _rec("RO-2", tag_line="Romania - Tents - Corturi si Generatoare 2026")
    groups = dedup.find_duplicate_groups([a, b])
    assert len(groups) == 1


def test_control_different_title_is_not_merged():
    # The CR's own control case — same buyer, genuinely different tender.
    a = _rec("RO-1", tag_line="Romania - Tents - Corturi si Generatoare")
    b = _rec("RO-3", tag_line="Romania - Catering services for military bases")
    assert dedup.find_duplicate_groups([a, b]) == []


def test_control_different_buyer_is_not_merged():
    a = _rec("RO-1", buyer="Ministry X")
    b = _rec("RO-2", buyer="Ministry Y")
    assert dedup.find_duplicate_groups([a, b]) == []


def test_deadline_within_7_day_window_still_merges():
    a = _rec("RO-1", deadline="2026-07-10T12:00:00+00:00")
    b = _rec("RO-2", deadline="2026-07-15T12:00:00+00:00")  # +5 days — a republished amendment
    assert len(dedup.find_duplicate_groups([a, b])) == 1


def test_deadline_beyond_7_day_window_is_not_merged():
    a = _rec("RO-1", deadline="2026-07-10T12:00:00+00:00")
    b = _rec("RO-2", deadline="2026-07-25T12:00:00+00:00")  # +15 days — too far
    assert dedup.find_duplicate_groups([a, b]) == []


def test_missing_deadline_is_not_merged():
    a = _rec("RO-1", deadline="")
    b = _rec("RO-2", deadline="")
    assert dedup.find_duplicate_groups([a, b]) == []


def test_already_excluded_record_is_not_considered():
    a = _rec("RO-1", exclude_reason="rental")
    b = _rec("RO-2")
    assert dedup.find_duplicate_groups([a, b]) == []


def test_three_way_republish_groups_together():
    a = _rec("RO-1", pub_date="2026-06-01")
    b = _rec("RO-2", pub_date="2026-06-10")
    c = _rec("RO-3", pub_date="2026-06-20")
    groups = dedup.find_duplicate_groups([a, b, c])
    assert len(groups) == 1
    assert groups[0][0]["pub_number"] == "RO-3"          # newest kept
    assert {r["pub_number"] for r in groups[0][1:]} == {"RO-1", "RO-2"}


def test_unrelated_third_party_notice_is_untouched():
    a = _rec("RO-1", pub_date="2026-06-01")
    b = _rec("RO-2", pub_date="2026-06-15")
    other = _rec("FR-1", buyer="Ministère de l'Intérieur", tag_line="France - Tentes")
    groups = dedup.find_duplicate_groups([a, b, other])
    assert len(groups) == 1
    assert "FR-1" not in {r["pub_number"] for g in groups for r in g}


# ── store.mark_superseded ─────────────────────────────────────────────────────

import normalize, store


def _stored_rec(pub_number, buyer, tag_line, deadline):
    return {"source": "TED", "pub_number": pub_number, "tag_line": tag_line,
            "description": "", "buyer": buyer, "country": "ROU", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": ["39522530"], "matched_terms": [],
            "match_source": "cpv", "url": "http://x", "first_seen": None}


def test_mark_superseded_sets_reason_and_supersedes_list(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    kept = _stored_rec("RO-2", "Ministry X", "Tents", "2026-07-10T12:00:00+00:00")
    old = _stored_rec("RO-1", "Ministry X", "Tents", "2026-07-10T12:00:00+00:00")
    store.upsert(conn, kept)
    store.upsert(conn, old)

    store.mark_superseded(conn, "RO-2", [dict(old, supersedes=[])])

    records = {r["pub_number"]: r for r in store.all_records(conn)}
    assert records["RO-1"]["exclude_reason"] == "superseded"
    assert records["RO-2"]["supersedes"] == ["RO-1"]
    assert records["RO-2"]["exclude_reason"] == ""   # the kept record stays surfaced


def test_mark_superseded_accumulates_multi_generation_chain(tmp_path):
    # RO-1 was already superseded by RO-2 in an earlier run; now RO-3 supersedes
    # RO-2 — RO-3's supersedes list should show the full chain, not just RO-2.
    conn = store.init_db(str(tmp_path / "t.db"))
    for pub in ("RO-1", "RO-2", "RO-3"):
        store.upsert(conn, _stored_rec(pub, "Ministry X", "Tents", "2026-07-10T12:00:00+00:00"))
    store.mark_superseded(conn, "RO-2", [dict(_stored_rec("RO-1", "Ministry X", "Tents",
                                                            "2026-07-10T12:00:00+00:00"), supersedes=[])])
    ro2 = next(r for r in store.all_records(conn) if r["pub_number"] == "RO-2")
    store.mark_superseded(conn, "RO-3", [ro2])

    ro3 = next(r for r in store.all_records(conn) if r["pub_number"] == "RO-3")
    assert set(ro3["supersedes"]) == {"RO-1", "RO-2"}
