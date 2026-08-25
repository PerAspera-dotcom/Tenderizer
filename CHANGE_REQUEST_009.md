# Change Request 009 — Precedent-based promotion, Odoo project handoff

**For:** Claude Code · **Against:** the Tenderizer repo (Scout + Portal)
**Status:** Scoping only — not implemented, not approved for build
**Priority:** N/A — reference for a later decision, raised in client feedback session 2
(2026-08-20) as "look into what's possible," not a build request

---

## Why this doc exists

Two ideas came out of the feedback session that weren't specified enough to build:
"positive reinforcement — promote good tenders based on precedent" and "integrate with Odoo to
create a project number on acceptance." The client asked to look into what's *possible*, not to
build anything. This CR captures that investigation — grounded in what already exists in the
codebase, not speculation — so a future session can pick a direction without re-deriving the
context. Nothing here should be built until the open questions below are answered and the client
picks a direction.

---

## A · Precedent-based promotion

### What already exists (this is closer to done than the original ask suggested)

CR-007 Phase B already built most of this. `src/relevance.py`'s `score_relevance` computes a
0–100 score + plain-English reasoning (e.g. "Higher — 8 similar tenders accepted") for every
undecided tender (`status` "new"/"needs_review"), based on CPV-code / matched-keyword / category
overlap against the org's own decision history. `api._attach_relevance` wires it into every tender
the Review Queue returns; a reviewer can override a score (`tender_relevance_overrides`, org-shared).
Confirmed live in production during CR-008 verification (2026-08-22) — this is not a proposal, it's
shipped and working today.

The **positive pool is `tenders.status == 'shortlisted'`** and the **negative pool is
`status == 'dismissed_final'`** (`api._attach_relevance`, `src/relevance.py:60-61`) — a reviewer's
in-Scout triage decision, not an actual contract outcome.

### The actual gap

"Promote good tenders based on precedent" most plausibly means something the current signal
doesn't capture: an *actual win*, not just a decision to pursue. `pipeline.outcome` already exists
(`src/schema.py:140`, values `pending`/`won`/`lost`, set manually in Portal → Follow-up,
`PortalFollowup.tsx`) and is never read by `relevance.py` today.

### Options (increasing scope)

1. **Weight wins/losses into the existing engine.** Add `pipeline.outcome == 'won'` tenders to
   `relevance.py`'s positive pool (and `'lost'` to negative) alongside shortlisted/dismissed_final
   — plausibly weighted higher, since a win is a stronger signal than a shortlist. Backend-only,
   no schema change, extends `_attach_relevance`'s existing pool-building query to join `pipeline`.
2. **Buyer-repeat-business signal.** A tender from a buyer the org has *won with before* is a
   qualitatively different, higher-confidence precedent than CPV overlap. Add "same buyer as a
   past `outcome='won'` pipeline entry" as a new signal in `relevance.is_similar` (or a distinct
   badge on the row, separate from the score) — the join is `tenders.buyer` against past pipeline
   wins, no new data collection needed.
3. **Make it a promotion, not just a number.** Today the score is a percentage bar + reasoning
   text in the tender detail view — informative, not literally "promoting" anything. If the client
   means visible ranking: add sort-by-relevance-score to the Review Queue (same `SortBy` pattern
   CR-008 W4 added for "Decision"), or a "Recommended" filter pill above some score threshold.

### Open question — client must answer before this is buildable

Has the client actually seen the existing relevance score/reasoning in the Review Queue detail
view? It's possible this ask predates awareness that it exists, in which case the real next step
is walking them through the current feature and asking what's still missing, rather than guessing
at #1–#3 blind.

---

## B · Odoo integration on acceptance

### Current state

No Odoo code, config, dependency, or credential exists anywhere in this repo — confirmed via
search. This is greenfield, not an extension of something partially built.

### What "accepted" could mean — two real candidates, genuinely ambiguous

- `tenders.status == 'shortlisted'` — the Scout decision to pursue. Fires earlier, more often
  (every shortlist, whether or not it's ultimately won).
- `pipeline.outcome == 'won'` — the contract is actually won (`PortalFollowup.tsx`). Fires later,
  far less often, but "create a project" makes more literal sense once there's a contract to
  execute, not just a candidate being pursued.

These aren't interchangeable — picking wrong means either spamming Odoo with projects for tenders
that go nowhere, or never creating one until a submission has already been made and possibly
executed. **The client has to pick.**

### What needs answering before this is a buildable CR

1. **Odoo deployment**: Odoo.com SaaS or self-hosted — changes nothing about the API shape (both
   speak the same XML-RPC/JSON-RPC external API) but determines the base URL and auth flow.
2. **Auth**: an API key (Odoo 17+ supports API keys directly; older versions need a
   username/password pair) — and where it lives. This repo's convention is Railway env vars, never
   committed (see `DEPLOYMENT.md`); a fresh secret, not reused from anywhere existing.
3. **Target model**: almost certainly `project.project` (Odoo's stock Projects app) via
   `execute_kw(db, uid, key, 'project.project', 'create', [...])`, but which fields matter is the
   Odoo owner's call — likely `name` (tender title), `partner_id` (buyer — needs a matching/lookup
   or create-on-the-fly Odoo contact), and some custom reference field for the TED/BOAMP
   `pub_number` so the two systems can be cross-referenced later. If the Odoo instance has a
   customized project template or required fields, that changes the create payload.
4. **Failure handling**: what happens when the Odoo call fails (network, auth, validation)? Given
   this codebase's existing pattern (`_send_forward_email` etc. are fire-and-forget background
   tasks that never block the triggering action), the likely shape is: don't block the
   shortlist/win action on Odoo being reachable, but the client should confirm whether a failed
   sync needs to be visibly flagged/retried rather than silently dropped.

### Shape once those are answered (not a commitment, just what fits existing patterns)

- A new `pipeline.odoo_project_number` column (or similar), nullable, populated once the sync
  succeeds — displayed back in Portal so the client can see the linkage.
- A background task triggered from whichever endpoint owns the "accepted" transition
  (`api.patch_tender` if shortlist-triggered, or the follow-up outcome endpoint if win-triggered),
  same fire-and-forget shape as `_send_forward_email`/`_send_needs_review_ping_email`.
- A `pipeline_history` entry recording the sync (mirrors the existing audit-trail convention for
  every other pipeline field).
- New Alembic migration for the column (altering an existing populated table, not a fresh one —
  per this repo's `create_all` vs. real-migration convention).

### Recommended next step

A short conversation with whoever owns the Odoo instance — the blocking unknowns here are
operational (which Odoo, what auth, what fields, what template) not technical. Not worth writing a
buildable CR until those are answered; the technical shape is otherwise straightforward and low-risk.

---

## Acceptance

N/A — this document is scoping only. It becomes actionable once:
- **A**: the client confirms whether they've seen the existing relevance score, and if not, what's
  still missing after seeing it; then picks from options 1–3 (or a combination).
- **B**: the client (or whoever owns Odoo) answers the four numbered questions above.

At that point, split into a real CR (or two) with concrete acceptance criteria, following this
repo's usual CR-00N format.
