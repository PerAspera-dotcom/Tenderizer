# CR-017 §4: Screen inventory and navigation proposal

**Status:** Proposal only; nothing changed. Written 2026-11-22.
**Fixed constraint (CR-017 §4):** Vault, Composer, the translated-documents library and Pipeline all stay. None of the proposals below removes, deletes or flags any of them out, or drops their tables, routes or pages. Their zero row counts mean "not yet", not "unwanted".
**Sources:**
- The navigation as built: `frontend/src/components/Layout.tsx` and `frontend/src/App.tsx`, on branch `cr-017-review-queue`.
- The CR-016 audit snapshot: `fe.txt` and `routes.txt` from the CR-016 session (103 API routes).
- The CR-016 measurements: `docs/cr-016/cr016_prod_output.tsv`.

There is no view tracking, so "used" below means "left rows in the database". A page that only displays data can't show use either way, and is marked *not measurable*.

## How the work actually happens

Each week about 10 tenders surface, and two reviewers handle them in about two sittings.
- **Review Queue:** 319 presence heartbeats from 3 users.
- **Decisions so far (surfaced tenders):** 15 shortlisted, 149 soft-dismissed, 27 final-dismissed, 3 needs review. 57 are still new, of which 14 are a live backlog.
- **After a shortlist:** Pipeline lists all shortlisted tenders, but only 1 pipeline row has ever been edited. Vault and Composer hold 0 rows, and the translation library holds 3 documents.

So one screen carries the whole workload, and everything else is either downstream of a shortlist or configuration.

## Screen inventory

The app has 22 destinations, spread over 5 areas that an app switcher toggles between: **Portal** (the default landing page), **Scout**, **Vault**, **Composer** and **Translate**.

| Area | Destination | What it is for | What the data says |
|---|---|---|---|
| Portal | Calendar (landing page) | Deadlines of all tenders plus pipeline items, on a calendar | not measurable; it's where every login lands |
| Portal | Pipeline & Deadlines | Shortlisted tenders: status, owner, notes, documents; **entry point to Composer** ("Draft proposal in Composer") | 16 shortlisted listed; 1 pipeline row ever edited; 1 history row |
| Portal | Follow-up & Results | Outcome after submission | 1 pipeline row, outcome *pending*; effectively unused |
| Scout | Dashboard | Run stats, health, the 5 nearest deadlines | not measurable |
| Scout | Tender Feed | The same list as the Review Queue, read-only, with filters | not measurable; duplicates the queue's data with no actions |
| Scout | Past Tenders | Award notices (market history) | 1,711 past_tender notices, 232 surfaced |
| Scout | **Review Queue** | Triage: shortlist / needs review / dismiss | **the workload** (see above) |
| Scout | Dismissed | Final-dismissed tenders, reinstate | 27 final, against 149 soft-dismissed that never got there |
| Scout | Add Tender | Manual entry (CR-013) | 1 manual tender, 4 drafts (1 confirmed, 3 discarded) |
| Scout | Portals & Health | Source on/off switches, run health | configuration; health covers 10 sources |
| Scout | Matching Config | CPV, keywords, dedup, deadline window, screening questions (5 tabs) | configuration; dedup and scout settings never changed from defaults |
| Scout | Settings | Run schedule and notification settings | configuration; one row per tenant, still daily / 02:00-06:00 / notifications off |
| Vault | Library | Company documents, searched by Composer | 0 documents (*not yet*) |
| Vault | Metadata Rules | Extraction rules | 0 rows, never configured |
| Vault | Tags | Collections | 0 |
| Vault | Settings | Vault settings | 0 rows, defaults |
| Composer | Ingest & Config | Per-tender session: drop the tender documents | 0 sessions; with no session it says "Open Composer from a shortlisted tender in Portal → Pipeline" |
| Composer | Proposal Review | Per-tender session: requirements and draft | 0 |
| Composer | Gaps Report | Per-tender session: unmet requirements | 0 |
| Composer | Style Guide | House style, examples | 0 |
| Composer | Settings | Composer settings | 0 rows, defaults |
| Translate | Translate | Translate documents with DeepL, "Save to Vault" | 3 documents, 0 attached to a tender |

## Testing CR-017's idea against the code

CR-017 suggested that Vault, Composer and Pipeline aren't destinations: they are what happens after a shortlist, so they should be reached from the shortlisted tender.

**For Pipeline and Composer, it holds, and the code already half-implements it:**
- Pipeline is literally "shortlisted tenders" (`store.get_pipeline_entries`: `status == 'shortlisted'`).
- Composer sessions are per tender and are already opened from Pipeline (`PortalPipeline.tsx`: `navigate('/composer/ingest?pub=…')`).
- Three of Composer's five pages (Ingest, Review, Gaps) only mean something inside one tender's session. Opened from the nav without a session, Ingest just tells the user to go to Pipeline.

**For Vault, it doesn't hold:**
- Vault is the company's own document library: certificates, references, technical sheets. It's filled before any tender exists, and Composer searches it scoped by CPV (`composer.py`, CR-007 F).
- If it could only be reached from a shortlisted tender, nobody could set it up ahead of time.
- It's a library, not a step in the workflow, and it needs a home. The translated-documents library is the same kind of thing, and Translate already saves into Vault.

