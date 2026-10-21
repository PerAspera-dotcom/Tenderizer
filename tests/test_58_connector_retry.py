"""connectors.retry — shared retry of transient HTTP failures (timeouts,
connection errors, 5xx) for every connector. Delays are zeroed by conftest.
"""
from datetime import date

import pytest
import requests

from connectors import retry, ted, tenderned


class _Resp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sequence(*outcomes):
    """A fake request fn that plays back outcomes (exceptions are raised)."""
    calls = []
    it = iter(outcomes)

    def fn(*args, **kwargs):
        calls.append(args)
        outcome = next(it)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fn, calls


def test_call_returns_first_success_without_retrying():
    fn, calls = _sequence(_Resp(200))
    assert retry.call(fn, "u").status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [
    requests.ReadTimeout("Read timed out."),
    requests.ConnectionError("reset"),
    _Resp(503),
])
def test_call_retries_a_transient_failure(failure):
    fn, calls = _sequence(failure, _Resp(200))
    assert retry.call(fn, "u").status_code == 200
    assert len(calls) == 2


def test_call_reraises_a_timeout_after_the_last_attempt():
    fn, calls = _sequence(*[requests.ReadTimeout("Read timed out.")] * retry.ATTEMPTS)
    with pytest.raises(requests.ReadTimeout):
        retry.call(fn, "u")
    assert len(calls) == retry.ATTEMPTS


def test_call_returns_the_last_5xx_for_the_callers_raise_for_status():
    fn, calls = _sequence(*[_Resp(502)] * retry.ATTEMPTS)
    assert retry.call(fn, "u").status_code == 502
    assert len(calls) == retry.ATTEMPTS


@pytest.mark.parametrize("status", [400, 404, 429])
def test_call_does_not_retry_client_errors_or_rate_limits(status):
    # 429 is left to the connector's own Retry-After handling (find_a_tender)
    fn, calls = _sequence(_Resp(status))
    assert retry.call(fn, "u").status_code == status
    assert len(calls) == 1


def test_ted_fetch_survives_one_timeout(monkeypatch):
    fn, calls = _sequence(requests.ReadTimeout("Read timed out."),
                          _Resp(200, {"notices": [], "totalNoticeCount": 0}))
    monkeypatch.setattr(ted.requests, "post", fn)
    assert ted.fetch(["35522000"], [], date(2026, 10, 1)) == []
    assert len(calls) == 2


def test_tenderned_session_calls_are_retried(monkeypatch):
    outcomes = iter([_Resp(500), _Resp(200, {"content": [], "last": True})])

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            return next(outcomes)

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    assert tenderned.fetch([], [], date(2026, 10, 1)) == []
