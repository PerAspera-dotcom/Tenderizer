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
