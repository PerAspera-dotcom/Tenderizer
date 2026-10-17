"""Netherlands — TenderNed connector.

CR-011 §4: confirmed live (2026-10-16) that TenderNed's own "TNS publication
webservice" needs NO credentials at all — contrary to the generic developer
docs, which only describe a separate, credential-gated "TenderNed API" (XML,
request access via functioneelbeheer@tenderned.nl) for bulk/historical pulls.
This build uses the confirmed-open JSON path instead:

  GET https://www.tenderned.nl/papi/tenderned-rs-tns/publicaties?page=N&size=M
    Confirmed live, no auth header sent, real data returned. Paginated
    (page/size), newest-first — inferred from the endpoint consistently
    returning today's publications first across repeated fetches and from
    the platform's own "latest publication" RSS feed sharing the name
    ("laatste-publicatie"), but the sort order itself isn't documented
    anywhere found — NOT independently confirmed via an explicit
    `sort=` param. fetch() below stops paging once it sees a notice older
    than `since`; if the assumption is ever wrong, the practical effect is
    an early stop that misses older same-day notices, not silent data
    corruption, and health would show an implausibly low count that's easy
    to notice.
  GET https://www.tenderned.nl/papi/tenderned-rs-tns/publicaties/{id}
    Confirmed live — carries the CPV code (with its official check-digit
    suffix, e.g. "18530000-3") and NUTS region the list endpoint omits.
    One extra request per in-range notice (N+1) — accepted for TenderNed's
    moderate daily volume (comparable to BOAMP), rate-limited below per the
    "be a good guest" stance CR-010 §1 set for government services.

CPV: confirmed check-digit-suffixed like Belgium's own CPV display
("18530000-3") — but unlike Belgium, nothing here needs to send a CPV
filter back to the server (no cpvCodes query param exists on this list
endpoint), so this only needs the INBOUND half: a plain prefix strip
(everything before the hyphen), not Belgium's static checksum lookup table.

VALUE: confirmed absent from both endpoints for the one notice fetched live
(a framework agreement, which is common — EU procurement rules make value
disclosure optional, same experience TED/BOAMP/Belgium already report).
Left as "" rather than assumed universally absent — a future build pass
should re-check against a value-disclosing notice before concluding this
source never has one.

LANGUAGE: always "nld" — Dutch-only source, same stance Bekanntmachungs-
service takes for "always German". Goes through DeepL like BOAMP.

NOTICE URL: the list endpoint's own `link.href` — confirmed live
(https://www.tenderned.nl/aankondigingen/overzicht/{id}).
"""
import time

import requests

import match

LIST_ENDPOINT = "https://www.tenderned.nl/papi/tenderned-rs-tns/publicaties"
DETAIL_ENDPOINT = "https://www.tenderned.nl/papi/tenderned-rs-tns/publicaties/{id}"
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"
REQUEST_DELAY_SECONDS = 0.5


# typePublicatie codes that only amend or close a notice published earlier:
# rectification, early termination, contract modification (~22% of the feed,
# tallied live 2026-10-17). Each candidate costs one detail request, which
# made this source ~30 min per org per run. Tender (AAO), award (AGO — feeds
# Past Tenders), prior information (VAK) and market consultation (MAC) stay.
SKIPPED_PUBLICATION_TYPES = {"REC", "VBE", "AAW"}


def _strip_checksum(code):
    """"18530000-3" -> "18530000" — see module docstring's CPV section."""
    return code.split("-")[0] if code else code


def parse_list_response(json_data):
    """Return the list of raw summary dicts; [] if none. Re-confirmed live
    2026-10-17: the endpoint now wraps results in a Spring-style page object
    ({"content": [...], "last": bool, "totalPages": ...}). It returned a bare
    array when this connector was built, and the unhandled change made every
    run silently report "ok, 0 notices" — so both shapes are accepted.
    """
    if isinstance(json_data, dict):
        content = json_data.get("content")
        return content if isinstance(content, list) else []
    return json_data if isinstance(json_data, list) else []


