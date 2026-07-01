"""TED confirmation — verifies the narrowed query (CPV OR title=distinctive).

Run from the project root:  python scratch_ted.py

Single-page call: prints the total count (should be ~384, close to CPV-only 378) and
dumps the first notice in full — now a genuinely relevant tender we can build Step 7's
normaliser against. Delete once confirmed.
"""
from datetime import date, timedelta
import sys, json
sys.path.insert(0, "src")

import requests
import config
from connectors import ted

since = date.today() - timedelta(days=30)
query = ted.build_query(config.cpv_codes(), config.distinctive_keywords(), since)

print("=== QUERY ===")
print(query[:500] + (" ..." if len(query) > 500 else ""))
print()

body = {
    "query": query,
    "fields": ted.FIELDS,
    "limit": 10,
    "scope": "ACTIVE",
    "paginationMode": "ITERATION",
    "checkQuerySyntax": False,
}
resp = requests.post(ted.ENDPOINT, json=body, timeout=60)
print("=== HTTP STATUS ===", resp.status_code)
if resp.status_code != 200:
    print(resp.text[:1500]); sys.exit()

data = resp.json()
print("=== totalNoticeCount ===", data.get("totalNoticeCount"), " (expect ~384)")
notices = ted.parse_response(data)
print("=== NOTICES ON THIS PAGE ===", len(notices))

if notices:
    print("\n=== FIRST NOTICE (full structure) ===")
    print(json.dumps(notices[0], indent=2, ensure_ascii=False))
