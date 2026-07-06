"""BOAMP (France) connector — OpenDataSoft Explore v2.1 API.

Verified live (June 2026):
  GET https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records
  Params: where (ODSQL), limit (<=100), offset (limit+offset<=10000), order_by.
  Response: {"total_count": N, "results": [ {flat fields}, ... ]}.
  No API key. Notices are French, single-language (no multilingual nesting).

The flat top-level fields have no clean CPV field (just BOAMP's own 'descripteur'
taxonomy) — but CPV codes ARE present, buried in the `donnees` field (a JSON-encoded
string of the notice's full source XML); normalize.normalize_boamp's _boamp_cpv_codes
extracts them (verified live, 2026-07 — see its docstring for the schema shapes).
cpv_codes is still accepted here for interface parity with the TED connector but isn't
used in the query — BOAMP's search API has no CPV filter, only full-text `where`.
"""
import requests

ENDPOINT = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"


def build_params(cpv_codes, keywords, since, limit=100, offset=0):
    """Build the ODS request params: distinctive keywords (full text) since `since`.

    Argument order (cpv_codes, keywords, since) matches ted.fetch/build_query
    for interface parity between the two connectors — cpv_codes itself isn't
    used in the query (see module docstring), it's accepted purely so a
    caller can invoke both connectors the same way.
    """
    terms = " OR ".join(f'"{k}"' for k in keywords)
    where = f"({terms}) AND dateparution >= date'{since.isoformat()}'"
    return {"where": where, "limit": limit, "offset": offset, "order_by": "dateparution desc"}


def parse_response(json_data):
    """Return the list of raw record dicts; [] if none."""
    return json_data.get("results") or []


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Live, paginated pull (offset paging). max_records is a safety cap."""
    out, offset = [], 0
    while True:
        params = build_params(cpv_codes, keywords, since, limit=limit, offset=offset)
        resp = requests.get(ENDPOINT, params=params, timeout=60)
        resp.raise_for_status()
        results = parse_response(resp.json())
        out.extend(results)
        offset += limit
        if not results or len(out) >= max_records or offset >= 10000:
            break
    return out[:max_records]
