# Incident 2026-11-23: backend down for ~26 minutes after the CR-017 deploy

**Impact:** Every request to the backend returned HTTP 502 from about 14:44 to 15:10 UTC (2026-11-23), about 26 minutes, so `tender-izer.com` couldn't load any data. No data was lost or changed: the app never got far enough to touch the database.
**Status:** Resolved by rollback. Root causes fixed in the repo; see "Remedies".

## Timeline (UTC)

| Time | Event |
|---|---|
| 14:42 | `git push origin master` (fast-forward to `4b616f1`, approved as a push only) **triggers a Railway deploy**: deployment `ba016be2`. Nobody knew pushes deploy; `DEPLOYMENT.md` said they don't. It builds the image from scratch. |
| ~14:44 | `ba016be2` is marked **SUCCESS** and takes traffic. The container crashes on import and restarts in a loop (restart policy `ON_FAILURE`, 10 retries). Every request gets 502. Unnoticed, because no deploy was thought to have happened. |
| 14:51 | `railway redeploy --service backend --from-source` deploys the same commit again (deployment `f505b181`), believed to be *the* deploy. Same crash. |
| ~14:54 | Post-deploy check: `/api/health-check` returns 502. The logs show `TypeError: Unknown option 'send_default_pii'` from `sentry_sdk` at `src/api.py:22`. |
| ~15:00 | First rollback attempt (Railway API) blocked by the session's permission check. The situation is reported and an explicit go-ahead requested. |
| ~15:08 | `deploymentRollback(8922cdc6…)` re-deploys the 2026-11-13 image of `114493a` without rebuilding (deployment `dfd538c5`). |
| ~15:10 | `/api/health-check` returns 200. Service restored. |

## What broke

`pyproject.toml` listed every dependency without a version, and the Dockerfile ran `pip install .` on a floating `python:3.12-slim` base. **Each image build therefore installed whatever was newest that day.**

The previous build, 2026-11-13, got `sentry-sdk 2.75.0`. Between then and 2026-11-23, `sentry-sdk 3.0.0` was released, and it no longer accepts the `send_default_pii` option. `src/api.py` calls `sentry_sdk.init(dsn=..., send_default_pii=False)` at import time whenever `SENTRY_DSN` is set, which it is in production. So the new image raised `TypeError` before uvicorn could serve anything.

Reproduced in a clean `python:3.12-slim` container: `sentry-sdk[fastapi]` (3.0.0) fails, while 2.75.0 and 2.64.0 initialise.

## Root causes

0. **Pushing to `master` deploys the backend, and nobody knew.** `DEPLOYMENT.md` said deploys were manual-only (a GitHub-App permission problem in October); that has since changed. So the push approved as "push and fast-forward master" was itself the first deploy. *This is why the outage started 9 minutes before anyone was watching.*

1. **Builds weren't reproducible.** No version pins and no lock file, so a deploy of unchanged dependency declarations could still install a different environment. CR-017 changed no dependency, yet its deploy re-resolved every one of the ~160 installed packages to whatever was newest that day. *This is the cause.*
2. **Railway couldn't tell a dead deployment from a live one.** The backend service had no healthcheck (`healthcheckPath: null`, read from Railway's API). Railway therefore considers a deployment live once its container starts, and switched traffic away from the working deployment to one that never served a request. With a healthcheck, the same bad build would have failed its deploy and the old deployment would have kept serving: zero downtime. *This is why a bad build became an outage.*
3. **The pre-deploy check looked at the wrong thing.** Before deploying, the assistant confirmed "no migration, no schema or config change, tests green, frontend builds" and called it a code-only deploy. That reasoning checked the *diff*, not the *artefact*. The Python image was never built or started before deploying, even though every deploy rebuilds it. A local `docker build` and start would have shown the crash before production did.
4. **The deploy status was not the success signal, and was briefly treated as one.** "SUCCESS" in Railway only meant "container started", for the reason in root cause 2. The real check (health 200, new routes present, clean logs) caught it within about a minute, and that check is now part of the runbook.

## Remedies (this commit)

