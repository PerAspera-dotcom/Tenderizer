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
    }
