"""Step 14 — keyword sync + terms/codes consistency check (CR-001 F8).
  config.term_code_gaps() -> {"codes_without_terms": [...], "terms_without_codes": [...]}
  Report-only: a broad safeguard term with no single matching code (e.g. 'gazebo')
  is expected, not a bug. The acceptance bar is codes_without_terms == [] for the
  F7-added codes, and F3-orphaned terms being fully gone.
"""
import config

F7_ADDED = {"35800000", "39511100", "39522520", "39522540"}

# cabin/cubicle/portable (+ FR/NL/DE forms) — orphaned by F3: their only source CPV
# codes (44211110, 44211200, 44211100) are now exclusion-only, not active.
ORPHANED_BY_F3 = {"cabin", "cabins", "cubicle", "cubicles", "portable",
                  "cabine", "cabines", "Kabine", "Kabinen", "Kleinkabine", "Kleinkabinen"}


def test_f7_codes_have_no_gap():
    gaps = config.term_code_gaps()
    assert F7_ADDED.isdisjoint(gaps["codes_without_terms"])


def test_orphaned_terms_fully_removed():
    assert set(config.keywords()).isdisjoint(ORPHANED_BY_F3)


def test_each_new_code_has_all_four_languages_represented():
    terms = config._load("keywords.yaml")["terms"]
    expected = {
        "en": {"blanket", "blankets", "camp bed", "camp beds",
               "sleeping bag", "sleeping bags", "individual equipment", "support equipment"},
        "fr": {"couverture", "couvertures", "lit de camp", "lits de camp",
               "sac de couchage", "sacs de couchage", "equipement individuel",
               "equipement de soutien"},
        "nl": {"deken", "dekens", "veldbed", "veldbedden", "slaapzak", "slaapzakken",
               "persoonlijke uitrusting", "ondersteunende uitrusting"},
        "de": {"Decke", "Decken", "Feldbett", "Feldbetten", "Schlafsack", "Schlafsäcke",
               "persönliche Ausrüstung", "Hilfsausrüstung"},
    }
    for lang, want in expected.items():
        assert want <= set(terms[lang])


def test_consistency_check_shape():
    gaps = config.term_code_gaps()
    assert set(gaps) == {"codes_without_terms", "terms_without_codes"}
    assert isinstance(gaps["codes_without_terms"], list)
    assert isinstance(gaps["terms_without_codes"], list)
