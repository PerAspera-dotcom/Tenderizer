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

def _record_language(tag_lang, desc_lang):
    """CR-008 P1: which language gates translation for this record (see
    run.py's `r["language"] == "eng"` skip and frontend utils.ts'
    needsTranslation — both treat a single `language` value as covering
    both tag_line and description).

    Historically just tag_lang, on the assumption notice-title and
    description-proc always share a language — confirmed false in
    production: TED notices commonly carry an English notice-title (buyers
    add one for visibility) while description-proc has no 'eng' key at all,
    so tag_lang alone reported 'eng' and description silently never reached
    DeepL (run.py's translation step never ran for the record at all).
    desc_lang now wins whenever tag_lang says 'eng' but desc_lang disagrees
    — that's the one combination the old assumption got wrong; when
    tag_lang is itself non-English (the common case, and already correct)
    it's left untouched.
    """
    if tag_lang == "eng" and desc_lang and desc_lang != "eng":
        return desc_lang
    return tag_lang


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
    description, desc_lang = _pick_lang(raw.get("description-proc", {}))
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
        # CR-007 Phase C: buyer's own procurement reference (BT-22-Procedure) —
        # see connectors/ted.py's FIELDS comment. Cross-portal dedup's signal 1
        # (dedup.find_cross_portal_duplicates matches this against BOAMP's
        # _boamp_internal_identifier for the same buyer reference).
        "internal_identifier": raw.get("internal-identifier-proc") or None,
        # CR-001 F6: procedure-level estimated value (BT-27-Procedure). Absent on
        # most notices — value disclosure is optional under EU procurement rules.
        "value": raw.get("estimated-value-proc") or "",
        "value_currency": raw.get("estimated-value-cur-proc") or "",
        # CR-001 R3 / CR-008 P1: 'eng' only when BOTH tag_line and description
        # are actually English — see _record_language's docstring for why
        # tag_lang alone isn't enough (TED's title/description don't
        # reliably share a language, unlike the original assumption here).
        "language": _record_language(tag_lang, desc_lang),
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


def _boamp_internal_identifier(raw):
    """CR-007 Phase C: the buyer's own procurement reference (BT-22-Procedure),
    the BOAMP-side counterpart to TED's internal-identifier-proc — see
    connectors/ted.py's FIELDS comment for why this pairing exists at all
    (no direct TED<->BOAMP link field exists on either portal; matching this
    buyer-assigned reference is dedup.find_cross_portal_duplicates' signal 1).

    Verified live (2026-08) against 5 real recent eForms notices: the
    procedure-level `cac:ProcurementProject.cbc:ID` sitting directly under
    `EFORMS.{ContractNotice|ContractAwardNotice}` — deliberately NOT the
    same-named `cbc:ID` nested inside each `cac:ProcurementProjectLot` entry
    (that one is the *lot's* own reference, e.g. a bare "01"/"02", and is
    usually a different value from the procedure-level one). Falls back to
    `CONDITION_ADMINISTRATIVE.REFERENCE_MARCHE` for pre-eForms legacy notices
    (donnees isn't EFORMS-wrapped there — same vintage split as
    _boamp_cpv_codes's docstring describes).
    """
    donnees = raw.get("donnees")
    if not donnees:
        return None
    try:
        data = json.loads(donnees)
    except (TypeError, ValueError):
        return None

    root = data.get("EFORMS")
    if isinstance(root, dict):
        inner = root.get("ContractAwardNotice") or root.get("ContractNotice")
        if isinstance(inner, dict):
            project = inner.get("cac:ProcurementProject")
            if isinstance(project, dict):
                ref = _boamp_text(project.get("cbc:ID"))
                if ref:
                    return ref
        return None

    admin = data.get("CONDITION_ADMINISTRATIVE")
    if isinstance(admin, dict):
        ref = _boamp_text(admin.get("REFERENCE_MARCHE"))
        if ref:
            return ref
    return None


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


