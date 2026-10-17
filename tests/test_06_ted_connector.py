"""Step 6 — TED connector.
Interface:
  connectors.ted.build_query(cpv_codes:list[str], keywords:list[str], since:date) -> str
  connectors.ted.parse_response(json_data:dict) -> list[dict]   # the raw notice dicts
  connectors.ted.fetch(cpv_codes, keywords, since) -> list[dict] # live, paginated (network)

Note: TED requires publication-date as YYYYMMDD (no dashes), pattern [0-9]{8}|today(...).
"""
from datetime import date
import pytest
from connectors import ted

CPV = ["35522000", "39522500"]
KW = ["tent", "Zelt"]
SINCE = date(2026, 6, 1)

def test_build_query_includes_every_cpv_code():
    q = ted.build_query(CPV, KW, SINCE)
    for code in CPV:
        assert code in q

def test_build_query_includes_every_keyword():
    q = ted.build_query(CPV, KW, SINCE)
    for k in KW:
        assert k in q

def test_build_query_ors_cpv_and_keyword_layers():
    # the safeguard: a tender should qualify on CPV OR on keyword
    assert "OR" in ted.build_query(CPV, KW, SINCE)

def test_build_query_includes_since_date_as_yyyymmdd():
    # TED wants YYYYMMDD with no dashes
    assert "20260601" in ted.build_query(CPV, KW, SINCE)

def test_parse_response_extracts_all_notices(sample_ted_api_json):
    recs = ted.parse_response(sample_ted_api_json)
    assert len(recs) == 2
    assert {r["publication-number"] for r in recs} == {"1-2026", "2-2026"}

def test_parse_response_empty_when_no_notices():
    assert ted.parse_response({"totalNoticeCount": 0}) == []

@pytest.mark.network
def test_fetch_live_returns_records():
    recs = ted.fetch(CPV, KW, SINCE)
    assert isinstance(recs, list)
    if recs:
        assert "publication-number" in recs[0]


def test_fields_request_publication_date():
    # normalize_ted reads publication-date; it was never requested, so every
    # TED tender stored an empty pub_date (found 2026-10-17).
    assert "publication-date" in ted.FIELDS


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-07+02:00", "2026-10-07"),   # live API shape
    (["2026-10-07+02:00"], "2026-10-07"),
    ("20260603", "2026-06-03"),           # query/fixture shape
    (None, ""),
])
def test_normalize_ted_pub_date_is_iso(raw, expected):
    import normalize
    assert normalize._ted_pub_date(raw) == expected
