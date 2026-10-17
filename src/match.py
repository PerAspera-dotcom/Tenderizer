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


_INFLECTION = r"(?:e|en|er|es|n|s)?"


def _compound_hit(kw, norm, compounds):
    """True if folded keyword `kw` sits inside a longer word of `norm` in a
    position `compounds` allows (head: end of word + inflection; modifier:
    start of word), skipping whole words on its "not" list."""
    stem = re.escape(kw)
    patterns = []
    if kw in compounds["head"]:
        patterns.append(r"\b\w+" + stem + _INFLECTION + r"\b")
    if kw in compounds["modifier"]:
        patterns.append(r"\b" + stem + r"\w+")
    return any(m.group(0) not in compounds["not"]
               for p in patterns for m in re.finditer(p, norm))


def match_keywords(text, keywords, compounds=None):
    """Return the keywords that appear as whole words in `text` (accent/case-insensitive).

    `compounds` ({"head", "modifier", "not"}, see keywords.yaml) additionally lets the
    listed keywords match inside German/Dutch compound words. Off by default: the
    exclusion and classification term lists stay whole-word.
    """
    norm = _fold(text)
    if compounds:
        compounds = {k: {_fold(w) for w in compounds.get(k) or []} for k in ("head", "modifier", "not")}
    found = set()
    for kw in keywords:
        folded = _fold(kw)
        if (re.search(r"\b" + re.escape(folded) + r"\b", norm)
                or (compounds and _compound_hit(folded, norm, compounds))):
            found.add(kw)
    return sorted(found)


def match_relevance(text, keywords):
    """match_keywords with keywords.yaml's compound stems — for tender relevance
    (tagging and connector keyword reinforcement), not exclusion lists."""
    import config  # local import: config is the YAML loader, not needed by the exclusion callers
    return match_keywords(text, keywords, compounds=config.compound_keywords())


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
