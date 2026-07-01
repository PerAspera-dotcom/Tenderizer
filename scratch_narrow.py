"""Diagnostic: find the keyword-search configuration that narrows the flood.

Run from the project root:  python scratch_narrow.py

Compares field (FT=full text vs notice-title) x operator (IN=stemmed vs ==exact)
x keyword set (all vs distinctive-only), so we can pick a sane safeguard query.
Each call uses limit=1 (reads totalNoticeCount only).
"""
from datetime import date, timedelta
import sys, requests
sys.path.insert(0, "src")
import config
from connectors import ted

since = date.today() - timedelta(days=30)
d = since.strftime("%Y%m%d")
cpv = config.cpv_codes()
allkw = config.keywords()

# distinctive subset (unambiguous tent/cover words; the common ones dropped)
distinctive = [
    "tent", "tents", "tente", "tentes", "chapiteau", "chapiteaux", "marquee",
    "Zelt", "Zelte", "Zelten", "tarpaulin", "tarpaulins", "bache", "baches",
    "dekzeil", "dekzeilen", "camouflage", "Tarnung", "Tarnnetz", "Sonnensegel",
    "Markise", "Markisen", "campement", "schuiltent", "gazebo",
]

def count(query):
    body = {"query": query, "fields": ["publication-number"], "limit": 1,
            "scope": "ACTIVE", "paginationMode": "ITERATION", "checkQuerySyntax": False}
    r = requests.post(ted.ENDPOINT, json=body, timeout=60)
    if r.status_code != 200:
        return f"ERR {r.status_code}: {r.text[:120]}"
    return r.json().get("totalNoticeCount")

def lst(terms, op):
    joiner = " OR " if op != "=" else " OR "
    return "(" + joiner.join(f'"{t}"' for t in terms) + ")"

cpv_q = f'classification-cpv IN ({" ".join(cpv)}) AND publication-date>={d}'

tests = {
    "A. CPV only (baseline)":                 cpv_q,
    "B. FT IN all (current)":                 f'FT IN {lst(allkw,"IN")} AND publication-date>={d}',
    "C. FT = all (exact, no stem)":           f'FT = {lst(allkw,"=")} AND publication-date>={d}',
    "D. notice-title IN all (stemmed)":       f'notice-title IN {lst(allkw,"IN")} AND publication-date>={d}',
    "E. notice-title = all (exact)":          f'notice-title = {lst(allkw,"=")} AND publication-date>={d}',
    "F. notice-title = distinctive (exact)":  f'notice-title = {lst(distinctive,"=")} AND publication-date>={d}',
    "G. CPV OR (title=distinctive) PROPOSED": f'(classification-cpv IN ({" ".join(cpv)}) OR notice-title = {lst(distinctive,"=")}) AND publication-date>={d}',
}

for name, q in tests.items():
    print(f"{name:42} -> {count(q)}")
