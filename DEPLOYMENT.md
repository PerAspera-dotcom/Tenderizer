# Deployment

Status as of 2026-07-08. One customer (tenant 2), single Postgres, Vercel frontend.

## Current state

| Piece | Status | Detail |
|---|---|---|
| Postgres | ✅ consolidated | Local `docker-compose` Postgres (`the_scout-postgres-1`) is the one store for everything — engine (`tenders`), Portal/tenant data, config. No SQLite in the loop except test isolation (`tests/conftest.py` forces DATABASE_URL unset per test). Legacy pre-tenancy `data/tenders.db` (1031 rows, 2026-07-01) migrated in under tenant 1 — see `scratch_migrate_legacy_sqlite.py`. |
| Backend code | ✅ prod-ready | `Procfile` (`uvicorn src.api:app --host 0.0.0.0 --port $PORT`), APScheduler daily run wired (`src/api.py` `_lifespan`, 02:00 UTC, `ENABLE_SCHEDULER` env toggle), optional Sentry (`SENTRY_DSN` env, no-op unset), `pyproject.toml` now declares fastapi/uvicorn/apscheduler/sentry-sdk (previously ad-hoc-installed, undeclared). |
| Backend host | ⛔ blocked on you | Picked **Railway** (unified web+Postgres project, Nixpacks auto-detects Python, always-on service fits in-process APScheduler with no separate cron primitive). Railway CLI installs fine via `npx @railway/cli` but needs an interactive login I can't do for you. |
| Frontend | 🟡 deployed, not wired | Live at **https://frontend-ochre-iota-72.vercel.app** (Vercel project `frontend`, under your existing `schafermaximilian1994-7909's projects` team). Build succeeds. Will show a blank/error screen right now — `VITE_CLERK_PUBLISHABLE_KEY` isn't set yet. |
| CORS | ⛔ pending backend domain | `ALLOWED_ORIGINS` needs to be set to `https://frontend-ochre-iota-72.vercel.app` (no wildcard, no localhost) once the backend is hosted. |
| Clerk | ⛔ blocked on you | Still using local dev's `pk_test_`/`sk_test_` keys. A production Clerk instance can only be created from the Clerk dashboard. |
| Secrets | ✅ | `.env`/`.env.*` gitignored (confirmed, `.env.example` is the only tracked one). Nothing to change here — same convention carries into prod as long as secrets go into Railway's/Vercel's env var UI, never into a committed file. |
| Monitoring | 🟡 code ready, needs DSN | Sentry SDK wired, inert until `SENTRY_DSN` is set. Railway's own health checks cover host-level uptime for free, no extra setup. |

## What I need from you to finish

1. **Create the Railway project.**
   - `! npx @railway/cli login` (opens a browser), then `! npx @railway/cli init` from the repo root — or just go to railway.app, "New Project" → "Deploy from GitHub repo" → pick `PerAspera-dotcom/Tenderizer`. Either way, add a Postgres plugin/service in the same project.
   - Once it exists, tell me and I'll wire env vars and verify the deploy, or give me a Railway API token and I'll drive it directly.
   - **Note:** Railway's Postgres is a *new*, separate cloud database — the 811 real + 1031 legacy rows currently live in your *local* docker-compose Postgres. Once Railway's Postgres exists, that data needs a `pg_dump`/`pg_restore` across (I'll do this — flagging now so it isn't a surprise).

2. **Create a Clerk production instance** (Clerk dashboard → your app → "Create production instance"). Send me the resulting `pk_live_...` / `sk_live_...` keys (or set them directly as env vars in Vercel/Railway yourself, if you'd rather not paste secret keys in chat).

3. **Sentry** (optional, your call): `! npx @sentry/wizard` or just sign up free at sentry.io, create a project, send me the DSN. Skip it and I'll leave `SENTRY_DSN` unset — Railway's logs still work without it.

4. Once 1–3 exist, I'll: set `VITE_API_BASE_URL`/`VITE_CLERK_PUBLISHABLE_KEY` on Vercel, set `ALLOWED_ORIGINS`/`DATABASE_URL`/`CLERK_JWKS_URL`/Clerk secret/`SENTRY_DSN` on Railway, migrate the Postgres data across, redeploy both, and verify end-to-end against the real URLs (health check, real login, a real scrape, Review Queue/Dashboard stats specifically).

## Redeploy runbook (once live)

- **Frontend:** push to `master` → Vercel's GitHub integration redeploys automatically (once connected in the Vercel dashboard; right now redeploys are manual via `npx vercel deploy --prod` from `frontend/`, since this project was created via CLI, not GitHub import).
- **Backend:** push to `master` → Railway redeploys automatically once the GitHub integration is set up during project creation (step 1 above).
- **Rollback:** both Vercel and Railway keep deployment history — redeploy a previous build from either dashboard, no code changes needed.

## Owners

- Vercel account: schafermaximilian1994-7909's projects (existing account)
- Railway account: _pending — you_
- Clerk production instance: _pending — you_
- Sentry (optional): _pending — you, if wanted_
