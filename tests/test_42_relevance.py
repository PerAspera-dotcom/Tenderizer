"""CR-007 Phase B (B3) — relevance scoring: simple, explainable, corrigible.

`relevance.py` unit tests cover the pure scoring function directly; the API
tests below cover the wiring (list_tenders/get_tender attaching a score only
to undecided tenders, and PATCH .../relevance storing/attributing a
reviewer's correction). Follows test_36/test_40's conventions.
"""
import pytest
from fastapi import HTTPException

import relevance
import store
import api
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME_B, TEST_CLERK_USER_ID_B, make_identity


# ── relevance.py unit tests ──────────────────────────────────────────────────

def test_is_similar_true_on_shared_cpv():
    a = {"cpv_codes": ["39522530"], "matched_terms": [], "category": ""}
    b = {"cpv_codes": ["39522530", "39522500"], "matched_terms": [], "category": ""}
    assert relevance.is_similar(a, b)


def test_is_similar_true_on_shared_matched_term():
    a = {"cpv_codes": [], "matched_terms": ["tent"], "category": ""}
    b = {"cpv_codes": [], "matched_terms": ["tent", "shelter"], "category": ""}
    assert relevance.is_similar(a, b)


def test_is_similar_true_on_same_category():
    a = {"cpv_codes": [], "matched_terms": [], "category": "Supply"}
    b = {"cpv_codes": [], "matched_terms": [], "category": "Supply"}
    assert relevance.is_similar(a, b)


def test_is_similar_false_on_no_overlap():
    a = {"cpv_codes": ["1"], "matched_terms": ["tent"], "category": "Supply"}
    b = {"cpv_codes": ["2"], "matched_terms": ["catering"], "category": "Services"}
    assert not relevance.is_similar(a, b)


def test_is_similar_false_when_both_categories_are_empty():
    a = {"cpv_codes": ["1"], "matched_terms": [], "category": ""}
    b = {"cpv_codes": ["2"], "matched_terms": [], "category": ""}
    assert not relevance.is_similar(a, b)


CANDIDATE = {"cpv_codes": ["39522530"], "matched_terms": ["tent"], "category": "Supply"}


def test_score_relevance_no_history_is_neutral():
    result = relevance.score_relevance(CANDIDATE, [], [])
    assert result["score"] == 50
    assert "no similar" in result["reasoning"].lower()


def test_score_relevance_positive_only_is_high():
    positive = [{"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply"}]
    result = relevance.score_relevance(CANDIDATE, positive, [])
    assert result["score"] == 100
    assert "accepted" in result["reasoning"].lower()


def test_score_relevance_negative_only_is_low_and_names_top_category():
    negative = [
        {"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply",
         "reason_category": "wrong_sector"},
        {"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply",
         "reason_category": "wrong_sector"},
        {"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply",
         "reason_category": "value_too_low"},
    ]
    result = relevance.score_relevance(CANDIDATE, [], negative)
    assert result["score"] == 0
    assert "wrong sector" in result["reasoning"].lower()


def test_score_relevance_mixed_history():
    positive = [{"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply"}]
    negative = [{"cpv_codes": ["39522530"], "matched_terms": [], "category": "Supply",
                 "reason_category": "duplicate"}]
    result = relevance.score_relevance(CANDIDATE, positive, negative)
    assert result["score"] == 50
    assert "accepted" in result["reasoning"].lower()
    assert "dismissed" in result["reasoning"].lower()


def test_score_relevance_ignores_dissimilar_history():
    unrelated = [{"cpv_codes": ["00000000"], "matched_terms": ["catering"], "category": "Services"}]
    result = relevance.score_relevance(CANDIDATE, unrelated, unrelated)
    assert result["score"] == 50
    assert "no similar" in result["reasoning"].lower()


# ── CR-009 A: CPV-group similarity + reinforced reason_category pattern ────

FIRE_GROUPS = {"11111111": "firefighting", "22222222": "firefighting"}


def test_is_similar_true_on_shared_cpv_group_even_with_different_codes():
    # Two distinct codes, same group -- the CR's own example (firefighting
    # gear under different exact codes should still count as alike).
    a = {"cpv_codes": ["11111111"], "matched_terms": [], "category": ""}
    b = {"cpv_codes": ["22222222"], "matched_terms": [], "category": ""}
    assert not relevance.is_similar(a, b)                      # no group map -> code-only, no match
    assert relevance.is_similar(a, b, cpv_groups=FIRE_GROUPS)   # group map supplied -> matches


def test_score_relevance_group_match_without_group_map_is_neutral():
    candidate = {"cpv_codes": ["22222222"], "matched_terms": [], "category": ""}
    negative = [{"cpv_codes": ["11111111"], "matched_terms": [], "category": "",
                 "reason_category": "wrong_sector"}]
    result = relevance.score_relevance(candidate, [], negative)  # no cpv_groups
    assert result["score"] == 50
    assert "no similar" in result["reasoning"].lower()


def test_score_relevance_group_match_with_group_map_counts_as_similar():
    candidate = {"cpv_codes": ["22222222"], "matched_terms": [], "category": ""}
    negative = [{"cpv_codes": ["11111111"], "matched_terms": [], "category": "",
                 "reason_category": "wrong_sector"}]
    result = relevance.score_relevance(candidate, [], negative, cpv_groups=FIRE_GROUPS)
    assert result["score"] == 0
    assert "dismissed" in result["reasoning"].lower()