| Cause | Remedy |
|---|---|
| 1 | **`requirements.lock`:** the full `pip freeze` of the working production image (158 packages, transitive included), captured from deployment `dfd538c5`. The Dockerfile installs with `pip install -c requirements.lock .`, so every package is fixed. |
| 1 | **`pyproject.toml`:** every runtime dependency pinned `==` to the same version (so `sentry-sdk==2.75.0`, the production version, not the 2.64.0 from the first emergency pin, which was only the local virtualenv's version). |
| 1 | **Dockerfile:** base image pinned to `python:3.12.16-slim`, the Python production ran. |
| 1 | **`tests/test_70_pinned_dependencies.py`:** fails if a runtime dependency is unpinned, disagrees with the lock, the lock contains the app or build tools, `sentry-sdk` moves to 3.x without `src/api.py` changing, the Dockerfile stops using the lock or a pinned base, or the Railway healthcheck goes away. Mutation-checked: unpinning `sentry-sdk` fails it. |
| 2 | **`railway.toml`:** `healthcheckPath = "/api/health-check"` (timeout 300 s), and builder `DOCKERFILE` made explicit. That endpoint answers without the database, and the boot command runs `alembic upgrade head` first, so a failing import, startup or migration all fail the deploy instead of replacing the live one. |
| 3, 4 | **`DEPLOYMENT.md` runbook:** build and start the image locally before deploying; after deploying, success means health 200, the expected routes in `/openapi.json` and clean logs, not the Railway status; rollback is `deploymentRollback` to the previous deployment ID, recorded before deploying. Dependency upgrades are their own change: edit lock and pin together, build, start, deploy. |

## Verification done (2026-11-23)

- **Image built locally** from this commit, as Railway builds it: installed set identical to `requirements.lock` (158 packages); Python 3.12.16; `sentry-sdk` 2.75.0; the image's `pyproject.toml`, `requirements.lock` and `src/api.py` identical to the repo.
- **Container started** with a `SENTRY_DSN` set against a Postgres 16 at the latest migration revision (as production is). `/api/health-check` answered 200 after about 15 s, with no restarts, and `/openapi.json` lists CR-017's routes.
- Full test suite: 1343 passed.

## The healthcheck in `railway.toml` did not take effect (found 2026-11-23, 16:47 UTC)

The next push (`132680d`, 15:36) auto-deployed as deployment `58ad6711`, which carried `railway.toml`. Railway read the file (the deployment's `propertyFileMapping` lists its four keys), but **the healthcheck was not applied**:
- the deployment's `serviceManifest` and the service's settings both show `healthcheckPath: null`
- no healthcheck lines appear in the build or deploy logs
- the only `/api/health-check` request in the runtime log was a manual one, an hour later

Railway's own notice in the build log says config-as-code (`railway.toml`) is deprecated in favour of `.railway/railway.ts`. **So remedy 2 is not in force yet.** It needs the healthcheck set on the service itself (Settings → Deploy → Healthcheck Path, or `serviceInstanceUpdate`), then confirmed on the next deploy by healthcheck lines in its logs.

Also observed: this service has a volume. On `58ad6711` the first container started before the volume was handed over, and was restarted when it mounted, about 90 s later. Whether Railway can keep an old deployment serving while a new one waits for a healthcheck, on a service with a volume, needs confirming against Railway's documentation before relying on "zero downtime".

## Follow-up found while verifying (not fixed)

**The migration chain can't build a database from empty.** `alembic upgrade head` on an empty Postgres fails at `5dce3f225121` ("vault documents tags column"): `relation "vault_documents" does not exist`. No earlier migration creates that table; it only ever came from `metadata.create_all`. Production isn't affected (its database is already at head, so the boot-time `upgrade head` does nothing). But restoring a backup into a new database, or standing up a new environment, would fail at boot. It needs its own fix: a migration that creates the missing tables if they don't exist, or a documented "create_all + stamp head" bootstrap.

## Not changed

- The frontend: Vercel builds with `npm` from `package-lock.json`, which is already a lock file.
- The `dev` extras (`pytest`, `pyflakes`) stay unpinned. The image never installs them.
- Upgrading `sentry-sdk` to 3.x is possible later, but means adapting the `sentry_sdk.init` call to 3.x first (it rejects `send_default_pii`). The test above will flag it.
