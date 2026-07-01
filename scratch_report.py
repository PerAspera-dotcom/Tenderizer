"""Step 9 payoff — run the whole pipeline so far and write a real Excel report.

Run from the project root:  python scratch_report.py
Produces reports/tenders.xlsx : live TED tent tenders, split Supply/Services/Works,
each tagged with matched terms and match-source.
"""
from datetime import date, timedelta
import os, sys
sys.path.insert(0, "src")

import config
from connectors import ted
import normalize, match, report

os.makedirs("reports", exist_ok=True)
since = date.today() - timedelta(days=30)

print("fetching from TED ...")
raw_notices = ted.fetch(config.cpv_codes(), config.distinctive_keywords(), since)
print(f"  {len(raw_notices)} notices")

full_kw = config.keywords()
cpv_set = set(config.cpv_codes())
records = []
for raw in raw_notices:
    rec = normalize.normalize_ted(raw)
    text = f"{rec['tag_line']} {rec.get('description', '')}"
    hits = match.match_keywords(text, full_kw)
    has_cpv = bool(set(rec["cpv_codes"]) & cpv_set)
    rec["matched_terms"] = hits
    rec["match_source"] = match.classify_match(has_cpv, hits)
    records.append(rec)

# quick breakdown
from collections import Counter
print("by category:", dict(Counter(r["category"] for r in records)))
print("by match   :", dict(Counter(r["match_source"] for r in records)))

health = {"TED": f"ok ({len(records)})"}
out = report.build_report(records, health, "reports/tenders.xlsx")
print(f"\nwrote {out}")
