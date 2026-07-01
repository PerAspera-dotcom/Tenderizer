"""Keyword matcher — the safeguard layer's client-side tagging.

Runs the full keyword library over a notice's text to record which terms fired and how a
notice qualified (CPV vs keyword). Whole-word, case-insensitive, and accent/umlaut-
insensitive (via unidecode), so 'tent' matches 'army tent' but NOT 'contents', and
'bache' matches 'bâche'. German umlaut and ue/ae/oe/ss forms both fold to the same base.
"""
import re
from unidecode import unidecode


def match_keywords(text, keywords):
    """Return the keywords that appear as whole words in `text` (accent/case-insensitive)."""
    norm = unidecode(text or "").lower()
    found = {kw for kw in keywords
             if re.search(r"\b" + re.escape(unidecode(kw).lower()) + r"\b", norm)}
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
