"""Notice-type classification (CR-002 §A) — additive tagging pass.

Runs after matching/filtering (CR-001 §A, see filters.py): it tags what's
already kept (or excluded), it never changes what gets kept. A notice
matches at most one type; checks run in `CHECKS` order, first match wins
(the CR's own precedence: past_tender is checked first since an empty
deadline is the least ambiguous signal). A notice matching nothing gets the
default "tender" — never left blank.

Each check function has the signature (rec) -> type_string | None, mirroring
filters.py's (rec, exclusions, now) -> reason | None shape (minus the two
args this module's checks don't need).
"""
import re
import match

DEFAULT_TYPE = "tender"


def _text(rec):
    return f"{rec.get('tag_line', '')} {rec.get('description', '')}"


def check_past_tender(rec):
    """A1 — identifier: empty deadline field. Historical/awarded, not active."""
    if not (rec.get("deadline") or "").strip():
        return "past_tender"
    return None


# A2 — Expressions of Interest. "EOI" is specific enough to stay a bare
# word-boundary term (unlike F2's "location", CR-002 doesn't flag an EOI
# false-positive risk). FR term is written unaccented — match.match_keywords'
# _fold (unidecode + French elision) normalises both the term and the source
# text before comparing, so "d'interet" here still matches "d'intérêt" in
# the notice.
_EOI_TERMS = ["expression of interest", "EOI", "appel a manifestation d'interet"]


def check_eoi(rec):
    if match.match_keywords(_text(rec), _EOI_TERMS):
        return "eoi"
    return None


CHECKS = [check_past_tender, check_eoi]


def classify(rec):
    """Run all active classification checks; first hit wins. Falls back to
    the default "tender" type, never blank (CR-002 A's own explicit rule).
    """
    for check in CHECKS:
        result = check(rec)
        if result:
            return result
    return DEFAULT_TYPE


# ── A1: award info extraction (best-effort, TED/BOAMP award notices) ────────
#
# Neither connector maps a dedicated "winner"/"awarded value" field today —
# normalize.py only carries the pre-award estimated value (CR-001 F6's
# value/value_currency). Award notices carry this in free text instead
# (tag_line/description), in fairly standard EN/FR phrasing (CR-002 A1) — so
# this is regex-over-text, not a structured field read. Never blocks
# classification: absence is null, never fabricated (CR-002 A1 acceptance).

_NAME = r"([A-Z][\w &\-\.,'’]{2,80}?)"
_NUM = r"([\d][\d.,\s]*\d|\d)"
_CUR = r"(EUR|GBP|USD|€|\$|£)?"

_AWARDED_TO_PATTERNS = [
    re.compile(r"(?:contract awarded to|awarded to|successful tenderer:?)\s*" + _NAME + r"(?=[.\n]|$)", re.IGNORECASE),
    re.compile(r"attributaire\s*[:\-]?\s*" + _NAME + r"(?=[.\n]|$)", re.IGNORECASE),
]

_VALUE_PATTERNS = [
    re.compile(r"(?:contract value|awarded value|value of the contract)[:\s]*(?:of\s*)?(?:approximately\s*)?" + _NUM + r"\s*" + _CUR, re.IGNORECASE),
    re.compile(r"montant\s*(?:du march[eé]|total)?\s*[:\-]?\s*(?:de\s*)?" + _NUM + r"\s*" + _CUR, re.IGNORECASE),
]

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}


def extract_award_info(rec):
    """(awarded_to, awarded_value, awarded_currency) — each None if not found.

    Independent per field: an awarded_to hit and a value hit can come from
    different patterns/languages in the same bilingual notice.
    """
    text = _text(rec)
    awarded_to = None
    for pattern in _AWARDED_TO_PATTERNS:
        m = pattern.search(text)
        if m:
            awarded_to = m.group(1).strip().rstrip(",.;")
            break

    awarded_value = None
    awarded_currency = None
    for pattern in _VALUE_PATTERNS:
        m = pattern.search(text)
        if m:
            awarded_value = re.sub(r"\s+", "", m.group(1)).strip(".,")
            currency = m.group(2)
            if currency:
                awarded_currency = _CURRENCY_SYMBOLS.get(currency, currency.upper())
            break

    return awarded_to, awarded_value, awarded_currency
