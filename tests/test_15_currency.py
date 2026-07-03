"""Step 15 — ECB daily-rate currency conversion (CR-001 D2, F6).
Interface:
  currency.fetch_ecb_rates() -> {"date": "YYYY-MM-DD", "rates": {"EUR": 1.0, ...}}  (network)
  currency.fetch_ecb_rates_or_fallback() -> same, but never raises
  currency.to_eur(amount, currency, snapshot) -> (eur_amount, rate_date) | (None, None)
"""
import pytest
import currency

SNAPSHOT = {"date": "2026-07-01", "rates": {"EUR": 1.0, "SEK": 11.23, "USD": 1.08}}


def test_to_eur_converts_using_snapshot_rate():
    eur, rate_date = currency.to_eur("112300", "SEK", SNAPSHOT)
    assert eur == pytest.approx(10000.0)
    assert rate_date == "2026-07-01"


def test_to_eur_identity_for_eur():
    eur, rate_date = currency.to_eur("200000", "EUR", SNAPSHOT)
    assert eur == pytest.approx(200000.0)
    assert rate_date == "2026-07-01"


def test_to_eur_lowercase_currency_code():
    eur, _ = currency.to_eur("112300", "sek", SNAPSHOT)
    assert eur == pytest.approx(10000.0)


def test_to_eur_missing_amount_returns_none():
    assert currency.to_eur("", "SEK", SNAPSHOT) == (None, None)
    assert currency.to_eur(None, "SEK", SNAPSHOT) == (None, None)


def test_to_eur_missing_currency_returns_none():
    assert currency.to_eur("100000", "", SNAPSHOT) == (None, None)


def test_to_eur_unknown_currency_returns_none():
    # currency not in the ECB snapshot (e.g. a typo, or a currency ECB doesn't quote)
    assert currency.to_eur("100000", "XXX", SNAPSHOT) == (None, None)


def test_to_eur_unparseable_amount_returns_none():
    assert currency.to_eur("not-a-number", "SEK", SNAPSHOT) == (None, None)


def test_fallback_snapshot_only_converts_eur():
    fallback = currency.FALLBACK_SNAPSHOT
    assert currency.to_eur("200000", "EUR", fallback) == (200000.0, None)
    assert currency.to_eur("200000", "SEK", fallback) == (None, None)


def test_fetch_or_fallback_never_raises_on_bad_url():
    result = currency.fetch_ecb_rates_or_fallback(url="https://does-not-exist.invalid/x.xml", timeout=5)
    assert result == currency.FALLBACK_SNAPSHOT


@pytest.mark.network
def test_fetch_ecb_rates_live():
    result = currency.fetch_ecb_rates()
    assert result["rates"]["EUR"] == 1.0
    assert len(result["rates"]) > 1   # at least one non-EUR rate quoted
    assert result["date"]
