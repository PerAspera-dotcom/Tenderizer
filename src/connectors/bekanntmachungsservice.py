"""Germany — Bekanntmachungsservice (oeffentlichevergabe.de) connector.

Fully documented, CC0 1.0, no key/registration. Verified live (2026-10-14)
against the OpenAPI spec at
https://oeffentlichevergabe.de/documentation/api/opendata and against real
exports for a full day (2026-10-13, 1035 notices) in all three formats.

  GET https://oeffentlichevergabe.de/api/notice-exports
  Params: pubMonth (YYYY-MM) OR pubDay (YYYY-MM-DD), mutually exclusive, plus
    format in {eforms.zip, ocds.zip, csv.zip}. Returns a ZIP; empty ZIP if no
    notices that day. Historical floor 2022-12-01 (confirmed inclusive, HTTP
    200). pubDay=today or future -> HTTP 400 (confirmed) — "T+1 only": only
    notices processed before midnight the previous day are retrievable.
  No server-side filtering of ANY kind — no search, no CPV, no keyword.
    cpv_codes/keywords are accepted (interface parity with ted/boamp/
    eprocurement) but applied locally, after the unzip, exactly as boamp.py
    documents for its own unused cpv_codes.

FORMAT CHOICE: CSV, not OCDS — reversing the first instinct (OCDS's single
nested JSON-per-notice object initially looked simpler than CSV's ~18 tables).
Decided against OCDS for one concrete, checked reason: CSV's purpose.csv
carries `internalIdentifier` (BT-22, the buyer's own procurement reference —
CR-010's named cross-portal join key), OCDS does not. Verified directly: 8
real notices with a populated purpose.csv internalIdentifier (e.g.
"PR1214534", "BEK-2026-0007") were grepped for that exact value inside their
matching OCDS JSON — zero hits, every time. Losing that field permanently
felt like the wrong trade for a one-time connector-build cost, even though
OCDS's extra convenience (lot-level CPV/title/description/award already
assembled into one object) is real. CSV's own lot-level CPV turned out just
as rich: classification.csv carries a notice-level row (empty lotIdentifier)
PLUS one row per lot, and a real multi-lot notice had genuinely DIFFERENT
CPV codes per lot vs. its own notice-level row — confirms CR-010's instinct
to match on lot-level CPV, not just the notice-level code.

Only ~8 of the CSV export's 18 tables are relevant to our schema — notice,
purpose, procedure, organisation, classification, placeOfPerformance,
noticeResult, (lot.csv is redundant with classification/purpose already
carrying lotIdentifier). Joined on (noticeIdentifier, noticeVersion[,
lotIdentifier]).

SUBMISSION DEADLINE: absent from BOTH CSV and OCDS — checked every CSV table
and the OCDS tender object (tenderPeriod, milestones, per-lot) across 30 real
active ("competition") notices; zero occurrences in either export. Confirmed
this isn't a populate-rate issue: pulled the raw eforms.zip for one of those
same notices and found cac:TenderSubmissionDeadlinePeriod/cbc:EndDate sitting
right there at the true source. This platform's own CSV/OCDS derivation
drops it entirely — not a format trade-off, a gap in both. Deferred per
CR-010 §2 (same call as Belgium's award-XML deferral): `deadline` is always
"" for this source. Getting it would mean fetching+parsing the THIRD format
(raw eForms XML) just for one field — real new scope, not built unasked.
run.py's expiry filter (`if dl and dl < run_date`) already treats an empty
deadline as "don't skip", so this is a visible product gap, not a latent bug.

AWARD FIELDS: unlike Belgium, no XML parsing needed — noticeResult.csv
already has noticeValue/noticeValueCurrency (the award total) as plain CSV,
and organisation.csv already names a winner via organisationRole. Populated
in v1 (raw_award_winner/value/currency) — not deferred, since it isn't extra
scope. raw_award_detail (the richer winner/lot/contract structure TED/BOAMP
build) IS left None for v1: CR-010 §2 never asks for it the way §1 discusses
Belgium's raw_award_detail explicitly, and assembling it needs contract.csv/
duration.csv joins this CR didn't scope — can add if wanted. One coverage
fact worth knowing regardless: of 317 award-type ("result") notices that
day, only 202 (63.7%) name a winning org in ANY role field at all — real
sparseness in the source, not a parser gap.

THE `since` CONTRACT: this endpoint can't take a lower bound and query
forward — it only serves whole days, in the past. fetch() iterates pubDay
from `since` to yesterday inclusive (confirmed boundary behavior: today/
future -> 400, pre-2022-12-01 -> 400, 2022-12-01 itself -> 200), one request
per day. CONSEQUENCE WORTH STATING PLAINLY (CR-010 §2): German notices
arrive at least a day later than TED's, by construction of this API, not an
implementation shortcut.

WITHIN-SOURCE DEDUPLICATION: new, neither TED nor BOAMP needs this — the
same notice legitimately reappears across its own noticeIdentifier's
versions as corrigenda land on later days. Checked live: within a SINGLE
day's export every noticeIdentifier is unique (all 1035, that day) — the
collapse only matters ACROSS the multiple days one fetch() call spans (e.g.
a catch-up pull after downtime). Landmine confirmed in real data:
noticeVersion is inconsistently zero-padded ("1" and "01" both occur as
distinct strings in the same day's export, likewise "2"/"02") — comparing
versions as raw strings would sort "10" < "2". Always compare via
int(noticeVersion). Deliberately NOT routed through run.py's cross-source
dedup (CR-010 §2 — conflating the two was exactly CR-008 P0's silent-
disappearance bug class). pub_number is the stable noticeIdentifier alone
(not noticeIdentifier+version), so a corrigendum arriving in a LATER,
separate pipeline run still upserts the same stored record via the existing
(source, pub_number) identity, rather than creating a duplicate.
"""
import csv
import datetime
import io
import logging
import re
import zipfile

