"""DeepL *document* translation — a separate API surface from translate.py's
synchronous text endpoint. Whole-file translation is async: upload a file
(POST /v2/document), poll its status (POST /v2/document/{id}), then download
the result (POST /v2/document/{id}/result) — three calls, not one, with
DeepL-side state in between. None of translate.py's single-call/content-hash-
cache machinery fits that shape, hence a new module rather than extending it.
Uses the same DEEPL_API_KEY env var as translate.py — one DeepL account,
one key, two API surfaces.

Storage for the original/translated files is S3-compatible object storage
(reusing backup.py's BACKUP_S3_* credential pattern, with its own bucket-or-
prefix) rather than local disk: these are the buyer's actual bid documents,
and the existing local-filesystem upload dirs (data/uploads, data/
vault_uploads, ...) have no confirmed persistent volume in production — see
DEPLOYMENT.md, which documents one for Postgres only. Don't repeat that
durability risk for a new feature.

Every function here degrades to a status/message rather than raising, same
convention as translate.py's _deepl_call — a DeepL outage or a bad document
should surface as a real error, not crash the caller.
"""
import logging
import os
import pathlib

import requests
from dotenv import load_dotenv

import alerts
import object_storage
import translate

load_dotenv()

logger = logging.getLogger(__name__)

FREE_DOCUMENT_ENDPOINT = "https://api-free.deepl.com/v2/document"
PRO_DOCUMENT_ENDPOINT = "https://api.deepl.com/v2/document"

# CR-012 §2: English (EN-GB specifically, not bare "EN") is the only target —
# confirmed as the client's own decision (CR-012's "Client decisions
# (confirmed)" §3), not a default left unconfigured. No language picker.
TARGET_LANG = "EN-GB"

# CR-012 §2: confirmed live (2026, DeepL's own published API limits) — this
# account is Pro/Growth tier (see translate.py's CR-012 §1 fix), which caps
# at 30 MB / 1,000,000 characters per document; Free tier is lower (10 MB /
# 500K) but this account isn't on it. Exposed for the frontend's drop-zone
# copy and a server-side reject-before-upload check — never trust the
# client-side check alone.
MAX_DOCUMENT_SIZE_BYTES = 30 * 1024 * 1024

# CR-012 §2's own accepted-format list (a deliberate subset of DeepL's wider
# format support — XLIFF/SRT are optional per the CR, everything else DeepL
# also accepts (IDML/XML/JSON/images-in-beta) isn't asked for here).
ACCEPTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".html", ".htm", ".xlf", ".xliff", ".srt"}

# Formats _sample_text_for_detection below can actually extract text from —
# a narrower set than ACCEPTED_EXTENSIONS. PPTX/XLSX/XLF/SRT uploads always
# go through DeepL normally (no already-English pre-check) rather than
# adding new parsing dependencies (python-pptx, openpyxl text extraction)
# for what CR-012 §2 treats as a cost-saving nicety, not a correctness
# requirement.
_TEXT_EXTRACTABLE_EXTENSIONS = {".pdf", ".docx", ".txt", ".html", ".htm"}

# DeepL bills every submitted docx/pptx/xlsx/pdf a flat minimum regardless of
# actual size or content (confirmed against DeepL's own best-practices docs,
# 2026-09-04) — a two-page annex costs the same as a 200-page SOW. The quota
# guard below prices uploads on this, not a text-length guess (unknowable
# pre-upload for a binary file anyway).
MINIMUM_BILLED_CHARACTERS = 50_000

# DeepL's own GET /v2/usage `character_limit` field does not reflect the real
# contractual ceiling on an API Growth plan — confirmed live: it returns an
# effectively-unlimited sentinel because overage is billed, not API-enforced.
# So the allowance is configured here, not discovered from the API. If DeepL
# ever starts enforcing a smaller character_limit than this, that's the more
# trustworthy number and check_quota() below prefers it.
DEFAULT_INCLUDED_ALLOWANCE = 12_000_000
DEFAULT_QUOTA_RESERVE = 2_000_000


def _api_key(api_key=None):
    # Same cleaning as translate.py (CR-012 §1) — a dirty key here would
    # both break the header and misroute free-vs-Pro in _document_endpoint.
    return translate._clean_key(api_key or os.getenv("DEEPL_API_KEY"))


def _document_endpoint(key):
    """Free keys (":fx" suffix) live on api-free.deepl.com — the same rule
    translate._endpoint applies to the text API. Hardcoding the Pro host
    here 403'd every document call on a Free key."""
    return FREE_DOCUMENT_ENDPOINT if key.endswith(":fx") else PRO_DOCUMENT_ENDPOINT


