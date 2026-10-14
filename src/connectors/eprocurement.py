"""Belgium e-Procurement (BDA) connector — the platform's own internal search
API, not a documented open-data API. There is no Swagger, no versioning
promise, no stability guarantee — every field here was confirmed live
(2026-10-14) by capturing real traffic from https://www.publicprocurement.be/bda,
not from any published spec.

Verified live:
  POST https://www.publicprocurement.be/api/sea/search/publications
  Body (all fields optional except page/pageSize; omit a filter key entirely
  when unused — e.g. no `cpvCodes` key at all when no CPV filter, not `[]`):
    {"terms": "free text, single string — see note below",
     "cpvCodes": ["45000000-7", ...], "nutsCodes": [...],
     "publicationLanguages": [...],
     "publicationDateFrom": "YYYY-MM-DD", "publicationDateTo": "YYYY-MM-DD",
     "includeOrganisationChildren": true,
     "page": 1 (1-indexed), "pageSize": 25}
  Response: {"publications": [...], "totalCount": N}.

  cpvCodes IS confirmed server-side-queryable (unlike BOAMP's full-text-only
  search) — CR-010 §1 calls this the primary net, and it's the only filter
  this connector actually sends alongside the date range.

  publicationDateFrom/-To is a PAIRED range filter, confirmed live via the
  UI's own date-range picker. An unpaired publicationDateFrom alone was never
  confirmed working — attempts to probe it directly (outside the UI) 400'd,
  but inconclusively, since those same attempts were also missing the auth
  header below and 400'd for that reason too (can't isolate which caused it
  without live access this build didn't use). So `since` always goes out
  paired with today's date as the upper bound — the one combination actually
  observed working — rather than risk an unconfirmed open-ended range.

  pageSize=25 is the only value confirmed live (the UI's own default).
  build_params's limit=100 default (kept for interface parity with
  ted.py/boamp.py) is UNCONFIRMED against the real API — verify before the
  first production run; fetch()'s page loop will simply get fewer results
  per call than expected if the real cap turns out lower, not crash.

  `terms` is a single free-text string, not an OR'd keyword list like
  BOAMP's ODSQL `where` — its real tokenization (AND vs OR, phrase vs
  per-word) was never confirmed live. `keywords` is accepted here purely for
  interface parity with ted.fetch/boamp.fetch (same precedent boamp.py sets
  for its own unused cpv_codes, reversed) but is NOT sent in the query:
  CPV is the confirmed, reliable net CR-010 §1 calls "primary" for this
  source specifically, and sending an unverified `terms` query risks
  silently narrowing results (a false negative) rather than safely doing
  nothing.

Auth (the surprise): every real search call carries
`Authorization: Bearer <JWT>` — an ANONYMOUS Keycloak client-credentials
token, not a real user login. The public frontend silently mints it from
credentials shipped in the platform's own public env.config.js (fetched by
every anonymous visitor's browser), realm "supplier", client "frontend-public".
Not a secret in the usual sense — but still read from config/env here
(EPROCUREMENT_AUTH_*), not hardcoded, since the platform can rotate it
without notice and a code change shouldn't be required when it does.

Award value / winner (raw_award_winner/-value/-currency, raw_award_detail):
NOT populated — deliberately, per CR-010 §1 ("defer award fields for v1"),
not an oversight. These fields are entirely absent from this search
response; they only exist in a separate per-notice detail call
(GET /api/dos/publication-workspaces/{uuid}) whose payload is raw eForms XML
(versions[].notice.xmlContent), not structured JSON like TED's or BOAMP's own
search APIs hand back — genuinely new parsing work this CR explicitly scopes
out. See normalize.normalize_eprocurement's docstring.

Not yet known / explicitly unconfirmed (flag before go-live, not a build
blocker): the real max pageSize; nutsCodes/publicationLanguages' exact
request shape (inferred from the UI's own filter names, never independently
fired — this connector doesn't send either); whether publicationDateFrom
works unpaired.
"""
import datetime
import os
import time

import requests

AUTH_URL = os.getenv("EPROCUREMENT_AUTH_URL", "https://www.publicprocurement.be/auth")
AUTH_REALM = os.getenv("EPROCUREMENT_AUTH_REALM", "supplier")
AUTH_CLIENT_ID = os.getenv("EPROCUREMENT_AUTH_CLIENT_ID", "frontend-public")
# No default — see module docstring on why this isn't hardcoded even though
# it isn't a real secret. Unset = fetch() raises; run_pipeline already
# catches a failing source into `health` without aborting the run (CR-010 §3),
# so this fails soft at the per-source level like any other connector error.
AUTH_CLIENT_SECRET = os.getenv("EPROCUREMENT_AUTH_CLIENT_SECRET")

SEARCH_ENDPOINT = "https://www.publicprocurement.be/api/sea/search/publications"
# CR-010 §1 "be a good guest" — this is an internal API on a government
# service, not a documented open-data endpoint; the platform's terms
# prohibit overloading its servers or hindering other users.
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"
REQUEST_DELAY_SECONDS = 1.0


def _get_token():
    """Anonymous Keycloak client-credentials grant — see module docstring."""
    if not AUTH_CLIENT_SECRET:
        raise RuntimeError(
            "EPROCUREMENT_AUTH_CLIENT_SECRET is not set — see .env.example"
        )
    url = f"{AUTH_URL}/realms/{AUTH_REALM}/protocol/openid-connect/token"
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": AUTH_CLIENT_ID,
            "client_secret": AUTH_CLIENT_SECRET,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_params(cpv_codes, keywords, since, limit=100, offset=0):
    """Build the search request body.

    Argument order (cpv_codes, keywords, since) matches ted.fetch/boamp.fetch
    for interface parity — keywords is accepted but not sent (see module
    docstring). limit/offset are kept as the shared connector signature;
    translated here to this API's own 1-indexed page/pageSize, which only
    maps cleanly when offset is always a multiple of limit — true as long as
    fetch() below is the only caller.
    """
    body = {
        "publicationDateFrom": since.isoformat(),
        "publicationDateTo": datetime.date.today().isoformat(),
        "includeOrganisationChildren": True,
        "page": offset // limit + 1,
        "pageSize": limit,
    }
    if cpv_codes:
        body["cpvCodes"] = list(cpv_codes)
    return body


def parse_response(json_data):
    """Return the list of raw publication dicts; [] if none."""
    return json_data.get("publications") or []


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Live, paginated pull (1-indexed page-number paging, unlike BOAMP/TED's
    offset/token paging). max_records is a safety cap.

    Rate-limited per CR-010 §1 — see USER_AGENT/REQUEST_DELAY_SECONDS above.
    """
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    out, offset = [], 0
    while True:
        body = build_params(cpv_codes, keywords, since, limit=limit, offset=offset)
        resp = requests.post(SEARCH_ENDPOINT, json=body, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        publications = parse_response(data)
        out.extend(publications)
        offset += limit
        total = data.get("totalCount", 0)
        if not publications or len(out) >= max_records or offset >= total:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return out[:max_records]
