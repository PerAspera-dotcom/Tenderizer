# CR-014 — Findings (investigation pass, 2026-11-09)

Investigation only. Nothing has been rotated, rewritten, migrated, deployed or committed.
Production was not read: `railway ssh` is blocked by the session's permission gate, and the
items that need production or the Railway dashboard are marked **NEEDS CLIENT**.

---

## §2 · Credential exposure — CLOSED (amendment to CR-014)

> **Status, 2026-11-09: closed.** The client verified independently: both commits contain
> permission rules only; a diff scan across all refs and `detect-secrets` over the full
> history returned zero findings. The CR's §2 premise was an error carried forward from a
> September report without verification. This file is the amendment of record; CR-014 is not
> reissued.
>
> - **No history rewrite.** None will be proposed.
> - **Rotation:** optional hygiene, not remediation — parked.
> - **Local `.claude/settings.local.json`:** correct as it is; leave it alone, keep it gitignored.
> - **Repository visibility:** still an open question for the client, independent of the above.
>
> The investigation below is kept as the evidence for that decision.

### Finding: the two production credentials are not in the git history

This contradicts the premise of §2 (and the CR-013 Stage 0 report it came from). The check:

| Check | Result |
|---|---|
| `.claude/settings.local.json` at `456abd2` (29 lines) and `c630112` (95 lines) | Permission-allowlist entries only (paths, localhost URLs, pytest/alembic commands). No Railway token, no Postgres password, no `PGPASSWORD`, no `postgres://` URL. |
| Commits touching that path, all refs | Exactly three: `456abd2`, `c630112`, `38ff4a7` (the untrack). `38ff4a7` removes the `c630112` blob, so no later version was ever committed. |
| Current working-tree copy (untracked, gitignored by `.gitignore:11`) | Contains 2 credential-shaped lines (counted, not printed). These were added locally *after* tracking stopped and have never been committed. |
| Remote coverage | `git ls-remote origin`: `master`, `cr-002-classification-workflow`, `pull/1/head`. Every remote commit is present locally, so the local scan covers everything GitHub serves. |
| Repository visibility | `PerAspera-dotcom/Tenderizer` — **PUBLIC**, since 2026-07-06. |

### Full-history scan (§7 Q1)

`gitleaks` / `trufflehog` could not be run: Docker Desktop is not running, and downloading
the gitleaks release binary was blocked by the session's permission gate. In their place a
pattern scan over every line ever added on every ref (`git log --all -p`, 155 commits) for:
Anthropic, OpenAI-style, AWS, Clerk secret, SendGrid, GitHub, Stripe-live, DeepL keys; Sentry
DSNs; JWTs; private-key blocks; Postgres URLs with a password; Railway token assignments;
generic `*_KEY|SECRET|TOKEN|PASSWORD|DSN = <opaque value>` assignments.

| Commit | File | What matched | Verdict |
|---|---|---|---|
| `970556d`, `456abd2` (`tenderizer_handoff/SETUP.md`), `5e7b448`, `94a3444` (`tests/test_19_db.py`) | `.env.example`, docs, tests | `postgresql://…@localhost` with 1- and 3-character passwords | Local dev/test URLs. Not a production credential. |
| `2b72403`, `c2aa009` | `DEPLOYMENT.md` | `Authorization: Bearer $RAILWAY_TOKEN` | Shell variable reference, no value. |
| `3d95781`, `ee958ae` | `frontend/src/main.tsx` | Clerk publishable key / Sentry DSN | `import.meta.env.VITE_*` references, no value. |

**No secret values found.** No Anthropic key anywhere in this repository's history. Files
ever added that look sensitive by name: only `.env.example` and `frontend/src/authToken.ts`
(code, no secret); `.env` has never been committed.

Confidence: this is a regex pass, not gitleaks' ruleset with entropy checks. **Run gitleaks
before treating §2 as closed** — either start Docker Desktop and approve
`docker run zricethezav/gitleaks … git /repo --redact --log-opts=--all`, or install gitleaks
yourself and run `gitleaks git . --redact --log-opts="--all"` from `the_scout/`.

The Anthropic key the CR mentions was handled loosely "elsewhere in this project". The
parent `Tenderizer/` folder is not a git repository, so nothing there is published, but its
handoff/brief files were not scanned.

### Consequences for the order of work

1. **Rotation — still recommended, but no longer an emergency.** The live values sit in a
   plaintext local file that coding-agent sessions read routinely, which is reason enough to
   rotate and to stop keeping them there. Checklist below.
2. **History rewrite — not recommended**, if gitleaks agrees with the scan above. Rewriting a
   published history to remove nothing sensitive is pure risk (every clone and the open
   `pull/1` diverge). Proposal held back; I will draft the exact `git filter-repo` command only
   if gitleaks finds something.
