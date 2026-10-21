"""Shared retry for connector HTTP calls.

Portals occasionally stall or 5xx for a moment (2026-10-20: one
Bekanntmachungsservice day hung past its 60 s read timeout, then downloaded
in ~1 s minutes later). Without a retry, one such blip fails the whole source
for the night. `call` retries timeouts, connection errors and 5xx; anything
else (2xx, 4xx, 429) comes straight back for the caller's own
raise_for_status / rate-limit handling — a malformed request won't fix
itself on retry.

Takes the request function itself (requests.get, session.post, ...) rather
than a method name, so it works the same for module-level calls and sessions.
Only used on read-only requests (searches and exports, incl. the POST-based
searches), so a retry can never duplicate a side effect.
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

ATTEMPTS = 3
DELAY_SECONDS = 10  # waits 10 s, then 20 s


def call(fn, *args, **kwargs):
    """fn(*args, **kwargs), retried on a transient failure. After the last
    attempt, a timeout/connection error is re-raised and a 5xx response is
    returned as-is (so the caller's raise_for_status reports it as before)."""
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(DELAY_SECONDS * attempt)
        last_try = attempt == ATTEMPTS - 1
        try:
            resp = fn(*args, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as e:
            if last_try:
                raise
            logger.warning("retrying %s after %s: %s", _url(args, kwargs), type(e).__name__, e)
            continue
        if resp.status_code < 500 or last_try:
            return resp
        logger.warning("retrying %s after HTTP %s", _url(args, kwargs), resp.status_code)


def _url(args, kwargs):
    return args[0] if args else kwargs.get("url", "?")
