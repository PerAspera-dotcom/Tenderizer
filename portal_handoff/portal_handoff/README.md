# Tenderizer — Handover Package

This folder is the complete design handover for the Tenderizer portal. Give it to Claude Code as the single source of truth.

## Contents
- **`TENDERIZER_HANDOFF.md`** — the spec. Read it first, top to bottom. It merges the design brief, the engine build brief, a data contract, the API to build, the engine additions needed, a data→UI mapping, the design system, a guided tour of every screen, the build order, and guardrails.
- **`Tenderizer-mockup.html`** — the approved interactive mockup, fully self-contained. Double-click to open in any browser (works offline). Use the app switcher (top-left, under the logo) and the left-nav to walk every screen.
- **`screenshots/`** — stills of the key screens, in case you want quick visual reference without opening the mockup:
  - `01-portal-home.png` — Portal home (launcher + deadline alerts + accepted tenders)
  - `02-portal-pipeline.png` — Pipeline & Deadlines (status, amend deadline, notes)
  - `03-portal-followup.png` — Follow-up & Results (won/lost)
  - `04-scout-dashboard.png` — Scout dashboard (KPIs, feed, portal health)
  - `05-scout-tender-feed.png` — Tender feed (slim: Title · Portal · Deadline · Match)
  - `06-review-queue.png` — Review queue (new/shortlisted/reviewed/dismissed)
  - `07-composer-ingest.png` — Composer Ingest & Config (validate → unlock draft)

## How to use it
1. Drop this whole folder into your engine repo at `the_scout/portal_handoff/`.
2. Open Claude Code in the `the_scout` root.
3. Start with this prompt:

   > Read `portal_handoff/TENDERIZER_HANDOFF.md` in full and open `portal_handoff/Tenderizer-mockup.html` for reference. Then follow the build order in §9 — start with the engine additions in §5 (including the §5.4 Portal workflow store) and keep the 52 tests green with `pytest -q`. Do not rebuild the engine; wrap it.

## Golden rules (also in the spec)
- **Do not rebuild the Scout engine** — call `run.run_pipeline`, read via `store.all_records`, config via `config.*`.
- **Do not change the `tenders` schema** except the additive `status` column. The Portal's workflow state lives in its **own new table**.
- The mockup is a **design reference**, not production code — recreate it in your chosen frontend stack wired to the real API.
- Keep the **52 engine tests green** after any engine touch.
- **2 portals are live** (TED + BOAMP). e-Procurement = planned, DTVP = paused.
