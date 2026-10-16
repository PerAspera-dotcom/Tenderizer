"""Step 18 — DeepL translation (CR-001 D1, R3, C1).
  translate.translate_to_english(text, api_key) -> (text|None, "ok"|"unavailable")
  translate.translate_cached(conn, text, api_key) -> same, cached by content hash

requests.post is mocked throughout — these tests never hit the real DeepL API.
"""
import requests
import alerts
import store
import translate


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text or (f"{status_code} error" if status_code >= 400 else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            # real requests.Response.raise_for_status() passes response=self —
            # matched here so _describe_failure's resp.status_code/.text access
            # works the same under test as it does against the real library.
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._json


def _ok_response(text="Bonjour translated"):
    return _FakeResponse(200, {"translations": [{"text": text, "detected_source_language": "FR"}]})


# ── translate_to_english (pure network call) ─────────────────────────────────

def test_successful_translation(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response("Hello"))
    text, status = translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert (text, status) == ("Hello", "ok")


def test_empty_text_short_circuits_without_network_call(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1) or _ok_response())
    text, status = translate.translate_to_english("   ", api_key="fakekey:fx")
    assert (text, status) == ("", "ok")
    assert called == []  # no network call made


def test_missing_api_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    text, status = translate.translate_to_english("Bonjour", api_key=None)
    assert (text, status) == (None, "unavailable")


def test_rate_limited_response_is_unavailable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(429))
    text, status = translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert (text, status) == (None, "unavailable")


def test_quota_exceeded_response_is_unavailable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(456))
    text, status = translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert (text, status) == (None, "unavailable")


def test_network_exception_is_unavailable(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(requests, "post", boom)
    text, status = translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert (text, status) == (None, "unavailable")


def test_free_tier_key_uses_free_endpoint(monkeypatch):
    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        return _ok_response()
    monkeypatch.setattr(requests, "post", fake_post)
    translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert captured["url"] == translate.FREE_ENDPOINT


def test_pro_key_uses_pro_endpoint(monkeypatch):
    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        return _ok_response()
    monkeypatch.setattr(requests, "post", fake_post)
    translate.translate_to_english("Bonjour", api_key="fakekey-no-suffix")
    assert captured["url"] == translate.PRO_ENDPOINT


# ── translate_cached (content-hash cache via store) ──────────────────────────

def test_cache_miss_calls_api_and_caches(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    calls = []
    def fake_post(*a, **k):
        calls.append(1)
        return _ok_response("Hello")
    monkeypatch.setattr(requests, "post", fake_post)

    text, status = translate.translate_cached(conn, "Bonjour", api_key="fakekey:fx")
    assert (text, status) == ("Hello", "ok")
    assert len(calls) == 1
    assert store.get_cached_translation(conn, translate.content_hash("Bonjour")) == "Hello"


def test_cache_hit_never_calls_api(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.cache_translation(conn, translate.content_hash("Bonjour"), "Hello (cached)")

    def boom(*a, **k):
        raise AssertionError("should not call the API on a cache hit")
    monkeypatch.setattr(requests, "post", boom)

    text, status = translate.translate_cached(conn, "Bonjour", api_key="fakekey:fx")
    assert (text, status) == ("Hello (cached)", "ok")


def test_failed_translation_is_not_cached(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(429))

    text, status = translate.translate_cached(conn, "Bonjour", api_key="fakekey:fx")
    assert (text, status) == (None, "unavailable")
    assert store.get_cached_translation(conn, translate.content_hash("Bonjour")) is None


# ── translate_and_detect (also surfaces detected_source_language) ───────────

def test_translate_and_detect_maps_deepl_code_to_iso3(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response("Hello"))
    text, language, status = translate.translate_and_detect("Bonjour", api_key="fakekey:fx")
    assert (text, language, status) == ("Hello", "fra", "ok")


def test_translate_and_detect_unmapped_code_falls_back_to_lowercase(monkeypatch):
    def fake_post(*a, **k):
        return _FakeResponse(200, {"translations": [{"text": "x", "detected_source_language": "XX"}]})
    monkeypatch.setattr(requests, "post", fake_post)
    _, language, status = translate.translate_and_detect("???", api_key="fakekey:fx")
    assert (language, status) == ("xx", "ok")


def test_translate_and_detect_empty_text_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1) or _ok_response())
    text, language, status = translate.translate_and_detect("   ", api_key="fakekey:fx")
    assert (text, language, status) == ("", None, "ok")
    assert called == []


def test_translate_and_detect_unavailable_has_no_language(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(456))
    text, language, status = translate.translate_and_detect("Bonjour", api_key="fakekey:fx")
    assert (text, language, status) == (None, None, "unavailable")


# ── CR-011 §1: failures are logged + sent to Sentry, never silent ──────────

def test_failed_call_logs_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(456))
    with caplog.at_level("WARNING", logger="translate"):
        translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert any("quota exceeded" in r.message.lower() for r in caplog.records)


