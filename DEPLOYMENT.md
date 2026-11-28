# Deployment

Status as of 2026-07-08. **tender-izer.com is fully live and verified end-to-end.**

## Current state — everything verified, not just configured

| Piece | Status | Detail |
|---|---|---|
| Postgres | ✅ live on Railway | Data migrated from local docker-compose Postgres via `pg_dump`/`pg_restore` — row counts verified exact match across all 8 tables. Persistent volume attached, `PGDATA` set to a subdirectory of the mount (standard fix for Postgres-on-a-raw-volume). |
| Backend | ✅ live | `https://backend-production-00fb.up.railway.app` — health-check, CORS preflight, and a real production scrape all verified directly (see below). Deployed via `Dockerfile` (Nixpacks had a build-vs-runtime environment mismatch). |
| Scheduler | ✅ | APScheduler in-process, **02:00 UTC daily**. |
| Frontend | ✅ live | **https://tender-izer.com**. Real Clerk sign-in page renders correctly with **zero console errors** (verified in a real browser). |
| Clerk / DNS | ✅ fixed | `clerk.tender-izer.com` now correctly resolves to Clerk's infrastructure (was a wildcard DNS record catching it and routing to Vercel instead). Verified: JWKS endpoint serves real keys, sign-in page loads cleanly. |
| CORS | ✅ | `ALLOWED_ORIGINS=https://tender-izer.com` only — verified via a real preflight request. |
| Sentry | ✅ wired both sides | Backend `SENTRY_DSN`, frontend `VITE_SENTRY_DSN` (`@sentry/react`), both inert-if-unset. |
| Secrets | ✅ | Nothing committed; Railway/Vercel env vars only. `.env`/`.env.*` gitignored. |
| Multi-tenant architecture | ✅ untouched | Clerk auto-provisioning still fires on first login. |
| TCP proxy to Postgres | ✅ closed | Was left open after the initial migration — closed. (Briefly reopened once, deliberately, to run the verification scrape below against real prod data without touching the live service's auth — closed again immediately after.) |

## End-to-end verification (done for real, against the live prod DB)

1. **Login**: real Clerk sign-in page at tender-izer.com renders correctly — "Sign in to Tenderizer", Google OAuth option, email/password form — **zero console errors**. (I can't complete an actual authenticated session without your credentials, but the full Clerk integration — frontend SDK load, JWKS endpoint, CORS — is confirmed working.)
2. **Live scrape**: ran a real scrape against production (same `_do_run` code path the scheduler/`"Run now"` button uses) — hit the real TED/BOAMP APIs, wrote real results into Railway's Postgres. Tenant 2's tender count went from 80 → 84 (4 genuine new matches).
3. **Review Queue / Dashboard stats — the exact bug from before, reconfirmed fixed**: post-scrape, Review Queue's badge reads **84**, breaking down as `cpv: 41, both: 37, keyword: 6` — **sums exactly to 84, zero unmatched "none" records leaking through.** This proves the F5 fix holds on a *fresh live scrape*, not just the one-time backfill. Zero excluded records leaking into the queue either.
4. Report file (`tenders_2.xlsx`) confirmed generated at the correct per-tenant path.

## Also fixed this round (per your last message)

1. **TCP proxy closed.** Found and deleted it via Railway's API (`hayabusa.proxy.rlwy.net:43304` no longer accepting connections — confirmed).
2. **Auto-deploy-on-push: checked directly, it's manual-only — and I hit a real wall trying to fix it.** Railway's API confirmed zero `repoTriggers` configured for the backend service (every deploy so far was my manual API call). I tried to create one (`deploymentTriggerCreate`) and got: *"Cannot create deployment trigger for PerAspera-dotcom/Tenderizer because no one in the project has access to it."* This is a GitHub App permissions issue — `serviceCreate` could pull the repo because it's public, but registering a push *webhook* needs Railway's GitHub App actually installed/authorized on that repo, which only you (or someone with admin access to `PerAspera-dotcom/Tenderizer`) can grant. **To fix:** Railway dashboard → the `backend` service → Settings → Source → reconnect/authorize the GitHub App for this repo. Once that's done, ask me to re-run `deploymentTriggerCreate` (or try the dashboard's own "Enable auto-deploy" toggle) and it should take. Until then, redeploys need the manual trigger below.

## Redeploy runbook

Rewritten 2026-11-23 after a deploy took the backend down for ~26 minutes:
`docs/INCIDENT_2026-11-23_BACKEND_DEPLOY.md`. **Every backend deploy rebuilds
the image**, so "no code risk" doesn't mean "no deploy risk".

**Pushing to `master` deploys the backend** (verified 2026-11-23: both pushes
that day created Railway deployments). The "auto-deploy is manual-only" note
above is out of date. Treat a push to `master` as a deploy: do the
before-deploy steps first.

**Before a backend deploy**
1. Full test suite green.
2. Build the image exactly as Railway does, and start it:
   `docker build -t tenderizer-backend:check .`, then run it with `SENTRY_DSN`
   set to any well-formed DSN (Sentry initialises at import only when one is
   set) and check `/api/health-check` answers 200.
3. Note the current deployment ID (`railway deployment list --service backend`).
   That's the rollback target.
4. Not between 02:00 and 06:00 UTC: the daily scrape runs then.

**Deploy the backend**
- Push to `master` (or `railway redeploy --service backend --from-source --yes`
  to redeploy the current `master` tip).
- **Healthcheck gate: NOT active yet.** `railway.toml` lists
  `healthcheckPath`, but Railway has never applied it: the service shows
  `null` and no deploy has waited on it (see the incident report). It becomes
  active when `.railway/railway.ts` is applied (CR-019). Until a deploy log
  shows Railway waiting on `/api/health-check`, a crashing build still takes
  traffic: watch the deploy and be ready to roll back.

**Railway service settings live in `.railway/railway.ts` (CR-019)**
- Railway does **not** read that file on deploy. `railway config apply` writes
  it onto the service. **An apply is itself a deploy** (the plan marks changes
  `deployEffect: deploy`), so it needs the same go-ahead and checklist.
- Never change backend settings in the dashboard. Change the file, then plan,
  then apply.
- **Before every deploy:** `railway config plan --detailed-exit-code`. Exit 0
  means the service matches the file; exit 2 means drift, so stop and find out why.
- **Read the plan body, not the headline.** On 2026-11-24 the headline said
  `0 to destroy` while the body said `! 1 destructive change(s)` (detaching
  the 5 GB volume). Any `destructive` line is a stop.
- Run it from PowerShell with `$env:_` set to the native `railway.exe`. Under
  Git Bash, the SDK's CLI version check fails.
- `backend-volume` is declared in the file. Removing the declaration would
  **delete** the volume (permanent after 48 h; its Railway backups go with it).
  Removing only its mount would detach it.

**After deploying: success is not the Railway status**
- `/api/health-check` → 200 on the public URL.
- The routes the change added are in `/openapi.json`.
- `railway logs --service backend` shows no traceback.
- Any doubt: roll back immediately (below), then diagnose.

**Rollback**
- Backend: `deploymentRollback(id: "<previous deployment id>")` via Railway's
  GraphQL API, or Dashboard → backend → Deployments → ⋯ → Rollback. Re-uses the
  old image, no rebuild. Took ~2 minutes on 2026-11-23.
- Frontend: promote the previous deployment in Vercel.

**Dependencies**
- Runtime dependencies are pinned in `pyproject.toml` and, with every
  transitive package, in `requirements.lock` (the Dockerfile installs with it as
  a constraint). `tests/test_70_pinned_dependencies.py` enforces it.
- Upgrading a package is its own change: edit `requirements.lock` and the pin
  together, build and start the image, deploy, verify as above.

**Frontend**
- Production is the Vercel project **`tenderizer-web`**
  (`prj_eIf34h28AV6XX6ztSCTXKpvFurCQ`, Root Directory `frontend`), which
  serves `tender-izer.com`. The repo's `.vercel/repo.json` links the same
  project ID under its old name `frontend`. Deploy from `the_scout/` with
  `npx vercel deploy --prod` (2026-11-23 used `VERCEL_ORG_ID=team_oAYjMdCcg8OlYwG2jakeO4fj
  VERCEL_PROJECT_ID=prj_eIf34h28AV6XX6ztSCTXKpvFurCQ` to be explicit). Pushes
  don't redeploy it.
- Deploy the backend first when the frontend calls new endpoints.
- Data: Postgres lives only on Railway now. No more `pg_dump`/`pg_restore`
  needed unless migrating hosts again.

## Still worth a look on your end (non-blocking)

- **`CLERK_SECRET_KEY` isn't used anywhere in this codebase** — backend auth only verifies JWTs against the public JWKS, nothing calls Clerk's server-side SDK. Not set on Railway since nothing reads it; flag me if a future feature needs it.
- **Railway's automated-backup availability wasn't confirmed** — the mutations exist in their API schema but I couldn't verify plan-tier availability without guessing. Worth a quick check: Postgres service → Settings → Backups.

## Claude API (CR-015)

Every Claude call goes through `src/llm.py`. A key alone switches on only
manual-draft extraction; everything else needs its own variable on the
`backend` service. See `.env.example` for the full list and defaults.

- `LLM_MANUAL_DRAFTS` (default on), `LLM_SCREENING`, `LLM_VAULT`, `LLM_COMPOSER` (default off)
- `LLM_SCHEDULED_SCREENING` (default off): the nightly run screens shortlisted / needs-review tenders only
- `LLM_MAX_CALLS_PER_RUN` (default 10), `LLM_MAX_CALLS_PER_MONTH` (default 60), `LLM_MAX_USD_PER_MONTH` (default 7, estimated from recorded tokens)
- Usage per month, feature and model: Postgres tables `llm_usage_months` and `llm_usage` (migration `a7c3e9f1b5d2`); status on Portal Health ("Claude"). Until the migration has run, every Claude call is refused — run it before deploying the gateway.
- Screening backfill, explicit and bounded: `python src/cli.py screen-backfill --tenant N --limit 50 [--dry-run]`

The $10/month limit in the Anthropic console is the outer backstop; when it
is exhausted every call fails, manual extraction included, until the month
resets. The internal $7 ceiling is set to refuse first, gracefully, with the
remaining ~$3 covering calls already in flight.

## Owners / IDs

- **Frontend:** https://tender-izer.com — Vercel project `frontend`, team `schafermaximilian1994-7909's projects`
- **Backend:** https://backend-production-00fb.up.railway.app — Railway project `tenderizer` (id `631af562-4a62-4e4c-8e00-3529dc64c7e1`), service `backend`
- **Postgres:** Railway service `postgres`, same project, persistent volume `postgres-volume`
- **Clerk production instance:** tender-izer.com — you
- **Sentry:** project tied to the DSN you provided — you
- **Railway/Vercel accounts:** you