def _error_message(resp):
    try:
        return resp.json().get("message") or f"DeepL returned HTTP {resp.status_code}"
    except Exception:
        return f"DeepL returned HTTP {resp.status_code}"


def _sample_text_for_detection(path, content_type, max_chars=2000):
    """Best-effort text sample for the already-English pre-check below — PDF/
    DOCX/TXT/HTML only, see _TEXT_EXTRACTABLE_EXTENSIONS. Reuses vault.py's
    own PDF/DOCX extraction (vault.parse_document) rather than a second
    implementation. Returns "" on any failure (corrupt file, scanned PDF
    with no text layer, etc.) — detection simply can't run, same as an
    unsupported format; never raises into the caller.
    """
    import pathlib

    ext = pathlib.Path(path).suffix.lower()
    if ext not in _TEXT_EXTRACTABLE_EXTENSIONS:
        return ""
    try:
        if ext in (".pdf", ".docx"):
            import vault
            text = vault.parse_document(path, content_type)
        else:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
        return (text or "")[:max_chars]
    except Exception:
        return ""


def detect_already_english(path, content_type, api_key=None):
    """CR-012 §2: "files already in English: skip, mark 'already English'".
    Runs a cheap DeepL *text* detection call (translate.translate_and_detect)
    on a short sample rather than submitting the whole document, so an
    already-English file never consumes a document-translation billing unit
    at all. Returns True only when detection actually succeeded AND came
    back "eng" — any failure/empty-sample/unsupported-format case returns
    False (upload normally; never silently skip a file we couldn't verify).
    """
    sample = _sample_text_for_detection(path, content_type)
    if not sample.strip():
        return False
    _, language, status = translate.translate_and_detect(sample, api_key=api_key)
    return status == "ok" and language == "eng"


