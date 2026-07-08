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

- **Backend (manual, until the GitHub App is authorized — see above):**
  `curl -X POST https://backboard.railway.app/graphql/v2 -H "Authorization: Bearer $RAILWAY_TOKEN" -d '{"query":"mutation { serviceInstanceDeploy(environmentId: \"7f5e871b-2567-47da-9cc4-e14434400746\", serviceId: \"5b12317b-18b8-44e2-9596-e945eef0b4ce\", latestCommit: true) }"}'` — or ask me.
- **Frontend:** `cd frontend && npx vercel deploy --prod` (linked via CLI, not GitHub import — pushes alone don't redeploy it).
- **Rollback:** both Railway and Vercel keep full deployment history — redeploy a prior build from either dashboard.
- **Data:** Postgres lives only on Railway now. No more `pg_dump`/`pg_restore` needed unless migrating hosts again.

## Still worth a look on your end (non-blocking)

- **`CLERK_SECRET_KEY` isn't used anywhere in this codebase** — backend auth only verifies JWTs against the public JWKS, nothing calls Clerk's server-side SDK. Not set on Railway since nothing reads it; flag me if a future feature needs it.
- **Railway's automated-backup availability wasn't confirmed** — the mutations exist in their API schema but I couldn't verify plan-tier availability without guessing. Worth a quick check: Postgres service → Settings → Backups.

## Owners / IDs

- **Frontend:** https://tender-izer.com — Vercel project `frontend`, team `schafermaximilian1994-7909's projects`
- **Backend:** https://backend-production-00fb.up.railway.app — Railway project `tenderizer` (id `631af562-4a62-4e4c-8e00-3529dc64c7e1`), service `backend`
- **Postgres:** Railway service `postgres`, same project, persistent volume `postgres-volume`
- **Clerk production instance:** tender-izer.com — you
- **Sentry:** project tied to the DSN you provided — you
- **Railway/Vercel accounts:** you
