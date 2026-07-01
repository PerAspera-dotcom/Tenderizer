"""Diagnostic: measure how much each layer (CPV vs keywords) contributes to the result
count, and which individual keywords flood. Run from the project root:

    python scratch_counts.py

Uses limit=1 (we only read totalNoticeCount), so it's cheap. ~33 quick calls.
"""
from datetime import date, timedelta
import sys, requests
sys.path.insert(0, "src")
import config
from connectors import ted

since = date.today() - timedelta(days=30)
d = since.strftime("%Y%m%d")
cpv = config.cpv_codes()
kw = config.keywords()

def count(query):
    body = {"query": query, "fields": ["publication-number"], "limit": 1,
            "scope": "ACTIVE", "paginationMode": "ITERATION", "checkQuerySyntax": False}
    r = requests.post(ted.ENDPOINT, json=body, timeout=60)
    if r.status_code != 200:
        return None
    return r.json().get("totalNoticeCount")

cpv_q = f'classification-cpv IN ({" ".join(cpv)}) AND publication-date>={d}'
kw_q  = 'FT IN (' + " OR ".join(f'"{k}"' for k in kw) + f') AND publication-date>={d}'
both_q = ted.build_query(cpv, kw, since)

print("=== LAYER TOTALS (last 30 days, scope ACTIVE) ===")
print(f"CPV-only     : {count(cpv_q)}")
print(f"Keyword-only : {count(kw_q)}")
print(f"Combined     : {count(both_q)}")

print("\n=== PER-KEYWORD COUNTS (sorted, biggest floods first) ===")
rows = []
for k in kw:
    c = count(f'FT IN ("{k}") AND publication-date>={d}')
    rows.append((c if c is not None else -1, k))
for c, k in sorted(rows, reverse=True):
    print(f"{c:>8}  {k}")
