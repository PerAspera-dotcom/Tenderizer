"""TED (Tenders Electronic Daily) connector.

Verified against docs.ted.europa.eu (June 2026):
  - Endpoint: POST https://api.ted.europa.eu/v3/notices/search
  - No API key required for reading published notices.
  - Body: query (expert query), fields (list), limit, scope ("ACTIVE" = live/recent),
    paginationMode ("ITERATION" with iterationNextToken), checkQuerySyntax.
  - Query language: field expressions + AND/OR/NOT + parentheses.

RELEVANCE STRATEGY (after measuring against the live API):
  - PRIMARY net = CPV codes: classification-cpv IN (...). Precise, language-independent.
  - SAFEGUARD = distinctive keywords matched in the TITLE with the exact '=' operator:
    notice-title = ("tent" OR "zelt" OR ...). This catches mis-coded tenders that have an
    unambiguous tent word in the title.
  - We deliberately DO NOT use full-text 'FT IN (...)': FT searches the whole notice body
    across ~24 languages AND the IN operator stems terms, which floods to ~75k matches.
    notice-title + '=' keeps the pull tight (CPV OR title=distinctive ≈ 384).
  - publication-date must be YYYYMMDD (pattern [0-9]{8}|today(...)).

TO CONFIRM when building Step 7: notice-title and place-of-performance come back as
multilingual / array structures (3-letter lang keys like 'eng','fra','nld'); pick a
language in normalize_ted. FIELDS may need country/deadline fields added.
"""
import requests

ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"

# Fields to return — confirmed via live field probe (June 2026).
# estimated-value-proc / estimated-value-cur-proc (CR-001 F6) verified via a live
# probe of the actual v3 API's UNSUPPORTED_VALUE error, which lists every valid
# field name — 'estimated-value' alone is NOT valid; per-notice-scope suffixes are
# required, and '-proc' is the procedure-level BT-27 (pre-award estimate).
#
# CR-003 G4 (2026-07 live re-probe): award-result fields were originally excluded
# here as "out of scope for open notices" — before CR-002 A1 needed them for
# *past/awarded* notices. Re-probing the same UNSUPPORTED_VALUE field list found
# the real names: winner-name (multilingual dict, same shape as notice-title),
# result-value-notice/result-value-cur-notice (the notice-level award total —
# confirmed against TED 391890-2026: "45290.32"/"EUR", matching CR-003's quoted
# "Value of all contracts awarded in this notice"), and tender-value/
# tender-value-cur (per-lot, kept as a fallback for notices with no
# notice-level total). See normalize.py's normalize_ted for how these feed
# classification.extract_award_info.
FIELDS = [
    "publication-number", "notice-title", "description-proc",
    "buyer-name", "buyer-country", "contract-nature", "procedure-type", "notice-type",
    "deadline-receipt-request", "place-of-performance", "classification-cpv", "links",
    "estimated-value-proc", "estimated-value-cur-proc",
    "winner-name", "result-value-notice", "result-value-cur-notice",
    "tender-value", "tender-value-cur",
]


def build_query(cpv_codes, keywords, since):
    """Primary net (CPV) OR safeguard (distinctive keywords in the title), since `since`.

    `keywords` should be the distinctive subset (config.distinctive_keywords()).
    """
    cpv_part = "classification-cpv IN (" + " ".join(cpv_codes) + ")"
    title_part = "notice-title = (" + " OR ".join(f'"{k}"' for k in keywords) + ")"
    date_part = f"publication-date>={since.strftime('%Y%m%d')}"
    return f"({cpv_part} OR {title_part}) AND {date_part}"


def parse_response(json_data):
    """Return the list of raw notice dicts; [] if none."""
    return json_data.get("notices") or json_data.get("results") or []


def fetch(cpv_codes, keywords, since, fields=FIELDS, limit=100, max_records=2000):
    """Live, paginated pull. Returns a flat list of raw notice dicts.

    max_records is a safety cap so a bad/broad query can never try to pull tens of
    thousands of notices.
    """
    query = build_query(cpv_codes, keywords, since)
    out, token = [], None
    while True:
        body = {
            "query": query, "fields": fields, "limit": limit,
            "scope": "ACTIVE", "paginationMode": "ITERATION",
            "checkQuerySyntax": False,
        }
        if token:
            body["iterationNextToken"] = token
        resp = requests.post(ENDPOINT, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        notices = parse_response(data)
        out.extend(notices)
        token = data.get("iterationNextToken")
        if not token or not notices or len(out) >= max_records:
            break
    return out[:max_records]
