"""UK — Find a Tender Service (FTS) connector.

CR-011 §4: the Procurement Act 2023 central digital platform for UK public
procurement — verified live (2026-10-16) to supersede MyTenders/MOD-specific
portals for this CR's purposes: defence notices are confirmed published here
(dedicated F16-F19 defence-form schemas live in the same system, per the
service's own developer docs), not on a separate MOD-only portal.

  GET https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages
  Params confirmed live: stages (planning/tender/award), updatedFrom/
  updatedTo (YYYY-MM-DDThh:mm:ss), limit (<=100), cursor.
  No API key, no login — confirmed live by fetching it directly with no
  auth header at all and getting real release data back. Published under
  OGL v3 (confirmed in the sample response's own `license` field).

  Response shape (OCDS release package, confirmed live against a real
  release): {"releases": [...], "links": {"next": "...?cursor=..."}}.
  Pagination: follow `links.next` verbatim rather than reconstructing params
  — simplest and safest, avoids guessing at cursor encoding.

STAGES (re-checked live 2026-10-18): the `stages=tender` query parameter
returns only a fraction of new tender notices — 10 on 2026-10-15, against
60 releases tagged "tender" in the unfiltered feed for the same day. So
fetch() sends no stage filter and keeps releases by their own OCDS `tag`:
"tender" (a new tender notice) and "planning" (planning / preliminary
market engagement notices, classified as prior information via form_type).
Awards, contracts and the *Update change notices are skipped. The
unfiltered feed is ~450 releases a day, so the look-back is capped at
MAX_LOOKBACK_DAYS (the nightly run passes 30 days for every source).

RATE LIMIT (confirmed live 2026-10-18): 12 requests, then HTTP 429 "Please
retry after 120 seconds". Requests are paced REQUEST_DELAY_SECONDS apart and
a 429 waits out its Retry-After before retrying.

No server-side CPV filter is documented (this is a generic OCDS feed, not a
classification-aware search index, same situation BOAMP/Bekanntmachungs-
service are in) — fetch() filters locally by cpv_codes, same stance bekanntmachungs-
service.py takes and for the same reason: nothing downstream of fetch()
drops a non-matching record before it's stored (run.py stores every raw
record it's handed, tagged with exclude_reason/match_source, never
filtered out by match_source alone).

CPV: UK kept the EU's CPV vocabulary after Brexit — confirmed live,
tender.classification.scheme == "CPV" with a bare (no check-digit suffix)
8-digit id, e.g. "90910000". No mapping needed, unlike AU/CA/SG/MY/PH in
this same CR — same bare-code convention as TED/BOAMP/the tenant's own
CPV list.

VALUE: tender.value is absent for lotted notices (confirmed live) — the
estimate lives on tender.lots[].value instead. Falls back to the first
lot's value when the notice-level one is absent, mirroring normalize_ted's
own "notice-level total, else per-lot" precedent.

LANGUAGE: always "eng" — an English-only source (CR-011 §4's own "English
sources (UK, AU, CA, SG, PH) skip translation" note) — set directly in
normalize_find_a_tender so run.py's translation step skips it the same way
it already skips any 'eng'-tagged record.

NOTICE URL: https://www.find-tender.service.gov.uk/Notice/{id} — confirmed
live via real search-result links (id matches the release's own top-level
`id`, not `ocid`, e.g. "098127-2026").
"""
import datetime
import time

import requests

import match

ENDPOINT = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"
MAX_LOOKBACK_DAYS = 7
REQUEST_DELAY_SECONDS = 5.5   # 12 requests a minute stays under the limit
MAX_RATE_LIMIT_RETRIES = 5
# release tag -> eForms-style form_type (see classification.check_past_tender);
# first match wins, so a release tagged both keeps "competition".
KEPT_TAGS = {"tender": "competition", "planning": "planning"}


def _cpv_codes(tender):
    codes = []
    classification = tender.get("classification") or {}
    if classification.get("scheme") == "CPV" and classification.get("id"):
        codes.append(classification["id"])
    for item in tender.get("items") or []:
        item_classification = item.get("classification") or {}
        if item_classification.get("scheme") == "CPV" and item_classification.get("id"):
            codes.append(item_classification["id"])
        for extra in item.get("additionalClassifications") or []:
            if extra.get("scheme") == "CPV" and extra.get("id"):
                codes.append(extra["id"])
    return list(dict.fromkeys(codes))


