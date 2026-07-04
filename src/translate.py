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


def translate_to_english(text, api_key=None, timeout=15):
    """One DeepL call. Returns (translated_text, status) — never raises.

    status is 'ok' or 'unavailable' (missing key, network error, timeout, or
    a non-2xx response including DeepL's 429 too-many-requests / 456 quota-
    exceeded). Empty/whitespace-only input short-circuits to ("", "ok").
    """
    if not text or not text.strip():
        return "", "ok"
    api_key = api_key or os.getenv("DEEPL_API_KEY")
    if not api_key:
        return None, "unavailable"
    try:
        resp = requests.post(
            _endpoint(api_key),
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            data={"text": text, "target_lang": "EN"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["translations"][0]["text"], "ok"
    except Exception:
        return None, "unavailable"


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
