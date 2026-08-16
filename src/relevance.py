"""CR-007 Phase B (B3) — simple, explainable relevance scoring.

Deliberately not similarity-search/embeddings (the CR's own "start simple"
bar): a candidate tender is scored purely against the org's own history of
decided tenders (shortlisted = positive, dismissed_final = negative — see
store.get_org_dismissed_final_reviews), using the same coarse signals
match.py/store already carry on every record (CPV codes, matched keywords,
category) rather than any new inference. Same shape as dedup.py: pure
functions over record lists, no DB access — api.py supplies the positive/
negative pools and calls this per candidate.
"""
from collections import Counter

# Human-readable labels for schema.RELEVANCE_REASON_CATEGORIES' codes — kept
# here rather than on the codes themselves since this module is the only
# place that turns a category into reasoning prose.
CATEGORY_LABELS = {
    "wrong_sector": "wrong sector/CPV mismatch",
    "value_too_low": "value too low",
    "wrong_region": "wrong region/country",
    "excluded_type": "excluded type (e.g. rental)",
    "duplicate": "duplicate/republished",
    "deadline_missed": "deadline missed",
    "other": "other reasons",
}

NEUTRAL_SCORE = 50
NEUTRAL_REASONING = "No similar tenders reviewed yet — neutral baseline."


def is_similar(a, b):
    """Two tenders are "similar" if they share a CPV code, share a matched
    keyword, or have the same non-empty category — any one signal is
    enough (not all three), matching the CR's "start simple" ask rather
    than a weighted/threshold model.
    """
    a_cpv = set(a.get("cpv_codes") or [])
    b_cpv = set(b.get("cpv_codes") or [])
    if a_cpv & b_cpv:
        return True
    a_terms = set(a.get("matched_terms") or [])
    b_terms = set(b.get("matched_terms") or [])
    if a_terms & b_terms:
        return True
    a_cat, b_cat = a.get("category"), b.get("category")
    return bool(a_cat) and a_cat == b_cat


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def score_relevance(candidate, positive, negative):
    """`positive`/`negative` are lists of tender-shaped dicts (cpv_codes,
    matched_terms, category); `negative` entries also carry
    `reason_category` (may be None). Returns {"score": int 0-100,
    "reasoning": str}. Score is simply the share of similar history that
    was positive — inspectable by construction, not a black-box model.
    """
    similar_pos = [p for p in positive if is_similar(candidate, p)]
    similar_neg = [n for n in negative if is_similar(candidate, n)]
    total = len(similar_pos) + len(similar_neg)
    if total == 0:
        return {"score": NEUTRAL_SCORE, "reasoning": NEUTRAL_REASONING}

    score = round(100 * len(similar_pos) / total)

    pos_clause = _plural(len(similar_pos), "similar tender") + " accepted" if similar_pos else None
    neg_clause = None
    if similar_neg:
        neg_clause = _plural(len(similar_neg), "similar tender") + " dismissed"
        categories = Counter(n["reason_category"] for n in similar_neg if n.get("reason_category"))
        if categories:
            top_category, _ = categories.most_common(1)[0]
            label = CATEGORY_LABELS.get(top_category, top_category)
            neg_clause += f", most often for: {label}"

    clauses = ", ".join(c for c in (pos_clause, neg_clause) if c)
    if score > NEUTRAL_SCORE:
        prefix = "Higher — "
    elif score < NEUTRAL_SCORE:
        prefix = "Lower — "
    else:
        prefix = "Mixed — "
    return {"score": score, "reasoning": f"{prefix}{clauses}."}