**The Review Queue never links forward.** After a shortlist, nothing on the card leads to Pipeline. That's the one missing contextual link in the chain "shortlist → pipeline → Composer".

## Proposals

### A. What earns the top level (4 entries instead of 5 areas and 22 links)

1. **Review Queue.** The workload, with the expired-undecided view from CR-017 §1.3 inside it. It should be the landing page instead of Portal Calendar: every session today starts one click away from the work.
2. **Pipeline.** Everything after a shortlist: the shortlisted list, with Calendar and Follow-up & Results as views or tabs of it, and Composer opened per tender from there.
3. **Library.** Vault documents and translated documents together, plus the Translate drop zone as the way in.
4. **Settings.** Every "configure once" page in one place.

The app switcher goes away, because every entry it led to lives in one of these four. Add Tender becomes a button on the Review Queue (it creates a queue item) instead of a nav entry.

### B. Group

- **Pipeline, Calendar and Follow-up & Results** become one entry with three views. They're the same shortlisted set (Calendar adds deadlines of tenders still in review).
- **Vault Library and Translate** become one Library entry. Both are document collections, and Translate already writes into Vault.
- **All configuration into Settings, as sections:** Portals & Health, Matching Config (keeping its 5 tabs), Scout Settings, Vault Metadata Rules / Tags / Settings, and Composer Style Guide / Settings. That's 8 configuration links today, all at the same level as the daily work, and and, apart from the run schedule, none changed since setup (the vault, composer, dedup and scout settings tables hold no rows).

### C. Demote to a sub-view or a contextual link

- **Composer Ingest, Proposal Review and Gaps Report:** reached only from a shortlisted tender in Pipeline, as today's "Draft proposal in Composer" button already does. Out of context they're a dead end.
- **Tender Feed:** becomes a filter of the Review Queue ("all surfaced, including decided") instead of a separate page with the same data and no actions.
- **Dismissed:** becomes a tab or filter of the Review Queue. It's the tail end of the same flow, reachable from the queue rather than beside it.
- **Past Tenders:** becomes a tab of the queue's browse views. It's market history, consulted occasionally (1,711 notices).
- **Dashboard:** its run health belongs with Portals & Health. Its "5 nearest deadlines" is what the Review Queue's deadline-first default (CR-017 §1.2) now shows by itself.
- **New contextual link:** "In Pipeline →" on a shortlisted tender's card in the Review Queue.

### D. Genuinely dead: the only removal candidates

For each, whether it's unwanted or merely undiscoverable, because those need opposite responses:

| Item | Evidence | Verdict |
|---|---|---|
| Relevance-correction control ("Correct this" on the card) | 0 corrections ever; it's visible on every undecided tender's card | **Unwanted.** Discoverable and ignored. Remove the control; keep `tender_relevance_overrides` until someone decides about the data. |
| `reviewed` status | 0 tenders; no UI action has set it since CR-008 W3. Still rendered in 4 places (NotificationBell, PortalCalendar, DismissedTenders, ReviewQueue) | **Unwanted.** It was replaced by needs_review / shortlisted / dismissed. |
| `sort` parameter on `/api/tenders` | accepted and ignored | **Already removed** in CR-017 §1.1. |
| `POST /api/config/rescore` | 0 references in the frontend (unchanged since CR-016 F15) | **Undiscoverable.** It's an operator action; if wanted, it belongs as a button under Settings → Matching, otherwise remove it. Ask. |
| `POST /api/vault/validate-metadata` | 0 references in the frontend | **Undiscoverable,** and it belongs to Vault, which stays. Keep it, but wire it to Vault Metadata Rules when Vault goes live. Not a removal. |
| `Routes` component in `router.tsx` | never mounted (App.tsx has its own route table); its fallback redirects to `/portal/home`, a route that doesn't exist | **Dead code.** Remove. |

### E. Not navigation, but it bears on how concise the queue can be

- **The two-stage dismiss.** 149 soft-dismissed against 27 final among surfaced tenders. Most dismissals never reach the Dismissed tab, and soft-dismissed rows stay in the queue greyed out.
  - CR-016 #5 proposes collapsing to one stage, and these numbers support it: the second stage mostly isn't done.
  - If it is collapsed, the Dismissed tab (proposal C) becomes the only place dismissed tenders live, and the queue shows only undecided ones.
- **Screening (0 answers)** lives inside the card already, so it adds no navigation. CR-016 #9 (auto-screen at ingest, or trim) still stands as its own decision.

## Suggested order, if adopted

1. Make the Review Queue the landing page and add the "In Pipeline →" link. This is small, has no structural change, and is where every session starts.
2. Merge the configuration links into Settings. This is purely navigational.
3. Group Pipeline / Calendar / Follow-up, and make the Composer pages contextual.
4. Library: Vault plus Translate.
5. Remove the dead items in D, one at a time, each with its own permission.
