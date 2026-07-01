"""Step 7 validation — fetch a few live notices, normalise them, and print.

Run from the project root:  python scratch_normalize.py

Confirms the normaliser produces clean records AND that contract-nature maps to a real
category (watch for 'Other' — that means map_category needs the real value added).
"""
from datetime import date, timedelta
import sys, json
sys.path.insert(0, "src")

import requests
import config
from connectors import ted
import normalize

since = date.today() - timedelta(days=30)
body = {
    "query": ted.build_query(config.cpv_codes(), config.distinctive_keywords(), since),
    "fields": ted.FIELDS, "limit": 8, "scope": "ACTIVE",
    "paginationMode": "ITERATION", "checkQuerySyntax": False,
}
resp = requests.post(ted.ENDPOINT, json=body, timeout=60)
resp.raise_for_status()
notices = ted.parse_response(resp.json())
print(f"fetched {len(notices)} notices\n")

print("=== raw contract-nature -> mapped category (watch for 'Other') ===")
for raw in notices:
    nat = raw.get("contract-nature")
    print(f"  {json.dumps(nat, ensure_ascii=False):<20} -> {normalize.map_category(normalize._first(nat))}")

print("\n=== normalised records ===")
for raw in notices:
    rec = normalize.normalize_ted(raw)
    print(json.dumps({k: rec[k] for k in
          ["pub_number","category","country","deadline","tag_line"]},
          ensure_ascii=False))
