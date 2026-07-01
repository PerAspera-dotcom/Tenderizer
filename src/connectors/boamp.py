"""BOAMP (France) connector — OpenDataSoft Explore v2.1 API.

Verified live (June 2026):
  GET https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records
  Params: where (ODSQL), limit (<=100), offset (limit+offset<=10000), order_by.
  Response: {"total_count": N, "results": [ {flat fields}, ... ]}.
  No API key. Notices are French, single-language (no multilingual nesting).

BOAMP exposes no clean CPV field (it uses its own 'descripteur' taxonomy), so relevance
here is keyword full-text on the notice. cpv_codes is accepted for interface parity with
the TED connector but is not used in the query — BOAMP matches are tagged keyword-based.
"""
import requests

ENDPOINT = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"


def build_params(keywords, cpv_codes, since, limit=100, offset=0):
    """Build the ODS request params: distinctive keywords (full text) since `since`."""
    terms = " OR ".join(f'"{k}"' for k in keywords)
    where = f"({terms}) AND dateparution >= date'{since.isoformat()}'"
    return {"where": where, "limit": limit, "offset": offset, "order_by": "dateparution desc"}


def parse_response(json_data):
    """Return the list of raw record dicts; [] if none."""
    return json_data.get("results") or []


def fetch(keywords, cpv_codes, since, limit=100, max_records=2000):
    """Live, paginated pull (offset paging). max_records is a safety cap."""
    out, offset = [], 0
    while True:
        params = build_params(keywords, cpv_codes, since, limit=limit, offset=offset)
        resp = requests.get(ENDPOINT, params=params, timeout=60)
        resp.raise_for_status()
        results = parse_response(resp.json())
        out.extend(results)
        offset += limit
        if not results or len(out) >= max_records or offset >= 10000:
            break
    return out[:max_records]
