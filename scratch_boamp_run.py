"""Step 10 validation — live BOAMP fetch + normalise.

Run from the project root:  python scratch_boamp_run.py
Confirms the ODSQL query works, category mapping, and the notice URL. Click a printed
URL to verify it opens the real BOAMP notice (we learned this lesson with TED).
"""
from datetime import date, timedelta
from collections import Counter
import sys
sys.path.insert(0, "src")

import config
from connectors import boamp
import normalize

since = date.today() - timedelta(days=30)
print("fetching from BOAMP ...")
raws = boamp.fetch(config.cpv_codes(), config.distinctive_keywords(), since)
print(f"  {len(raws)} records")

records = [normalize.normalize_boamp(r) for r in raws]
print("by category:", dict(Counter(r["category"] for r in records)))

print("\nsample (first 5):")
for rec in records[:5]:
    print(f"  [{rec['category']:8}] {rec['pub_number']:10} {rec['deadline'][:10]:10} {rec['tag_line'][:60]}")
    print(f"            {rec['url']}")
