"""CR-013 §2 — durable object storage for every uploaded / generated document.

One private S3 bucket (target: AWS S3, eu-central-1), keys namespaced by
tenant:

    {tenant_id}/{area}/{pub_number or "_"}/{uuid}{ext}

`area` is one of AREAS. Tenant-wide areas (vault, composer_style,
translations) use "_" in the pub_number slot so every key has the same
shape and the tenant prefix is always the first path segment — which is
what `key_belongs_to` checks before any presign or confirm.

Configuration (same inert-when-unset convention as backup.py):

    DOCUMENTS_S3_BUCKET        unset -> filesystem mode (dev, tests, rollback)
    DOCUMENTS_S3_REGION        e.g. eu-central-1
    DOCUMENTS_S3_ENDPOINT_URL  optional, only for a non-AWS S3-compatible store
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  standard boto3 credential chain

Encryption at rest is the bucket's default encryption (SSE-KMS with the
client's customer-managed key) — the application never names the key, so
rotating or replacing it is a bucket setting, not a code change. See
docs/DOCUMENT_STORAGE_SETUP.md.

Filesystem mode stores the same keys under data/objects/ (on the backend's
persistent volume in production). Legacy rows from before CR-013 still
carry an absolute `storage_path` and no `object_key`; the row_* helpers
read either, so nothing breaks before `cli storage-migrate` has run.

Cutover safety: in S3 mode a key missing from the bucket falls back to the
same key on local disk, so uploads made between the copy and the switch
stay readable until the copy is re-run.
"""
import contextlib
import functools
import os
import pathlib
import re
import shutil
import tempfile
import uuid
from urllib.parse import quote

AREAS = frozenset({
    "tender_documents",   # CR-002 E documents slice; manual-entry PDFs (CR-013 §5)
    "vault",              # Vault library
    "composer",           # Composer inputs (SOW/tech/... and the compliance matrix)
    "composer_style",     # Composer style-guide example proposals
    "composer_output",    # generated proposal / filled matrix / gaps report
    "translations",       # Scout document translation originals + results
    "manual_drafts",      # CR-013 §5: PDFs of a manual-entry draft, before it has a pub_number
})
TENANT_WIDE_AREAS = frozenset({"vault", "composer_style", "translations", "manual_drafts"})

# 100 MB per file. Tender document packs (drawings, scanned annexes) routinely
# exceed the old 20 MB "minimal-slice sanity cap"; 100 MB covers a full
# scanned pack while keeping a single upload well inside the 30-minute
# presigned-POST window below on a slow line.
MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# Downloads: short, the browser fetches immediately. Uploads: 30 minutes, so
# the signed POST outlives a full 100 MB transfer on an ordinary office or
# mobile uplink (100 MB at ~1 Mbit/s is ~14 minutes), with margin for retries.
DOWNLOAD_URL_TTL_SECONDS = 300
UPLOAD_URL_TTL_SECONDS = 1800

_DATA_ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
LOCAL_ROOT = _DATA_ROOT / "objects"
# translate_document's pre-CR-013 keys ("documents/{tenant}/...") were stored
# directly under data/ — still resolvable until storage-migrate renames them.
LEGACY_ROOT = _DATA_ROOT
_LEGACY_KEY_PREFIX = "documents/"


class ObjectNotFound(FileNotFoundError):
    pass


# ── configuration ────────────────────────────────────────────────────────────

def bucket():
    return os.getenv("DOCUMENTS_S3_BUCKET") or None


def configured():
    return bucket() is not None


def backend_name():
    return "s3" if configured() else "filesystem"


@functools.lru_cache(maxsize=4)
def _cached_client(region, endpoint_url):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", region_name=region, endpoint_url=endpoint_url,
        # SigV4 is mandatory for SSE-KMS objects and for presigned URLs in
        # every post-2014 region (eu-central-1 included).
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _client():
    return _cached_client(os.getenv("DOCUMENTS_S3_REGION") or None,
                          os.getenv("DOCUMENTS_S3_ENDPOINT_URL") or None)


# ── keys ─────────────────────────────────────────────────────────────────────

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_segment(value):
    """Path-segment-safe form of a pub_number (some portals use '/' or
    spaces in theirs). Never empty, never '.'/'..'."""
    s = _SAFE.sub("_", str(value or "")).strip(".") or "_"
    return s[:120]


def _prefix(tenant_id, area, pub_number=None):
    if area not in AREAS:
        raise ValueError(f"unknown storage area {area!r}")
    pub = "_" if (area in TENANT_WIDE_AREAS or not pub_number) else safe_segment(pub_number)
    return f"{int(tenant_id)}/{area}/{pub}/"


def new_key(tenant_id, area, filename, pub_number=None):
    """Server-generated key; the user's filename only contributes a short
    extension, never a path component."""
    ext = pathlib.PurePath(filename or "").suffix[:10]
    ext = _SAFE.sub("", ext)
    return f"{_prefix(tenant_id, area, pub_number)}{uuid.uuid4().hex}{ext}"


def fixed_key(tenant_id, area, pub_number, name):
    """Deterministic key for generated outputs (overwritten on regenerate;
    bucket versioning keeps the history)."""
    return f"{_prefix(tenant_id, area, pub_number)}{safe_segment(name)}"


def key_belongs_to(key, tenant_id, area=None, pub_number=None):
    if not isinstance(key, str) or ".." in key.split("/"):
        return False
    if area is None:
        return key.startswith(f"{int(tenant_id)}/")
    return key.startswith(_prefix(tenant_id, area, pub_number))


def local_path(key):
    root = (LEGACY_ROOT if key.startswith(_LEGACY_KEY_PREFIX) else LOCAL_ROOT).resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError(f"storage key escapes the storage root: {key!r}")
    return path


