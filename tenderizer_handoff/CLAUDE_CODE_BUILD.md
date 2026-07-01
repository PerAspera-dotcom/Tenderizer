# Tenderizer — Build Brief for Claude Code

**Read this with `TENDERIZER_HANDOFF.md` open** — that doc is the source of truth for the data
contract (§3), API surface (§4), engine additions (§5), and screen-by-screen behaviour (§8).
This file pins the **stack and architecture decisions** so you can start building without
re-litigating them.

---

## 0. Context (what you are and aren't doing)

- **Scout** (`the_scout/`) is a **finished Python pipeline** — scrape → normalise → match →
  dedup → SQLite. It runs end-to-end via `run.run_pipeline()`. **Do not rebuild it.** It is the
  POC that proves the product is viable (no tender capture → no automated offers).
- The **product** is the whole **Portal**: Scout + Vault + Composer. Phase 1 ships Scout + the
  Portal workspace; Vault + Composer are Phase 2, stubbed in the nav as preview-only.
- The goal is a **multi-tenant SaaS** sold to multiple procurement companies. Build for that from
  the start (tenant isolation, per-tenant config), even while the first tenant is the co-dev company.
- `Tenderizer.dc.html` is the **approved design reference** — recreate it in the real stack; do
  not ship the HTML.

---

## 1. Locked stack decisions

| Layer | Choice | Notes |
|---|---|---|
| Frontend | **React + Vite + TypeScript** | TanStack Query for data, React Router for routing |
| Backend | **FastAPI** (thin, over the existing engine) | Reads DB via `store`; only `POST /api/run` triggers engine work |
| Database | **Postgres** | Migrate off SQLite *before* multi-tenant. Use SQLAlchemy + **Alembic** migrations |
| Auth | **Clerk** (or Auth0 / Supabase Auth) | Managed. Do **not** hand-roll auth. Provides login, SSO, roles, resets |
| Scheduled scrape | **APScheduler** (single box) → Celery if it grows | Runs `run.run_pipeline` per tenant on a schedule |
| Hosting — frontend | **Vercel** (static) | Cheap, CDN |
| Hosting — backend + DB | **Railway or Render** | Managed always-on container + managed Postgres w/ automated backups |
| Infra glue | **Docker**, Sentry, uptime monitor, secrets manager | Package engine + API in one image |

**Why not Vercel for the backend:** the engine needs a persistent process and a writable
database; serverless can't run the scheduler or hold state between invocations. Split frontend
(static) from backend (always-on).

---

## 2. Repository layout (monorepo suggested)

```
tenderizer/
  the_scout/            # EXISTING engine — leave intact except §5 additive changes
    src/ config/ tests/ data/
  api/                  # NEW — FastAPI app
    main.py             # app + CORS + auth middleware
    routers/            # tenders, stats, health, run, config, pipeline, followup
    deps.py             # db session, current_user/current_tenant
    db.py               # SQLAlchemy + Alembic
    connectors/         # registry that wraps the_scout connectors behind one interface
  web/                  # NEW — React + Vite + TS frontend
    src/ (screens mirror the mockup) .env
  docker-compose.yml    # api + postgres for local dev
  alembic/              # migrations
```

---

## 3. Build order (each phase has an acceptance bar)

Follow the handoff's recommended sequence. **Run `pytest -q` (52 tests) after any engine touch.**

1. **Engine additions — `TENDERIZER_HANDOFF.md` §5 (additive only).**
   - Persist run metadata → `data/last_run.json`.
   - Add `status` column to `tenders` (default `new`) + `store.set_status`.
   - Config writers: `config.write_cpv`, `config.write_keywords` (validate vs `cpv_reference.json`).
   - **Separate `pipeline` table** (workflow state) — keyed by `pub_number`, FK → tenders. Build now.
   - *Acceptance:* 52 tests still green; new helpers unit-tested.

2. **Postgres migration + multi-tenancy.**
   - Move the schema to Postgres via Alembic. Add `tenant_id` to every table; enforce isolation
     (row-level: every query filtered by the authenticated tenant).
   - Per-tenant config rows for CPV set, keywords, enabled portals.
   - *Acceptance:* two tenants' data never cross; engine reads/writes scoped by tenant.

