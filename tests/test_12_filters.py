"""Step 12 — post-match filter stage (CR-001 F1 + F2 + F3).
  filters.apply_filters(rec, exclusions, now=None) -> exclude_reason:str|None
  F3: container / modular / prefabricated structures are hard-excluded, even
  alongside a tent/shelter signal. Reason: 'container_modular_prefab'.
  F2: rental tenders are hard-excluded, all languages. Reason: 'rental'.
  F1: notices due in under 72h are hard-excluded. Reason: 'deadline_too_soon'.
"""
from datetime import datetime, timedelta, timezone
import config, filters

EXCLUSIONS = config.exclusions()

D5_CODES = {"44211000", "44211100", "44211110", "44211200",
            "45223800", "45223810", "34221000"}


def _rec(tag_line="", description="", cpv_codes=None, deadline=""):
    return {"tag_line": tag_line, "description": description,
            "cpv_codes": cpv_codes or [], "deadline": deadline}


def test_prefab_cpv_code_is_excluded():
    rec = _rec("Delivery of prefabricated cabins", cpv_codes=["44211100"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "container_modular_prefab"


def test_mobile_container_cpv_code_is_excluded():
    rec = _rec("Mobile containers for site office", cpv_codes=["34221000"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "container_modular_prefab"


def test_excluded_by_keyword_term_without_cpv_code():
    # French "conteneurs modulaires" with no CPV code at all — keyword-only trip.
    rec = _rec("Fourniture de conteneurs modulaires")
    assert filters.apply_filters(rec, EXCLUSIONS) == "container_modular_prefab"


def test_tent_and_prefab_notice_still_excluded():
    # Tent CPV + prefab term in the title — hard exclude wins over the tent signal.
    rec = _rec("Tente préfabriquée pour usage militaire", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "container_modular_prefab"


def test_tent_only_notice_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_unrelated_notice_is_kept():
    rec = _rec("Office furniture supply", cpv_codes=["39130000"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_d5_codes_removed_from_active_cpv_set():
    assert D5_CODES.isdisjoint(set(config.cpv_codes()))


def test_d5_codes_present_in_exclusion_set():
    assert D5_CODES == set(EXCLUSIONS["container_modular_prefab"]["codes"])


def test_removed_terms_gone_from_active_keywords():
    active = set(config.keywords())
    removed = {"modular", "prefabricated", "modulaire", "prefabrique", "prefabriques",
               "prefabrication", "modulair", "prefab", "geprefabriceerd",
               "Fertigkonstruktion", "Fertigkonstruktionen", "vorgefertigt",
               "vorgefertigte", "vorgefertigten", "Containergebäude", "Großbehälter"}
    assert active.isdisjoint(removed)


# ── F2: rental exclusion ─────────────────────────────────────────────────────

def test_french_rental_phrase_is_excluded():
    rec = _rec("Location de tentes", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "rental"


def test_french_tent_purchase_is_kept():
    # The CR's own acceptance case — "acquisition" (purchase) must NOT trip the
    # rental filter just because the notice is otherwise about tents.
    rec = _rec("Acquisition de tentes", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_bare_french_location_word_is_not_excluded():
    # 'location' alone is also plain French for "place" — must not misfire when
    # it's not in a rental-shaped phrase ('location de' / 'en location').
    rec = _rec("Fourniture de tentes — précision de la location géographique du site",
                cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_english_rental_is_excluded():
    rec = _rec("Tent rental services for events", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "rental"


def test_german_miete_is_excluded():
    rec = _rec("Miete von Zelten für Feldlager", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "rental"


def test_dutch_huur_is_excluded():
    rec = _rec("Huur van tenten voor evenementen", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "rental"


# ── F1: deadline lead-time floor ─────────────────────────────────────────────

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _rec_due_in(hours):
    deadline = (NOW + timedelta(hours=hours)).isoformat()
    return _rec("Army field tent, qty 200", cpv_codes=["39522530"], deadline=deadline)


def test_due_in_48h_is_excluded():
    assert filters.apply_filters(_rec_due_in(48), EXCLUSIONS, NOW) == "deadline_too_soon"


def test_due_in_exactly_72h_is_kept():
    # CR wording is "less than 72 hours" — exactly 72h is the boundary, not excluded.
    assert filters.apply_filters(_rec_due_in(72), EXCLUSIONS, NOW) is None


def test_due_in_96h_is_kept():
    assert filters.apply_filters(_rec_due_in(96), EXCLUSIONS, NOW) is None


def test_deadline_in_different_timezone_compares_correctly():
    # 2026-07-04T08:00+02:00 == 2026-07-04T06:00 UTC == 66h after NOW — under the floor.
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"],
                deadline="2026-07-04T08:00:00+02:00")
    assert filters.apply_filters(rec, EXCLUSIONS, NOW) == "deadline_too_soon"


def test_naive_deadline_treated_as_utc():
    deadline = (NOW + timedelta(hours=48)).replace(tzinfo=None).isoformat()
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], deadline=deadline)
    assert filters.apply_filters(rec, EXCLUSIONS, NOW) == "deadline_too_soon"


def test_missing_deadline_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], deadline="")
    assert filters.apply_filters(rec, EXCLUSIONS, NOW) is None


def test_unparseable_deadline_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], deadline="not-a-date")
    assert filters.apply_filters(rec, EXCLUSIONS, NOW) is None
