# Document storage — AWS S3 setup (CR-013 §2)

Everything a user uploads, plus Composer's generated outputs, lives in **one private S3
bucket**, with keys namespaced by tenant:

```
{tenant_id}/{area}/{pub_number or _}/{uuid}{ext}
area = tender_documents | vault | composer | composer_style | composer_output | translations
```

The application only ever touches objects under `arn:aws:s3:::<bucket>/*`. It never creates,
configures or deletes the bucket itself.

The client provisions the AWS side. Do not paste credentials into chat or commit them;
set them directly as Railway variables on the `backend` service.

---

## 1 · Create

| Resource | Setting |
|---|---|
| **Bucket** | Name e.g. `tenderizer-documents-prod`, region **`eu-central-1` (Frankfurt)** |
| Object Ownership | *Bucket owner enforced* (ACLs disabled) |
| Block Public Access | **All four on** |
| Versioning | **Enabled** |
| Default encryption | **SSE-KMS** with the customer-managed key below, **Bucket Key enabled** |
| **KMS key** | Symmetric, customer-managed, `eu-central-1`, alias e.g. `alias/tenderizer-documents`. Automatic yearly rotation on. Key policy: your admins administer; the app's IAM user is granted use via the IAM policy below |
| **IAM user** (or role) | e.g. `tenderizer-backend-documents`, programmatic access only, with only the policy below |

### Lifecycle rules (recommended)

- **Expire noncurrent versions after 90 days.** With versioning on, a user's delete only adds a
  delete marker; this rule makes the delete final after 90 days instead of never.
- **Abort incomplete multipart uploads after 7 days.**

### Bucket policy: refuse anything that isn't TLS

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": ["arn:aws:s3:::<bucket>", "arn:aws:s3:::<bucket>/*"],
    "Condition": { "Bool": { "aws:SecureTransport": "false" } }
  }]
}
```

### CORS (needed for direct browser upload/download)

```json
[{
  "AllowedOrigins": ["https://tender-izer.com"],
  "AllowedMethods": ["POST", "GET"],
  "AllowedHeaders": ["*"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3000
}]
```

Add `http://localhost:5173` only on a separate dev bucket, never on production.

---

## 2 · Minimum IAM policy for the application

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DocumentObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<bucket>/*"
    },
    {
      "Sid": "DistinguishMissingFromForbidden",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>"
    },
    {
      "Sid": "UseTheDocumentsKey",
      "Effect": "Allow",
      "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
      "Resource": "arn:aws:kms:eu-central-1:<account-id>:key/<key-id>"
    }
  ]
}
```

How this differs from the list in CR-013:

- **"Presign" is not an IAM action.** A presigned URL is signed locally with the user's
  credentials and carries exactly the `GetObject` or `PutObject` permission above. Nothing
  extra is needed.
- **`kms:GenerateDataKey` / `kms:Decrypt` are required** under SSE-KMS with a customer-managed
  key. Without them every upload and download fails with `AccessDenied`. They are scoped to
  that one key.
- **`s3:ListBucket` on the bucket ARN.** It lists key names only and grants no admin or
  configuration rights. Without it, S3 answers **403 instead of 404** for a missing key. The
  app relies on telling those apart during the cutover ("not in the bucket yet, read the
  local copy"), and the rollback command lists objects. `storage-check` verifies it.

No `s3:*`, no bucket-level configuration actions, no `kms:*`.

---

## 3 · Railway variables (backend service)

| Variable | Value |
|---|---|
| `DOCUMENTS_S3_BUCKET` | bucket name. **Setting this switches the app to S3**, so set it last (step 4 of the cutover) |
| `DOCUMENTS_S3_REGION` | `eu-central-1` |
| `AWS_ACCESS_KEY_ID` | the IAM user's access key |
| `AWS_SECRET_ACCESS_KEY` | its secret |
| `DOCUMENTS_S3_ENDPOINT_URL` | leave unset for AWS. Only for an S3-compatible alternative (Scaleway, Hetzner) |

When `DOCUMENTS_S3_BUCKET` is unset, the app stores the same keys on the backend's persistent
volume under `/app/data/objects/`. This is how dev, the test suite and rollback run.

**Frontend (Vercel):** the frontend can be deployed before or after the backend. The backend
still accepts multipart uploads in both modes, so old and new frontends both work. With the
new frontend and the bucket set, files go browser → S3 directly.

---

## Pre-cutover state (CR-013 §9 Q1–Q2, recorded 2026-11-09)

**Q1 — Persistent volume, and was anything lost?** Yes, `backend` has a persistent volume
(`backend-volume`, about 184 MB, created 2026-10-17; confirmed in the Railway dashboard).
**Nothing was lost.** Production row counts on 2026-11-09 (`railway connect postgres`):

| Table | Rows |
|---|---|
| `documents` | 0 |
| `vault_documents` | 0 |
| `composer_documents` | 0 |
| `composer_matrix` | 0 |
| `composer_style_examples` | 0 |
| `translated_documents` | 4 |

No Vault or Composer document had been uploaded to production before the volume existed, so
there was nothing to lose.

**Q2 — What needs migrating?** The files behind the 4 `translated_documents` rows (originals
and results, under legacy `documents/{tenant}/…` keys). That is the whole migration scope.

## 4 · Verify, then cut over

Every step below needs explicit approval first (CR-013 §0).

1. **Deploy the CR-013 code** with the bucket variable unset. `alembic upgrade head` runs on
   start and adds the nullable `object_key` columns. Nothing else changes.
2. **Credential check.** From a shell with the four variables set (locally, or
   `railway ssh` with them inline):
   ```
   python src/cli.py storage-check
   ```
   It runs put, head (reports SSE-KMS key and versioning), a presigned GET fetched over
   HTTPS, get, missing-key → 404, and delete. Every line must read `OK`.
3. **Copy.** Run inside the backend container, because the files are on its volume. Pass the
   variables inline for that one command so the live app keeps running in filesystem mode:
   ```
   DOCUMENTS_S3_BUCKET=… DOCUMENTS_S3_REGION=eu-central-1 AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=… \
     python src/cli.py storage-migrate          # dry run: counts only
     python src/cli.py storage-migrate --yes    # copy + verify (SHA-256 read-back) + key rows
   ```
   This never deletes a local file. Rows whose file is already gone are listed as `MISSING`.
4. **Switch.** Set the variables on the `backend` service (this redeploys). Anything uploaded
   between steps 3 and 4 is still on the volume and is served from there automatically.
5. **Sweep.** Run `storage-migrate --yes` once more (inside the container, now without the
   inline variables) to copy those last files.

**Rollback:** inside the container, run `python src/cli.py storage-restore-local --yes`
(downloads anything that exists only in the bucket), then unset `DOCUMENTS_S3_BUCKET`.
Legacy `storage_path` values are kept, so pre-CR-013 rows keep working either way.

Once the bucket has run cleanly for a while, the old `data/uploads`, `data/vault_uploads`,
`data/composer_*` directories can be removed from the volume. That is a separate,
approval-gated step.

---

## 5 · Open decision: compute region

The backend runs in Railway **`sfo`** (US). An EU bucket with US compute means every document
still transits and is processed in the US, which a procurement security review will notice.
Moving `backend` (and `postgres`) to **`europe-west4` (Amsterdam)** is a separate decision for
the client. Nothing in this change depends on it.