def test_failed_call_reports_to_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(456))
    captured = []
    monkeypatch.setattr(alerts, "capture_error", lambda msg, **k: captured.append(msg))
    translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert len(captured) == 1
    assert "quota" in captured[0].lower()


def test_missing_key_does_not_spam_sentry(monkeypatch):
    # deliberate: check_health() below is the dedicated once-per-run place
    # for "not configured" — logging/capturing it on every translate call
    # would spam identically for every single notice in a run.
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    captured = []
    monkeypatch.setattr(alerts, "capture_error", lambda msg, **k: captured.append(msg))
    translate.translate_to_english("Bonjour", api_key=None)
    assert captured == []


def test_network_exception_is_logged_with_its_own_type(monkeypatch, caplog):
    def boom(*a, **k):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(requests, "post", boom)
    with caplog.at_level("WARNING", logger="translate"):
        translate.translate_to_english("Bonjour", api_key="fakekey:fx")
    assert any("ConnectionError" in r.message for r in caplog.records)


def test_quota_vs_rate_limit_vs_auth_are_distinguished(monkeypatch):
    for status, expect in [(456, "quota"), (429, "rate-limited"), (401, "auth rejected")]:
        monkeypatch.setattr(requests, "post", lambda *a, s=status, **k: _FakeResponse(s))
        captured = []
        monkeypatch.setattr(translate.logger, "warning", lambda msg, *a, c=captured: c.append(msg % a if a else msg))
        translate.translate_to_english("Bonjour", api_key="fakekey:fx")
        assert any(expect in m.lower() for m in captured), f"status {status} -> {captured}"


# ── CR-011 §1: check_health (the /api/health DeepL status) ─────────────────

def test_check_health_ok(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, {"character_count": 1}))
    status, detail = translate.check_health(api_key="fakekey:fx")
    assert (status, detail) == ("ok", "")


def test_check_health_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    status, detail = translate.check_health(api_key=None)
    assert status == "unavailable"
    assert "DEEPL_API_KEY" in detail


def test_check_health_failing_call_is_failing(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(401))
    status, detail = translate.check_health(api_key="fakekey:fx")
    assert status == "failing"
    assert "auth rejected" in detail.lower()


def test_check_health_does_not_consume_translation_quota(monkeypatch):
    # hits /v2/usage, never /v2/translate
    called_translate = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called_translate.append(1) or _ok_response())
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, {}))
    translate.check_health(api_key="fakekey:fx")
    assert called_translate == []


def test_check_health_uses_free_vs_pro_usage_endpoint(monkeypatch):
    captured = {}
    def fake_get(url, **kwargs):
        captured["url"] = url
        return _FakeResponse(200, {})
    monkeypatch.setattr(requests, "get", fake_get)
    translate.check_health(api_key="fakekey:fx")
    assert captured["url"] == translate.FREE_USAGE_ENDPOINT
    translate.check_health(api_key="fakekey-no-suffix")
    assert captured["url"] == translate.PRO_USAGE_ENDPOINT


def test_different_text_different_cache_entry(tmp_path, monkeypatch):
    conn = store.init_db(str(tmp_path / "t.db"))
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response("A"))
    translate.translate_cached(conn, "Texte un", api_key="fakekey:fx")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response("B"))
    translate.translate_cached(conn, "Texte deux", api_key="fakekey:fx")

    assert store.get_cached_translation(conn, translate.content_hash("Texte un")) == "A"
    assert store.get_cached_translation(conn, translate.content_hash("Texte deux")) == "B"
