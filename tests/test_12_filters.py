"""Step 12 — post-match filter stage (CR-001 F3).
  filters.apply_filters(rec, exclusions) -> exclude_reason:str|None
  F3: container / modular / prefabricated structures are hard-excluded, even
  alongside a tent/shelter signal. Reason: 'container_modular_prefab'.
"""
import config, filters

EXCLUSIONS = config.exclusions()

D5_CODES = {"44211000", "44211100", "44211110", "44211200",
            "45223800", "45223810", "34221000"}


def _rec(tag_line="", description="", cpv_codes=None):
    return {"tag_line": tag_line, "description": description, "cpv_codes": cpv_codes or []}


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
