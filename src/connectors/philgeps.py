"""Philippines — PhilGEPS connector.

CR-011 §4: the CR's own preliminary table only knew of PhilGEPS' public
bid-notice *search page* and flagged "check for open data" — the separate
"PhilGEPS Open Data" portal (open.philgeps.gov.ph) turned out to be
aggregate analytics only (yearly/monthly summaries), not a per-notice feed,
confirmed via its own page content. This connector instead reads the real
search page directly, confirmed live (2026-10-16) to be genuinely public
and unauthenticated:

  GET https://notices.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashOpportunitiesSearchUI.aspx?menuIndex=3&ClickFrom=OpenOpp&Result=3
    Confirmed live with a completely cookie-less request (no session, no
    login) — returns a real, nationwide, cross-category, cross-agency list
    of every currently open opportunity, newest-published-first (13,000+
    rows at time of writing). This one URL replaces what looked at first
    like a 150-category enumeration problem (the "View By Category" page
    the CR's own exploration would naturally land on first) — the
    cross-category "Search" view covers everything in one sorted list.

  Pagination is the real complication: this is a classic ASP.NET WebForms
  app (GridView + custom pager), not a REST API. Page 1 is a plain GET;
  every subsequent page requires replaying the page's own __VIEWSTATE/
  __VIEWSTATEGENERATOR/__EVENTVALIDATION hidden fields in a POST with
  __EVENTTARGET=pgCtrlDetailedSearch$nextLB (confirmed via the page's own
  live DOM, then independently confirmed by a cold Python request chaining
  3 real pages with no browser involved — each page's own freshly-returned
  VIEWSTATE must be used for the NEXT request, not the original page 1's).
  No separate login/API-key gate exists on this read path at all.

ROW SHAPE (confirmed against real HTML, not guessed): each result row is
  <td>index</td><td>publish date</td><td>closing date</td>
  <td><a id="...hyLinkTitle" href="...refID=NNNN...">title</a>
      <span id="...lblOrgAndBusCat">, {category} ,{agency}</span></td>
`lblOrgAndBusCat`'s text is ONE string combining category + agency, not two
separate elements — splitting it needed care: category names never carry an
internal comma in any example seen, but agency names sometimes do (Philippine
addressing commonly writes "CITY OF X, PROVINCE") — split(",", 2) (maxsplit
from the START) correctly isolates category as the middle piece and leaves
any agency-internal commas alone, confirmed against a real "CITY OF MANDAUE,
CEBU" agency name.

NO CPV, NO VALUE, NO PLACE, NO PROCEDURE: confirmed absent from this list
view. PhilGEPS has its own ~150-category free-text vocabulary (e.g.
"Information Technology", "Catering Services") — no verified crosswalk to
our CPV set exists, and inventing a fuzzy keyword-based one from category
NAMES alone risked silently wrong matches (same reasoning connectors/
canadabuys.py gives for its own GSIN/UNSPSC gap) — `cpv_codes` stays [],
relevance is keyword-matched only. Value/place/procedure simply aren't in
this list view at all (they may exist on the per-notice detail/abstract
page, but fetching that for every one of 13,000+ live notices was never a
reasonable N+1 cost — same call as not fetching Belgium's per-notice XML
detail for award fields in CR-010 §1).

LANGUAGE: always "eng" — every notice observed (hundreds, across many
regions/agencies) was in English, matching CR-011 §4's own "English
sources (UK, AU, CA, SG, PH) skip translation" expectation for PH.

RATE LIMIT: no documented limit found; REQUEST_DELAY_SECONDS below is this
build's own "be a good guest" choice (CR-010 §1's standing stance toward
government services), not a confirmed server-side requirement.
"""
import datetime
import html as html_lib
import logging
import re
import time

import requests

import match
from connectors import Records, retry

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://notices.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashOpportunitiesSearchUI.aspx"
    "?menuIndex=3&ClickFrom=OpenOpp&Result=3"
)
USER_AGENT = "Tenderizer-Scout/1.0 (+https://tender-izer.com; contact: ops@tender-izer.com)"
REQUEST_DELAY_SECONDS = 1.0
DETAIL_URL = ("https://notices.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashBidNoticeAbstractUI.aspx"
              "?menuIndex=3&refID={ref}&Result=3")
_BUDGET_RE = re.compile(r"Approved Budget for the Contract:\s*([A-Z]{3})\s*([\d,]+(?:\.\d+)?)")


def parse_approved_budget(detail_html):
    """("PHP", "100000.00") from a notice's abstract page, or (None, None).
    The list view has no budget; the abstract page shows it as
    "Approved Budget for the Contract: PHP 100,000.00" (confirmed live
    2026-10-17)."""
    text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", detail_html or "")))
    m = _BUDGET_RE.search(text)
    return (m.group(1), m.group(2).replace(",", "")) if m else (None, None)


