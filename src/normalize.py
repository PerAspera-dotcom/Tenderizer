"""Normalise a raw TED notice into the common 15(+1)-key record.

TED multilingual fields use 3-letter language keys (eng, fra, deu, nld, pol, ...):
  notice-title / description-proc : {lang: "string"}
  buyer-name                      : {lang: ["string"]}
  buyer-country                   : ["POL"]            (ISO3 list)
  contract-nature                 : "supplies"/"services"/"works"  (drives category)
  procedure-type                  : "open"             (string)
  deadline-receipt-request        : ["2026-07-13T..."] (ISO datetime list)
  place-of-performance            : ["PL514","POL"]    (NUTS + ISO3)
  classification-cpv              : ["39522530", ...]
"""
import hashlib
import json

LANG_PREF = ["eng", "fra", "deu", "nld"]

_CAT = {
    "supplies": "Supply", "supply": "Supply", "goods": "Supply",
    "leveringen": "Supply", "fournitures": "Supply", "lieferungen": "Supply",
    "services": "Services", "service": "Services", "diensten": "Services",
    "dienstleistungen": "Services",
    "works": "Works", "werken": "Works", "travaux": "Works", "bauleistungen": "Works",
    "training": "Training", "opleiding": "Training",
}

def map_category(value):
    """Map a nature-of-contract value to a report section."""
    return _CAT.get((value or "").strip().lower(), "Other")

def _first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v if v is not None else ""

def _pick_lang(field):
    """Pick a language from a multilingual dict; value may be str or [str].

    Returns (text, lang_code). lang_code is '' when the source has no language
    metadata (a plain string field) — CR-001 R3 uses this to decide whether a
    notice needs translation (tag_line's lang_code != 'eng').
    """
    if isinstance(field, str):
        return field, ""
    if not isinstance(field, dict) or not field:
        return "", ""
    for lg in LANG_PREF:
        if lg in field:
            return _first(field[lg]), lg
    lang, value = next(iter(field.items()))
    return _first(value), lang

def _is_country_code(code):
    return isinstance(code, str) and len(code) == 3 and code.isalpha()

def _country(raw):
    c = _first(raw.get("buyer-country"))
    if c:
        return c
    for code in raw.get("place-of-performance") or []:
        if _is_country_code(code):
            return code
    return ""

def _place(raw):
    pop = raw.get("place-of-performance") or []
    nuts = [c for c in pop if not _is_country_code(c)]
    return ", ".join(dict.fromkeys(nuts))

def _url(raw):
    """Working notice URL. Prefer the API's own links.html (English first); the bare
    /notice/{pub} path 404s — TED requires a format suffix like /html."""
    html = (raw.get("links") or {}).get("html") or {}
    if isinstance(html, dict) and html:
        for lg in ("ENG", "FRA", "DEU", "NLD"):
            if lg in html:
                return html[lg]
        return next(iter(html.values()))
    pub = raw.get("publication-number", "")
    return f"https://ted.europa.eu/en/notice/{pub}/html" if pub else ""

def _dedupe(codes):
    """Unique codes, stable (first-seen) order — TED can list the same CPV code twice."""
    return list(dict.fromkeys(codes))


def _trim_date(v):
    """'2026-05-28+03:00' -> '2026-05-28' — TED's date fields carry a UTC
    offset suffix; every other date field in this app (deadline, pub_date)
    is a plain YYYY-MM-DD, so award dates match that convention too.
    """
    return (v or "")[:10] or None


def _duration_str(period):
    """{"unit": "MONTH", "value": "6"} -> "6 months" (contract-duration-
    period-lot's shape). None if the field wasn't present or isn't the
    expected shape — never guessed.
    """
    if not isinstance(period, dict):
        return None
    value, unit = period.get("value"), period.get("unit")
    if not value or not unit:
        return None
    unit = unit.lower()
    return f"{value} {unit}" + ("" if value == "1" else "s")


def _prune_none(d):
    """Drop keys whose value is None (or, recursively, a dict of all-None
    values) — keeps the stored award_detail JSON small and lets callers
    check "was anything found at all" with a plain truthiness test.
    """
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _prune_none(v)
            if not v:
                continue
        elif v is None:
            continue
        out[k] = v
    return out


