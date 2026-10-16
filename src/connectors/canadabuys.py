"""Canada — CanadaBuys open tender notices connector.

CR-011 §4: CanadaBuys (not the canada.ca defence-marine page, which is
informational only — confirmed via its own text, no notice data) is the
system of record for federal tender notices since 2022-08-08.

  GET https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv

Confirmed live (2026-10-16) by fetching the real file: a single bilingual
CSV, refreshed daily, no auth, no key. Header row and one full real example
row captured directly — every field name below is a literal CSV column,
not inferred from the (incomplete/inconsistent) third-party docs found
first. Published under the Open Government Licence — Canada.

NO ESTIMATED VALUE: confirmed absent from this file entirely (checked the
full header row) — same kind of real, stated source gap as Belgium's award
XML or Germany's deadline (CR-010 §1/§2), not a parsing miss. `value`/
`value_currency` are always "" for this source.

CLASSIFICATION: carries `gsin-nibs` (Goods and Services Identification
Number) and `unspsc` columns — NOT CPV (CR-011 §4's own "non-EU sources
don't use CPV" point). No verified GSIN/UNSPSC → CPV mapping table exists
yet (the CR asks to "map... where a mapping exists" — building one
correctly needs the official cross-reference data, which this build didn't
have verified access to; inventing one would risk silently wrong matches,
worse than no match at all). `cpv_codes` is therefore always [] for this
source — this notice's relevance is decided by keyword matching alone
(run.py's existing `_tag()` already does this generically: `has_cpv` is
simply false when cpv_codes is empty, so `match_source` always comes back
"keyword" or None here, never "cpv"/"both" — an honest, visible tag, not a
silent gap). Revisit if/when a verified GSIN/UNSPSC-CPV crosswalk shows up.

PROCUREMENT CATEGORY: `procurementCategory-categorieApprovisionnement`
carries a short asterisk-prefixed code — only "*CNST" (construction) is
confirmed live (from the one real example row captured). The other codes
in CanadaBuys' own vocabulary (goods/services/other) weren't observed in
that single sample; map_category below only translates what's confirmed,
everything else falls to "Other" rather than guessed.

LANGUAGE: always "eng" — this build only reads the *-eng columns (the
French counterpart is the literal same notice, not a separate one; reading
both would double-ingest every tender). Matches CR-011 §4's "English
sources (UK, AU, CA, SG, PH) skip translation".

NOTICE URL: `noticeURL-URLavis-eng` — confirmed live, points to the actual
posting platform (MERX in the captured example), not a CanadaBuys page
itself; passed through as-is since CanadaBuys' own CSV already resolves it.
"""
import csv
import datetime
import io

import requests

CSV_URL = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"


def parse_response(csv_bytes):
    """Decode + parse the bilingual CSV into a list of raw per-notice dicts,
    English columns only (see module docstring on LANGUAGE). [] if empty.
    """
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        out.append({
            "title": row.get("title-titre-eng") or "",
            "description": row.get("tenderDescription-descriptionAppelOffres-eng") or "",
            "referenceNumber": row.get("referenceNumber-numeroReference") or "",
            "publicationDate": row.get("publicationDate-datePublication") or "",
            "closingDate": row.get("tenderClosingDate-appelOffresDateCloture") or "",
            "gsin": row.get("gsin-nibs") or "",
            "unspsc": row.get("unspsc") or "",
            "procurementCategory": (row.get("procurementCategory-categorieApprovisionnement") or "").strip(),
            "procurementMethod": row.get("procurementMethod-methodeApprovisionnement-eng") or "",
            "regionsOfDelivery": row.get("regionsOfDelivery-regionsLivraison-eng") or "",
            "contractingEntityName": row.get("contractingEntityName-nomEntitContractante-eng") or "",
            "noticeURL": row.get("noticeURL-URLavis-eng") or "",
        })
    return out


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Live daily-snapshot pull — this is the file's own CURRENT open-tender
    set (not a date-range query the server supports), so `since` only
    filters locally via publicationDate; `cpv_codes` is unused (see module
    docstring's CLASSIFICATION section — no CPV filter is possible here).
    `limit` is accepted for interface parity but unused, matching
    bekanntmachungsservice.py's own stance on its endpoint's lack of
    pagination.
    """
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(CSV_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    since_str = since.isoformat()
    out = []
    for raw in parse_response(resp.content):
        pub_date = (raw.get("publicationDate") or "")[:10]
        if pub_date and pub_date < since_str:
            continue
        out.append(raw)
        if len(out) >= max_records:
            break
    return out