def _place(tender):
    locations = []
    for item in tender.get("items") or []:
        desc = (item.get("deliveryLocation") or {}).get("description")
        if desc:
            locations.append(desc)
    return ", ".join(dict.fromkeys(locations))


def _buyer_name(release):
    for party in release.get("parties") or []:
        if "buyer" in (party.get("roles") or []):
            return party.get("name") or ""
    return ""


def _value(tender):
    """Notice-level tender.value, else the first lot's — see module
    docstring's VALUE section. (amount, currency), both None if neither
    is present (never fabricated).
    """
    value = tender.get("value") or {}
    if not value.get("amount"):
        lots = tender.get("lots") or []
        if lots:
            value = lots[0].get("value") or {}
    return value.get("amount"), value.get("currency")


def build_params(since, limit=100):
    """updatedFrom needs a datetime, not a bare date — confirmed live (the
    API's own date-range param format is YYYY-MM-DDThh:mm:ss); midnight on
    `since` is the lower bound. No `stages` — see module docstring's STAGES.
    """
    return {"updatedFrom": f"{since.isoformat()}T00:00:00", "limit": limit}


def parse_response(json_data):
    """Return the list of raw release dicts; [] if none."""
    return json_data.get("releases") or []


def form_type(release):
    """"competition" / "planning" for a release worth keeping, else None
    (awards, contracts, change notices). A release without tags is treated
    as a tender notice — OCDS requires the tag, so that's only a safety net."""
    tags = release.get("tag") or ["tender"]
    return next((KEPT_TAGS[t] for t in KEPT_TAGS if t in tags), None)


def _get(url, params, headers):
    """GET with the API's rate limit: a 429 waits out Retry-After (120 s
    when absent) and retries, up to MAX_RATE_LIMIT_RETRIES times."""
    for _ in range(MAX_RATE_LIMIT_RETRIES):
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        if resp.status_code != 429:
            break
        time.sleep(int(resp.headers.get("Retry-After") or 120) + 1)
    resp.raise_for_status()
    return resp


def _flatten(release):
    """Extract the fields normalize_find_a_tender needs into a flat dict —
    same "parse_response assembles exactly what normalize needs" shape
    connectors/bekanntmachungsservice.py's parse_response uses, rather than
    passing the full nested OCDS object through.
    """
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
        "place": _place(tender),
        "buyerName": _buyer_name(release),
        "valueAmount": amount,
        "valueCurrency": currency,
        "formType": form_type(release) or "",
    }


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000, max_lookback_days=MAX_LOOKBACK_DAYS):
    """Live, cursor-paginated pull, filtered locally by cpv_codes (see module
    docstring — no server-side classification filter exists here). `keywords`
    is accepted for interface parity with ted.fetch/boamp.fetch/etc. but
    unused: UK is CPV-native, same stance bekanntmachungsservice.py takes.
    """
    # CPV OR a distinctive keyword (2026-10-17): CPV-only dropped notices filed
    # under a generic CPV ("Marquee hire" under event services) before the
    # keyword check ever ran. Same matcher as run._tag (whole-word, plus compounds).
    cpv_filter = set(cpv_codes) if cpv_codes else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    since = max(since, datetime.date.today() - datetime.timedelta(days=max_lookback_days))
    out = []
    url, params = ENDPOINT, build_params(since, limit)
    first = True
    while url:
        if not first:
            time.sleep(REQUEST_DELAY_SECONDS)
        first = False
        data = _get(url, params, headers).json()
        for release in parse_response(data):
            if form_type(release) is None:
                continue  # award / contract / change notice
            flat = _flatten(release)
            if (cpv_filter is not None and cpv_filter.isdisjoint(flat["cpvCodes"])
                    and not match.match_relevance(f"{flat['title']} {flat['description']}", keywords or [])):
                continue
            out.append(flat)
            if len(out) >= max_records:
                return out
        url, params = (data.get("links") or {}).get("next"), None
    return out
