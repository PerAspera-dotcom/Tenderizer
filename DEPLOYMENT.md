# Deployment

Status as of 2026-07-08. One customer (tenant 2), single Postgres, live at tender-izer.com.

## Current state

| Piece | Status | Detail |
|---|---|---|
| Postgres | ✅ live on Railway | Railway-managed Postgres (service `postgres`, project `tenderizer`). Data migrated from the local docker-compose Postgres via `pg_dump`/`pg_restore` over a temporary TCP proxy — **row counts verified to match exactly** across all 8 tables (tenders: 1842, tenants: 2, translations: 471, etc.). Persistent volume attached (`PGDATA` set to a subdirectory of the mount, per the standard Postgres-on-a-raw-volume fix). |
| Backend | ✅ live | `https://backend-production-00fb.up.railway.app` — `GET /api/health-check` → `200`. Deployed via a `Dockerfile` (Nixpacks' Python provider had a build-vs-runtime environment mismatch — `uvicorn`/deps installed one place, run from another; a Dockerfile removes the ambiguity). `pyproject.toml` gained three previously-undeclared-but-actually-required dependencies this round: `fastapi`, `uvicorn`, `unidecode` — all were only ever ad-hoc `pip install`ed into the local dev venv, never captured. |
| Scheduler | ✅ | APScheduler in-process, **02:00 UTC daily** (no prior schedule existed to match — see the last deployment round). `ENABLE_SCHEDULER=true` on Railway. |
| Frontend | ✅ live | **https://tender-izer.com** (custom domain, aliased to the Vercel project `frontend`). `VITE_API_BASE`, `VITE_CLERK_PUBLISHABLE_KEY`, `VITE_SENTRY_DSN` all set on Vercel production. |
| CORS | ✅ | `ALLOWED_ORIGINS=https://tender-izer.com` on the backend — verified via a real preflight request, no wildcard, no localhost. |
| Sentry | ✅ wired both sides | Backend: `SENTRY_DSN` env, inert-if-unset (same convention as `CLERK_JWKS_URL`). Frontend: `@sentry/react`, `VITE_SENTRY_DSN`, same convention (`frontend/src/main.tsx`). |
| Secrets | ✅ | Nothing committed. Railway/Vercel env vars only. `.env`/`.env.*` gitignored locally (`.env.example` is the only tracked one) — same convention, nothing to change for prod. |
| Multi-tenant architecture | ✅ untouched | Clerk auto-provisioning (`create_tenant_for_clerk_user`) still fires on first login — no rebuild needed to add a second customer later. |
| **Clerk login** | ⛔ **blocked on DNS — see below** | Production keys are wired in, but `clerk.tender-izer.com` currently resolves to **Vercel**, not Clerk (confirmed: `X-Vercel-Error: DEPLOYMENT_NOT_FOUND`, and the frontend's Clerk JS fails to load with a CORS error as a result). This breaks *both* login (frontend) and token verification (backend `CLERK_JWKS_URL`) — it's one DNS record, not two separate problems. |

## The one thing blocking full end-to-end verification

**`clerk.tender-izer.com` needs a DNS fix — I can't do this myself, it's your domain registrar/DNS provider, not anything Railway/Vercel/Clerk-side.**

1. Go to the Clerk dashboard → your production instance → **Domains**. It shows the exact DNS records Clerk expects (typically a few CNAMEs: `clerk`, `accounts`, `clkmail`/`clk._domainkey`).
2. Go to wherever `tender-izer.com`'s DNS is managed (your registrar or DNS host) and check the `clerk` subdomain's record. Right now it's pointing at Vercel — likely a wildcard (`*`) record catching every subdomain including `clerk.`, rather than a specific CNAME for `clerk` pointing at Clerk's target.
3. Once that CNAME is correct and propagates (Clerk's dashboard will show the domain as "Verified"), login will work — nothing else needs to change on my end, the keys and code are already wired correctly.

**As soon as this is fixed**, ping me and I'll finish task 7 for real: a live login, a real scheduled/triggered scrape, and confirming Dashboard/Review Queue load correctly with sane counts under the real prod DB (the exact bug fixed earlier this week).

## Also worth knowing

- **A TCP proxy to Postgres is still open** (`hayabusa.proxy.rlwy.net:43304`, used for the one-time data migration). Protected by the generated Postgres password, but you may want to remove it via the Railway dashboard (Postgres service → Settings → Networking) now that the migration's done — I couldn't find the right API call to remove it myself without more trial-and-error against Railway's API than felt worth it for a low-risk cleanup.
- **Auto-deploy-on-push isn't confirmed.** Every deploy so far was triggered manually via Railway's API (I have a working token). Whether pushing to `master` alone triggers a Railway rebuild depends on the GitHub deploy-trigger setting, which I couldn't fully verify from the API — check Railway dashboard → service → Settings → "Deploy Triggers" if you want push-to-deploy confirmed.
- **`CLERK_SECRET_KEY` isn't used anywhere in this codebase.** Backend auth only verifies JWTs against Clerk's public JWKS (`CLERK_JWKS_URL`); nothing calls Clerk's server-side SDK. I didn't set it on Railway since nothing reads it — flag me if a future feature needs server-side Clerk API calls and I'll wire it in then.
- **Railway's automated-backup mutations exist in their API schema** but I couldn't get a straight answer on whether they're actually available on your current plan without more guessing than I was comfortable doing silently — worth a quick check in the Postgres service's Settings → Backups tab.

## Redeploy runbook

- **Backend:** `curl -X POST https://backboard.railway.app/graphql/v2 -H "Authorization: Bearer $RAILWAY_TOKEN" -d '{"query":"mutation { serviceInstanceDeploy(environmentId: \"7f5e871b-2567-47da-9cc4-e14434400746\", serviceId: \"5b12317b-18b8-44e2-9596-e945eef0b4ce\", latestCommit: true) }"}'` — or ask me, I have the token context.
- **Frontend:** `cd frontend && npx vercel deploy --prod` (this project was linked via CLI, not GitHub import, so pushes alone don't redeploy it — either connect GitHub in the Vercel dashboard, or keep redeploying this way / asking me to).
- **Rollback:** both Railway and Vercel keep full deployment history — redeploy a prior build from either dashboard, no code changes needed.
- **Data:** Postgres now lives only on Railway. No more `pg_dump`/`pg_restore` needed unless you're migrating hosts again.

## Owners / IDs

- **Frontend:** https://tender-izer.com — Vercel project `frontend`, team `schafermaximilian1994-7909's projects`
- **Backend:** https://backend-production-00fb.up.railway.app — Railway project `tenderizer` (id `631af562-4a62-4e4c-8e00-3529dc64c7e1`), service `backend`
- **Postgres:** Railway service `postgres` in the same project, persistent volume `postgres-volume`
- **Clerk production instance:** created against `tender-izer.com` — you
- **Sentry:** project tied to the DSN you provided — you
- **Railway/Vercel accounts:** you
