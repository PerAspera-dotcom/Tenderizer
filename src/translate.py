"""DeepL translation (CR-001 D1, R3, C1). Generic — not tender-specific, so
Composer's document ingest can reuse translate_cached() directly once that
pipeline exists (it doesn't yet; see C1 note in the CR-001 summary).

Free tier for now (confirmed by the key's ':fx' suffix, which also picks the
right endpoint — see _endpoint()); upgrading to Pro later is a key swap, no
code change (api.deepl.com replaces api-free.deepl.com automatically).

Two layers:
  - translate_to_english(text, api_key) -> (text|None, "ok"|"unavailable")
    Pure network call. Never raises — a failure/timeout/rate-limit (DeepL 429/
    456) degrades to (None, "unavailable") so a temporary DeepL outage never
    breaks the pipeline; callers fall back to showing the original text.
  - translate_cached(conn, text, api_key) -> (text|None, "ok"|"unavailable")
    Wraps the above with store.py's translations table, keyed by a sha256 of
    the source text, so the same notice/document is never re-translated on a
    repeat run.

CR-011 §1: every failure above used to collapse into the same generic
"unavailable" with NOTHING logged — a wrong key, the wrong endpoint for the
key type, an exhausted quota, and a network blip were all indistinguishable,
including in production. _deepl_call now logs the real reason (logger.warning)
and reports it to Sentry (alerts.capture_error) on every failure, and
check_health() gives /api/health something to show without waiting for a
real notice to need translation first — a lightweight GET /v2/usage ping
(doesn't consume translation quota, unlike an actual /v2/translate call).
"""
import hashlib
import logging
import os
import requests
from dotenv import load_dotenv

import alerts
import store

load_dotenv()

logger = logging.getLogger(__name__)

FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
PRO_ENDPOINT = "https://api.deepl.com/v2/translate"
FREE_USAGE_ENDPOINT = "https://api-free.deepl.com/v2/usage"
PRO_USAGE_ENDPOINT = "https://api.deepl.com/v2/usage"


def _endpoint(api_key):
    return FREE_ENDPOINT if api_key.endswith(":fx") else PRO_ENDPOINT


def _usage_endpoint(api_key):
    return FREE_USAGE_ENDPOINT if api_key.endswith(":fx") else PRO_USAGE_ENDPOINT


def _describe_failure(exc, resp=None):
    """A short, human-readable reason for a failed DeepL call — distinguishing
    quota (456) and rate-limit (429) from an outright auth/network failure is
    the whole point of CR-011 §1's "never fail silently again"; a bare
    exception repr wasn't enough to diagnose the production issue this CR
    exists to fix.
    """
    if resp is not None:
        if resp.status_code == 456:
            return "DeepL quota exceeded (456)"
        if resp.status_code == 429:
            return "DeepL rate-limited (429)"
        if resp.status_code in (401, 403):
            return f"DeepL auth rejected ({resp.status_code}) — check DEEPL_API_KEY / endpoint"
        return f"DeepL HTTP {resp.status_code}: {resp.text[:200]}"
    return f"{type(exc).__name__}: {exc}"


def content_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# DeepL's detected_source_language -> this app's 3-letter convention (TED's
# own multilingual-dict keys — see normalize.py's LANG_PREF/_pick_lang and
# BOAMP's hardcoded "fra"). Covers DeepL's documented source languages;
# unmapped codes fall back to a lowercased copy of whatever DeepL returned
# rather than raising, since a novel/unlisted code is still worth recording.
DEEPL_TO_ISO3 = {
    "BG": "bul", "CS": "ces", "DA": "dan", "DE": "deu", "EL": "ell", "EN": "eng",
    "ES": "spa", "ET": "est", "FI": "fin", "FR": "fra", "HU": "hun", "ID": "ind",
    "IT": "ita", "JA": "jpn", "KO": "kor", "LT": "lit", "LV": "lav", "NB": "nob",
    "NL": "nld", "PL": "pol", "PT": "por", "RO": "ron", "RU": "rus", "SK": "slk",
    "SL": "slv", "SV": "swe", "TR": "tur", "UK": "ukr", "ZH": "zho",
}


