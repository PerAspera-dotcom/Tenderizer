"""CR-013 §2 — operator tasks for the object-storage cutover.

    tenderizer storage-check            verify bucket credentials end to end
    tenderizer storage-migrate          copy everything on disk into the bucket
    tenderizer storage-restore-local    reverse: copy bucket objects back to disk

All three need DOCUMENTS_S3_BUCKET (+ region/credentials) in the environment
of the process running them. storage-migrate and storage-restore-local must
run *inside* the backend container (railway ssh), because the files live on
its volume — pass the bucket variable inline for that one command so the
live app keeps running in filesystem mode until the switch.

Cutover sequence (each step needs explicit approval — CR-013 §0):
  1. Deploy this code. Bucket unset -> nothing changes; new uploads get keys
     under data/objects/ on the volume.
  2. Client provisions the bucket/IAM/KMS key (docs/DOCUMENT_STORAGE_SETUP.md);
     run storage-check with the variables set.
  3. storage-migrate --dry-run, then storage-migrate. Copies every file,
     verifies each by SHA-256 read-back, assigns object_key to legacy rows,
     renames legacy translation keys. Never deletes anything local.
  4. Set DOCUMENTS_S3_BUCKET etc. on the backend service (redeploy).
     Anything still only on disk stays readable via object_storage's local
     fallback.
  5. storage-migrate again to sweep uploads made between 3 and 4.
  Rollback: storage-restore-local, then unset DOCUMENTS_S3_BUCKET.

Idempotent throughout: re-running skips whatever is already in place.
"""
import hashlib
import os
import pathlib
import uuid
from collections import Counter

from sqlalchemy import select, update

import object_storage
from schema import (composer_documents, composer_matrix, composer_style_examples, documents,
                    translated_documents, vault_documents)

_DATA_ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
COMPOSER_OUTPUT_DIR = _DATA_ROOT / "composer_output"

# (table, area, has pub_number)
_FILE_TABLES = (
    (documents, "tender_documents", True),
    (vault_documents, "vault", False),
    (composer_documents, "composer", True),
    (composer_matrix, "composer", True),
    (composer_style_examples, "composer_style", False),
)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_verified(key, src_path):
    """Upload and confirm by reading the object back. ETags can't be used
    for this: under SSE-KMS they are not the content MD5."""
    object_storage.put_file(key, src_path)
    if hashlib.sha256(object_storage.get(key)).hexdigest() != _sha256_file(src_path):
        raise RuntimeError(f"checksum mismatch after upload: {key}")


def _in_bucket(key):
    return object_storage._remote_exists(key)


def _require_bucket():
    if not object_storage.configured():
        raise RuntimeError("DOCUMENTS_S3_BUCKET is not set — nothing to migrate to")


def migrate(engine, dry_run=True, log=print):
    """Returns a Counter of outcomes plus a list of rows whose file is gone
    (row present, file absent on disk and not in the bucket)."""
    _require_bucket()
    stats, missing = Counter(), []

    for table, area, has_pub in _FILE_TABLES:
        name = table.name
        cols = [table.c.id, table.c.tenant_id, table.c.storage_path, table.c.object_key, table.c.filename]
        if has_pub:
            cols.append(table.c.pub_number)
        with engine.connect() as c:
            rows = c.execute(select(*cols)).fetchall()
        for r in rows:
            row_id, tenant_id, storage_path, key, filename = r[0], r[1], r[2], r[3], r[4]
            pub = r[5] if has_pub else None
            if key:
                if _in_bucket(key):
                    stats[f"{name}:already_in_bucket"] += 1
                    continue
                src = object_storage.local_path(key)
                if not src.is_file() and storage_path and os.path.isfile(storage_path):
                    src = pathlib.Path(storage_path)
                if not src.is_file():
                    stats[f"{name}:missing"] += 1
                    missing.append((name, row_id, key))
                    continue
                if not dry_run:
                    _copy_verified(key, src)
                stats[f"{name}:copied"] += 1
                continue
            if not storage_path or not os.path.isfile(storage_path):
                stats[f"{name}:missing"] += 1
                missing.append((name, row_id, storage_path))
                continue
            new_key = object_storage.new_key(tenant_id, area, filename or storage_path, pub_number=pub)
            if not dry_run:
                _copy_verified(new_key, storage_path)
                with engine.begin() as c:
                    c.execute(update(table).where(table.c.id == row_id).values(object_key=new_key))
            stats[f"{name}:copied_and_keyed"] += 1

    # composer_matrix.filled_path: legacy absolute path -> fixed output key
    with engine.connect() as c:
        rows = c.execute(select(composer_matrix.c.id, composer_matrix.c.tenant_id,
                                composer_matrix.c.pub_number, composer_matrix.c.filled_path)).fetchall()
    for row_id, tenant_id, pub, filled in rows:
        if not filled or not os.path.isabs(filled):
            continue
        if not os.path.isfile(filled):
            stats["composer_matrix.filled:missing"] += 1
            missing.append(("composer_matrix.filled_path", row_id, filled))
            continue
        key = object_storage.fixed_key(tenant_id, "composer_output", pub, "matrix_filled.xlsx")
        if not dry_run:
            _copy_verified(key, filled)
            with engine.begin() as c:
                c.execute(update(composer_matrix).where(composer_matrix.c.id == row_id).values(filled_path=key))
        stats["composer_matrix.filled:copied_and_keyed"] += 1

    # Generated outputs on disk: data/composer_output/{tenant}/{pub}/{name}
    if COMPOSER_OUTPUT_DIR.is_dir():
        for path in COMPOSER_OUTPUT_DIR.glob("*/*/*"):
            if not path.is_file() or not path.parts[-3].isdigit():
                continue
            key = object_storage.fixed_key(int(path.parts[-3]), "composer_output", path.parts[-2], path.name)
            if _in_bucket(key):
                stats["composer_output:already_in_bucket"] += 1
                continue
            if not dry_run:
                _copy_verified(key, path)
            stats["composer_output:copied"] += 1

    # translated_documents: rename legacy "documents/{t}/{id}/{version}{ext}" keys
    with engine.connect() as c:
        rows = c.execute(select(translated_documents.c.id, translated_documents.c.tenant_id,
                                translated_documents.c.original_storage_key,
                                translated_documents.c.translated_storage_key)).fetchall()
    for row_id, tenant_id, original, translated in rows:
        for column, key in (("original_storage_key", original), ("translated_storage_key", translated)):
            if not key:
                continue
            target = key
            if key.startswith("documents/"):
                parts = key.split("/")  # documents, tenant, key_id, "{version}{ext}"
                version_ext = parts[-1]
                target = f"{int(tenant_id)}/translations/_/{parts[2]}-{version_ext}"
            if _in_bucket(target):
                stats["translated_documents:already_in_bucket"] += 1
            else:
                src = object_storage.local_path(key)
                if not src.is_file():
                    stats["translated_documents:missing"] += 1
                    missing.append(("translated_documents." + column, row_id, key))
                    continue
                if not dry_run:
                    _copy_verified(target, src)
                stats["translated_documents:copied"] += 1
            if target != key and not dry_run:
                with engine.begin() as c:
                    c.execute(update(translated_documents).where(translated_documents.c.id == row_id)
                              .values(**{column: target}))
                # The old file stays where it is; also mirror it at the new
                # key's local path so filesystem-mode rollback finds it.
                dst = object_storage.local_path(target)
                if not dst.exists() and object_storage.local_path(key).is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(object_storage.local_path(key).read_bytes())

    for name, row_id, ref in missing:
        log(f"MISSING  {name} id={row_id}  {ref}")
    return stats, missing


