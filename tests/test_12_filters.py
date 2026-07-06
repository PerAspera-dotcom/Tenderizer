"""Step 12 — post-match filter stage (CR-001 F1 + F2 + F3 + F6).
  filters.apply_filters(rec, exclusions, now=None) -> exclude_reason:str|None
  F3: container / modular / prefabricated structures are hard-excluded, even
  alongside a tent/shelter signal. Reason: 'container_modular_prefab'.
  F2: rental tenders are hard-excluded, all languages. Reason: 'rental'.
  F1: notices due in under 72h are hard-excluded. Reason: 'deadline_too_soon'.
  F6: notices with a (pre-converted) EUR value under the floor are hard-
  excluded. Reason: 'below_value_floor'. Currency conversion itself lives in
  currency.py (network); this module only reads rec['value_eur'].
"""
from datetime import datetime, timedelta, timezone
import config, filters

EXCLUSIONS = config.exclusions()

D5_CODES = {"44211000", "44211100", "44211110", "44211200",
            "45223800", "45223810", "34221000"}


def _rec(tag_line="", description="", cpv_codes=None, deadline="", value_eur=None,
         match_source=None, matched_terms=None, category=None):
    return {"tag_line": tag_line, "description": description,
            "cpv_codes": cpv_codes or [], "deadline": deadline, "value_eur": value_eur,
            "match_source": match_source, "matched_terms": matched_terms or [],
            "category": category}


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


def test_french_rental_elided_form_is_excluded():
    # real gap found 2026-07 via the calibration export: "location d'engins"
    # (elided) wasn't caught by the "location de" phrase term — match.py now
    # folds French "d'" -> "de " before matching (see test_08_matcher.py for
    # the actual TED/BOAMP notice text this was missing).
    rec = _rec("Location d'engins et de materiels pour evenements", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "rental"


# Elision-folding lives in match.match_keywords itself, so every exclusion
# category gets it automatically, not just rental — proven here against a
# synthetic term (none of F3/F4's *current* real terms happen to end in "de",
# so this exercises the shared mechanism directly rather than relying on
# today's yaml wording never changing).
def test_elision_folding_applies_to_other_exclusion_categories_too():
    synthetic = dict(EXCLUSIONS)
    synthetic["container_modular_prefab"] = dict(EXCLUSIONS["container_modular_prefab"],
                                                  terms={"fr": ["assemblage de"]})
    rec = _rec("Assemblage d'elements modulaires sur site")
    assert filters.apply_filters(rec, synthetic) == "container_modular_prefab"


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


# ── F6: value floor ───────────────────────────────────────────────────────────

def test_value_below_floor_is_excluded():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], value_eur=150_000)
    assert filters.apply_filters(rec, EXCLUSIONS) == "below_value_floor"


def test_value_above_floor_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], value_eur=250_000)
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_value_absent_is_kept():
    # most notices don't disclose a value at all — don't exclude on missing data
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], value_eur=None)
    assert filters.apply_filters(rec, EXCLUSIONS) is None


# ── F4: construction works (D3 hard exclude) ─────────────────────────────────

def test_45xxx_code_is_excluded():
    rec = _rec("Construction of a military base", cpv_codes=["45000000"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "construction_works"


def test_tent_only_notice_has_no_45xxx_and_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_45xxx_excludes_even_alongside_a_tent_signal():
    # hard exclude, no tent-signal override (D3) — a notice can carry both a tent
    # code and a 45xxx works code; the works code still wins.
    rec = _rec("Construction of shelter facilities", cpv_codes=["39522530", "45216129"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "construction_works"


def test_shelter_supply_code_44112100_is_not_45xxx_and_is_kept():
    # the supply-side shelter code stays active; only the WORKS variants died to F4.
    rec = _rec("Shelters, qty 50", cpv_codes=["44112100"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_f4_retired_codes_removed_from_active_cpv_set():
    # these three would always trip construction_works, so they were pulled from
    # the active list (see cpv.yaml's F4 note) — no point matching on a dead code.
    retired = {"45216129", "45216230", "45421144"}
    assert retired.isdisjoint(set(config.cpv_codes()))


# ── F4 post-launch refinement: BOAMP never carries CPV codes at all, so
# cpv_prefix alone can't catch its construction notices — category and a
# narrow term list close that gap (see config/exclusions.yaml's note).

def test_works_category_is_excluded_even_with_no_cpv_code_at_all():
    # BOAMP shape: no CPV, category comes from its own type_marche field.
    rec = _rec("Travaux de renovation des ateliers techniques CLSH", category="Works")
    assert filters.apply_filters(rec, EXCLUSIONS) == "construction_works"


def test_construction_phrase_is_excluded_even_when_category_is_wrong():
    # real BOAMP case: title says "Marche de travaux..." but type_marche was
    # mis-tagged as Fournitures (Supply) at the source.
    rec = _rec("Marche de travaux dans le cadre de la renaturation du campus", category="Supply")
    assert filters.apply_filters(rec, EXCLUSIONS) == "construction_works"


def test_bare_travaux_mention_in_a_services_notice_is_kept():
    # a project-management/oversight "mission" that merely mentions travaux in
    # passing is not itself a works contract — category says Services, and the
    # term list deliberately only matches multi-word construction phrases, not
    # the bare word (same shape as F2's rental scoping).
    rec = _rec("Mission de maitrise d'oeuvre pour la realisation de travaux de mise en securite",
               category="Services")
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_bare_realisation_mention_in_a_supply_notice_is_kept():
    rec = _rec("Marche de fournitures pour les travaux en regie", category="Supply")
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_tent_notice_with_non_works_category_is_kept():
    rec = _rec("Army field tent, qty 200", cpv_codes=["39522530"], category="Supply")
    assert filters.apply_filters(rec, EXCLUSIONS) is None


# ── F5: core-signal narrowing (D4 core list confirmed) ───────────────────────

def test_cpv_match_is_always_core_regardless_of_terms():
    rec = _rec("Tent supply", cpv_codes=["39522530"], match_source="cpv", matched_terms=[])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_both_match_is_core():
    rec = _rec("Tent supply", cpv_codes=["39522530"], match_source="both",
                matched_terms=["tent"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_keyword_only_with_distinctive_term_is_kept():
    # 'tent' is in the distinctive subset — a real core signal even without CPV.
    rec = _rec("Tent supply", match_source="keyword", matched_terms=["tent"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_keyword_only_with_generic_term_is_demoted():
    # 'bivouac' is in the broad terms library but NOT the distinctive subset —
    # mechanical noise per the CR, not a real tent/shelter signal on its own.
    rec = _rec("Bivouac equipment maintenance", match_source="keyword",
                matched_terms=["bivouac"])
    assert filters.apply_filters(rec, EXCLUSIONS) == "no_core_signal"


def test_keyword_only_mixed_generic_and_distinctive_is_kept():
    # as long as ONE matched term is distinctive, the record is core.
    rec = _rec("Tent and canopy supply", match_source="keyword",
                matched_terms=["canopy", "tent"])
    assert filters.apply_filters(rec, EXCLUSIONS) is None


def test_no_match_source_is_untouched_by_this_check():
    # match_source=None means nothing matched at all — not F5's concern.
    rec = _rec("Unrelated notice", match_source=None, matched_terms=[])
    assert filters.apply_filters(rec, EXCLUSIONS) is None