def _boamp_dict(node):
    """A dict node stays as-is; anything else (missing, or a list — eForms'
    XML-to-JSON conversion turns a *repeated* sibling element into a list but
    leaves a single one as a bare dict, and several of these wrapper fields
    can legitimately repeat on a real notice even when the notice is
    otherwise single-lot/single-winner: verified live, crashing with
    "'list' object has no attribute 'get'" first for efac:Tenderer
    [consortium winners] and then for efac:LotTender [multiple tender refs
    under one lot result]) is treated as "this one field wasn't usably
    present" — the surrounding winner-org correlation (which is what
    actually has to be unambiguous) is unaffected, only that one leaf value
    is left None rather than guessed.
    """
    return node if isinstance(node, dict) else {}


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

    # efac:Tenderer is a list, not a single dict, when the winning bid was a
    # joint venture/consortium of several organizations (verified live —
    # crashed a first production backfill run) — same "don't guess" call as
    # LotResult/TenderingParty above rather than picking one org arbitrarily.
    tenderer = tendering_party.get("efac:Tenderer")
    if not isinstance(tenderer, dict):
        return None
    winner_org_id = _boamp_text(tenderer.get("cbc:ID"))
    if not winner_org_id:
        return None

    orgs = _boamp_find_first(data, "efac:Organization") or []
    if isinstance(orgs, dict):
        orgs = [orgs]
    winner_org = next(
        (o for o in orgs if _boamp_text(
            _boamp_dict(_boamp_dict(o.get("efac:Company")).get("cac:PartyIdentification")).get("cbc:ID")
        ) == winner_org_id),
        None,
    )
    if winner_org is None:
        return None

    company = _boamp_dict(winner_org.get("efac:Company"))
    address = _boamp_dict(company.get("cac:PostalAddress"))
    settled_contract = _boamp_dict(notice_result.get("efac:SettledContract"))
    framework = notice_result.get("efbc:OverallMaximumFrameworkContractsAmount")

    listed = winner_org.get("efbc:ListedOnRegulatedMarketIndicator")
    detail = {
        "winner": {
            "registration_number": _boamp_text(_boamp_dict(company.get("cac:PartyLegalEntity")).get("cbc:CompanyID")),
            "city": _boamp_text(address.get("cbc:CityName")),
            "postal_code": _boamp_text(address.get("cbc:PostalZone")),
            "nuts": _boamp_text(address.get("cbc:CountrySubentityCode")),
            "country": _boamp_text(_boamp_dict(address.get("cac:Country")).get("cbc:IdentificationCode")),
            "size": _boamp_text(company.get("efbc:CompanySizeCode")),
            "regulated_market": (listed == "true") if listed is not None else None,
        },
        "lot": {
            "identifier": _boamp_text(_boamp_dict(lot_result.get("efac:TenderLot")).get("cbc:ID")),
            "title": None,  # not reliably present at this path across vintages — never guessed
            "duration": None,
        },
        "contract": {
            "identifier": _boamp_text(_boamp_dict(settled_contract.get("efac:ContractReference")).get("cbc:ID")),
            "conclusion_date": _trim_date(_boamp_text(settled_contract.get("cbc:IssueDate"))),
            "tender_identifier": _boamp_text(_boamp_dict(lot_result.get("efac:LotTender")).get("cbc:ID")),
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
        # CR-007 Phase C: buyer's own procurement reference — see
        # _boamp_internal_identifier's docstring and normalize_ted's
        # internal_identifier for the TED-side counterpart.
        "internal_identifier": _boamp_internal_identifier(raw),
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


# ── CR-010 §1: Belgium e-Procurement (BDA) ──────────────────────────────────
# Raw shape verified live (2026-10-14) against connectors/eprocurement.py's
# search endpoint — see that module's docstring for the endpoint/auth/field
# discovery notes this normalizer depends on.

# BE's own multilingual shape is a list of {"language": "NL"/"FR"/"DE"/"EN",
# "text": ...} dicts (2-letter ISO) — e.g. dossier.titles, dossier.descriptions,
# organisation.organisationNames — unlike TED's {lang: value} dict (_pick_lang)
# or BOAMP's flat single-language fields. Map to the shared 3-letter-ISO dict
# shape _pick_lang already expects, so the existing language-preference order
# and _record_language's translation gate (run.py's `language == "eng"` skip)
# stay identical across all three sources instead of reinventing them for one
# connector. Unrecognised codes are dropped rather than guessed.
_EPROC_LANG_2_TO_3 = {"EN": "eng", "FR": "fra", "NL": "nld", "DE": "deu"}


def _eprocurement_lang_dict(items):
    return {
        _EPROC_LANG_2_TO_3[item["language"]]: item["text"]
        for item in (items or [])
        if item.get("language") in _EPROC_LANG_2_TO_3 and item.get("text")
    }


def _eprocurement_strip_checksum(code):
    """BE's own CPV codes carry an official check-digit suffix
    ("45000000-7") — confirmed live against /api/loc/cpvs's full 9454-code
    reference list, every entry keyed the same way. Every other source in
    this system (TED, BOAMP) and the tenant's own configured CPV list
    (config/cpv.yaml) use the bare 8-digit code with no suffix — found during
    review (2026-10-15) that leaving BE's suffix on `cpv_codes` broke CPV-
    based match classification for every Belgian record (run._tag's
    `cpv_codes & tenant cpv_set` intersection could never hit, since one side
    always carried "-N" and the other never did). Stripped here so BE's
    output matches the shared bare-code convention everywhere downstream.
    """
    return code.split("-", 1)[0]


def _eprocurement_cpv_codes(raw):
    codes = []
    main = raw.get("cpvMainCode") or {}
    if main.get("code"):
        codes.append(_eprocurement_strip_checksum(main["code"]))
    for extra in raw.get("cpvAdditionalCodes") or []:
        if extra.get("code"):
            codes.append(_eprocurement_strip_checksum(extra["code"]))
    return _dedupe(codes)


def normalize_eprocurement(raw):
    """Normalise a Belgium e-Procurement (BDA) search-result record into the
    SAME schema as normalize_ted/normalize_boamp.

    CR-010 §1: award fields (raw_award_winner/value/currency/detail) and
    value/value_currency are deliberately left absent — NOT an oversight.
    The search endpoint this raw shape comes from never returns them at all
    (confirmed live against real award-type notices); they only exist in a
    separate per-notice detail call whose payload is raw eForms XML, not
    structured JSON — genuinely new parsing work, deferred per CR-010 §1
    rather than built unasked. See connectors/eprocurement.py's docstring.
    """
    dossier = raw.get("dossier") or {}
    tag_line, tag_lang = _pick_lang(_eprocurement_lang_dict(dossier.get("titles")))
    description, desc_lang = _pick_lang(_eprocurement_lang_dict(dossier.get("descriptions")))
    buyer, _buyer_lang = _pick_lang(
        _eprocurement_lang_dict((raw.get("organisation") or {}).get("organisationNames"))
    )
    procedure_type = dossier.get("procurementProcedureType") or ""
    return {
        "source": "e-Procurement",
        "pub_number": raw.get("referenceNumber", ""),
        "tag_line": tag_line,
        "description": description,
        "buyer": buyer,
        "country": "BE",
        "place": ", ".join(dict.fromkeys(raw.get("nutsCodes") or [])),
        "category": map_category(_first(raw.get("natures")).lower()),
        "procedure": procedure_type.lower(),
        "pub_date": raw.get("publicationDate") or "",
        "deadline": raw.get("vaultSubmissionDeadline") or "",
        "cpv_codes": _eprocurement_cpv_codes(raw),
        "matched_terms": [],
        "match_source": None,
        "url": (
            f"https://www.publicprocurement.be/publication-workspaces/{raw['publicationWorkspaceId']}"
            if raw.get("publicationWorkspaceId") else ""
        ),
        "first_seen": None,
        # CR-010 §1 Q3: dossier.referenceNumber is the buyer's own procurement
        # reference (confirmed live, e.g. "PPP0TA-235/6035/2026-002-..."),
        # distinct from the publication-level referenceNumber used as
        # pub_number above (which changes per republish/corrigendum) — same
        # cross-portal join key role as TED's internal-identifier-proc /
        # BOAMP's _boamp_internal_identifier.
        "internal_identifier": dossier.get("referenceNumber") or None,
        # Absent from the search response entirely — see docstring above.
        "value": "",
        "value_currency": "",
        # CR-010 §1: Belgium is NOT monolingual like BOAMP — notices appear in
        # NL/FR/DE (and sometimes EN for EU-funded ones). Derived per notice
        # via the same tag/description-language reconciliation TED uses
        # (_record_language), now that both are in the shared 3-letter form.
        "language": _record_language(tag_lang, desc_lang),
        # CR-010 §1: deferred — see docstring above.
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }


# ── CR-010 §2: Germany — Bekanntmachungsservice ─────────────────────────────
# Raw shape produced by connectors/bekanntmachungsservice.py's own CSV join
# (NOT the source's native CSV columns 1:1 — that join already happened in
# the connector; see its docstring for the real per-table field names this
# was assembled from and why CSV was chosen over OCDS).

def _bekanntmachungsservice_notice_url(raw):
    notice_id = raw.get("noticeIdentifier")
    return f"https://oeffentlichevergabe.de/ui/en/search/details?noticeId={notice_id}" if notice_id else ""


def normalize_bekanntmachungsservice(raw):
    """Normalise a Bekanntmachungsservice (Germany) record into the SAME
    schema as normalize_ted/normalize_boamp/normalize_eprocurement.

    CR-010 §2: `deadline` is always "" — confirmed absent from BOTH this
    source's structured exports (CSV and OCDS alike), present only in the
    raw eForms XML this connector doesn't parse (deferred, same call as
    Belgium's award-XML deferral). Unlike Belgium, award fields (raw_award_
    winner/-value/-currency) ARE populated here — this source's CSV export
    already has them as plain columns, no XML parsing needed. raw_award_detail
    is still None (not asked for here the way CR-010 §1 discusses Belgium's
    explicitly; would need contract.csv/duration.csv joins this CR didn't
    scope). See connectors/bekanntmachungsservice.py's docstring throughout.
    """
    return {
        "source": "Bekanntmachungsservice",
        "pub_number": raw.get("noticeIdentifier", ""),
        "tag_line": raw.get("title") or "",
        "description": raw.get("description") or "",
        "buyer": raw.get("buyerName") or "",
        "country": "DE",
        "place": ", ".join(dict.fromkeys(raw.get("nutsCodes") or [])),
        "category": map_category(raw.get("mainNature") or ""),
        # Mixed formats confirmed live: some notices carry a clean eForms
        # procedure code ("open", "de-open", "neg-w-call", ...), others a
        # raw German descriptive string (e.g. "beabsichtigte Beschränkte
        # Ausschreibung") — passed through as-is, not reconciled into one
        # convention (new normalization work this CR didn't ask for).
        "procedure": (raw.get("procedureType") or "").lower(),
        "pub_date": _trim_date(raw.get("publicationDate")) or "",
        # CR-010 §2: deliberately absent — see docstring above.
        "deadline": "",
        "cpv_codes": _dedupe(raw.get("cpvCodes") or []),
        "matched_terms": [],
        "match_source": None,
        "url": _bekanntmachungsservice_notice_url(raw),
        "first_seen": None,
        # purpose.csv's internalIdentifier (BT-22) — THE reason CSV was
        # chosen over OCDS (see connectors/bekanntmachungsservice.py's
        # docstring): confirmed absent from OCDS entirely.
        "internal_identifier": raw.get("internalIdentifier") or None,
        # Pre-award estimate (BT-27), distinct from the award total below —
        # same notice-level-estimate-vs-award-total split normalize_ted draws
        # between estimated-value-proc and result-value-notice. Sparse by
        # design (confirmed live: ~7% fill, matching TED's own "optional
        # under EU rules" experience) — not a Germany-specific gap.
        "value": raw.get("estimatedValue") or "",
        "value_currency": raw.get("estimatedValueCurrency") or "",
        # Germany publishes exclusively in German (per the connector's own
        # docs) — no per-notice detection needed, always translate (CR-001 R3),
        # same stance BOAMP takes for France.
        "language": "deu",
        # CR-010 §2: populated — see docstring above on why this source
        # doesn't need Belgium's XML-parsing deferral.
        "raw_award_winner": raw.get("winnerName") or None,
        "raw_award_value": raw.get("noticeValue") or None,
        "raw_award_currency": raw.get("noticeValueCurrency") or None,
        "raw_award_detail": None,
    }


# ── CR-011 §4: UK — Find a Tender Service ───────────────────────────────────
# Raw shape produced by connectors/find_a_tender.py's own flattening of the
# OCDS release object (NOT the raw OCDS structure 1:1 — see that connector's
# docstring for the live-confirmed nested field paths this was assembled
# from, same "connector flattens, normalize consumes flat" split
# bekanntmachungsservice.py/normalize_bekanntmachungsservice already use).

_UK_CATEGORY = {"goods": "Supply", "services": "Services", "works": "Works"}


def normalize_find_a_tender(raw):
    """CR-011 §4: no award fields — this connector only pulls `stages=tender`
    releases (open opportunities, the Review Queue's actual use case), never
    the `award` stage, so raw_award_* stay None/absent, same as any other
    source's not-yet-awarded notices.
    """
    return {
        "source": "Find a Tender",
        "pub_number": raw.get("id", ""),
        "tag_line": raw.get("title") or "",
        "description": raw.get("description") or "",
        "buyer": raw.get("buyerName") or "",
        "country": "GBR",
        "place": raw.get("place") or "",
        "category": _UK_CATEGORY.get((raw.get("mainProcurementCategory") or "").lower(), "Other"),
        "procedure": (raw.get("procurementMethodDetails") or "").lower(),
        "pub_date": _trim_date(raw.get("date")) or "",
        "deadline": _trim_date(raw.get("tenderPeriodEndDate")) or "",
        "cpv_codes": _dedupe(raw.get("cpvCodes") or []),
        "matched_terms": [],
        "match_source": None,
        "url": f"https://www.find-tender.service.gov.uk/Notice/{raw['id']}" if raw.get("id") else "",
        "first_seen": None,
        "internal_identifier": None,
        "value": str(raw["valueAmount"]) if raw.get("valueAmount") is not None else "",
        "value_currency": raw.get("valueCurrency") or "",
        # CR-011 §4: English-only source — see connectors/find_a_tender.py's
        # module docstring. No per-notice detection needed, same stance
        # bekanntmachungsservice.py takes for "always German".
        "language": "eng",
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }


# ── CR-011 §4: UK — Contracts Finder ────────────────────────────────────────
# Raw shape produced by connectors/contracts_finder.py's own flattening —
# see that connector's docstring for the confirmed-live field paths.

def normalize_contracts_finder(raw):
    """CR-011 §4: no `place` — confirmed live that this source's delivery-
    location data sits under `tender.items[].deliveryAddresses[]` as a list
    of partial address fragments (postal code, or just a bare country name
    with no code), not the single clean `deliveryLocation.description`
    string Find a Tender's items carry — assembling a place string from
    that fragment shape wasn't confirmed worth guessing at, so left "" for
    v1 rather than render a partial/misleading location.
    """
    return {
        "source": "Contracts Finder",
        "pub_number": raw.get("id", ""),
        "tag_line": raw.get("title") or "",
        "description": raw.get("description") or "",
        "buyer": raw.get("buyerName") or "",
        "country": "GBR",
        "place": "",
        "category": _UK_CATEGORY.get((raw.get("mainProcurementCategory") or "").lower(), "Other"),
        "procedure": (raw.get("procurementMethodDetails") or "").lower(),
        "pub_date": _trim_date(raw.get("date")) or "",
        "deadline": _trim_date(raw.get("tenderPeriodEndDate")) or "",
        "cpv_codes": _dedupe(raw.get("cpvCodes") or []),
        "matched_terms": [],
        "match_source": None,
        "url": raw.get("noticeUrl") or "",
        "first_seen": None,
        "internal_identifier": None,
        "value": str(raw["valueAmount"]) if raw.get("valueAmount") is not None else "",
        "value_currency": raw.get("valueCurrency") or "",
        "language": "eng",
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }


# ── CR-011 §4: Netherlands — TenderNed ──────────────────────────────────────
# Raw shape produced by connectors/tenderned.py's own list+detail merge — see
# that connector's docstring for the confirmed-live field names this was
# read from.

_NL_TYPE_OPDRACHT = {"L": "Supply", "D": "Services", "W": "Works"}  # typeOpdracht.code


def normalize_tenderned(raw):
    """CR-011 §4: no value/value_currency — confirmed absent from both
    TenderNed endpoints this connector reads (see connectors/tenderned.py's
    module docstring). No award fields — this is the open-notice list
    endpoint, never award/result data.
    """
    return {
        "source": "TenderNed",
        "pub_number": str(raw["publicatieId"]) if raw.get("publicatieId") is not None else "",
        "tag_line": raw.get("aanbestedingNaam") or "",
        "description": raw.get("opdrachtBeschrijving") or "",
        "buyer": raw.get("opdrachtgeverNaam") or "",
        "country": "NLD",
        "place": raw.get("nuts") or "",
        "category": _NL_TYPE_OPDRACHT.get(raw.get("typeOpdracht") or "", "Other"),
        "procedure": (raw.get("procedure") or "").lower(),
        "pub_date": (raw.get("publicatieDatum") or "")[:10],
        "deadline": (raw.get("sluitingsDatum") or "")[:10],
        "cpv_codes": _dedupe(raw.get("cpvCodes") or []),
        "matched_terms": [],
        "match_source": None,
        "url": raw.get("url") or "",
        "first_seen": None,
        # The buyer's own procurement reference — see
        # connectors/tenderned.py's _fetch_detail docstring.
        "internal_identifier": raw.get("referentieNummer"),
        "value": "",
        "value_currency": "",
        # CR-011 §4: Dutch-only source — see connectors/tenderned.py's module
        # docstring. Goes through DeepL like BOAMP.
        "language": "nld",
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }


# ── CR-011 §4: Philippines — PhilGEPS ───────────────────────────────────────
# Raw shape produced by connectors/philgeps.py's own regex row parsing — see
# that connector's docstring for the confirmed-live HTML structure this was
# read from.

def normalize_philgeps(raw):
    """CR-011 §4: no cpv_codes, value, place, or procedure — all confirmed
    absent from the list view this connector reads (see connectors/
    philgeps.py's module docstring). category stays "Other" — PhilGEPS' own
    ~150-name free-text category vocabulary has no verified crosswalk to our
    CPV-derived category enum, same honest-gap stance normalize_canadabuys
    takes for its GSIN/UNSPSC codes. No award fields — this is the open-
    opportunity list, never award data.
    """
    return {
        "source": "PhilGEPS",
        "pub_number": raw.get("refID") or "",
        "tag_line": raw.get("title") or "",
        "description": "",
        "buyer": raw.get("agency") or "",
        "country": "PHL",
        "place": "",
        "category": "Other",
        "procedure": "",
        "pub_date": raw.get("publicationDate") or "",
        "deadline": raw.get("closingDate") or "",
        "cpv_codes": [],
        "matched_terms": [],
        "match_source": None,
        "url": (
            f"https://notices.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashBidNoticeAbstractUI.aspx"
            f"?menuIndex=3&refID={raw['refID']}&Result=3"
        ) if raw.get("refID") else "",
        "first_seen": None,
        "internal_identifier": None,
        "value": "",
        "value_currency": "",
        # CR-011 §4: English-only source — see connectors/philgeps.py's
        # module docstring.
        "language": "eng",
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }


# ── CR-011 §4: Canada — CanadaBuys ──────────────────────────────────────────
# Raw shape produced by connectors/canadabuys.py's own CSV row parsing — see
# that connector's docstring for the exact confirmed-live CSV column names
# this was read from.

_CA_CATEGORY = {"*cnst": "Works"}  # see connectors/canadabuys.py — only this code confirmed live


def normalize_canadabuys(raw):
    """CR-011 §4: no `cpv_codes` — see connectors/canadabuys.py's module
    docstring (GSIN/UNSPSC, no verified mapping to CPV built). No award
    fields — this file is the open-tender-notice feed, never award data
    (CanadaBuys publishes awards as a SEPARATE dataset this CR doesn't pull).
    No `value`/`value_currency` — confirmed absent from the source entirely.
    """
    return {
        "source": "CanadaBuys",
        "pub_number": raw.get("referenceNumber") or "",
        "tag_line": raw.get("title") or "",
        "description": raw.get("description") or "",
        "buyer": raw.get("contractingEntityName") or "",
        "country": "CAN",
        "place": raw.get("regionsOfDelivery") or "",
        "category": _CA_CATEGORY.get((raw.get("procurementCategory") or "").lower(), "Other"),
        "procedure": (raw.get("procurementMethod") or "").lower(),
        "pub_date": (raw.get("publicationDate") or "")[:10],
        "deadline": (raw.get("closingDate") or "")[:10],
        "cpv_codes": [],
        "matched_terms": [],
        "match_source": None,
        "url": raw.get("noticeURL") or "",
        "first_seen": None,
        "internal_identifier": None,
        "value": "",
        "value_currency": "",
        # CR-011 §4: English-only read of a bilingual source — see
        # connectors/canadabuys.py's module docstring.
        "language": "eng",
        "raw_award_winner": None,
        "raw_award_value": None,
        "raw_award_currency": None,
        "raw_award_detail": None,
    }
