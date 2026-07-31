# Change Request 002 — Notice classification, review queue upgrades, portal workflow

**For:** Claude Code · **Against:** the Tenderizer repo (engine `the_scout/` + `api/` + `web/`)
**Source:** live-environment customer feedback, 15 Jul 2026
**Read `TENDERIZER_HANDOFF.md`, `CLAUDE_CODE_BUILD.md`, and `CHANGE_REQUEST_001.md` before starting** —
several items here extend CR-001 work (value floor, translation) rather than duplicate it.

---

## How to work this CR

Same discipline as CR-001: read the affected modules first and report a plan before editing;
branch `cr-002-classification-workflow`; implement smallest/safest first; run `pytest -q` (must
stay green) after every engine-side change and add tests as you go; commit per item.

**Golden rule unchanged:** never let the existing engine test suite go red.

---

## A · Notice-type classification (engine — new additive field)

Add a `notice_type` column to `tenders` (default `"tender"`), populated as an additive
classification pass **after** matching/filtering (CR-001 §A) — it tags what's already kept, it
does not change what gets kept. If a notice matches none of the special patterns below, leave it
`"tender"` — never leave it blank, and never guess a specific type you're not confident of; the
customer's own rule is "if it can't be identified as a specific sort, just 'tender' will do."

**A1 · Past / awarded tenders.** Identifier: **empty deadline field**. These are historical, not
active opportunities. When detected, also attempt to extract `awarded_to` (winning bidder) and
`awarded_value`/`awarded_currency` from the notice body — TED and BOAMP award notices carry these
in fairly standard fields/phrases (e.g. FR "attributaire" / "montant"). Store both as nullable —
don't block classification on a successful extraction.
*Acceptance:* a notice with no deadline is tagged `past_tender`; when the award fields are present
in source text, they populate; when absent, they stay null (never fabricated).

**A2 · Expressions of Interest (EOI).** Identifier: the phrase "expression of interest" or the
acronym "EOI" (word-boundary, case-insensitive). Note the FR equivalent commonly used on EU
portals is "appel à manifestation d'intérêt" — include it so French notices classify too.
*Acceptance:* a notice mentioning either form is tagged `eoi`.

**A3 · Forward Business Opportunities (FBO).** Identifier: "forward business opportunit(y/ies)" or
the acronym "FBO". **Flag before building:** FBO is a legacy US federal-procurement term (the old
system SAM.gov replaced) — confirm with the customer that this actually appears in TED/BOAMP
notices, or whether they mean something else. Don't spend time on the term list until confirmed
(see Decisions).

**A4 · Prequalifications.** Not a tender — a closed/invitation-only selection of candidates for a
later restricted tender. **Identifier: customer's message describing the detection rule was cut
off ("Identified by the following" with nothing after) — needs the actual terms before
implementing.** Proposed defaults to confirm: "prequalification" / "pre-qualification" / "PQQ", FR
"sélection des candidats" / "procédure restreinte — appel à candidatures". Do not implement A4
until confirmed — see Decisions D-A.

**Precedence:** a notice matches at most one type — check in order past_tender → prequalification
→ eoi → fbo → tender, first match wins (past/empty-deadline is the least ambiguous signal, so it
goes first).

---

## B · Past tenders get their own aggregated view (not mixed into active review)

**B1.** `past_tender` notices never enter the Review Queue — they don't need triage. Route them
directly to a **separate "Past Tenders" section**, aggregated the same way the Tender Feed
aggregates active ones (table, filters, counts), but a distinct page/tab — not merged with active
tenders anywhere. Each row shows `awarded_to` and `awarded_value` when present.
*Acceptance:* a past-tender notice never appears in Review Queue or the active Tender Feed count;
it does appear, with award info, on the Past Tenders page.

**B2.** Dashboard KPIs (New today, Matched, etc.) count active tenders only — past tenders don't
inflate them. Add a distinct count/link for Past Tenders if there's room in the KPI row.

---

## C · Review Queue upgrades

