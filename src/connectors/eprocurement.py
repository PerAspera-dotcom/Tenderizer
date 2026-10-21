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

  pageSize=100 IS confirmed live (2026-10-14, CR-010 §3 duplicate-rate
  check) — the UI's own page-size selector offers 10/25/50/100 natively, and
  a real 100-row page came back successfully (used to pull a 600-notice
  sample for the TED↔BE duplicate-rate figure in this CR's findings).
  build_params's limit=100 default is therefore confirmed, not just kept for
  interface parity. Whether anything above 100 is accepted was never tried
  (not offered in the UI) — fetch()'s page loop would just get fewer results
  per call than expected if some higher value silently caps lower, not crash.

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
blocker): nutsCodes/publicationLanguages' exact request shape (inferred from
the UI's own filter names, never independently fired — this connector
doesn't send either); whether publicationDateFrom works unpaired.

Second header requirement, found during a later review pass (2026-10-15):
every request to this API — reference GET endpoints included, not just the
search POST — also needs a `BelGov-Trace-Id` header, a valid v4 UUID. Without
it: HTTP 400 ("BelGov-Trace-Id header must be a valid v4 UUID"), rejected by
a gateway layer BEFORE auth is even checked — confirmed by the error
changing to 403 (an auth rejection) the moment a valid UUID is added, even
with no bearer token at all. The browser-based testing this connector was
originally built and verified against never surfaced this: the page's own
HTTP client attaches it automatically, invisibly. A fair amount of the
"inconclusive 400" confusion earlier in this module's own history (the
publicationDateFrom-pairing question above) was likely this header too, not
only the missing Authorization header blamed at the time — never
re-diagnosed once the fix below made the actual search call succeed in
principle; worth re-probing the unpaired-date question now that both known
header requirements are met.

CPV CHECK-DIGIT TRANSLATION (found in review, fixed 2026-10-16): BE's own
CPV codes carry an official check-digit suffix ("45000000-7") — confirmed
against /api/loc/cpvs's full 9454-code reference list, every entry keyed
this way, one-to-one with the bare 8-digit code (zero collisions). Every
other source (TED, BOAMP) and the tenant's own configured CPV list
(config/cpv.yaml) use the bare code with no suffix. normalize.py's
_eprocurement_strip_checksum already fixes the INBOUND half (BE's response
codes -> bare, so downstream matching/dedup/relevance scoring stays
consistent); build_params below fixes the OUTBOUND half (the tenant's bare
codes -> BE's hyphenated form) via config/cpv_be_checksums.json, a static
table generated once from that same reference endpoint rather than either
(a) reimplementing the official CPV check-digit algorithm from memory with
no way to verify it's right, or (b) re-fetching 9454 reference entries on
every pipeline run against a government service CR-010 §1 asks us to be "a
good guest" toward. The CPV 2008 vocabulary is stable/rarely revised; see
that file's own header for how to regenerate it if BE ever adds codes.
"""
import datetime
import json
import os
import time
import uuid
import warnings
from pathlib import Path

import requests

from connectors import retry

_CPV_CHECKSUMS_PATH = Path(__file__).resolve().parents[2] / "config" / "cpv_be_checksums.json"

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
    resp = retry.call(
        requests.post,
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


def _be_cpv_checksums():
    """config/cpv_be_checksums.json's bare->hyphenated CPV code map — see
    module docstring's CPV CHECK-DIGIT TRANSLATION section. Not cached: this
    is only read from build_params() a handful of times per fetch() call
    (once per page), and matches config.py's own cpv_reference() — also a
    plain re-read-on-every-call loader for its own static JSON reference file.
    """
    with open(_CPV_CHECKSUMS_PATH, encoding="utf-8") as f:
        return json.load(f)["codes"]


def _to_be_cpv_codes(cpv_codes):
    """Translate bare 8-digit CPV codes to BE's own check-digit-suffixed form.

    A code absent from the table (not expected for the stable CPV 2008
    vocabulary config/cpv_be_checksums.json was generated from, but not
    impossible if BE's taxonomy ever moves past that generation date) is
    dropped rather than sent bare — sending it bare wouldn't just miss that
    one code, a malformed entry in `cpvCodes` could invalidate the whole
    filter. A RuntimeWarning surfaces the gap instead of failing silently.
    """
    table = _be_cpv_checksums()
    translated = []
    for code in cpv_codes:
        mapped = table.get(code)
        if mapped:
            translated.append(mapped)
        else:
            warnings.warn(
                f"eprocurement: CPV code {code!r} has no BE check-digit "
                "mapping (config/cpv_be_checksums.json may be stale) — "
                "dropped from the server-side filter rather than sent bare.",
                RuntimeWarning,
            )
    return translated


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
        # cpv_codes arrives bare (TED/BOAMP/tenant-config convention) — BE's
        # own filter needs its check-digit-suffixed form. See module
        # docstring's CPV CHECK-DIGIT TRANSLATION section. If every code
        # fails to translate, the filter is omitted entirely (falls back to
        # an unfiltered pull) rather than sending an empty/garbage filter
        # that would silently match nothing.
        translated = _to_be_cpv_codes(cpv_codes)
        if translated:
            body["cpvCodes"] = translated
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
    base_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    out, offset = [], 0
    while True:
        body = build_params(cpv_codes, keywords, since, limit=limit, offset=offset)
        # BelGov-Trace-Id (valid v4 UUID): required on every call to this
        # gateway, checked before auth — see module docstring. Fresh per
        # request, matching what a trace ID is for.
        headers = {**base_headers, "BelGov-Trace-Id": str(uuid.uuid4())}
        resp = retry.call(requests.post, SEARCH_ENDPOINT, json=body, headers=headers, timeout=60)
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