def test_reinforced_reason_category_pulls_the_score_down_further():
    # Same total evidence shape (1 positive, 2 negatives, each matching on
    # one dimension) -- only difference is whether the two negatives share
    # a reason_category. A repeated, articulated reason should be weighted
    # as a stronger, established pattern than two isolated one-off calls.
    candidate = {"cpv_codes": ["1"], "matched_terms": [], "category": ""}
    positive = [{"cpv_codes": ["1"], "matched_terms": [], "category": ""}]

    scattered = [
        {"cpv_codes": ["1"], "matched_terms": [], "category": "", "reason_category": "wrong_sector"},
        {"cpv_codes": ["1"], "matched_terms": [], "category": "", "reason_category": "value_too_low"},
    ]
    reinforced = [
        {"cpv_codes": ["1"], "matched_terms": [], "category": "", "reason_category": "wrong_sector"},
        {"cpv_codes": ["1"], "matched_terms": [], "category": "", "reason_category": "wrong_sector"},
    ]

    scattered_score = relevance.score_relevance(candidate, positive, scattered)["score"]
    reinforced_score = relevance.score_relevance(candidate, positive, reinforced)["score"]
    assert reinforced_score < scattered_score
    assert scattered_score == 33   # 1 pos-dim vs 1+1 unweighted neg-dims: 100*1/3
    assert reinforced_score == 20  # each reinforced neg gets +1: 100*1/5


# ── API wiring ────────────────────────────────────────────────────────────────

def _rec(pub_number, cpv="39522530", category="Supply", deadline="2030-01-01T00:00:00+00:00"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": category, "procedure": "open", "pub_date": "2026-06-01",
            "deadline": deadline, "cpv_codes": [cpv], "matched_terms": ["tent"],
            "match_source": "cpv", "url": "http://x", "first_seen": None, "exclude_reason": ""}


def _seed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def test_new_tender_carries_a_relevance_score(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-1"))

    rec = api.get_tender("PUB-1", identity=make_identity())
    assert rec["relevance_score"] == 50
    assert rec["relevance_corrected"] is False


def test_decided_tenders_never_carry_a_relevance_score(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-1"))
    api.patch_tender("PUB-1", api.StatusBody(status="shortlisted"), identity=make_identity())

    rec = api.get_tender("PUB-1", identity=make_identity())
    assert "relevance_score" not in rec


def test_relevance_reflects_a_similar_dismissal_by_a_colleague(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _rec("DISMISSED-1"))
    store.upsert(conn, TEST_TENANT_ID, _rec("CANDIDATE-1"))

    colleague = make_identity(account_name=TEST_ACCOUNT_NAME_B, clerk_user_id=TEST_CLERK_USER_ID_B)
    api.patch_tender("DISMISSED-1", api.StatusBody(status="dismissed", note="Not for us",
                                                     reason_category="wrong_sector"),
                      identity=colleague)
    api.patch_tender("DISMISSED-1", api.StatusBody(status="dismissed_final", note="Confirmed",
                                                     reason_category="wrong_sector"),
                      identity=colleague)

    rec = api.get_tender("CANDIDATE-1", identity=make_identity())
    assert rec["relevance_score"] == 0
    assert "wrong sector" in rec["relevance_reasoning"].lower()


def test_relevance_correction_overrides_the_computed_score(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-1"))

    api.patch_tender_relevance(
        "PUB-1", api.RelevanceBody(score=90, note="Actually a great fit"),
        identity=make_identity(account_name="reviewer@example.com"))

    rec = api.get_tender("PUB-1", identity=make_identity())
    assert rec["relevance_score"] == 90
    assert rec["relevance_corrected"] is True
    assert rec["relevance_corrected_by"] == "reviewer@example.com"


def test_relevance_correction_out_of_range_is_422(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _rec("PUB-1"))
    with pytest.raises(HTTPException) as exc:
        api.patch_tender_relevance("PUB-1", api.RelevanceBody(score=150), identity=make_identity())
    assert exc.value.status_code == 422


def test_relevance_reflects_a_group_level_cpv_match_not_just_exact_code(tmp_path, monkeypatch):
    # CR-009 A wired end-to-end: 39522000 and 39522100 are different exact
    # codes but share config/cpv_reference.json's "tents_covers" group.
    # category/matched_terms deliberately differ so the CPV-group dimension
    # is the *only* thing that can make these two records match -- _rec's
    # own defaults (category="Supply", matched_terms=["tent"]) would
    # otherwise make them similar regardless of CPV, defeating the point.
    conn = _seed(tmp_path, monkeypatch)
    dismissed = dict(_rec("DISMISSED-1", cpv="39522000"), category="Works", matched_terms=["tarpaulin"])
    candidate = dict(_rec("CANDIDATE-1", cpv="39522100"), category="Services", matched_terms=["awning"])
    store.upsert(conn, TEST_TENANT_ID, dismissed)
    store.upsert(conn, TEST_TENANT_ID, candidate)

    api.patch_tender("DISMISSED-1", api.StatusBody(status="dismissed", note="Not for us",
                                                     reason_category="wrong_sector"),
                      identity=make_identity())
    api.patch_tender("DISMISSED-1", api.StatusBody(status="dismissed_final", note="Confirmed",
                                                     reason_category="wrong_sector"),
                      identity=make_identity())

    rec = api.get_tender("CANDIDATE-1", identity=make_identity())
    assert rec["relevance_score"] == 0
    assert "dismissed" in rec["relevance_reasoning"].lower()


def test_relevance_correction_on_another_tenants_tender_is_403(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    other_conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(other_conn, 999, _rec("OTHER-TENANT-1"))
    with pytest.raises(HTTPException) as exc:
        api.patch_tender_relevance("OTHER-TENANT-1", api.RelevanceBody(score=80),
                                    identity=make_identity())
    assert exc.value.status_code == 403