**C1 · Type field on the review card.** For `eoi`/`fbo`/`prequalification`, show a small type
badge alongside the existing confidence/match badges. Plain "Tender" (or no badge — designer's
call) when `notice_type = "tender"`.

**C2 · Optional note on dismiss.** Add a note field to the dismiss action (inline or small modal) —
optional, not required to dismiss. Persist as an additive `dismiss_note` column on `tenders` so
it's there later for shortlist learning/audit. No note = null, not empty string.
*Acceptance:* dismissing with a note persists it and it's visible on that record later; dismissing
without one leaves it null.

**C3 · Sort by release date.** Add `publication_date` as a sort option and make it the Review
Queue's default order (newest first). Leave the Tender Feed's own default (deadline, supply-first
per handoff) untouched — this only changes Review Queue.

**C4 · Show estimated budget.** Surface the value field from CR-001 F6 on the review card/detail.
If value is absent for a given notice, omit the line — never show a placeholder like "$0" or "—".

---

## D · Shortlist → Portal pipeline (verify, don't rebuild)

Per the handoff, `status = shortlisted` already routes a tender out of the Review Queue's `new`
list and into Portal Home's "Accepted tenders" + Pipeline & Deadlines (`GET /api/pipeline`). **This
is spec'd, not new** — confirm the round trip actually works end-to-end in the live environment
(shortlist in Review Queue → gone from queue → appears in Pipeline) and close any gap found. Don't
build a second, parallel "shortlist" concept.

---

## E · Document upload after shortlist (Vault/Composer scope — decision needed, not a build yet)

What's being asked for here — uploading a shortlisted tender's documents — is exactly Composer's
**Ingest & Config** screen (`POST /api/composer/ingest`), which the handoff explicitly scopes as
Phase 2 and currently stubs 🚧. Don't quietly build a parallel upload feature. Raise with the
customer: pull forward a minimal slice (upload + store docs against `pub_number`, no requirement
parsing/translation yet) ahead of full Composer, or wait for the Phase 2 scoping pass already
queued for Vault/Composer? See Decision D-C. No code for this item until that's answered.

---

## F · Calendar overview for shortlisted tenders (new)

New Portal screen, **Calendar** — a month/week grid over shortlisted tenders' deadlines
(`deadline_override` if set, else `deadline`) to prioritize response work at a glance. Complements
the existing Pipeline & Deadlines list view; doesn't replace it. Additive nav item under Portal.
*Acceptance:* every tender with `status = shortlisted` appears on its deadline date; amending a
deadline (existing Pipeline action) moves it on the calendar too.

---

## Decisions needed (raise before/at implementation)

- **D-A · Prequalification identifiers (A4).** Customer's detection rule was cut off mid-sentence —
  get the actual terms/acronyms before building A4. Proposed defaults above are a starting point,
  not a substitute for confirming.
- **D-B · FBO — does this term actually occur on TED/BOAMP (A3)?** It's a legacy US
  federal-procurement acronym; confirm the customer means something findable on EU portals before
  spending time on a term list that may never match.
- **D-C · Document upload timing (§E).** Minimal upload-only slice now, or wait for the full
  Vault/Composer Phase 2 scoping conversation already on the roadmap?
- **D-D · Calendar (§F) — new screen or a view toggle on the existing Pipeline & Deadlines page?**
  Either is fine engineering-wise; pick based on nav real estate.

---

## Suggested order (safest first)

1. C3, C4 — sort + budget display on Review Queue; pure frontend/display, uses data that already exists.
2. C2 — dismiss note; additive column + small UI.
3. A1 (past tenders) + B1, B2 — unambiguous signal (empty deadline), no decision blocking it.
4. D — verify shortlist → pipeline round trip; fix if broken.
5. A2 (EOI) + C1 badge wiring — term list is already confident, low risk.
6. F — calendar view, once D-D is answered.
7. A3, A4 (FBO, prequalification) — blocked on D-A/D-B; do once answered.
8. E — document upload; blocked on D-C, do last.

Keep `pytest -q` green after every engine-touching step.