def restore_local(dry_run=True, log=print):
    """Rollback: every bucket object without a local copy is downloaded to
    its data/objects/ path, so the app can run in filesystem mode again."""
    _require_bucket()
    client, bucket = object_storage._client(), object_storage.bucket()
    stats = Counter()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.startswith("_healthcheck/"):
                continue
            dst = object_storage.local_path(key)
            if dst.is_file() and dst.stat().st_size == obj["Size"]:
                stats["already_local"] += 1
                continue
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(client.get_object(Bucket=bucket, Key=key)["Body"].read())
            stats["downloaded"] += 1
    return stats


def check(log=print):
    """End-to-end credential check against the configured bucket: put, head,
    presigned GET fetched over HTTPS, get, delete. Reports whether the
    object came back SSE-KMS encrypted and versioned. Returns True on success.
    """
    import requests

    if not object_storage.configured():
        log("FAIL  DOCUMENTS_S3_BUCKET is not set")
        return False
    client, bucket = object_storage._client(), object_storage.bucket()
    key = f"_healthcheck/{uuid.uuid4().hex}.txt"
    payload = b"tenderizer storage-check"
    log(f"bucket={bucket} region={os.getenv('DOCUMENTS_S3_REGION') or '(default)'}")
    ok = True
    try:
        client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="text/plain")
        log("OK    PutObject")
        head = client.head_object(Bucket=bucket, Key=key)
        sse = head.get("ServerSideEncryption")
        log(f"OK    HeadObject  encryption={sse or 'none'}"
            f"{' key=' + head['SSEKMSKeyId'] if head.get('SSEKMSKeyId') else ''}"
            f"  versioned={'yes' if head.get('VersionId') not in (None, 'null') else 'NO'}")
        if sse != "aws:kms":
            log("WARN  bucket default encryption is not SSE-KMS")
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                            ExpiresIn=60)
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and r.content == payload:
            log("OK    presigned GET (signature + kms:Decrypt)")
        else:
            ok = False
            log(f"FAIL  presigned GET returned HTTP {r.status_code}")
        if client.get_object(Bucket=bucket, Key=key)["Body"].read() == payload:
            log("OK    GetObject")
        else:
            ok = False
            log("FAIL  GetObject returned different bytes")
        # Without s3:ListBucket, S3 answers 403 (not 404) for a missing key,
        # which breaks the cutover fallback to local copies.
        from botocore.exceptions import ClientError
        try:
            client.head_object(Bucket=bucket, Key=f"_healthcheck/{uuid.uuid4().hex}-absent")
            ok = False
            log("FAIL  HeadObject on a missing key succeeded?")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                log("OK    missing key -> 404 (s3:ListBucket granted)")
            else:
                ok = False
                log(f"FAIL  missing key -> {code}; grant s3:ListBucket on the bucket ARN")
    except Exception as e:
        ok = False
        log(f"FAIL  {type(e).__name__}: {e}")
    finally:
        try:
            client.delete_object(Bucket=bucket, Key=key)
            log("OK    DeleteObject")
        except Exception as e:
            ok = False
            log(f"FAIL  DeleteObject: {type(e).__name__}: {e}")
    log("storage-check: " + ("PASSED" if ok else "FAILED"))
    return ok
