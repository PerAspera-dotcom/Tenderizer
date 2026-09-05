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
import os

import requests
from dotenv import load_dotenv

load_dotenv()

DOCUMENT_ENDPOINT = "https://api.deepl.com/v2/document"
USAGE_ENDPOINT = "https://api.deepl.com/v2/usage"

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
    return api_key or os.getenv("DEEPL_API_KEY")


def _error_message(resp):
    try:
        return resp.json().get("message") or f"DeepL returned HTTP {resp.status_code}"
    except Exception:
        return f"DeepL returned HTTP {resp.status_code}"


def upload_document(file_bytes, filename, target_lang, source_lang=None, api_key=None, timeout=30):
    """POST /v2/document. Returns (document_id, document_key, error_message)
    — error_message is None on success, otherwise DeepL's own explanation
    when available (e.g. an unsupported format or a size-limit rejection)
    so the UI can show something better than "translation failed".
    """
    key = _api_key(api_key)
    if not key:
        return None, None, "DeepL API key is not configured"
    data = {"target_lang": target_lang}
    if source_lang:
        data["source_lang"] = source_lang
    try:
        resp = requests.post(
            DOCUMENT_ENDPOINT,
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data=data,
            files={"file": (filename, file_bytes)},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return body["document_id"], body["document_key"], None
    except requests.HTTPError:
        return None, None, _error_message(resp)
    except Exception as e:
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
            f"{DOCUMENT_ENDPOINT}/{document_id}",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"document_key": document_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return {"status": body.get("status"), "seconds_remaining": body.get("seconds_remaining"),
                "billed_characters": body.get("billed_characters"), "error_message": body.get("message")}
    except Exception as e:
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
            f"{DOCUMENT_ENDPOINT}/{document_id}/result",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"document_key": document_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.content, None
    except requests.HTTPError:
        return None, _error_message(resp)
    except Exception as e:
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
        resp = requests.get(USAGE_ENDPOINT, headers={"Authorization": f"DeepL-Auth-Key {key}"}, timeout=timeout)
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


# ── S3-compatible object storage ─────────────────────────────────────────────
# Same one-endpoint-covers-AWS/B2/GCS approach as backup.py's _s3_client, with
# its own bucket-or-prefix — DOCUMENTS_S3_BUCKET falls back to BACKUP_S3_BUCKET
# (a "documents/" key prefix keeps the two apart in a shared bucket) so a
# deployment doesn't need a second full credential set unless it wants one.

def _s3_client():
    bucket = os.getenv("DOCUMENTS_S3_BUCKET") or os.getenv("BACKUP_S3_BUCKET")
    if not bucket:
        return None, None
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("DOCUMENTS_S3_ENDPOINT_URL") or os.getenv("BACKUP_S3_ENDPOINT_URL") or None,
        aws_access_key_id=(os.getenv("DOCUMENTS_S3_ACCESS_KEY_ID")
                            or os.getenv("BACKUP_S3_ACCESS_KEY_ID") or None),
        aws_secret_access_key=(os.getenv("DOCUMENTS_S3_SECRET_ACCESS_KEY")
                                or os.getenv("BACKUP_S3_SECRET_ACCESS_KEY") or None),
        region_name=os.getenv("DOCUMENTS_S3_REGION") or os.getenv("BACKUP_S3_REGION") or None,
    )
    return client, bucket


class StorageNotConfigured(RuntimeError):
    pass


def upload_to_s3(key, content_bytes):
    client, bucket = _s3_client()
    if client is None:
        raise StorageNotConfigured("DOCUMENTS_S3_BUCKET (or BACKUP_S3_BUCKET) is not configured")
    client.put_object(Bucket=bucket, Key=key, Body=content_bytes)


def download_from_s3(key):
    client, bucket = _s3_client()
    if client is None:
        raise StorageNotConfigured("DOCUMENTS_S3_BUCKET (or BACKUP_S3_BUCKET) is not configured")
    obj = client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def storage_key(tenant_id, key_id, version, filename):
    """documents/{tenant}/{key_id}/{original|translated}{ext} — `key_id` is
    typically the row's content hash (known before the DB insert, for the
    original) or its id (for the translated result, written after DeepL
    hands back a document). Mirrors the uuid-based, path-traversal-safe
    convention `documents.storage_path` already uses, but as an S3 key
    rather than a filesystem path.
    """
    import pathlib
    ext = pathlib.Path(filename or "").suffix[:10]
    return f"documents/{tenant_id}/{key_id}/{version}{ext}"
