"""ECB daily reference-rate currency conversion (CR-001 D2, F6).

Fetch once per run and cache the day's snapshot — never call ECB per tender.
Free, no API key. Rates are EUR-based (1 EUR = X <currency>). The snapshot's
own date is stored on each converted record (store.fx_rate_date) so a
conversion is reproducible: re-fetching ECB's historical feed for that exact
date reproduces the same rate.
"""
import xml.etree.ElementTree as ET
import requests

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_NS = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}

FALLBACK_SNAPSHOT = {"date": None, "rates": {"EUR": 1.0}}


def fetch_ecb_rates(url=ECB_DAILY_URL, timeout=30):
    """Return {"date": "YYYY-MM-DD", "rates": {"EUR": 1.0, "SEK": 11.23, ...}}."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    cube_time = root.find(".//ecb:Cube[@time]", _NS)
    rates = {"EUR": 1.0}
    for cube in cube_time.findall("ecb:Cube", _NS):
        rates[cube.get("currency")] = float(cube.get("rate"))
    return {"date": cube_time.get("time"), "rates": rates}


def fetch_ecb_rates_or_fallback(url=ECB_DAILY_URL, timeout=30):
    """Same as fetch_ecb_rates, but never raises — a network/parse failure
    degrades to EUR-only conversion (F6 then only ever excludes EUR values;
    non-EUR values fail the lookup and are kept, not excluded, on that run).
    """
    try:
        return fetch_ecb_rates(url, timeout)
    except Exception:
        return FALLBACK_SNAPSHOT


def to_eur(amount, currency, snapshot):
    """Convert `amount` in `currency` to EUR using `snapshot` (see fetch_ecb_rates).

    Returns (eur_amount, rate_date). Both None if amount/currency is missing,
    unparseable, or `currency` isn't in the snapshot (kept, not excluded, by F6).
    """
    if not amount or not currency:
        return None, None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None, None
    rate = snapshot["rates"].get(currency.upper())
    if not rate:
        return None, None
    return amount / rate, snapshot["date"]
