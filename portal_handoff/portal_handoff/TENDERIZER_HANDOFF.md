# Tenderizer — Consolidated Build Handoff

**For:** Claude Code (API layer + frontend integration, deployment)
**From:** Design (UI mockup + reconciliation)

### This folder (`portal_handoff/`) contains
- `TENDERIZER_HANDOFF.md` — **this document**; the single source of truth. Read it first.
- `Tenderizer-mockup.html` — the approved, interactive mockup, self-contained (opens offline in any browser). Click the **app switcher** (top-left, under the logo) and the left-nav items to walk every screen.
- `screenshots/` — `01-portal-home.png`, `02-portal-pipeline.png`, `03-portal-followup.png`, `04-scout-dashboard.png`, `05-scout-tender-feed.png`, `06-review-queue.png`, `07-composer-ingest.png`.

> The mockup is a **design reference** showing intended look + behaviour — not production code to copy. Recreate it in the chosen frontend stack (React + Vite suggested) wired to the real API; do not ship the HTML directly.

This single document supersedes the two earlier drafts (`PORTAL_BUILD_BRIEF.md`, `tenderizer-claude-design-prompt-69884665.md`). Where they disagreed, the corrections are flagged **⚑ CORRECTION** below. Read §1 first, then §8 (the guided mockup tour) with the mockup open beside you.

---

## 0. One-paragraph orientation

**Scout** is a finished Python pipeline that pulls public tender notices from EU portals, filters them for the tent / shelter / camp-supply sector, tags and deduplicates them into a SQLite database, and writes a categorised report. It already runs end-to-end via one function, `run_pipeline()`. **Do not rebuild it.** The portal is a thin **read / trigger layer** on top: a small HTTP API that serves what's already in the database and exposes a "Run now" button, plus a browser UI that renders it. Every number on the dashboard already exists in the engine's output. The **Portal** is the product's home workspace and default landing screen: a launchpad into each tool, plus a working **pipeline** of accepted tenders (deadlines, submission status, extensions, notes) and a **follow-up** board for won/lost results — this layer needs a little new state the engine doesn't own (§5.4). Tenderizer is also the umbrella for two further apps shown in the mockup — **Vault** (technical-document library) and **Composer** (proposal drafting) — which are **phase-2**, vision-stage only (§9).

---

## 1. Corrections to the earlier drafts (READ THIS)

The mockup has been reconciled to the engine's real state. These three points override anything to the contrary in the older docs:

1. **⚑ Portals: 2 live, not 3.** Only **TED (EU)** and **BOAMP (France)** are live. **e-Procurement (Belgium) is *planned*** — the connector is not built. **DTVP (Germany) is *paused*** (ToS prohibits automated use). The dashboard KPI reads **"2 / 4 · BE planned · DE paused"**, and both the dashboard Portal Health panel and the Portals page render e-Procurement with a muted grey "Planned" dot — *not* a live teal dot. The mockup's sample feed therefore contains **TED + BOAMP rows only** (no Belgian rows).

2. **⚑ Review Queue vocabulary now matches the API.** The mockup's triage states are **`new` / `shortlisted` / `reviewed` / `dismissed`**, mapping 1:1 to the `status` column and `PATCH /api/tenders/{pub_number}` body. (Earlier "relevant / to evaluate / not relevant" wording is gone.) Button → status: **Shortlist → `shortlisted`**, **Mark reviewed → `reviewed`**, **Dismiss → `dismissed`**; unreviewed items are `new` and feed the sidebar badge count.

3. **⚑ Sample data mirrors the real schema.** The mockup's `tenderData` rows now carry the real `tenders.db` field shapes — `source`, `pub_number` (TED `381972-2026`, BOAMP `26-63438`), `country` as **ISO3 for TED** (`SWE`, `POL`, `FRA`) and `FR` for BOAMP, `match_source` (`both`/`cpv`/`keyword`), `cpv_codes` / `matched_terms` as arrays, real `url`s, etc. **They are still placeholder content** — the live values come from `tenders.db`. See §6 for which on-screen fields are real columns vs. illustrative.

