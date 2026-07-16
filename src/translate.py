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
"""
import hashlib
import os
import requests
from dotenv import load_dotenv

import store

load_dotenv()

FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
PRO_ENDPOINT = "https://api.deepl.com/v2/translate"


def _endpoint(api_key):
    return FREE_ENDPOINT if api_key.endswith(":fx") else PRO_ENDPOINT


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
    except Exception:
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
