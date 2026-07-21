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


# A4 (D-A decided: CR-002's proposed defaults, not yet customer-confirmed).
# "prequalification"/"pre-qualification"/"PQQ"/"selection des candidats" are
# specific enough to stand alone. "procedure restreinte" (restricted
# procedure) is NOT — it's a common, generic French procedure label that
# appears on plenty of ordinary competitive tenders, not just prequalification
# rounds — so per the CR's own paired wording ("procédure restreinte — appel
# à candidatures") that term only counts alongside "appel a candidatures"
# (call for candidates), both present, not either alone.
_PREQUALIFICATION_TERMS = ["prequalification", "pre-qualification", "PQQ", "selection des candidats"]
_RESTRICTED_PROCEDURE_TERM = ["procedure restreinte"]
_CALL_FOR_CANDIDATES_TERM = ["appel a candidatures"]


def check_prequalification(rec):
    text = _text(rec)
    if match.match_keywords(text, _PREQUALIFICATION_TERMS):
        return "prequalification"
    if (match.match_keywords(text, _RESTRICTED_PROCEDURE_TERM)
            and match.match_keywords(text, _CALL_FOR_CANDIDATES_TERM)):
        return "prequalification"
    return None


# A3 (D-B decided: the customer's "FBO" ask maps to TED/BOAMP's real
# forward-looking notice type, Prior Information Notice — see CR-002 D-B).
# Stored as notice_type="fbo" to match the CR's own schema/precedence text;
# NoticeTypeBadge.tsx labels it "PIN" in the UI. Deliberately NOT matching
# the bare acronym "PIN" — unlike "EOI", it's a common short string with
# high collision risk (personal identification numbers, etc.) and nothing in
# the CR authorises it as a standalone term the way EOI's bare acronym was.
_PIN_TERMS = ["prior information notice", "avis de preinformation"]


def check_fbo_pin(rec):
    if match.match_keywords(_text(rec), _PIN_TERMS):
        return "fbo"
    return None


# Precedence (CR-002 A): past_tender -> prequalification -> eoi -> fbo -> tender.
CHECKS = [check_past_tender, check_prequalification, check_eoi, check_fbo_pin]


def classify(rec):
    """Run all active classification checks; first hit wins. Falls back to
    the default "tender" type, never blank (CR-002 A's own explicit rule).
    """
    for check in CHECKS:
        result = check(rec)
        if result:
            return result
    return DEFAULT_TYPE


# ── A1: award info extraction (TED/BOAMP award notices) ─────────────────────
#
# CR-003 G4: both connectors DO expose structured award fields — they just
# weren't fetched. normalize_ted (winner-name / result-value-notice /
# result-value-cur-notice, falling back to the per-lot tender-value(-cur) if
# the notice-level total is absent) and normalize_boamp (titulaire, and
# donnees' EFORMS.ContractAwardNotice...efac:NoticeResult.cbc:TotalAmount)
# populate raw_award_winner/raw_award_value/raw_award_currency on the
# normalized record — see normalize.py. Those are preferred here; the
# regex-over-text patterns below are the fallback for whichever field a
# structured source didn't supply (e.g. an older BOAMP schema with no CPV/
# award JSON at all — same permanent-gap case _boamp_cpv_codes documents).
# Never blocks classification: absence is null, never fabricated (CR-002 A1
# acceptance).

_NAME = r"([^\W\d_][\w &\-\.,'’]{2,80}?)"  # any Unicode letter, not just ASCII A-Z
_NUM = r"([\d][\d.,\s]*\d|\d)"
_CUR = r"(EUR|GBP|USD|€|\$|£)?"

_AWARDED_TO_PATTERNS = [
    re.compile(r"(?:contract awarded to|awarded to|successful tenderer:?)\s*" + _NAME + r"(?=[.\n]|$)", re.IGNORECASE),
    re.compile(r"attributaire\s*[:\-]?\s*" + _NAME + r"(?=[.\n]|$)", re.IGNORECASE),
]

_VALUE_PATTERNS = [
    # TED's own standard "Results" template string (not per-country prose).
    re.compile(r"value of all contracts awarded in this notice[:\s]*" + _NUM + r"\s*" + _CUR, re.IGNORECASE),
    re.compile(r"(?:contract value|awarded value|value of the contract)[:\s]*(?:of\s*)?(?:approximately\s*)?" + _NUM + r"\s*" + _CUR, re.IGNORECASE),
    re.compile(r"montant\s*(?:du march[eé]|total)?\s*[:\-]?\s*(?:de\s*)?" + _NUM + r"\s*" + _CUR, re.IGNORECASE),
]

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}


def extract_award_info(rec):
    """(awarded_to, awarded_value, awarded_currency, award_detail) — each
    None if not found.

    Structured fields (rec's raw_award_* keys, set by normalize.py from the
    connector's own winner/result-value fields) win per-field; regex-over-
    text fills in whichever field a structured source didn't supply.
    Independent per field: an awarded_to hit and a value hit can come from
    different patterns/languages in the same bilingual notice.

    award_detail (winner registration number/city/NUTS/size, lot/contract
    identifiers, framework max value — past-tenders data-coverage follow-up)
    has no regex fallback: it's only ever the structured connector data
    (normalize.py's _ted_award_detail/_boamp_award_detail), already scoped to
    single-lot/single-winner notices there — never guessed from free text.
    """
    awarded_to = rec.get("raw_award_winner") or None
    awarded_value = rec.get("raw_award_value") or None
    awarded_currency = rec.get("raw_award_currency") or None
    award_detail = rec.get("raw_award_detail") or None
    if awarded_to and awarded_value and awarded_currency:
        return awarded_to, awarded_value, awarded_currency, award_detail

    text = _text(rec)
    if not awarded_to:
        for pattern in _AWARDED_TO_PATTERNS:
            m = pattern.search(text)
            if m:
                awarded_to = m.group(1).strip().rstrip(",.;")
                break

    if not (awarded_value and awarded_currency):
        for pattern in _VALUE_PATTERNS:
            m = pattern.search(text)
            if m:
                if not awarded_value:
                    awarded_value = re.sub(r"\s+", "", m.group(1)).strip(".,")
                currency = m.group(2)
                if currency and not awarded_currency:
                    awarded_currency = _CURRENCY_SYMBOLS.get(currency, currency.upper())
                break

    return awarded_to, awarded_value, awarded_currency, award_detail
