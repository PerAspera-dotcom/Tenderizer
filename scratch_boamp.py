"""BOAMP probe — learn the real record structure before building the connector.

Run from the project root:  python scratch_boamp.py

BOAMP = OpenDataSoft Explore v2.1 API (France). No API key needed. We make two calls:
 1) structure probe (newest records, no filter) -> dumps field NAMES + one full record
 2) keyword query probe -> confirms the ODSQL `where` syntax works for our French terms
Paste the output back so we can build normalize_boamp against the real fields.
"""
import json, requests

BASE = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"

print("=== 1) STRUCTURE PROBE (newest 2 records, no filter) ===")
r = requests.get(BASE, params={"limit": 2}, timeout=60)
print("HTTP", r.status_code)
if r.status_code == 200:
    data = r.json()
    print("total_count:", data.get("total_count"))
    results = data.get("results", [])
    if results:
        print("\nFIELD NAMES:")
        print(list(results[0].keys()))
        print("\nFIRST RECORD (truncated to 3500 chars):")
        print(json.dumps(results[0], ensure_ascii=False, indent=2)[:3500])
else:
    print(r.text[:800])

print("\n\n=== 2) KEYWORD QUERY PROBE (where='\"tente\"') ===")
r2 = requests.get(BASE, params={"where": '"tente"', "limit": 3,
                                "order_by": "dateparution desc"}, timeout=60)
print("HTTP", r2.status_code)
if r2.status_code == 200:
    d2 = r2.json()
    print("total_count:", d2.get("total_count"))
    for rec in d2.get("results", [])[:3]:
        # print whichever 'object/title' field exists so we can see relevance
        for k in ("objet", "intitule", "titre", "object"):
            if k in rec:
                print(f"  [{k}] {str(rec[k])[:120]}")
                break
else:
    print("(query syntax to adjust)  body:", r2.text[:500])
