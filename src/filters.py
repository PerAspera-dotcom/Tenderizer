"""Post-match filter stage (CR-001 §A) — engine filtering & scope.

Runs on an already-tagged record (see run._tag) and returns an `exclude_reason`
string, or None if the record is kept. Exclusion is recorded, not silently
dropped: the caller still stores the record (see store.COLUMNS) but omits it
from what's surfaced (the report).

Every check shares the signature (rec, exclusions, now) so they can share one
loop in apply_filters, even though most ignore `now` and F1 ignores `exclusions`.
"""
from datetime import datetime, timedelta, timezone
import match

DEADLINE_FLOOR = timedelta(hours=72)


def _text(rec):
    return f"{rec.get('tag_line', '')} {rec.get('description', '')}"


def check_container_modular_prefab(rec, exclusions, now=None):
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


def check_rental(rec, exclusions, now=None):
    """F2 — hard-exclude rental tenders (all languages).

    FR uses rental-shaped phrase terms ('location de', 'en location'), not the
    bare word — see config/exclusions.yaml for why.
    """
    cfg = exclusions["rental"]
    terms = [w for lang in cfg["terms"].values() for w in lang]
    if match.match_keywords(_text(rec), terms):
        return "rental"
    return None


def check_deadline_too_soon(rec, exclusions, now=None):
    """F1 — exclude notices due in under 72 hours (proposal lead-time floor).

    Uses the deadline's own UTC offset if it carries one (ISO 8601, as normalize.py
    already produces), else treats it as UTC. A missing/unparseable deadline is
    kept, not excluded — consistent with F6's "keep on missing data" rule.
    """
    deadline = rec.get("deadline") or ""
    try:
        dl = datetime.fromisoformat(deadline)
    except ValueError:
        return None
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if dl - now < DEADLINE_FLOOR:
        return "deadline_too_soon"
    return None


CHECKS = [check_container_modular_prefab, check_rental, check_deadline_too_soon]


def apply_filters(rec, exclusions, now=None):
    """Run all active exclusion checks; first hit wins. None = kept."""
    for check in CHECKS:
        reason = check(rec, exclusions, now)
        if reason:
            return reason
    return None
