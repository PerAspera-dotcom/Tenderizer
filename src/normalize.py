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
    """Pick a language from a multilingual dict; value may be str or [str]."""
    if isinstance(field, str):
        return field
    if not isinstance(field, dict) or not field:
        return ""
    for lg in LANG_PREF:
        if lg in field:
            return _first(field[lg])
    return _first(next(iter(field.values())))

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
    return {
        "source": "TED",
        "pub_number": raw.get("publication-number", ""),
        "tag_line": _pick_lang(raw.get("notice-title", {})),
        "description": _pick_lang(raw.get("description-proc", {})),
        "buyer": _pick_lang(raw.get("buyer-name", {})),
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
    }

def record_hash(record):
    return hashlib.sha256(f"{record['source']}|{record['pub_number']}".encode()).hexdigest()


def _boamp_place(raw):
    depts = raw.get("code_departement") or []
    if isinstance(depts, str):
        depts = [depts]
    return ", ".join(f"FR-{d}" for d in depts)


def normalize_boamp(raw):
    """Normalise a BOAMP record into the SAME schema as normalize_ted.

    BOAMP fields are flat/single-language. Country is always FR; no CPV (keyword source).
    """
    idweb = raw.get("idweb", "")
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
        "cpv_codes": [],
        "matched_terms": [],
        "match_source": None,
        "url": f'https://www.boamp.fr/pages/avis/?q=idweb:%22{idweb}%22' if idweb else "",
        "first_seen": None,
    }
