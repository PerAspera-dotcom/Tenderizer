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
Number) and `unspsc` columns — NOT CPV. GSIN is empty in the current feed;
UNSPSC is filled as asterisk-prefixed codes, one per line ("*49121503").
config/canadabuys_unspsc.yaml maps the tenant's CPV codes to UNSPSC
equivalents taken from the Government of Canada's own GSIN-UNSPSC code set
(added 2026-10-17). A notice whose UNSPSC matches gets the CPV codes it
matched as its `cpvCodes`, so run.py's `_tag()` sees a code match
("cpv"/"both"). Keyword matching still runs on every notice as
reinforcement, and is the only signal for CPV codes with no UNSPSC
equivalent (see the YAML's header). fetch() still returns every notice
in the window — nothing is dropped for lacking a code match.

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
Empty for notices posted on CanadaBuys itself — see NOTICE_PAGE_URL.
"""
import csv
import datetime
import io

import requests

import config

CSV_URL = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"
# Notices posted on CanadaBuys itself (reference "cb-...") have an empty
# noticeURL column in the CSV — only notices hosted elsewhere (MERX, "MX-...")
# carry one. Their own page is at this address, keyed on the reference number
# (confirmed live 2026-10-17: real references return the notice, made-up
# ones 404).
NOTICE_PAGE_URL = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{ref}"


def notice_url(notice_url_column, reference):
    """The CSV's own noticeURL, else the CanadaBuys page for a "cb-" reference."""
    if notice_url_column:
        return notice_url_column
    if reference.lower().startswith("cb-"):
        return NOTICE_PAGE_URL.format(ref=reference)
    return ""


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
            "cpvCodes": [],  # filled by fetch() from the UNSPSC crosswalk
            "procurementCategory": (row.get("procurementCategory-categorieApprovisionnement") or "").strip(),
            "procurementMethod": row.get("procurementMethod-methodeApprovisionnement-eng") or "",
            "regionsOfDelivery": row.get("regionsOfDelivery-regionsLivraison-eng") or "",
            "contractingEntityName": row.get("contractingEntityName-nomEntitContractante-eng") or "",
            "noticeURL": notice_url(row.get("noticeURL-URLavis-eng") or "",
                                    row.get("referenceNumber-numeroReference") or ""),
        })
    return out


def parse_unspsc(value):
    """"*49121503
*95131700" -> ["49121503", "95131700"]."""
    return [c.strip().lstrip("*").strip() for c in (value or "").splitlines() if c.strip().lstrip("*").strip()]


def _unspsc_matches(notice_code, mapped_code):
    """Exact, or hierarchical: a class/family code (trailing "00" pairs)
    covers every code under it — 49121500 matches 49121503."""
    if notice_code == mapped_code:
        return True
    prefix = mapped_code
    while prefix.endswith("00") and len(prefix) > 2:
        prefix = prefix[:-2]
    return prefix != mapped_code and notice_code.startswith(prefix)


def matched_cpv_codes(unspsc_value, wanted):
    """The tenant CPV codes (keys of `wanted`: cpv -> [unspsc]) whose UNSPSC
    equivalents match this notice's UNSPSC codes."""
    notice_codes = parse_unspsc(unspsc_value)
    return [cpv for cpv, mapped in wanted.items()
            if any(_unspsc_matches(n, m) for n in notice_codes for m in mapped)]


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
    crosswalk = config.canadabuys_unspsc()
    wanted = {cpv: crosswalk[cpv] for cpv in (cpv_codes or []) if cpv in crosswalk}
    resp = requests.get(CSV_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    since_str = since.isoformat()
    out = []
    for raw in parse_response(resp.content):
        pub_date = (raw.get("publicationDate") or "")[:10]
        if pub_date and pub_date < since_str:
            continue
        raw["cpvCodes"] = matched_cpv_codes(raw["unspsc"], wanted)
        out.append(raw)
        if len(out) >= max_records:
            break
    return out