3. **API layer — handoff §4.**
   - The ~12 endpoints (tenders read/patch, stats, health, run, config read/write, pipeline,
     followup). Serialize `cpv_codes`/`matched_terms` as real JSON arrays. `portals_active` = `"2/4"`.
   - Wire Clerk: every route requires a valid token → resolves `current_tenant` + role.
   - *Acceptance:* endpoints return handoff-shaped payloads; unauthenticated requests 401;
     cross-tenant access 403.

4. **Frontend — recreate the mockup wired to the API.**
   - Build order: Scout Dashboard (read-only) → Tender Feed → Review Queue (status triage) →
     Portal Home / Pipeline / Follow-up → CPV / Keywords config.
   - `VITE_API_BASE_URL` env var; auth-gated routes via Clerk; TanStack Query for fetching.
   - Keep all 🚧 screens stubbed exactly as the mockup shows.
   - *Acceptance:* every "real" element in handoff §6 reads live data; 🚧 screens match the mockup.

5. **Scheduled scrape + reliability.**
   - APScheduler job runs `run_pipeline` per tenant; writes health + `last_run.json`.
   - Alert when a source fails N consecutive days. Daily DB backups + a tested restore.
   - *Acceptance:* a scheduled run populates the dashboard; a failing source is captured, not fatal.

6. **Notifications.** Deadline alerts and new-tender digest → email/Slack per tenant.

7. **Phase 2 (Vault + Composer)** — leave preview-only until explicitly scoped.

---

## 4. Connector interface (most important for sell-to-many)

New portals must plug in without touching the core. Define one contract; `ted.py` and `boamp.py`
are the reference implementations.

```python
# api/connectors/base.py
class Connector(Protocol):
    name: str
    region: str
    def fetch(self, since: date, config: dict) -> list[RawNotice]: ...

# A registry maps portal key -> Connector. run_pipeline iterates enabled connectors
# for the tenant. Adding "Belgium e-Procurement" = one new connector file + a registry entry
# + a portal config row. No changes to normalize / match / store.
```

Respect ToS: **DTVP stays paused**; e-Procurement (BE) is planned, not built. Per-portal legal
review (handled with the co-dev company) gates turning any source live.

---

## 5. Environment / config

```
# web/.env
VITE_API_BASE_URL=https://api.tenderizer.app    # or http://localhost:8000 in dev
VITE_CLERK_PUBLISHABLE_KEY=...

# api (.env / secrets manager — never in code)
DATABASE_URL=postgresql://...
CLERK_SECRET_KEY=...
SENTRY_DSN=...
ALLOWED_ORIGINS=https://app.tenderizer.app,http://localhost:5173
```

API on its own subdomain (`api.tenderizer.app`); CORS locked to `ALLOWED_ORIGINS`. Tenants are
resolved from the auth token — **never** a URL or path per customer.

---

## 6. Local dev

```bash
# backend
docker compose up -d postgres
alembic upgrade head
uvicorn api.main:app --reload          # http://localhost:8000

# frontend
cd web && npm install && npm run dev    # http://localhost:5173

# engine sanity
cd the_scout && pytest -q               # must stay 52 passing
```

---

## 7. Guardrails (do NOT)

- **Do not rebuild the engine** — call `run.run_pipeline`, read via `store.all_records`, config
  via `config.*`.
- **Do not change the record schema** except the additive `status` column. Tests enforce it.
- **Do not put matching/relevance logic in the API or frontend** — confidence tiers derive from
  `match_source` (`cpv`/`both` = High, `keyword` = Medium, `None` = Low).
- **Do not hard-code tender data** — everything comes from the DB; mockup rows are placeholders.
- **Do not wire a DTVP scraper** — Germany stays paused.
- **Do not roll your own auth** — use the managed provider.
- **Keep the 52 tests green** — `pytest -q` after any engine touch.
- Est. value, Language, and the relevance %/signal bars are **illustrative** (handoff §6) — derive
  or omit; don't assume the engine supplies them.