def upload_document(file_bytes, filename, target_lang, source_lang=None, api_key=None, timeout=30):
    """POST /v2/document. Returns (document_id, document_key, error_message)
    — error_message is None on success, otherwise DeepL's own explanation
    when available (e.g. an unsupported format or a size-limit rejection)
    so the UI can show something better than "translation failed".

    CR-012 §2: failures are logged + sent to Sentry here, same "never fail
    silently" convention translate.py's _deepl_call uses — this module had
    no such reporting before (it degrades to a status/message for its
    caller, same as before, but a production failure needs to be visible
    the same way a text-translation failure already is).
    """
    key = _api_key(api_key)
    if not key:
        return None, None, "DeepL API key is not configured"
    data = {"target_lang": target_lang}
    if source_lang:
        data["source_lang"] = source_lang
    try:
        resp = requests.post(
            _document_endpoint(key),
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data=data,
            files={"file": (filename, file_bytes)},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return body["document_id"], body["document_key"], None
    except requests.HTTPError:
        detail = _error_message(resp)
        logger.warning("DeepL document upload failed: %s", detail)
        alerts.capture_error(f"DeepL document upload failed: {detail}")
        return None, None, detail
    except Exception as e:
        logger.warning("DeepL document upload failed: %s: %s", type(e).__name__, e)
        alerts.capture_error(f"DeepL document upload failed: {type(e).__name__}: {e}")
        return None, None, str(e)


def check_status(document_id, document_key, api_key=None, timeout=15):
    """POST /v2/document/{id}. Returns a dict:
    {"status": "queued"|"translating"|"done"|"error"|"unavailable",
     "seconds_remaining": int|None, "billed_characters": int|None,
     "error_message": str|None}

    "unavailable" is this wrapper's own status for a network/auth failure —
    distinct from DeepL's "error", which means DeepL itself gave up on the
    document (e.g. a scanned PDF with no extractable text, or a password-
    protected file) and comes with a real `message` to surface.
    """
    key = _api_key(api_key)
    if not key:
        return {"status": "unavailable", "seconds_remaining": None,
                "billed_characters": None, "error_message": "DeepL API key is not configured"}
    try:
        resp = requests.post(
            f"{_document_endpoint(key)}/{document_id}",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"document_key": document_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return {"status": body.get("status"), "seconds_remaining": body.get("seconds_remaining"),
                "billed_characters": body.get("billed_characters"), "error_message": body.get("message")}
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        logger.warning("DeepL document status check failed: %s", detail)
        alerts.capture_error(f"DeepL document status check failed: {detail}")
        return {"status": "unavailable", "seconds_remaining": None,
                "billed_characters": None, "error_message": str(e)}


def download_result(document_id, document_key, api_key=None, timeout=60):
    """POST /v2/document/{id}/result — the translated file's raw bytes.
    This call is also when DeepL finalizes billing for the document. Returns
    (content_bytes, error_message); content_bytes is None on failure.
    """
    key = _api_key(api_key)
    if not key:
        return None, "DeepL API key is not configured"
    try:
        resp = requests.post(
            f"{_document_endpoint(key)}/{document_id}/result",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"document_key": document_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.content, None
    except requests.HTTPError:
        detail = _error_message(resp)
        logger.warning("DeepL document download failed: %s", detail)
        alerts.capture_error(f"DeepL document download failed: {detail}")
        return None, detail
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        logger.warning("DeepL document download failed: %s", detail)
        alerts.capture_error(f"DeepL document download failed: {detail}")
        return None, str(e)


def get_usage(api_key=None, timeout=15):
    """GET /v2/usage. Returns {"character_count": int, "character_limit": int}
    or None on any failure — the quota guard fails open on this (see
    check_quota) rather than blocking translation over a usage-check hiccup.
    """
    key = _api_key(api_key)
    if not key:
        return None
    try:
        resp = requests.get(translate._usage_endpoint(key), headers={"Authorization": f"DeepL-Auth-Key {key}"}, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        return {"character_count": body.get("character_count"), "character_limit": body.get("character_limit")}
    except Exception:
        return None


def estimate_cost(document_count):
    return MINIMUM_BILLED_CHARACTERS * document_count


def check_quota(document_count, api_key=None, included_allowance=None, reserve=None):
    """Light guard, not defensive: reads live usage, prices the upload at
    DeepL's real flat per-document minimum, and refuses only when accepting
    it would eat into the configured reserve protecting ingest translation
    (translate.py's hot path). Never blocks on a usage-read failure — that's
    advisory, not a hard gate.

    Returns {"allowed": bool, "estimated_cost": int,
             "remaining_characters": int|None, "remaining_documents": int|None,
             "reason": str|None}. remaining_* is None only when usage
    couldn't be read at all.
    """
    included_allowance = (included_allowance if included_allowance is not None
                           else int(os.getenv("DEEPL_INCLUDED_ALLOWANCE", DEFAULT_INCLUDED_ALLOWANCE)))
    reserve = reserve if reserve is not None else int(os.getenv("DEEPL_QUOTA_RESERVE", DEFAULT_QUOTA_RESERVE))
    estimated_cost = estimate_cost(document_count)

    usage = get_usage(api_key=api_key)
    if usage is None:
        return {"allowed": True, "estimated_cost": estimated_cost, "remaining_characters": None,
                "remaining_documents": None, "reason": "usage_unavailable"}

    allowance = included_allowance
    character_limit = usage.get("character_limit")
    if character_limit and character_limit < included_allowance:
        allowance = character_limit  # DeepL started enforcing a real (smaller) cap — trust it
    remaining = allowance - (usage.get("character_count") or 0)
    remaining_documents = max(remaining // MINIMUM_BILLED_CHARACTERS, 0)
    allowed = (remaining - estimated_cost) >= reserve
    return {
        "allowed": allowed,
        "estimated_cost": estimated_cost,
        "remaining_characters": remaining,
        "remaining_documents": remaining_documents,
        "reason": None if allowed else "This upload would breach the reserve kept for ingest translation.",
    }


# ── Object storage ───────────────────────────────────────────────────────────
# CR-013 §2: delegated to object_storage — one bucket (DOCUMENTS_S3_BUCKET)
# for every document area, filesystem fallback on the persistent volume when
# unset. The old fallback to BACKUP_S3_BUCKET and its credentials is gone on
# purpose: tender documents must not land in the database-backup bucket under
# the backup credentials. Function names kept so callers and tests stay put.

class StorageNotConfigured(RuntimeError):
    """Kept for callers' except-clauses; storage always has a filesystem
    fallback now, so nothing raises this any more."""


def upload_to_s3(key, content_bytes):
    object_storage.put(key, content_bytes)


def download_from_s3(key):
    return object_storage.get(key)


def delete_from_s3(key):
    """Idempotent on both backends, so safe to call after a partial delete."""
    object_storage.delete(key)


def storage_key(tenant_id, key_id, version, filename):
    """{tenant}/translations/_/{key_id}-{original|translated}{ext} — CR-013's
    tenant-first key layout. `key_id` is a fresh uuid (original) or the row
    id (translated result). Pre-CR-013 rows keep their old
    "documents/{tenant}/{key_id}/{version}{ext}" keys until storage-migrate
    renames them; object_storage still resolves those.
    """
    ext = pathlib.Path(filename or "").suffix[:10]
    return f"{int(tenant_id)}/translations/_/{key_id}-{version}{ext}"