def content_disposition(filename):
    """RFC 6266/5987 attachment header that survives any filename (ASCII
    fallback + UTF-8 `filename*`)."""
    filename = filename or "download"
    fallback = "".join(c for c in filename.encode("ascii", "replace").decode("ascii")
                       if c.isprintable() and c not in '"\\') or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ── primitive operations ────────────────────────────────────────────────────

def _is_missing(err):
    code = getattr(err, "response", {}).get("Error", {}).get("Code")
    return code in ("NoSuchKey", "404", "NotFound")


def put(key, data, content_type=None):
    if not configured():
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    extra = {"ContentType": content_type} if content_type else {}
    _client().put_object(Bucket=bucket(), Key=key, Body=data, **extra)


def put_file(key, src_path, content_type=None):
    if not configured():
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if pathlib.Path(src_path).resolve() != path:
            shutil.copyfile(src_path, path)
        return
    extra = {"ExtraArgs": {"ContentType": content_type}} if content_type else {}
    _client().upload_file(str(src_path), bucket(), key, **extra)


def _remote_exists(key):
    from botocore.exceptions import ClientError
    try:
        _client().head_object(Bucket=bucket(), Key=key)
        return True
    except ClientError as e:
        if _is_missing(e):
            return False
        raise


def get(key):
    if configured():
        from botocore.exceptions import ClientError
        try:
            return _client().get_object(Bucket=bucket(), Key=key)["Body"].read()
        except ClientError as e:
            if not _is_missing(e):
                raise
    path = local_path(key)
    if not path.is_file():
        raise ObjectNotFound(key)
    return path.read_bytes()


def exists(key):
    if configured() and _remote_exists(key):
        return True
    return local_path(key).is_file()


def size(key):
    """Byte size, or None if the object doesn't exist."""
    if configured():
        from botocore.exceptions import ClientError
        try:
            return _client().head_object(Bucket=bucket(), Key=key)["ContentLength"]
        except ClientError as e:
            if not _is_missing(e):
                raise
    path = local_path(key)
    return path.stat().st_size if path.is_file() else None


def delete(key):
    """Idempotent. In S3 mode also clears any local cutover copy. With
    versioning on, S3 keeps the prior version until the lifecycle rule
    expires it."""
    if configured():
        _client().delete_object(Bucket=bucket(), Key=key)
    local_path(key).unlink(missing_ok=True)


def presigned_get(key, filename=None, content_type=None, ttl=DOWNLOAD_URL_TTL_SECONDS):
    """Short-lived download URL, or None in filesystem mode / when the
    object only exists on local disk (the caller then streams it)."""
    if not configured() or not _remote_exists(key):
        return None
    params = {"Bucket": bucket(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = content_disposition(filename)
    if content_type:
        params["ResponseContentType"] = content_type
    return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)


def presigned_post(key, content_type=None, max_size=MAX_UPLOAD_SIZE, ttl=UPLOAD_URL_TTL_SECONDS):
    """Direct browser-to-bucket upload. A presigned POST (not PUT) so the
    size limit is enforced by S3 itself via content-length-range. None in
    filesystem mode — the caller then uploads through the API as before."""
    if not configured():
        return None
    fields, conditions = {}, [["content-length-range", 1, int(max_size)]]
    if content_type:
        fields["Content-Type"] = content_type
        conditions.append({"Content-Type": content_type})
    return _client().generate_presigned_post(
        bucket(), key, Fields=fields, Conditions=conditions, ExpiresIn=ttl)


@contextlib.contextmanager
def local_copy(key, suffix=None):
    """Yields a filesystem path holding the object's bytes, for parsers that
    need a real file (PyMuPDF, openpyxl, python-docx). Filesystem mode
    yields the stored file itself; S3 mode a temp file, removed on exit."""
    if not configured():
        path = local_path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        yield str(path)
        return
    data = get(key)
    suffix = suffix if suffix is not None else pathlib.PurePath(key).suffix
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        yield tmp
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


# ── row helpers: object_key (CR-013) or legacy storage_path ─────────────────

def _row_key(row):
    return row.get("object_key") or None


def _legacy_path(row):
    p = row.get("storage_path") or ""
    return p if p and os.path.isfile(p) else None


def row_exists(row):
    key = _row_key(row)
    if key and exists(key):
        return True
    return _legacy_path(row) is not None


def row_read(row):
    key = _row_key(row)
    if key:
        try:
            return get(key)
        except ObjectNotFound:
            pass
    legacy = _legacy_path(row)
    if legacy is None:
        raise ObjectNotFound(key or row.get("storage_path") or "")
    with open(legacy, "rb") as f:
        return f.read()


@contextlib.contextmanager
def row_local_copy(row):
    key = _row_key(row)
    if key and exists(key):
        with local_copy(key) as path:
            yield path
        return
    legacy = _legacy_path(row)
    if legacy is None:
        raise ObjectNotFound(key or row.get("storage_path") or "")
    yield legacy


def row_delete(row):
    key = _row_key(row)
    if key:
        delete(key)
    legacy = _legacy_path(row)
    if legacy:
        with contextlib.suppress(OSError):
            os.remove(legacy)


def row_presigned_get(row, filename=None, content_type=None):
    key = _row_key(row)
    return presigned_get(key, filename, content_type) if key else None


def row_local_file(row):
    """A real on-disk file for the row when one exists locally (filesystem
    mode, or a not-yet-migrated legacy row) — lets downloads keep using
    FileResponse without reading the whole file into memory."""
    key = _row_key(row)
    if key:
        if configured() and _remote_exists(key):
            return None
        path = local_path(key)
        if path.is_file():
            return str(path)
    return _legacy_path(row)