import requests

import match

logger = logging.getLogger(__name__)

EXPORT_ENDPOINT = "https://oeffentlichevergabe.de/api/notice-exports"
# Note: the public notice detail page URL (confirmed live via the site's own
# search UI — click-through on a real result, not from the OpenAPI spec,
# which only documents the bulk-export API) is built in
# normalize.normalize_bekanntmachungsservice, not here — same convention
# normalize_boamp already uses for its own URL template.
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"

HISTORICAL_FLOOR = datetime.date(2022, 12, 1)

_DEADLINE_BLOCK_RE = re.compile(
    r"<cac:TenderSubmissionDeadlinePeriod>(.*?)</cac:TenderSubmissionDeadlinePeriod>", re.S)
_END_DATE_RE = re.compile(r"<cbc:EndDate>\s*(\d{4}-\d{2}-\d{2})")


def deadline_from_eforms(xml_text):
    """Earliest cac:TenderSubmissionDeadlinePeriod/cbc:EndDate in one eForms
    notice (multi-lot notices carry one per lot), as "YYYY-MM-DD"; "" if none.
    """
    dates = [m.group(1) for block in _DEADLINE_BLOCK_RE.findall(xml_text or "")
             for m in _END_DATE_RE.finditer(block)]
    return min(dates) if dates else ""


def _fill_deadlines(raws_by_day, headers):
    """Best-effort submission deadlines, which the CSV/OCDS exports drop (see
    the module docstring). Downloads the same day's eForms export once per day
    that has a matching notice and reads each notice's XML
    (`<noticeIdentifier>-<version>.xml`). Confirmed live 2026-10-17: that
    export is CAPPED AT 1000 FILES per day with no paging parameter and no
    per-notice endpoint (2026-10-15: 1301 notices, 1000 files), so a notice
    outside the cap keeps deadline "". Never raises — a missing deadline is
    the pre-existing behaviour, not a reason to fail the source.
    """
    for day, raws in raws_by_day.items():
        try:
            resp = requests.get(EXPORT_ENDPOINT, params={"pubDay": day.isoformat(), "format": "eforms.zip"},
                                headers=headers, timeout=120)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = set(zf.namelist())
                for raw in raws:
                    version = raw["noticeVersion"]
                    candidates = [f"{raw['noticeIdentifier']}-{version}.xml"]
                    if version.isdigit():
                        candidates.append(f"{raw['noticeIdentifier']}-{int(version):02d}.xml")
                    name = next((n for n in candidates if n in names), None)
                    if name:
                        raw["deadline"] = deadline_from_eforms(zf.read(name).decode("utf-8", "ignore"))
        except Exception as e:
            logger.warning("Bekanntmachungsservice: deadline lookup failed for %s: %s: %s",
                           day, type(e).__name__, e)