def _fetch_detail(session, publicatie_id):
    """CPV + NUTS + the buyer's own reference number for one notice — see
    module docstring. Confirmed live shape: `cpvCodes`/`nutsCodes` are each
    a list of {"code": ..., "omschrijving": ...} objects (NOT plain code
    strings), and `referentieNummer` is the buyer's own procurement
    reference (e.g. "IUC25-711") — the same cross-portal-join-key role as
    TED's internal-identifier-proc/BOAMP's own internal identifier, though
    nothing in this CR wires it into cross-portal dedup yet (no other new
    source here shares a join key with TenderNed to match against).
    """
    resp = session.get(DETAIL_ENDPOINT.format(id=publicatie_id), timeout=30)
    resp.raise_for_status()
    detail = resp.json()
    cpv_codes = [_strip_checksum(c["code"]) for c in (detail.get("cpvCodes") or []) if c.get("code")]
    nuts = ", ".join(dict.fromkeys(c["code"] for c in (detail.get("nutsCodes") or []) if c.get("code")))
    reference = detail.get("referentieNummer") or None
    return list(dict.fromkeys(cpv_codes)), nuts, reference


def fetch(cpv_codes, keywords, since, limit=100, max_records=2000):
    """Page newest-first until a notice older than `since` is seen (see
    module docstring on why that's an inferred, not confirmed, sort order),
    fetch each in-range notice's detail for CPV, then filter locally —
    same "no server-side classification filter, so filter after the fact"
    stance find_a_tender.py/bekanntmachungsservice.py take. `keywords` is
    accepted for interface parity but unused (CPV-native source).
    """
    cpv_filter = set(cpv_codes) if cpv_codes else None
    since_str = since.isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    candidates, page = [], 0
    while True:
        resp = session.get(LIST_ENDPOINT, params={"page": page, "size": limit}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = parse_list_response(data)
        if not batch:
            break
        stop = False
        for item in batch:
            pub_date = (item.get("publicatieDatum") or "")[:10]
            if pub_date and pub_date < since_str:
                stop = True
                break
            if (item.get("typePublicatie") or {}).get("code") in SKIPPED_PUBLICATION_TYPES:
                continue  # never an opportunity — skip before the per-notice detail call
            candidates.append(item)
        if stop or len(candidates) >= max_records:
            break
        if isinstance(data, dict) and data.get("last"):
            break  # final page of the wrapped shape
        page += 1

    out = []
    for item in candidates[:max_records]:
        publicatie_id = item.get("publicatieId")
        cpv_here, nuts, reference = ([], "", None)
        if publicatie_id:
            cpv_here, nuts, reference = _fetch_detail(session, publicatie_id)
            time.sleep(REQUEST_DELAY_SECONDS)
        # CPV OR a distinctive keyword (2026-10-17): CPV-only dropped notices filed
        # under a generic CPV ("Marquee hire" under event services) before the
        # keyword check ever ran. Same matcher as run._tag (whole-word, plus compounds).
        text = f"{item.get('aanbestedingNaam') or ''} {item.get('opdrachtBeschrijving') or ''}"
        if (cpv_filter is not None and cpv_filter.isdisjoint(cpv_here)
                and not match.match_relevance(text, keywords or [])):
            continue
        type_opdracht = (item.get("typeOpdracht") or {}).get("code", "")
        procedure = (item.get("procedure") or {}).get("omschrijving", "")
        out.append({
            "publicatieId": publicatie_id,
            "publicatieDatum": item.get("publicatieDatum") or "",
            "aanbestedingNaam": item.get("aanbestedingNaam") or "",
            "opdrachtBeschrijving": item.get("opdrachtBeschrijving") or "",
            "opdrachtgeverNaam": item.get("opdrachtgeverNaam") or "",
            "sluitingsDatum": item.get("sluitingsDatum") or "",
            "typeOpdracht": type_opdracht,
            "procedure": procedure,
            "cpvCodes": cpv_here,
            "nuts": nuts,
            "referentieNummer": reference,
            "url": (item.get("link") or {}).get("href") or "",
        })
    return out
