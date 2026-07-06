"""Keyword matcher — the safeguard layer's client-side tagging.

Runs the full keyword library over a notice's text to record which terms fired and how a
notice qualified (CPV vs keyword). Whole-word, case-insensitive, and accent/umlaut-
insensitive (via unidecode), so 'tent' matches 'army tent' but NOT 'contents', and
'bache' matches 'bâche'. German umlaut and ue/ae/oe/ss forms both fold to the same base.

Also folds French elision: "d'engins"/"d'équipements" -> "de engins"/"de equipements" —
verified live (2026-07): a phrase term like "location de" was silently missing notices
worded "location d'engins", since which form a French notice uses depends only on whether
the next word starts with a vowel, not on the notice's actual meaning. Same fold applies
everywhere match_keywords is used (the core tent/shelter keyword library and every
exclusion list alike) — like unidecode's accent-folding, it can only make a real match
match, never introduce a new false-positive substring that wasn't already there un-elided.
"""
import re
from unidecode import unidecode

_ELISION = re.compile(r"\bd'")


def _fold(text):
    folded = unidecode(text or "").lower()
    return _ELISION.sub("de ", folded)


def match_keywords(text, keywords):
    """Return the keywords that appear as whole words in `text` (accent/case-insensitive)."""
    norm = _fold(text)
    found = {kw for kw in keywords
             if re.search(r"\b" + re.escape(_fold(kw)) + r"\b", norm)}
    return sorted(found)


def classify_match(has_cpv, matched_keywords):
    """Tag how a notice qualified: 'cpv', 'keyword', 'both', or None."""
    kw = bool(matched_keywords)
    if has_cpv and kw:
        return "both"
    if has_cpv:
        return "cpv"
    if kw:
        return "keyword"
    return None