# Winner-naming role priority — organisation.csv's organisationRole is one of
# several values (buyer/reviewer/tenderer/serv-prov/mediator/winner/...);
# 'winner' is the most direct but rarer than 'tenderer' in practice
# (confirmed live: 28 vs 171 occurrences across 317 award notices that day).
# Lower number = higher priority.
_WINNER_ROLE_PRIORITY = {"winner": 0, "tenderer": 1, "serv-prov": 2}


def build_params(cpv_codes, keywords, since, limit=100, offset=0):
    """Build the query params for ONE day's export request.

    Argument order (cpv_codes, keywords, since) matches ted.fetch/boamp.fetch/
    eprocurement.fetch for interface parity — cpv_codes/keywords/limit/offset
    are accepted but unused (this endpoint has no server-side filter or
    pagination at all; filtering happens locally after the unzip, same stance
    boamp.py takes on its own unused cpv_codes). Unlike every other
    connector's build_params, `since` here is the SPECIFIC day being
    requested, not a lower bound — fetch() below calls this once per day in
    its pubDay loop.
    """
    return {"pubDay": since.isoformat(), "format": "csv.zip"}


def parse_response(zip_bytes):
    """Parse one day's csv.zip into a list of per-notice raw dicts.

    Unlike ted.py/boamp.py/eprocurement.py's parse_response (JSON in, JSON
    out), this source returns a ZIP of relational CSV tables — the parameter
    holds those raw bytes, not parsed JSON. Joins the ~8 tables relevant to
    our schema on (noticeIdentifier, noticeVersion[, lotIdentifier]).
    Returns [] for an empty ZIP (confirmed: a day with no notices returns one).
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        def rows(name):
            try:
                with zf.open(name) as f:
                    return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))
            except KeyError:
                return []

        notices = rows("notice.csv")
        if not notices:
            return []

        procedure_by_key = {(r["noticeIdentifier"], r["noticeVersion"]): r for r in rows("procedure.csv")}
        result_by_key = {(r["noticeIdentifier"], r["noticeVersion"]): r for r in rows("noticeResult.csv")}

        purpose_notice_level = {}
        purpose_first_lot = {}
        for r in rows("purpose.csv"):
            key = (r["noticeIdentifier"], r["noticeVersion"])
            if r.get("lotIdentifier", "").strip():
                purpose_first_lot.setdefault(key, r)
            else:
                purpose_notice_level[key] = r

        cpv_by_key = {}
        for r in rows("classification.csv"):
            if r.get("classificationType") != "cpv" or not r.get("mainClassificationCode", "").strip():
                continue
            key = (r["noticeIdentifier"], r["noticeVersion"])
            cpv_by_key.setdefault(key, []).append(r["mainClassificationCode"])

        nuts_by_key = {}
        for r in rows("placeOfPerformance.csv"):
            sub = r.get("placePerformanceCountrySubdivision", "").strip()
            if not sub:
                continue
            key = (r["noticeIdentifier"], r["noticeVersion"])
            nuts_by_key.setdefault(key, []).append(sub)

        buyer_by_key = {}
        winner_by_key = {}  # key -> (priority, name); lower priority number wins
        for r in rows("organisation.csv"):
            key = (r["noticeIdentifier"], r["noticeVersion"])
            role = r.get("organisationRole", "")
            if role == "buyer":
                buyer_by_key.setdefault(key, r["organisationName"])
                continue
            priority = _WINNER_ROLE_PRIORITY.get(role)
            if priority is None:
                continue
            current = winner_by_key.get(key)
            if current is None or priority < current[0]:
                winner_by_key[key] = (priority, r["organisationName"])
        winner_best = {key: name for key, (_priority, name) in winner_by_key.items()}

        out = []
        for n in notices:
            key = (n["noticeIdentifier"], n["noticeVersion"])
            purpose = purpose_notice_level.get(key) or {}
            lot_purpose = purpose_first_lot.get(key) or {}
            procedure = procedure_by_key.get(key) or {}
            result = result_by_key.get(key) or {}
            out.append({
                "noticeIdentifier": n["noticeIdentifier"],
                "noticeVersion": n["noticeVersion"],
                "formType": n.get("formType", ""),
                "noticeType": n.get("noticeType", ""),
                "procedureLegalBasis": n.get("procedureLegalBasis", ""),
                "publicationDate": n.get("publicationDate", ""),
                "procedureType": procedure.get("procedureType", ""),
                "title": purpose.get("title") or "",
                "description": purpose.get("description") or "",
                "internalIdentifier": purpose.get("internalIdentifier") or "",
                "estimatedValue": purpose.get("estimatedValue") or "",
                "estimatedValueCurrency": purpose.get("estimatedValueCurrency") or "",
                # mainNature lives on the LOT row, not the notice row (confirmed
                # live: notice-level purpose rows are blank here in 1036/1036
                # cases) — best-effort: first lot's nature. Multi-lot notices
                # with genuinely mixed natures aren't disambiguated further,
                # same "don't guess beyond a reasonable primary" stance TED/
                # BOAMP/eprocurement already take elsewhere.
                "mainNature": purpose.get("mainNature") or lot_purpose.get("mainNature") or "",
                "cpvCodes": list(dict.fromkeys(cpv_by_key.get(key, []))),
                "nutsCodes": list(dict.fromkeys(nuts_by_key.get(key, []))),
                "buyerName": buyer_by_key.get(key, ""),
                "winnerName": winner_best.get(key),
                "noticeValue": result.get("noticeValue") or "",
                "noticeValueCurrency": result.get("noticeValueCurrency") or "",
            })
        return out


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Iterate pubDay from `since` to yesterday inclusive, collapse to the
    latest version per noticeIdentifier across the whole span, then filter
    locally by cpv_codes (match on EITHER the notice-level code or any
    lot-level code — see module docstring on why lot-level matters).

    `limit` is accepted for interface parity but unused — no pagination
    exists on this endpoint; each day is one request, one response.
    """
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    if since > yesterday:
        return []  # nothing retrievable yet — same "fail soft, don't crash" stance as every other connector

    start = max(since, HISTORICAL_FLOOR)
    headers = {"User-Agent": USER_AGENT}
    cpv_filter = set(cpv_codes) if cpv_codes else None
    max_version = {}  # noticeIdentifier -> highest int(version) seen, matching or not
    matched = {}      # noticeIdentifier -> (int(version), raw dict), CPV-matching versions only
    day_of = {}       # noticeIdentifier -> pubDay its matched version came from (for _fill_deadlines)

    # Newest day first, CPV-filtered as we go, capped on MATCHES. This used to
    # walk oldest-first with a 2000 cap on RAW notices — at ~1000 notices/day
    # that stopped after ~2 days, so a 30-day window only ever saw its two
    # oldest days and never anything recent (found 2026-10-17: one stored
    # notice in total since go-live, published exactly 30 days earlier).
    day = yesterday
    while day >= start:
        params = build_params(cpv_codes, keywords, day)
        resp = requests.get(EXPORT_ENDPOINT, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        for raw in parse_response(resp.content):
            notice_id = raw["noticeIdentifier"]
            version = int(raw["noticeVersion"])  # see module docstring: never compare as strings
            if version > max_version.get(notice_id, -1):
                max_version[notice_id] = version
            # CPV OR a distinctive keyword (2026-10-17): CPV-only dropped notices filed
            # under a generic CPV ("Marquee hire" under event services) before the
            # keyword check ever ran. Same matcher as run._tag (whole-word, plus compounds).
            if (cpv_filter is not None and cpv_filter.isdisjoint(raw["cpvCodes"])
                    and not match.match_relevance(f"{raw['title']} {raw['description']}", keywords or [])):
                continue
            current = matched.get(notice_id)
            if current is None or version > current[0]:
                matched[notice_id] = (version, raw)
                day_of[notice_id] = day
        if len(matched) >= max_records:
            break
        day -= datetime.timedelta(days=1)

    # Keep a notice only if its latest version is the one that matched — a
    # newer version whose CPV no longer matches supersedes the older match.
    out = [raw for notice_id, (version, raw) in matched.items() if version == max_version[notice_id]]
    out = out[:max_records]
    raws_by_day = {}
    for raw in out:
        raws_by_day.setdefault(day_of[raw["noticeIdentifier"]], []).append(raw)
    _fill_deadlines(raws_by_day, headers)
    return out
