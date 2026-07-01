"""Post-match filter stage (CR-001 §A) — engine filtering & scope.

Runs on an already-tagged record (see run._tag) and returns an `exclude_reason`
string, or None if the record is kept. Exclusion is recorded, not silently
dropped: the caller still stores the record (see store.COLUMNS) but omits it
from what's surfaced (the report).
"""
import match


def _text(rec):
    return f"{rec.get('tag_line', '')} {rec.get('description', '')}"


def check_container_modular_prefab(rec, exclusions):
    """F3 — hard-exclude container / modular / prefabricated structures.

    Trips on either the exclusion CPV codes or any-language exclusion term,
    even if the record also matched a tent/shelter signal (customer: hard exclude).
    """
    cfg = exclusions["container_modular_prefab"]
    if set(rec.get("cpv_codes") or []) & set(cfg["codes"]):
        return "container_modular_prefab"
    terms = [w for lang in cfg["terms"].values() for w in lang]
    if match.match_keywords(_text(rec), terms):
        return "container_modular_prefab"
    return None


def check_rental(rec, exclusions):
    """F2 — hard-exclude rental tenders (all languages).

    FR uses rental-shaped phrase terms ('location de', 'en location'), not the
    bare word — see config/exclusions.yaml for why.
    """
    cfg = exclusions["rental"]
    terms = [w for lang in cfg["terms"].values() for w in lang]
    if match.match_keywords(_text(rec), terms):
        return "rental"
    return None


CHECKS = [check_container_modular_prefab, check_rental]


def apply_filters(rec, exclusions):
    """Run all active exclusion checks; first hit wins. None = kept."""
    for check in CHECKS:
        reason = check(rec, exclusions)
        if reason:
            return reason
    return None
