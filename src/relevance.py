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


def _cpv_group_set(cpv_codes, cpv_groups):
    if not cpv_groups:
        return set()
    return {cpv_groups[c] for c in (cpv_codes or []) if c in cpv_groups}


def _match_dimensions(a, b, cpv_groups=None):
    """CR-009 A ("teach the platform" from every category dimension a
    decision reinforces, per the CR's own example: repeatedly dismissing
    firefighting-related tenders should down-rank the next one even when
    it's a different exact CPV code): counts how many independent signals
    two tenders agree on, rather than is_similar's plain yes/no —
      - shared CPV code(s)
      - shared CPV *group* (config.cpv_reference's `group` field, broader
        than exact code — two different but related codes, e.g. two
        distinct firefighting-equipment codes, land in the same group;
        `cpv_groups` is an optional {code: group} map, supplied by the
        caller so this module stays DB/config-file-free itself, same
        convention as positive/negative being supplied rather than
        queried here)
      - shared matched keyword(s)
      - same non-empty contract category
    A tender that agrees with history on multiple independent dimensions
    is stronger evidence than one that only coincidentally shares one.
    """
    dims = 0
    a_cpv, b_cpv = set(a.get("cpv_codes") or []), set(b.get("cpv_codes") or [])
    if a_cpv & b_cpv:
        dims += 1
    if _cpv_group_set(a_cpv, cpv_groups) & _cpv_group_set(b_cpv, cpv_groups):
        dims += 1
    a_terms, b_terms = set(a.get("matched_terms") or []), set(b.get("matched_terms") or [])
    if a_terms & b_terms:
        dims += 1
    a_cat, b_cat = a.get("category"), b.get("category")
    if a_cat and a_cat == b_cat:
        dims += 1
    return dims


def is_similar(a, b, cpv_groups=None):
    """Two tenders are "similar" if they agree on at least one dimension —
    see _match_dimensions. `cpv_groups` optional; omitting it just means
    the CPV-group dimension never fires (exact-code/keyword/category
    still do), so existing two-arg callers are unaffected.
    """
    return _match_dimensions(a, b, cpv_groups) > 0


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def score_relevance(candidate, positive, negative, cpv_groups=None):
    """`positive`/`negative` are lists of tender-shaped dicts (cpv_codes,
    matched_terms, category); `negative` entries also carry
    `reason_category` (may be None). `cpv_groups` is an optional
    {code: group} map (see _match_dimensions) letting group-level CPV
    similarity count even without an exact code match. Returns
    {"score": int 0-100, "reasoning": str} — inspectable by construction,
    not a black-box model.

    CR-009 A: each similar tender's vote is weighted by how many
    dimensions it agrees with the candidate on (_match_dimensions), not
    counted flatly — and a negative whose `reason_category` recurs across
    two or more similar dismissals gets an extra point on top of that,
    since a *repeated, articulated* reason (the CR's own example: several
    firefighting-related tenders all dismissed as wrong_sector) is a more
    established pattern than one reviewer's single call and should teach
    the platform to weight the next alike tender down harder.
    """
    similar_pos = [(p, d) for p in positive if (d := _match_dimensions(candidate, p, cpv_groups)) > 0]
    similar_neg = [(n, d) for n in negative if (d := _match_dimensions(candidate, n, cpv_groups)) > 0]
    if not similar_pos and not similar_neg:
        return {"score": NEUTRAL_SCORE, "reasoning": NEUTRAL_REASONING}

    reason_counts = Counter(n["reason_category"] for n, _ in similar_neg if n.get("reason_category"))

    def neg_weight(n, dims):
        rc = n.get("reason_category")
        return dims + 1 if rc and reason_counts[rc] >= 2 else dims

    pos_total = sum(d for _, d in similar_pos)
    neg_total = sum(neg_weight(n, d) for n, d in similar_neg)
    score = round(100 * pos_total / (pos_total + neg_total))

    pos_clause = _plural(len(similar_pos), "similar tender") + " accepted" if similar_pos else None
    neg_clause = None
    if similar_neg:
        neg_clause = _plural(len(similar_neg), "similar tender") + " dismissed"
        if reason_counts:
            top_category, _ = reason_counts.most_common(1)[0]
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
