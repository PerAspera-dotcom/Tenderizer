"""Field probe — discover the real TED field aliases we need for Step 7.

Run from the project root:  python scratch_fields.py

We need: nature of contract (Supply/Services/Works), submission deadline, buyer country,
procedure type, and the description text. Their exact API field names aren't documented
clearly, so we probe candidates one at a time and print which are accepted and what the
value looks like on a real notice.
"""
from datetime import date, timedelta
import sys, json
sys.path.insert(0, "src")

import requests
import config
from connectors import ted

since = date.today() - timedelta(days=30)
query = ted.build_query(config.cpv_codes(), config.distinctive_keywords(), since)

candidates = [
    # nature of contract (Supply / Services / Works)
    "contract-nature", "nature-of-contract", "main-nature",
    # submission deadline
    "deadline-receipt-tenders", "deadline-receipt-request", "deadline-date-lot", "deadline",
    # buyer country
    "buyer-country", "organisation-country", "country",
    # procedure + description + value (useful extras)
    "procedure-type", "description-lot", "description-proc",
    "estimated-value-lot", "total-value",
]

def probe(field):
    body = {"query": query, "fields": ["publication-number", field], "limit": 1,
            "scope": "ACTIVE", "paginationMode": "ITERATION", "checkQuerySyntax": False}
    r = requests.post(ted.ENDPOINT, json=body, timeout=60)
    if r.status_code != 200:
        try:
            return "INVALID", r.json().get("message", r.text[:80])
        except Exception:
            return "INVALID", r.text[:80]
    notices = ted.parse_response(r.json())
    if notices and field in notices[0]:
        val = json.dumps(notices[0][field], ensure_ascii=False)
        return "OK", val[:140]
    return "valid-but-empty", "(field accepted; no value on this notice)"

for f in candidates:
    status, info = probe(f)
    print(f"{f:28} {status:16} {info}")
