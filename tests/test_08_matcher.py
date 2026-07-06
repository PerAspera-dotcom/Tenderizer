"""Step 8 — keyword matcher.
Interface:
  match.match_keywords(text:str, keywords:list[str]) -> list[str]
      whole-word, case-insensitive, accent-insensitive; returns the keywords that fired
  match.classify_match(has_cpv:bool, matched_keywords:list[str]) -> 'cpv'|'keyword'|'both'|None
"""
from match import match_keywords, classify_match

def test_matches_whole_word():
    assert match_keywords("Army field tent, qty 200", ["tent"]) == ["tent"]

def test_does_not_match_substring():
    # the correctness rule: 'tent' must NOT fire inside 'contents'
    assert match_keywords("Table of contents", ["tent"]) == []

def test_case_insensitive():
    assert match_keywords("LEVERING VAN ZELT", ["zelt"]) == ["zelt"]

def test_accent_insensitive():
    assert match_keywords("Fourniture de bâches", ["bache", "baches"]) == ["baches"]

def test_multilingual():
    got = match_keywords("Lieferung von Zelten und tentes", ["zelten", "tentes", "tent"])
    assert got == ["tentes", "zelten"]  # sorted; 'tent' must NOT match inside 'tentes'

def test_no_match_returns_empty():
    assert match_keywords("office furniture supply", ["tent", "zelt"]) == []

def test_classify_cpv_only():
    assert classify_match(True, []) == "cpv"

def test_classify_keyword_only():
    assert classify_match(False, ["tent"]) == "keyword"

def test_classify_both():
    assert classify_match(True, ["tent"]) == "both"

def test_classify_none_when_nothing_matched():
    assert classify_match(False, []) is None


# ── French elision ("d'X" -> "de X") — real gap found 2026-07 via the
# calibration export: a phrase term ending in "de" was missing notices worded
# with the elided "d'" form (used before a vowel-starting word).

def test_elision_matches_the_apostrophe_form():
    assert match_keywords("Location d'engins de chantier", ["location de"]) == ["location de"]

def test_elision_does_not_break_the_unelided_form():
    assert match_keywords("Location de materiel", ["location de"]) == ["location de"]

def test_elision_the_actual_boamp_notice_that_was_missed():
    # real title (TED's English-page copy of a BOAMP notice) that slipped past
    # F2's rental exclusion before match.py folded elision — see
    # config/exclusions.yaml's rental note.
    tag_line = ("LOCATION D'ENGINS ET DE MATERIELS, DE VEHICULES UTILITAIRES, "
                "DE MOBILIER, DE MATERIELS DIVERS POUR LES RECEPTIONS ET EVENEMENTS "
                "DE LA VILLE DE TOURS ET DE TOURS METROPOLE VAL DE LOIRE")
    assert match_keywords(tag_line, ["location de", "en location"]) == ["location de"]

def test_elision_fold_does_not_fire_mid_word():
    # "aujourd'hui" has no word boundary before its "d'" (preceded by "r") —
    # must be left alone, not rewritten into nonsense like "aujourde hui".
    from match import _fold
    assert _fold("aujourd'hui") == "aujourd'hui"
    assert _fold("Location d'engins") == "location de engins"