Also note (older docs drift): the active CPV set is **22 codes** (not the legacy 7); the keyword library is **112 terms** with a **25-term distinctive subset** used in live queries (the design prompt's "68 keywords" was illustrative).

---

## 2. What already exists — the engine (DO NOT REBUILD)

**Project root:** `C:\Users\Maximilian\Projects\Tenderizer\the_scout`

```
the_scout/
  config/  cpv.yaml (22 codes) · cpv_reference.json (EN/FR/NL/DE labels) ·
           keywords.yaml (112 terms + 25 distinctive) · portals.yaml
  src/     config.py · connectors/ted.py · connectors/boamp.py ·
           normalize.py · store.py · match.py · report.py · run.py
  tests/   test_06 .. test_11  — 52 passing tests
  data/    tenders.db          — THE PORTAL'S DATA SOURCE
  reports/ tenders.xlsx        — one run's categorised output
```

Runs today via `python src\run.py` (wired for Windows Task Scheduler). It fetches TED + BOAMP, normalises, matches, dedups into `tenders.db`, writes `tenders.xlsx`, prints health (`TED: ok (369)` / `BOAMP: ok (540)`). A failing source is captured in health and never aborts the run.

### Key interfaces — call these, don't reimplement

```python
# run.py
run.run_pipeline(sources, db_path, out_path) -> health: dict   # the "Run now" target
run._default_sources(since)                                    # real TED+BOAMP source list

# store.py
store.init_db(path) -> conn
store.all_records(conn) -> list[dict]   # main read; cpv_codes & matched_terms come back as lists
store.upsert(conn, record) -> bool      # False on duplicate (hash dedup)
store.COLUMNS

# config.py
config.cpv_codes() / config.keywords() / config.distinctive_keywords()
config.cpv_reference() -> {code: {en,fr,nl,de, group, category}}
config.cpv_label(code, lang)
```

### Relevance model (drives the UI's confidence tiers)

- **CPV codes = primary net** (language-independent; high confidence).
- **Keywords = safeguard** (catches mis-coded notices; lower confidence / "candidates").
- This maps to `match_source`: `cpv` / `both` → high-confidence ("Matched by CPV"); `keyword` → candidate ("Keyword only"); `None` → low-confidence tail.

---

## 3. The data contract — `tenders.db`

One table, `tenders`. **This is the exact schema the API serves. Do not change it** except the additive `status` column in §5.

| Column | Type | Notes |
|---|---|---|
| `hash` | TEXT (PK) | sha256(source\|pub_number); dedup key |
| `source` | TEXT | `"TED"` or `"BOAMP"` |
| `pub_number` | TEXT | TED `"381972-2026"`, BOAMP `"26-63438"` |
| `tag_line` | TEXT | notice title (language-picked for TED) |
| `description` | TEXT | longer text (TED only; BOAMP empty) |
| `buyer` | TEXT | contracting authority |
| `country` | TEXT | TED = ISO3 (`SWE`,`FRA`,`POL`); BOAMP = `"FR"` |
| `place` | TEXT | NUTS codes (TED) or `FR-75` dept codes (BOAMP) |
| `category` | TEXT | `Supply` \| `Services` \| `Works` \| `Training` \| `Other` |
| `procedure` | TEXT | e.g. `open`, `Procédure ouverte` |
| `pub_date` | TEXT | publication date |
| `deadline` | TEXT | ISO datetime; **may be empty** for some notices |
| `cpv_codes` | TEXT (JSON) | JSON array; BOAMP = `[]` |
| `matched_terms` | TEXT (JSON) | JSON array of keywords that fired |
| `match_source` | TEXT | `cpv` \| `both` \| `keyword` \| `None` |
| `url` | TEXT | direct link to the live notice (verified) |
| `first_seen` | TEXT | ISO date first stored |
| `status` | TEXT | **NEW (§5)** — `new` (default) \| `reviewed` \| `shortlisted` \| `dismissed` |

> **No `estimated value` and no `language` column exist.** The mockup shows "Est. value" and "Language" in the tender/review detail — these are **illustrative** (see §6). Either omit them, derive language from `source`/`country`, or add them as a deferred enhancement; do not assume the engine supplies them.

---

## 4. The API layer to build — FastAPI, `src/api.py` (NEW, thin)

Reads `data/tenders.db` via `store`; never normalises/matches/fetches. The only place it triggers engine work is `POST /api/run`.

```
GET  /api/tenders
       query: source, category, match_source, country, q (search tag_line+buyer),
              status, has_deadline (bool), limit (100), offset (0), sort ("deadline")
       -> { "total": int, "results": [ {full record}, ... ] }
GET  /api/tenders/{pub_number}        -> single record or 404
PATCH /api/tenders/{pub_number}       body {"status": "reviewed"|"shortlisted"|"dismissed"|"new"}

GET  /api/stats
       -> { last_sync, next_run|null, notices_scanned, matched_total, new_today,
            by_match:{cpv,both,keyword,none}, by_category:{Supply,Services,Works,Training,Other},
            portals_active:"2/4" }
GET  /api/health
       -> [ {name:"TED",region:"EU",status:"live",last_result:"ok (369)"},
            {name:"BOAMP",region:"France",status:"live",last_result:"ok (540)"},
            {name:"e-Procurement",region:"Belgium",status:"planned"},
            {name:"DTVP",region:"Germany",status:"paused",
             detail:"Scraper paused — ToS review pending"} ]
POST /api/run                         -> run_pipeline in a BackgroundTask; returns {"status":"started"} so the UI polls /api/stats

GET  /api/config/cpv                  -> [ {code, labels{en,fr,nl,de}, group, category} ]
PUT  /api/config/cpv                  body {codes:[...]}    # validate vs cpv_reference.json, then write
GET  /api/config/keywords             -> {terms:{en,fr,nl,de}, distinctive:[...]}
PUT  /api/config/keywords             body {...}            # validate, then write
GET  /api/reports/latest              -> serve/download reports/tenders.xlsx

# Portal — pipeline & follow-up (workflow state; needs the §5.4 store)
GET   /api/pipeline                   -> accepted tenders (status='shortlisted') + {submission_status, deadline_override, owner, notes}
PATCH /api/pipeline/{pub_number}      body {submission_status?|deadline_override?|notes?|owner?}
GET   /api/followup                   -> submitted tenders + {submitted, result_due, outcome}
PATCH /api/followup/{pub_number}      body {outcome:"pending"|"won"|"lost"}

# Composer — ingest & requirement validation (PHASE 2)
POST  /api/composer/ingest            # upload tender docs -> parsed requirements [{req, extracted, source, confidence}]
PATCH /api/composer/requirements/{id} body {status:"validated"|"flagged"}
POST  /api/composer/{pub}/generate    # 403 until every requirement is validated
```

Notes: serialize `cpv_codes`/`matched_terms` as real JSON arrays; the static portal list + statuses live in `portals.yaml`, merge in per-source `last_result` from the last run; **`portals_active` is `"2/4"`**. Keep it thin — if you're re-implementing matching or fetching, stop and call the engine.

---

## 5. Small engine additions needed (additive — keep the 52 tests green; run `pytest -q` after each)

1. **Run-metadata persistence.** At the end of `run_pipeline`, write `data/last_run.json` = `{timestamp, health, notices_scanned, matched_total}`. Feeds `/api/stats` "Last sync" + "N notices scanned" and `/api/health` `last_result`. (~10 lines.)
2. **Review `status` column.** Append `status` to the `tenders` table (default `"new"`) in `store.py` (`init_db` + `COLUMNS`), plus `store.set_status(conn, pub_number, status)`. Powers the Review Queue + `PATCH`. (~15 lines, append-and-default so existing tests pass.)
3. **Config writers.** `config.write_cpv(codes)` and `config.write_keywords(data)` — validate (CPV codes must exist in `cpv_reference.json`; warn on unknowns), then save back to the YAML in its current structure. Powers CPV Config / Keywords screens.
4. **Portal workflow store (NEW table — additive, separate from `tenders`). ⟵ build this in step 1, not later.** The Portal's pipeline & follow-up need state the engine doesn't own. Add a small `pipeline` table keyed by `pub_number` (FK → `tenders`): `submission_status` (`not_started`|`drafting`|`submitted`, default `not_started`), `deadline_override` (nullable — set when an extension is granted; the UI shows this **instead of** the engine's `deadline` and flags the row "extended"), `owner`, `notes`, plus follow-up fields `submitted_date`, `result_due`, `outcome` (`pending`|`won`|`lost`). Keep it in **its own table** so the engine's `tenders` schema and its 52 tests stay untouched. "Accepted" = tenders the analyst set to `status='shortlisted'` in the Review Queue. Deadline **alerts** are derived (not stored): closing within N days AND `submission_status != 'submitted'`.

---

## 6. Data → UI mapping (what's real vs. illustrative)

**Backed by real engine output** (wire directly):

| Mockup element | Source |
|---|---|
| "N notices scanned" / "N matched" | `/api/stats` `notices_scanned` / `matched_total` |
| "New tenders today" | `/api/stats` `new_today` (`first_seen == today`) |
| "Portals active 2/4 · BE planned · DE paused" | `/api/stats` `portals_active` + `/api/health` |
| "Matched by CPV" / "Keyword only" KPIs | `by_match.cpv + by_match.both` / `by_match.keyword` |
| "Last sync …" / "Next run in …" | `/api/stats` `last_sync` / `next_run` |
| Tender Feed rows (Title/Portal/Deadline/Match/Open — slimmed) | `/api/tenders`: `tag_line`,`source`,`deadline`,`match_source`,`url`. **Country + CPV moved to the detail view** to de-clutter the row |
| Match chips Both/CPV/Keyword | `match_source` |
| "Open" button | record `url` |
| Portal Health panel | `/api/health` |
| Review Queue items + badge | `/api/tenders?status=new` |
| Triage actions | `PATCH /api/tenders/{pub}` |
| "Run now" | `POST /api/run` |
| Reports export | `GET /api/reports/latest` |
| Portal pipeline (deadlines, submission status, notes, amend) | `/api/pipeline` + `PATCH /api/pipeline/{pub}` (§5.4) |
| Portal deadline alert banners (closing, no tender sent) | derived from `deadline_override`/`deadline` vs today + `submission_status != submitted` |
| Portal Follow-up (won/lost + win rate) | `/api/followup` + `PATCH /api/followup/{pub}` |

**Illustrative / not in the schema** (mockup uses placeholders — decide per item):

- **Est. value** — no column. Omit, or add as a deferred TED-parsed field.
- **Language** — derive from `source`/`country`, or omit.
- **Relevance confidence %** and the **4 signal bars** (Match CPV / Adjacent CPV / Terms / Adjacent terms) — the engine produces `match_source`, `cpv_codes`, `matched_terms`, *not* a numeric score or signal breakdown. Treat the % as a **derived tier** from `match_source` (cpv/both = High, keyword = Medium/candidate, none = Low) unless/until a real scorer is added. The signal panel is design-vision; keep it visually but back it with what the engine actually has.
- **Country flag emoji** — UI affordance derived from `country`.

---

## 7. Design system (as built in the mockup)

- **Theme:** dark navy/charcoal (`#0f1623` page, `#151d2c` cards, `#1a2334`/`#222e44` borders). Primary accent **electric teal `#2EE6D4`**.
- **Type:** Public Sans (UI), IBM Plex Mono (codes/dates/IDs). Dense-but-readable — the user is a non-technical procurement analyst scanning many rows.
- **Match-tag colours (keep consistent everywhere):** Both = purple `#c084fc`, CPV = green `#34d399`, Keyword = blue `#60a5fa`.
- **Status colours:** live = teal/green dot, paused = amber `#e3b341`, planned = muted grey `#4c5a70`.
- **Review status colours:** new = grey outline, shortlisted = green, reviewed = amber, dismissed = red `#f87171`.
- **Under-construction treatment:** amber 🚧 badge + diagonal-stripe overlay + "Coming soon — in active development". Per-app accent: Scout = teal, Vault = blue, Composer = purple.
- Cards with subtle borders; compact spacing; the feed table is the centre of gravity.

---

## 8. Guided tour of the mockup (`Tenderizer.dc.html`)

Open it and follow along. The **app switcher** sits under the logo (top-left); the left nav changes per app.

**Global chrome.** Left sidebar: app switcher → per-app menu → "Scout engine online" status pill at the foot. Top bar: search (tenders / CPV / buyers), "Synced HH:MM", **Run now**, user avatar.

### Portal (home workspace — the default landing, built)
- **Home** — welcome header; **app cards** to launch Scout / Vault / Composer; **deadline alert banners** (red = closing ≤7 days with no tender sent, amber = ≤14 days); an **Accepted tenders** snapshot (deadline + submission status).
- **Pipeline & Deadlines** — master/detail over accepted tenders: per-tender **submission status** (Not started / Drafting / Tender sent), **Amend deadline** (records a granted extension — shows the new date + an "extended" badge), a **notes** textarea, owner + value. The detail shows a red/amber banner when a tender is closing without a submission.
- **Follow-up & Results** — submitted tenders with **outcome** (pending / won / lost), Mark won / Mark lost actions, and a **win rate**.

### Scout (the build target — mostly real)
- **Dashboard** *(default, fully real)* — title + subtitle; last-run strip (Last sync · notices scanned · matched · Next run · Run now); 4 KPI cards (New today · **Portals active 2/4** · Matched by CPV · Keyword only); **Tender Feed** panel (LIVE badge, top-N matched, "Open" → notice url); **Portal Health** panel (TED/BOAMP live, e-Procurement *planned*, DTVP *paused* with ToS caveat). *Build first — fastest, all read-only.*
- **Tender Feed** *(real)* — full filterable table over `/api/tenders` (filters: portal, country, match type, search; sort by deadline; Supply-first default). **Slimmed to Title · Portal · Deadline · Match** — Country + CPV live in the detail/slide-over so rows stay scannable.
- **Review Queue** *(real, needs §5 `status`)* — left list (status dot + confidence bar) + right detail (core elements, confidence/signals, triage). Actions PATCH `status`. Sidebar badge = `new` count.
- **Portals & Health** *(🚧 phase 2)* — per-portal throughput + ToS compliance; placeholder over a stripe overlay.
- **CPV Config** *(🚧 phase 2)* — searchable table of the 22 codes, 4-language labels, toggle active, "+ Add CPV" (writes `cpv.yaml`, validated).
- **Keywords** *(🚧 phase 2)* — multilingual library (EN/FR/NL/DE) + distinctive subset; edit with care (affects live queries).
- **Reports** *(🚧 phase 2)* — weekly digest email preview + "Export to Excel" (`reports/tenders.xlsx`).
- **Settings** *(🚧 phase 2)* — schedule, run window, notifications.
- **Tender Detail slide-over** *(🚧)* — slide-in panel: metadata, highlighted matched CPV, "Flag for follow-up".

### Composer (phase-2 preview)
- **Ingest & Config** — drop tender documents; Composer parses them and lists **interpreted requirements** (extracted value, source reference, confidence); the responsible person **validates / flags** each, and **draft generation stays locked until all are validated**. This is the gate into Proposals.
- **Proposals** — per-requirement source matching (Vault), section list, responsible-role assignment, .docx export.

### Vault (phase-2 preview) — see §9.

---

## 9. Scope & phasing

**Phase 1 (this build):** engine additions (§5, incl. the §5.4 Portal store) → API (§4) → **Portal** Home / Pipeline / Follow-up → Scout Dashboard → Tender Feed → Review Queue → CPV Config / Keywords (these *write*) → Reports / Settings. Everything marked 🚧 stays stubbed exactly as the mockup shows.

**Recommended build order (sequence these explicitly):**
1. **Engine additions §5 first — including the §5.4 Portal workflow store.** Build the `pipeline` table and its accessors up front, *not* later. The Portal (the default landing screen) is dead without it, and retrofitting a second table after the API is wired is more churn than doing it now. Keep it in its own table; `pytest -q` must stay green (52 tests).
2. **API §4** — Scout read endpoints + the Portal `pipeline`/`followup` endpoints together (they share the new store).
3. **Portal Home / Pipeline / Follow-up** — the landing experience; depends on step 1's store.
4. **Scout Dashboard → Tender Feed → Review Queue** — read-only first, then the `status` triage.
5. **CPV Config / Keywords** (these *write* YAML) → **Reports / Settings**.
6. Leave all 🚧 screens stubbed.

> Rationale for putting the Portal store in step 1: it's the only *new* persistence Phase 1 introduces, every Portal screen and the home deadline-alert banners read from it, and isolating it in its own table means the engine's `tenders` schema and its tests are never touched. Factor it in now, not as a later migration.

**Phase 2 (vision — do NOT build now; included for context):**
- **Vault** — ingest a library of technical documents (datasheets, drawings, certificates); extract structured metadata (material, water column, fire rating, linked CPV codes) that Scout and Composer reuse. Mockup: "Library" screen, blue accent, fully Under Construction.
- **Composer** — an **Ingest & Config** step reads the tender documents, extracts requirements, and requires the responsible person to **validate each** before draft generation unlocks; it then drafts a proposal from predefined structures + Vault's metadata for that person to edit (per-requirement source matching, section list, .docx export). Mockup: "Ingest & Config" + "Proposals" screens, purple accent, Under Construction.

Keep both in the navigation/app-switcher as preview-only so the product vision is legible, but treat them as out of scope for the current Claude Code build.

---

## 10. Deployment

> The user is handling hosting (Vercel) themselves and will sort the topology with Claude Code. One honest constraint to surface so it isn't a late surprise: **the engine is local Python + a file-based SQLite `tenders.db`, scheduled by Windows Task Scheduler.** A serverless host (Vercel) can serve the frontend and can't run a long-lived scheduler or hold a writable local SQLite file across invocations. The frontend is a clean static deploy; the FastAPI + engine + DB need a stateful host (a small VM/container, or a managed Postgres if the DB is ever migrated). The `POST /api/run` background task assumes a persistent process. Decide this split before wiring the frontend's API base URL.

---

## 11. Guardrails — what NOT to do

- **Do not rebuild the engine** — no re-implementing fetch/normalise/match/dedup/report. Call `run.run_pipeline`, read via `store.all_records`, config via `config.*`.
- **Do not change the record schema** except the additive `status` column. Tests enforce it.
- **Do not put matching/relevance logic in the frontend or API** — confidence tiers come from `match_source`.
- **Do not hard-code tender data** — everything comes from `tenders.db`. (The mockup's rows are placeholders.)
- **Keep the 52 tests green** — `pytest -q` after any engine touch.
- **Respect DTVP do-not-scrape** — Germany stays paused; UI shows the caveat; do not wire a DTVP scraper.
- **Don't hand-build notice links** — use the engine's stored `url` per record.

---

### Deferred refinements (known, non-blocking)
- BOAMP keyword search could scope to the `objet` field to cut noise.
- TED's small `None`-match bucket could be investigated.
- A few TED notices leave `deadline` empty (procedure-dependent) — a fallback field could be added.
- A real numeric relevance scorer (to back the Review Queue's confidence % + signals) is design-vision, not yet in the engine.
