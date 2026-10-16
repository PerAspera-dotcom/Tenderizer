"""CR-012 §3 — generic email-alert ingestion adapter for the four sources
SOURCES_VIABILITY.md's CR-012 §3 section found no scraping or API path for
(Saudi Etimad/AusTender/GeBIZ/ePerolehan): the client registers as a
supplier directly on each portal and points that portal's own tender-alert
emails at a dedicated Tenderizer inbound address; this module turns an
inbound email into the same normalized record shape every connector's
normalize_* function produces, so it flows through the identical
tag/filter/classify/store pipeline (run.process_record) as TED/BOAMP/etc.

NO SENDER-SPECIFIC PARSER SHIPS IN THIS BUILD — stated plainly, not a quiet
gap: CR-012 §3's own acceptance criterion asks for "at least one parser
tested against a real alert email", and no such email exists yet (nobody
has registered a supplier/alert account on any of the four portals — see
SOURCES_VIABILITY.md's registration-eligibility findings). Writing a parser
against a guessed email format would break the "verified live" discipline
every connector in this codebase follows (ted.py/boamp.py/eprocurement.py/
etc. all cite real captured traffic, never an assumed shape). What IS built:
the inbound webhook, tenant routing, sender *recognition* (SOURCE_DOMAINS —
confirmed-real domains for the four target portals, used for labeling/
health even before a parser exists), the raw-email log every unmatched
email lands in, and the PARSER_REGISTRY extension point. The moment a real
alert arrives (check email_alerts rows via GET /api/email-alerts, or the
raw row's own raw_text/raw_html), add one function here, map it in
PARSER_REGISTRY, done — no other code changes needed.

Each registered parser:
    parser(from_address: str, subject: str, text: str, html: str) -> list[dict]
returns a list of normalized records (same 20-key shape as normalize_ted,
etc. — zero or more, since one alert digest email commonly lists several
notices at once). Never raises — a parser error is caught by the caller
and the email is logged as parse_status="error", same fail-soft stance
every connector's fetch() takes toward a single bad record.
"""
import logging

logger = logging.getLogger(__name__)

# Confirmed-real sender domains for the four CR-012 §3 target portals
# (verified against each portal's own site — SOURCES_VIABILITY.md's CR-012
# §3 section) — used to LABEL an inbound email with a recognizable source
# name even before a parser exists for it, so the raw log in GET
# /api/email-alerts reads "AusTender (no parser yet)" rather than just
# "unknown". Domain matching is substring-on-the-sender's-domain-part, not
# an exact match, since alert systems commonly send from a subdomain
# (e.g. alerts.tenders.gov.au, notify.gebiz.gov.sg).
SOURCE_DOMAINS = {
    "tenders.gov.au": "AusTender",
    "gebiz.gov.sg": "GeBIZ",
    "etimad.sa": "Etimad",
    "eperolehan.gov.my": "ePerolehan",
}

# source name -> parser(from_address, subject, text, html) -> list[dict].
# Empty by design — see module docstring. Add an entry here once a real
# alert email exists to build and test a parser against.
PARSER_REGISTRY = {}


def _domain_of(address):
    """"Alerts <noreply@notify.tenders.gov.au>" -> "notify.tenders.gov.au".
    Returns "" for anything that doesn't look like an email address rather
    than raising — a malformed From header shouldn't crash ingestion.
    """
    if "@" not in (address or ""):
        return ""
    return address.rsplit("@", 1)[-1].strip().strip(">").lower()


def recognize_source(from_address):
    """The friendly source name for a sender domain, or None if this isn't
    one of the four recognized portals — domain SUFFIX match (not full
    containment) so "fake-tenders.gov.au.evil.example" is correctly NOT
    recognized as AusTender.
    """
    domain = _domain_of(from_address)
    if not domain:
        return None
    for known_domain, name in SOURCE_DOMAINS.items():
        if domain == known_domain or domain.endswith("." + known_domain):
            return name
    return None


def parse_email(from_address, subject, text, html):
    """Returns (source_name_or_None, records, error_message_or_None).
    records is [] whenever no parser exists yet (recognized sender or not)
    — this is the expected, common case in this build; the caller (api.py)
    logs the raw email either way so nothing is lost.
    """
    source = recognize_source(from_address)
    parser = PARSER_REGISTRY.get(source)
    if parser is None:
        return source, [], None
    try:
        records = parser(from_address, subject, text, html)
        return source, records, None
    except Exception as e:
        logger.warning("email_alerts: parser for %s failed: %s: %s", source, type(e).__name__, e)
        return source, [], f"{type(e).__name__}: {e}"