def _ted_award_detail(raw):
    """Richer per-winner/lot/contract detail (past-tenders data-coverage
    follow-up): winner org registration number/city/postal code/NUTS/
    country/size/decision date, lot identifier/title/duration, contract
    identifier/conclusion date/tender identifier, and any framework-
    agreement max value — all confirmed live against TED's search API
    (see connectors/ted.py's FIELDS comment).

    Only populated for single-lot/single-winner notices. TED's search API
    returns these as flat, independently-populated arrays with NO shared
    index guarantee once a notice has more than one lot result — verified
    live against real multi-lot notices, where e.g. winner-name had fewer
    entries than result-lot-identifier (an unsuccessful lot has a result
    but no winner) and the two arrays were ordered differently besides.
    Blindly zipping them by position would silently mis-pair a winner with
    the wrong lot, so multi-lot notices are left with only the existing
    notice-level total (raw_award_value/-currency) — never a fabricated
    pairing.
    """
    lot_ids = raw.get("result-lot-identifier") or []
    if len(lot_ids) != 1:
        return None

    def one(key):
        # _first() returns "" (not None) for a wholly-missing field — strip
        # first, then let the final `or None` turn any resulting empty/
        # whitespace-only string back into a real absence.
        v = _first(raw.get(key))
        if isinstance(v, str):
            v = v.strip()
        return v or None

    title_lot, _ = _pick_lang(raw.get("title-lot", {}))
    duration = _duration_str(_first(raw.get("contract-duration-period-lot")))

    detail = {
        "winner": {
            "registration_number": one("winner-identifier"),
            "city": one("winner-city"),
            "postal_code": one("winner-post-code"),
            "nuts": one("winner-country-sub"),
            "country": one("winner-country"),
            "size": one("winner-size"),
            "decision_date": _trim_date(one("winner-decision-date")),
        },
        "lot": {
            "identifier": lot_ids[0],
            "title": title_lot or None,
            "duration": duration,
        },
        "contract": {
            "identifier": one("contract-identifier"),
            "conclusion_date": _trim_date(one("contract-conclusion-date")),
            "tender_identifier": one("tender-identifier"),
        },
        "framework_max_value": one("result-framework-maximum-value-notice"),
        "framework_max_currency": one("result-framework-maximum-value-cur-notice"),
    }
    return _prune_none(detail) or None


def normalize_ted(raw):
    tag_line, tag_lang = _pick_lang(raw.get("notice-title", {}))
    description, _desc_lang = _pick_lang(raw.get("description-proc", {}))
    buyer, _buyer_lang = _pick_lang(raw.get("buyer-name", {}))
    # CR-003 G4: structured award fields (see connectors/ted.py's FIELDS comment
    # for how these names were confirmed). winner-name is multilingual like
    # notice-title/buyer-name; _pick_lang handles it the same way. Prefer the
    # notice-level total (result-value-notice/-cur-notice) over the per-lot
    # tender-value(-cur), falling back to the latter when the former is absent.
    award_winner, _winner_lang = _pick_lang(raw.get("winner-name", {}))
    award_value = _first(raw.get("result-value-notice")) or _first(raw.get("tender-value"))
    award_currency = _first(raw.get("result-value-cur-notice")) or _first(raw.get("tender-value-cur"))
    return {
        "source": "TED",
        "pub_number": raw.get("publication-number", ""),
        "tag_line": tag_line,
        "description": description,
        "buyer": buyer,
        "country": _country(raw),
        "place": _place(raw),
        "category": map_category(_first(raw.get("contract-nature"))),
        "procedure": _first(raw.get("procedure-type")),
        "pub_date": raw.get("publication-date", ""),
        "deadline": _first(raw.get("deadline-receipt-request")),
        "cpv_codes": _dedupe(raw.get("classification-cpv", [])),
        "matched_terms": [],
        "match_source": None,
        "url": _url(raw),
        "first_seen": None,
        # CR-001 F6: procedure-level estimated value (BT-27-Procedure). Absent on
        # most notices — value disclosure is optional under EU procurement rules.
        "value": raw.get("estimated-value-proc") or "",
        "value_currency": raw.get("estimated-value-cur-proc") or "",
        # CR-001 R3: language tag_line/description were picked in (assumes both
        # fields share a language, true for TED's parallel per-language dicts).
        # 'eng' when TED provided an English translation, else whatever
        # LANG_PREF/fallback found — anything != 'eng' needs translation.
        "language": tag_lang,
        # CR-003 G4: structured award fields, consumed by
        # classification.extract_award_info in preference to its regex fallback.
        "raw_award_winner": award_winner or None,
        "raw_award_value": award_value or None,
        "raw_award_currency": award_currency or None,
        # Past-tenders data-coverage follow-up: winner/lot/contract detail,
        # single-lot notices only (see _ted_award_detail's docstring).
        "raw_award_detail": _ted_award_detail(raw),
    }

def record_hash(record):
    return hashlib.sha256(f"{record['source']}|{record['pub_number']}".encode()).hexdigest()


def _boamp_place(raw):
    depts = raw.get("code_departement") or []
    if isinstance(depts, str):
        depts = [depts]
    return ", ".join(f"FR-{d}" for d in depts)