def _deepl_call(text, api_key, timeout):
    """Shared implementation for translate_to_english/translate_and_detect.
    Returns (translated_text, detected_source_language|None, status) — never
    raises; status is 'ok' or 'unavailable' (missing key, network error,
    timeout, or a non-2xx response including DeepL's 429/456). Empty/
    whitespace-only input short-circuits to ("", None, "ok").

    CR-011 §1: a missing key stays silent here (deliberately — logging it on
    EVERY call would spam a run with dozens of identical lines; check_health()
    below is the dedicated once-per-run place that reports "not configured"
    clearly). An actual failed call (wrong key, quota, network, DeepL outage)
    IS logged + sent to Sentry here, every time — unlike email alerts, Sentry
    dedupes repeats into one issue by message, so this isn't spam there.
    """
    if not text or not text.strip():
        return "", None, "ok"
    api_key = api_key or os.getenv("DEEPL_API_KEY")
    if not api_key:
        return None, None, "unavailable"
    try:
        resp = requests.post(
            _endpoint(api_key),
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            data={"text": text, "target_lang": "EN"},
            timeout=timeout,
        )
        resp.raise_for_status()
        translation = resp.json()["translations"][0]
        return translation["text"], translation.get("detected_source_language"), "ok"
    except requests.HTTPError as e:
        detail = _describe_failure(e, resp=resp)
        logger.warning("DeepL translation call failed: %s", detail)
        alerts.capture_error(f"DeepL translation call failed: {detail}")
        return None, None, "unavailable"
    except Exception as e:
        detail = _describe_failure(e)
        logger.warning("DeepL translation call failed: %s", detail)
        alerts.capture_error(f"DeepL translation call failed: {detail}")
        return None, None, "unavailable"


def translate_to_english(text, api_key=None, timeout=15):
    """One DeepL call. Returns (translated_text, status) — never raises.

    status is 'ok' or 'unavailable' (missing key, network error, timeout, or
    a non-2xx response including DeepL's 429 too-many-requests / 456 quota-
    exceeded). Empty/whitespace-only input short-circuits to ("", "ok").
    """
    text_out, _detected_lang, status = _deepl_call(text, api_key, timeout)
    return text_out, status


def translate_and_detect(text, api_key=None, timeout=15):
    """Same call as translate_to_english, but also surfaces DeepL's own
    detected_source_language (a 2-letter code, e.g. "FR") mapped to this
    app's 3-letter convention via DEEPL_TO_ISO3 — used by
    scratch_backfill_language.py to backfill `language` for tenders ingested
    before CR-001 R3's language-tagging existed (store.upsert() is
    insert-only, so those rows were never retroactively populated and the
    translation loop's `not r.get("language")` check silently skips them
    forever otherwise). Returns (translated_text, language|None, status).
    """
    text_out, detected, status = _deepl_call(text, api_key, timeout)
    language = DEEPL_TO_ISO3.get((detected or "").upper(), (detected or "").lower() or None)
    return text_out, language, status


def translate_cached(conn, text, api_key=None):
    """translate_to_english, cached by content hash in store's `translations`
    table — a repeat run (or a second notice with identical text) never
    re-calls DeepL for the same string.
    """
    if not text or not text.strip():
        return "", "ok"
    h = content_hash(text)
    cached = store.get_cached_translation(conn, h)
    if cached is not None:
        return cached, "ok"
    translated, status = translate_to_english(text, api_key=api_key)
    if status == "ok":
        store.cache_translation(conn, h, translated)
    return translated, status


def check_health(api_key=None, timeout=10):
    """CR-011 §1: a lightweight DeepL connectivity+auth check, independent of
    whether any real notice needed translating this run — GET /v2/usage
    doesn't consume translation quota, unlike an actual /v2/translate call,
    so this is safe to call once every pipeline run regardless. Deliberately
    NOT used for the "quota %" part of CR-011's ask: /v2/usage's own
    character_limit is already documented (.env.example, DEEPL_INCLUDED_
    ALLOWANCE's comment) to return an effectively-unlimited sentinel on this
    account's plan rather than a real number — reporting it here would be
    reporting something known to be meaningless, not a missing feature.

    Returns (status, detail): status is 'ok' | 'unavailable' (no key
    configured at all) | 'failing' (key present but the call itself failed).
    detail is '' for 'ok', a short human-readable reason otherwise.
    """
    api_key = api_key or os.getenv("DEEPL_API_KEY")
    if not api_key:
        return "unavailable", "DEEPL_API_KEY is not set"
    try:
        resp = requests.get(
            _usage_endpoint(api_key),
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return "ok", ""
    except requests.HTTPError as e:
        detail = _describe_failure(e, resp=resp)
        logger.warning("DeepL health check failed: %s", detail)
        return "failing", detail
    except Exception as e:
        detail = _describe_failure(e)
        logger.warning("DeepL health check failed: %s", detail)
        return "failing", detail