3. **Repository visibility — still worth deciding** (below).

### Rotation checklist (client actions — not executed, no values requested)

**Railway API token**
1. Railway → Account settings → Tokens: create a new token; revoke the old one *after* step 3.
2. Replace it wherever it is used: the local `.claude/settings.local.json` permission entries
   (better: remove them — the token does not belong in an allowlist), any shell profile /
   `.env` exporting `RAILWAY_TOKEN`, any CI secret.
3. The `railway` CLI session uses its own login (`railway login`), not this token; re-run
   `railway login` only if `railway whoami` fails afterwards.
4. Revoke the old token.

**Postgres password**
1. Railway → `postgres` service: rotate the password (regenerating the credential updates the
   service's `PGPASSWORD` / `DATABASE_URL` variables).
2. `backend` must pick up the new `DATABASE_URL`. If its variable is a reference
   (`${{Postgres.DATABASE_URL}}`) it follows automatically on its next deploy; if it is a
   pasted literal, update it by hand. **Check which before rotating.**
3. Update any local `.env` / `.claude/settings.local.json` entries that embed the old password.

**Expected interruption (§7 Q4) — estimate, not measured:** from the moment the password
changes until `backend` has redeployed with the new value, new database connections fail.
Pooled connections opened before the change usually survive until recycled, so the effect is
typically a burst of 5xx on requests that need a fresh connection, plus whatever the redeploy
takes (normally 1–3 minutes for this image). The 03:00 scrape/backup jobs would fail if they
ran inside that window — rotate during working hours, then trigger a redeploy at once.

### Visibility — for the client to decide

| Keep public | Make private |
|---|---|
| No change; free CI/Actions minutes; easy to share | Removes a whole class of disclosure: a future slip leaks to collaborators, not the internet |
| Code itself contains no secrets (per this scan) | Procurement-security reviewers will ask; "public repo" is a hard answer to give for defence-adjacent data |
| | Already-published history stays cached by forks/scrapers; going private does not undo past exposure |

Recommendation: private, because what it protects against (the next slip) is worth more than
what it costs. The decision is yours.

---

## §3 · Backups

### Verdict (client, 2026-11-09)

Railway volume backups are necessary but not sufficient. Keep them for fast self-service
recovery **and** configure `src/backup.py` to send a logical dump off-platform: to a bucket
that is not the CR-013 documents bucket and ideally not in the same cloud account.
Retention is **not** fixed in any proposal yet; the client is asking what is required
(tender/proposal records may need years, possibly by contract). The client is checking the
Backups tab.

### What `src/backup.py` actually does (verified by reading it)

**It is a real logical dump, not a SQLite-era file copy.** Written 2026-07-21 (`3040a1d`,
CR-004 F4), after the Postgres cutover (`970556d`, 2026-07-04). It branches on
`db.database_url()`:

- **Postgres (production):** `pg_dump --format=custom --file=data/backups/tenders_<stamp>.dump
  <DATABASE_URL>`. Portable: restores into any Postgres of the same or newer major with
  `pg_restore`.
- **SQLite (dev):** sqlite3's online backup API (`.backup()`), not a raw file copy.

Daily at 03:00 (`src/api.py:97`, only when `ENABLE_SCHEDULER` is not `false`):
`create_dump` → `upload` (to `BACKUP_S3_*` if set, else a warning and the file stays local) →
`cleanup_old_backups` (local *and remote*, by age, `BACKUP_RETENTION_DAYS`, default 30) → append
to `data/backups/backup_log.json`. Failures go to `alerts.send_alert` (Sentry + email, each a
no-op if unconfigured) and never raise.

Restore (`cli restore <date> --yes`): finds the dump locally then remotely, refuses an empty
file, runs `pg_restore --clean --if-exists` **into the live `DATABASE_URL`**.

**So the existing backups are misplaced, not worthless — if they are succeeding at all.**
Whether they are is unknown:

| Gap | Effect |
|---|---|
| **The Postgres path has never been tested.** `tests/test_31_backup.py` covers only SQLite; nothing exercises `pg_dump` or `pg_restore`. | No evidence the production dump or restore works. |
| `postgresql-client` from Debian bookworm is **v15**; `pg_dump` refuses a newer server. | If prod Postgres is ≥ 16, every nightly run has failed since July. |
| Evidence would be in `data/backups/backup_log.json` and `tenders_*.dump` on the backend volume, and in 03:00 logs. | NEEDS CLIENT (production read). |

Design points for the proposal, recorded now so they are not lost:

- **The app deletes its own remote backups** (`cleanup_old_backups`). An app with delete rights
  on the off-platform bucket undoes the point of it: whoever compromises the app or the
  project can erase the backups too. Retention belongs in bucket lifecycle/Object Lock; the
  app's credentials should be write-only.
- `list_objects_v2` is not paginated: listing and cleanup see only the first 1,000 dumps (~2.7
  years of dailies). That matters if retention turns out to be years.
- Restore writes straight over the live database. A restore drill wants a
  `--target <url>` option to restore into a scratch Postgres instead.
- The dump covers the database only. Documents are covered separately (CR-013 bucket,
  versioned).
- `DATABASE_URL`, including the password, is passed on the `pg_dump` command line, so it is
  visible in the process list inside the container. Minor; `PGPASSWORD`/a passfile avoids it.

### What the code does (summary)

- No `BACKUP_S3_*` in production (per CR-013 Stage 0), so every dump that succeeds shares the
  volume with the data.

> **Correction (2026-11-09, CR-014 parked):** the paragraph below is wrong on its premise.
> The unpinned `python:3.12-slim` tag has resolved to **Debian trixie** since Debian 13's
> release (Aug 2025), so every build of this image since July 2026 installs trixie's
> `postgresql-client` **17**, which can dump a Postgres 16 server. The version-mismatch
> failure described below does not apply. Unverified: `pg_dump --version` in the running
> container, and whether nightly dumps succeed for other reasons.

### A further risk, unverified (superseded — see correction above)

The image is `python:3.12-slim` (Debian bookworm), whose `postgresql-client` is **v15**.
`pg_dump` refuses to dump a server newer than itself. If Railway's Postgres is 16 or 17,
**every nightly backup has been failing** with `server version mismatch`, and the failure
only reaches Sentry/logs. NEEDS CLIENT: check the Postgres version (dashboard, or
`select version();` in `railway connect postgres`) and the backend logs around 03:00 for
`pg_dump failed` or `backup stayed local only`.

### What Railway's managed backups provide (§7 Q2, platform side)

Source: Railway docs, [Volumes → Backups](https://docs.railway.com/volumes/backups), read
2026-11-09. They are **volume backups**: the Backups tab sits on any service with a volume, so
here that means the `postgres` service's volume (and separately `backend-volume`).

| Property | What the docs say |
|---|---|
| Mechanism | Incremental, copy-on-write snapshots of the volume. Not a logical (`pg_dump`) dump. |
| Schedules | Daily (kept **6 days**), Weekly (kept **27 days**), Monthly (kept **89 days**); several can be combined. Manual backups too, capped at 50% of volume size. |
| Enabling | Per service, Backups tab. Not stated to be on by default. |
| Restore | Self-service: pick a backup → Restore. Creates a **new volume** mounted at the same path; the old volume stays, unmounted; applied with Deploy. |
| Restore target | **Only the same project + environment.** No restore into a scratch project, no download. |
| Fate-sharing | **"Wiping a volume deletes all backups."** Behaviour on volume/project deletion not documented. |
| Point-in-time | Not offered in the docs (a Railway guide mentions it; the docs do not). |
| Cost | Billed like volume storage, only for data unique to each backup. |

**What this means against §3's criterion.** Railway's backups protect against *logical*
damage — a bad migration, an accidental `DELETE`, a corrupted table — with a self-service
restore. They do **not** fully break shared fate: they live on the same platform, inside the
same project, tied to the volume (a wipe takes them with it), and they cannot be restored
anywhere else, which also means a restore drill can only be run against production's own
environment. An account compromise, a project deletion, or a Railway-side incident takes data
and backups together.

### Still NEEDS CLIENT (§7 Q2 project side, Q5)

From the dashboard, `postgres` service → **Backups** tab:
- Which schedules are enabled (none / daily / weekly / monthly)? Any backups listed, and the
  oldest date?
- Same question for `backend-volume` (documents, until CR-013's cutover).
- Has anyone ever pressed Restore, here or in another environment?
- Postgres server version (`select version();`) — decides whether the nightly `pg_dump` has
  been failing (above).

No design is proposed until these are answered (CR-014 §3: investigate first). Until a
restore is done and written up with its date, the answer to Q5 is "not known to have been
exercised".

---

## §4 · Migration chain vs `metadata` (§7 Q3)

Method: `alembic upgrade head` against an empty SQLite file, no `create_all`, then
`alembic.autogenerate.compare_metadata` against `schema.metadata`. Postgres not yet run
(needs Docker Desktop).

### `vault_documents` is not the only one — the chain crashes

`alembic upgrade head` on an empty database **aborts at the 5th revision**,
`5dce3f225121` (*vault documents tags column*): `no such table: vault_documents`.

The whole chain only ever creates **7 tables** — `b6d81352be4c` creates `tenants`,
`translations`, `pipeline`, `tenders`; `92041be0f186` creates `tenant_cpv`, `tenant_keywords`,
`tenant_portals`. `metadata` has **35**. The other **28 tables are created only by
`create_all`**:

composer_documents, composer_document_history, composer_matrix, composer_requirements,
composer_requirement_history, composer_style_examples, document_tender_links, documents,
email_alerts, pipeline_history, source_health, tenant_composer_settings,
tenant_dedup_settings, tenant_scout_settings, tenant_screening_questions, tenant_settings,
tenant_style_guide, tenant_vault_rules, tenant_vault_settings, tender_duplicates,
tender_history, tender_notifications, tender_presence, tender_relevance_overrides,
tender_screening_answers, translated_documents, vault_document_history, vault_documents

Migrations that **alter a table no migration creates** (each would crash on a fresh DB):

| Revision | Alters |
|---|---|
| `5dce3f225121` | `vault_documents` |
| `d4e2b8f19a6c` | `vault_documents` |
| `e5f3c7a2b8d1` | `composer_requirements` |
| `f6a1c9d3e7b2` | `tender_reviews` — **not in `metadata` at all** (a retired table; exists in prod only if an old `create_all` made it) |
| `b3d8f1a4c7e9` | `tender_duplicates` |
| `d9a4f2c6b8e1` (CR-013, uncommitted) | `documents`, `vault_documents`, `composer_documents`, `composer_matrix`, `composer_style_examples` |

### Full extent: stepping past each failure

To stop the first crash hiding later gaps, the chain was walked one revision at a time; a
revision that failed was recorded, stamped past, and the walk continued. On SQLite,
**8 of 15 revisions fail**:

| Revision | Cause |
|---|---|
| `5dce3f225121` | `vault_documents` never created |
| `9b1e4a7c3d6f` | `create_unique_constraint` on `tenants` — SQLite cannot ALTER constraints (needs batch mode). **SQLite-only**; expected to pass on Postgres. The column is added, the constraint is not. |
| `d4e2b8f19a6c` | `vault_documents` never created |
| `e5f3c7a2b8d1` | `composer_requirements` never created |
| `f6a1c9d3e7b2` | `tender_reviews` never created (and retired from `metadata`) |
| `a7c2e4f9b1d6` | Its two `add_column`s on `tenders` are fine; the **backfill `SELECT … FROM tender_reviews`** fails |
| `b3d8f1a4c7e9` | `tender_duplicates` never created |
| `d9a4f2c6b8e1` (CR-013, uncommitted) | `documents` and the other four file tables never created |

With every failure stepped past, the remaining diff against `metadata` is exactly:

- **28 tables** absent (list above) — the chain creates only 7 of 35;
- **18 indexes** absent, every one on one of those 28 tables;
- **1 constraint** absent: `uq_tenants_clerk_org_id` (the SQLite-only failure above);
- **no column, type or nullability differences** on the 7 tables the chain does create.

So the gap is, in full: 28 tables (with their 18 indexes) that exist only through
`create_all`, six revisions that depend on those tables, and one SQLite-incompatible
constraint operation. `vault_documents` is one of 28.

**Postgres:** not yet run — needs Docker Desktop running and a `postgres` image matching
production's major version. Expected result: the same, minus the `9b1e4a7c3d6f` failure. Must be
confirmed before any fix is proposed as verified.

### Worse than the CR states: a rebuild does not boot

The image starts with `python -m alembic upgrade head && python -m uvicorn …`
(`Dockerfile`). On an empty Postgres the migration fails, `&&` stops, **uvicorn never starts**,
so the `create_all` safety net never runs. A disaster-recovery deploy onto a fresh database
is a crash loop, not a degraded app.

### Fix options — preliminary, held until the Postgres run confirms the extent

Appending migrations at head cannot fix this on its own: a fresh database never reaches head.

- **A — Guard + catch-up (recommended).** (1) Guard the six offending revisions with
  `if not inspector.has_table(...): return`. For production they are already applied, so editing
  them changes nothing there. (2) Add one new head revision that creates every missing table,
  index and constraint **from a frozen, literal definition** (autogenerated, not
  `schema.metadata` imported live), each guarded by `has_table` / index existence, so on
  production it is a no-op and on an empty DB it builds the rest. Downgrade drops what it
  defines (destructive by nature, like any `create_table` downgrade — documented).
  No production write beyond the normal migrate-on-boot, which is a no-op.
- **B — New baseline.** Collapse the chain into one revision equal to current `metadata` and
  `alembic stamp` production onto it. Cleanest result, but needs a write to production's
  `alembic_version` and throws away the per-change history.

Plus, for either: a test that builds from migrations alone (SQLite in the suite; Postgres in a
Docker-backed run) and asserts `compare_metadata` is empty, and `tender_reviews` either added
back to `metadata` or explicitly dropped/ignored.