def _boamp_cpv_codes(raw):
    """CPV codes buried in BOAMP's `donnees` field — a JSON-encoded string of
    the notice's full source XML (verified live, 2026-07). Contrary to this
    module's earlier assumption ("no CPV, keyword source only"), BOAMP does
    carry CPV — it's just never at the flat top level, and its shape depends
    on the notice's schema/vintage:
      - Current EU eForms notices tag it as {"@listName": "cpv", "#text": CODE}
        (main + additional commodity classifications, incl. per-lot).
      - Older FNSimple/MAPA notices nest it as
        {"codeCPV"|"CPV": {"objetPrincipal": {"classPrincipale": CODE}}}.
      - Pre-2024 legacy notices (Boamp_v230.xsd) carry no CPV field at all —
        that gap is real and permanent for archived data, not a bug; keyword
        matching (and F4's category/term checks) remain the fallback for it.
    Walking the whole tree rather than hardcoding one path per schema, since
    OpenDataSoft mixes several notice-type/vintage shapes in the same feed.
    """
    donnees = raw.get("donnees")
    if not donnees:
        return []
    try:
        data = json.loads(donnees)
    except (TypeError, ValueError):
        return []

    codes = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("@listName") == "cpv" and node.get("#text"):
                codes.append(node["#text"])
            principal = node.get("objetPrincipal")
            if isinstance(principal, dict) and principal.get("classPrincipale"):
                codes.append(principal["classPrincipale"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return _dedupe(codes)


def _boamp_award_info(raw):
    """(value, currency) from BOAMP's award-result total, buried in `donnees`
    (verified live, 2026-07, against a real ATTRIBUTION/"Résultat de marché"
    notice): EFORMS.ContractAwardNotice...efac:NoticeResult.cbc:TotalAmount ==
    {"@currencyID": "EUR", "#text": "2557672"} — the notice-level award total,
    same tier as TED's result-value-notice/-cur-notice. Scoped to
    efac:NoticeResult specifically (not a bare `cbc:TotalAmount` walk) so this
    doesn't pick up a per-lot cac:LegalMonetaryTotal.cbc:PayableAmount instead.
    Older/legacy BOAMP schemas with no award JSON at all (see
    _boamp_cpv_codes's docstring) leave this (None, None), same permanent gap.
    """
    donnees = raw.get("donnees")
    if not donnees:
        return None, None
    try:
        data = json.loads(donnees)
    except (TypeError, ValueError):
        return None, None

    found = {}

    def walk(node):
        if found:
            return
        if isinstance(node, dict):
            notice_result = node.get("efac:NoticeResult")
            if isinstance(notice_result, dict):
                total = notice_result.get("cbc:TotalAmount")
                if isinstance(total, dict) and total.get("#text"):
                    found["value"] = total["#text"]
                    found["currency"] = total.get("@currencyID")
                    return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found.get("value"), found.get("currency")


def _boamp_find_first(data, key):
    """First value found for `key` anywhere in the tree, or None — same
    whole-tree walk as _boamp_cpv_codes/_boamp_award_info (BOAMP mixes
    several notice-type/vintage shapes in the same feed, so a fixed path
    isn't reliable across all of them).
    """
    found = []

    def walk(node):
        if found:
            return
        if isinstance(node, dict):
            if key in node:
                found.append(node[key])
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found[0] if found else None


def _boamp_text(node):
    """eForms leaf values are either a bare string or {"#text": ..., "@...":
    ...} depending on whether the tag carries attributes — normalise both to
    a plain string (or None).
    """
    if isinstance(node, dict):
        v = node.get("#text")
        return v.strip() if isinstance(v, str) else v
    if isinstance(node, str):
        return node.strip()
    return None


def _boamp_amount(node):
    return _boamp_text(node) if isinstance(node, dict) else None


def _boamp_currency(node):
    return node.get("@currencyID") if isinstance(node, dict) else None


def _boamp_award_detail(raw):
    """Same idea as _ted_award_detail, but reading BOAMP's raw eForms
    XML-as-JSON (`donnees`) directly rather than a flat search-API field
    list — BOAMP's structure is properly ID-correlated (efac:Organizations
    entries carry their own ORG-xxxx id, referenced from efac:NoticeResult's
    TenderingParty -> Tenderer), so the winner org can be resolved correctly
    even when several organizations/roles appear in the same notice
    (verified live against a real notice: SAS EXHIBIT / ORG-0003, among two
    unrelated buyer-side organizations in the same Organizations list).

    Still restricted to single-lot notices, same reasoning as
    _ted_award_detail: efac:LotResult can also repeat for multiple lots, and
    fully correlating those would mean walking LotResult -> LotTender ->
    TenderingParty -> Tenderer chains that can each independently become
    lists — not worth the complexity for a domain that's overwhelmingly
    single-lot in practice. Multi-lot notices are left with only the
    existing notice-level total (raw_award_value/-currency).
    """
    donnees = raw.get("donnees")
    if not donnees:
        return None
    try:
        data = json.loads(donnees)
    except (TypeError, ValueError):
        return None

    notice_result = _boamp_find_first(data, "efac:NoticeResult")
    if not isinstance(notice_result, dict):
        return None
    lot_result = notice_result.get("efac:LotResult")
    if not isinstance(lot_result, dict):  # missing, or a list (multi-lot) -> skip
        return None
    tendering_party = notice_result.get("efac:TenderingParty")
    if not isinstance(tendering_party, dict):
        return None

    winner_org_id = _boamp_text((tendering_party.get("efac:Tenderer") or {}).get("cbc:ID"))
    if not winner_org_id:
        return None

    orgs = _boamp_find_first(data, "efac:Organization") or []
    if isinstance(orgs, dict):
        orgs = [orgs]
    winner_org = next(
        (o for o in orgs if _boamp_text(
            (o.get("efac:Company") or {}).get("cac:PartyIdentification", {}).get("cbc:ID")
        ) == winner_org_id),
        None,
    )
    if winner_org is None:
        return None

    company = winner_org.get("efac:Company") or {}
    address = company.get("cac:PostalAddress") or {}
    settled_contract = notice_result.get("efac:SettledContract") or {}
    framework = notice_result.get("efbc:OverallMaximumFrameworkContractsAmount")

    listed = winner_org.get("efbc:ListedOnRegulatedMarketIndicator")
    detail = {
        "winner": {
            "registration_number": _boamp_text((company.get("cac:PartyLegalEntity") or {}).get("cbc:CompanyID")),
            "city": _boamp_text(address.get("cbc:CityName")),
            "postal_code": _boamp_text(address.get("cbc:PostalZone")),
            "nuts": _boamp_text(address.get("cbc:CountrySubentityCode")),
            "country": _boamp_text((address.get("cac:Country") or {}).get("cbc:IdentificationCode")),
            "size": _boamp_text(company.get("efbc:CompanySizeCode")),
            "regulated_market": (listed == "true") if listed is not None else None,
        },
        "lot": {
            "identifier": _boamp_text((lot_result.get("efac:TenderLot") or {}).get("cbc:ID")),
            "title": None,  # not reliably present at this path across vintages — never guessed
            "duration": None,
        },
        "contract": {
            "identifier": _boamp_text((settled_contract.get("efac:ContractReference") or {}).get("cbc:ID")),
            "conclusion_date": _trim_date(_boamp_text(settled_contract.get("cbc:IssueDate"))),
            "tender_identifier": _boamp_text((lot_result.get("efac:LotTender") or {}).get("cbc:ID")),
        },
        "framework_max_value": _boamp_amount(framework),
        "framework_max_currency": _boamp_currency(framework),
    }
    return _prune_none(detail) or None


def normalize_boamp(raw):
    """Normalise a BOAMP record into the SAME schema as normalize_ted.

    BOAMP's flat fields are single-language; CPV isn't among them (see
    _boamp_cpv_codes for where it actually lives).
    """
    idweb = raw.get("idweb", "")
    # CR-003 G4: `titulaire` (winner) is a flat top-level field, unlike CPV —
    # no donnees-walk needed for the name itself, only for the award value.
    award_winner = _first(raw.get("titulaire"))
    award_value, award_currency = _boamp_award_info(raw)
    return {
        "source": "BOAMP",
        "pub_number": idweb,
        "tag_line": raw.get("objet") or "",
        "description": "",
        "buyer": raw.get("nomacheteur") or "",
        "country": "FR",
        "place": _boamp_place(raw),
        "category": map_category(_first(raw.get("type_marche"))),
        "procedure": raw.get("procedure_libelle") or "",
        "pub_date": raw.get("dateparution") or "",
        "deadline": raw.get("datelimitereponse") or "",
        "cpv_codes": _boamp_cpv_codes(raw),
        "matched_terms": [],
        "match_source": None,
        "url": f'https://www.boamp.fr/pages/avis/?q=idweb:%22{idweb}%22' if idweb else "",
        "first_seen": None,
        # BOAMP exposes no value/amount field at all (verified against its live
        # OpenDataSoft schema) — always absent, so F6 never excludes on BOAMP alone.
        "value": "",
        "value_currency": "",
        # BOAMP is French-only, single-language (per the connector's own docs) —
        # no per-notice detection needed, always translate (CR-001 R3).
        "language": "fra",
        # CR-003 G4: structured award fields, consumed by
        # classification.extract_award_info in preference to its regex fallback.
        "raw_award_winner": award_winner or None,
        "raw_award_value": award_value or None,
        "raw_award_currency": award_currency or None,
        # Past-tenders data-coverage follow-up: winner/lot/contract detail,
        # single-lot notices only (see _boamp_award_detail's docstring).
        "raw_award_detail": _boamp_award_detail(raw),
    }
