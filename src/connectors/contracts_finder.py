"""UK — Contracts Finder connector.

CR-011 §4: SOURCES_VIABILITY.md originally rated this amber (behind a
Sid4Gov account) going only off the generic developer docs, which describe
OAuth2 client-credentials for the notice-SUBMISSION side of the API. The
actual READ side is a separate, fully public endpoint — confirmed live
(2026-10-16) by calling it with no auth header at all and getting real
release data back, the same eSender-submits/anyone-reads split Find a
Tender Service has. Upgraded to green on that finding.

  GET https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search
  Params confirmed live: stages, publishedFrom/publishedTo (ISO 8601),
  limit (1-100), cursor. No API key, no login. Published under OGL —
  confirmed in the response's own top-level `license` field (same license
  field Find a Tender's package carries).

  Response shape (OCDS release, confirmed live against a real release):
  simpler than Find a Tender's — `buyer` is a top-level object directly
  (not just a role inside `parties[]`), and `tender.value` was present
  directly on the one real example pulled (not only per-lot) — still falls
  back to a lot's value when absent, same defensive stance
  find_a_tender.py takes, in case a lotted notice behaves like FTS's does.

NOTICE URL: `tender.documents[]` carries an entry with
`documentType == "tenderNotice"` whose `url` is the real
`/Notice/{guid}` page — confirmed live, used directly rather than
constructed, since the GUID in that URL does NOT equal the release's own
top-level `id` verbatim (confirmed: release id carried a trailing
"-nnnnnn" suffix the document URL's GUID didn't have) — building the URL
from `id` would have been subtly wrong.

CPV: same as Find a Tender — UK's post-Brexit vocabulary is still CPV,
bare codes, no mapping needed.

RATE LIMIT: confirmed documented (not independently tested against the
live limit) — HTTP 403 after exceeding it, 5-minute cooldown. No special
handling added here (requests.raise_for_status() surfaces it as any other
HTTP error, caught by run.py's per-source try/except same as every
connector's failure mode).

Relationship to Find a Tender Service: this is Contracts Finder, NOT the
Procurement Act 2023 central platform (that's find_a_tender.py) — a
separate, older system still in active use for notices below FTS's
central-platform threshold. Registered as its own portal, not merged into
Find a Tender's; cross-portal dedup (CR-011 §2's existing, source-agnostic
description-similarity signal) applies to any FTS/Contracts Finder pair
the same way it already does for TED/BOAMP, no new code needed.
"""
import requests

import match

ENDPOINT = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"


def _cpv_codes(tender):
    codes = []
    classification = tender.get("classification") or {}
    if classification.get("scheme") == "CPV" and classification.get("id"):
        codes.append(classification["id"])
    for extra in tender.get("additionalClassifications") or []:
        if extra.get("scheme") == "CPV" and extra.get("id"):
            codes.append(extra["id"])
    return list(dict.fromkeys(codes))


def _value(tender):
    value = tender.get("value") or {}
    if not value.get("amount"):
        lots = tender.get("lots") or []
        if lots:
            value = lots[0].get("value") or {}
    return value.get("amount"), value.get("currency")


def _notice_url(tender):
    for doc in tender.get("documents") or []:
        if doc.get("documentType") == "tenderNotice" and doc.get("url"):
            return doc["url"]
    return ""


def build_params(since, limit=100):
    return {"stages": "tender", "publishedFrom": f"{since.isoformat()}T00:00:00", "limit": limit}


def parse_response(json_data):
    """Return the list of raw release dicts; [] if none."""
    return json_data.get("releases") or []


def _flatten(release):
    tender = release.get("tender") or {}
    amount, currency = _value(tender)
    return {
        "id": release.get("id", ""),
        "date": release.get("date", ""),
        "title": tender.get("title") or "",
        "description": tender.get("description") or "",
        "mainProcurementCategory": tender.get("mainProcurementCategory") or "",
        "procurementMethodDetails": tender.get("procurementMethodDetails") or tender.get("procurementMethod") or "",
        "tenderPeriodEndDate": (tender.get("tenderPeriod") or {}).get("endDate") or "",
        "cpvCodes": _cpv_codes(tender),
        "buyerName": (release.get("buyer") or {}).get("name") or "",
        "valueAmount": amount,
        "valueCurrency": currency,
        "noticeUrl": _notice_url(tender),
    }


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Live, cursor-paginated pull, filtered locally by cpv_codes — same
    stance find_a_tender.py takes (no server-side classification filter
    documented). `keywords` accepted for interface parity, unused.
    """
    # CPV OR a distinctive keyword (2026-10-17): CPV-only dropped notices filed
    # under a generic CPV ("Marquee hire" under event services) before the
    # keyword check ever ran. Same whole-word matcher as run._tag.
    cpv_filter = set(cpv_codes) if cpv_codes else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    out = []
    url, params = ENDPOINT, build_params(since, limit)
    while url:
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for release in parse_response(data):
            flat = _flatten(release)
            if (cpv_filter is not None and cpv_filter.isdisjoint(flat["cpvCodes"])
                    and not match.match_keywords(f"{flat['title']} {flat['description']}", keywords or [])):
                continue
            out.append(flat)
            if len(out) >= max_records:
                return out
        url, params = (data.get("links") or {}).get("next"), None
    return out