def _enrich_budgets(session, rows, keywords):
    """Fetch the approved budget for rows whose title matches a distinctive
    keyword — the only ones that can surface (no CPV here), ~100 of a few
    thousand per run. Why (2026-10-17): one barangay posted 28 separate
    PHP 100,000 (~EUR 1,600) negotiated purchases of chairs and a steel-pipe
    tent, each with its own solicitation number — legally distinct, so not
    duplicates, but noise. With a value, the existing value floor
    (exclusions.yaml) filters them. Never raises; a row without a budget is
    kept, as before.
    """
    for row in rows:
        if not match.match_relevance(row.get("title") or "", keywords or []):
            continue
        try:
            resp = session.get(DETAIL_URL.format(ref=row["refID"]), headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            row["budgetCurrency"], row["approvedBudget"] = parse_approved_budget(resp.text)
        except Exception:
            pass
        time.sleep(REQUEST_DELAY_SECONDS)

_ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td[^>]*>\d+</td>'
    r'<td[^>]*>(?P<pub>[\d/]+)</td>'
    r'<td[^>]*>(?P<close>[\d/:apmAPM ]+)</td>'
    r'<td[^>]*>\s*'
    r'<a id="[^"]*hyLinkTitle" href="[^"]*refID=(?P<ref>\d+)[^"]*">(?P<title>[^<]*)</a>\s*'
    r'<span id="[^"]*lblOrgAndBusCat">(?P<combined>[^<]*)</span>',
    re.S,
)


def _describe(e):
    """The error, plus where a redirect loop started — "Exceeded 30
    redirects" alone (production, 2026-10-21; not reproducible from a
    residential IP the same day, a full 200-page run went clean) says
    nothing about which hop bounced."""
    history = getattr(getattr(e, "response", None), "history", None) or []
    if isinstance(e, requests.TooManyRedirects) and history:
        first = history[0]
        return f"{e} (first hop: {first.status_code} {first.url} -> {first.headers.get('Location')})"
    return f"{type(e).__name__}: {e}"


def _field(html, name):
    m = re.search(r'id="' + name + r'" value="([^"]*)"', html)
    return m.group(1) if m else None


def _to_iso_date(ddmmyyyy):
    """"16/10/2026" -> "2026-10-16"; "" if unparseable (never guessed)."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", ddmmyyyy or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def parse_response(html):
    """Return the list of raw per-row dicts on this one page; [] if none.

    See module docstring's ROW SHAPE section for the split(",", 2) reasoning.
    """
    out = []
    for m in _ROW_RE.finditer(html):
        combined = m.group("combined")
        parts = combined.split(",", 2)
        category = parts[1].strip() if len(parts) > 1 else ""
        agency = parts[2].strip() if len(parts) > 2 else ""
        out.append({
            "refID": m.group("ref"),
            "title": m.group("title").strip(),
            "category": category,
            "agency": agency,
            "publicationDate": _to_iso_date(m.group("pub")),
            "closingDate": _to_iso_date(m.group("close")),
        })
    return out


def _next_page(session, html):
    """POST the pager's own __doPostBack target, replaying THIS page's own
    fresh hidden fields (not the original page 1's — see module docstring).
    Returns the next page's HTML, or None if the required fields are
    missing (end of a session-less response / unexpected shape — fail soft
    rather than POST a garbage request).
    """
    viewstate = _field(html, "__VIEWSTATE")
    generator = _field(html, "__VIEWSTATEGENERATOR")
    validation = _field(html, "__EVENTVALIDATION")
    if viewstate is None or validation is None:
        return None
    data = {
        "__EVENTTARGET": "pgCtrlDetailedSearch$nextLB",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": generator or "",
        "__EVENTVALIDATION": validation,
    }
    resp = retry.call(session.post, SEARCH_URL, data=data, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch(cpv_codes, keywords, since, limit=100, max_records=4000):
    """`max_records` is 4000, not the shared 2000: production hit 2000 on every
    run (2026-10-17), which is only ~2 days of PhilGEPS volume — one missed
    nightly run would lose notices. 4000 (~4 days) costs ~7 more minutes.

    Page the nationwide, newest-first open-opportunities list until a row
    older than `since` is seen (same "stop once past the cutoff" stance
    tenderned.py/bekanntmachungsservice.py take), or a page repeats/returns
    no rows (safe termination — see module docstring on the untested "last
    of 682 pages" edge). `cpv_codes`/`keywords` are accepted for interface
    parity but unused — see module docstring's classification gap.
    """
    since_str = since.isoformat()
    session = requests.Session()
    try:
        resp = retry.call(session.get, SEARCH_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.TooManyRedirects as e:
        raise RuntimeError(f"page 1: {_describe(e)}") from e
    resp.raise_for_status()
    html = resp.text

    # A later page failing (after connectors.retry) keeps what's already
    # collected — the list is newest-first, so that's the most recent notices
    # — and flags it as a warning rather than discarding the whole run.
    out, seen_refs, page, warning = Records(), set(), 1, None
    while True:
        rows = parse_response(html)
        if not rows:
            break
        new_refs = {r["refID"] for r in rows}
        if new_refs <= seen_refs:
            break  # repeated page — same termination signal every other source's loop uses
        seen_refs |= new_refs

        stop = False
        for row in rows:
            if row["publicationDate"] and row["publicationDate"] < since_str:
                stop = True
                break
            out.append(row)
            if len(out) >= max_records:
                stop = True
                break
        if stop:
            break

        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            next_html = _next_page(session, html)
        except requests.RequestException as e:
            warning = f"stopped after page {page} ({len(out)} notices): {_describe(e)}"
            logger.warning("PhilGEPS: %s", warning)
            break
        if next_html is None:
            break
        html, page = next_html, page + 1
    _enrich_budgets(session, out, keywords)
    out.warning = warning
    return out
