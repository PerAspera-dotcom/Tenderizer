# Change Request 006 — Dismissed-tenders review tab, mandatory dismissal reason, attribution & reinstatement

**For:** Claude Code · **Against:** the Tenderizer repo (Scout + Portal)
**Status:** New feature request
**Priority:** Medium — workflow/auditability improvement, not a deploy blocker

---

## Why

Dismissed tenders currently disappear from the Scout with no way to review them, no record of *why*
they were dismissed, and no record of *who* dismissed them. Reviewers occasionally dismiss a tender
in error, or a dismissal decision needs revisiting once more context arrives — today that decision
is irreversible and unaccountable. This CR makes dismissal a reviewable, attributed, reversible action.

---

## What to build

### D1 · Dismissed-tenders review tab in the Scout
- Add a separate **"Dismissed"** tab to the Scout (alongside the existing New / Review Queue /
  Past Tenders views — match their layout and styling exactly).
- It lists every tender whose appraisal state is `dismissed`, most-recently-dismissed first.
- Each row shows the tender identity (title, buyer, CPV, deadline) plus the dismissal metadata from
  D2/D3: **reason**, **dismissed by**, and **dismissed at** timestamp.
- Reuse the existing tender row/slide-over components; don't fork a new design language.

### D2 · Mandatory dismissal reason
- When a reviewer dismisses a tender, prompt for a **clarification / reason** before the dismissal commits.
- This field is **required** — the dismiss action cannot complete with an empty reason. Enforce
  both client-side (disable confirm until non-empty) **and** server-side (reject the state change to
  `dismissed` if no reason is supplied).
- Store the reason as `dismissal_reason` (TEXT, not null when state = dismissed) on the tender/appraisal record.

### D3 · Attribution — record who dismissed it
- Always store the **account name of the user** who performed the dismissal as `dismissed_by`
  (from the authenticated Clerk/session identity — do not trust a client-supplied name).
- Also store `dismissed_at` (timestamp).
- Display `dismissed_by` on every row in the Dismissed tab.

### D4 · Reinstate / change appraisal state from the Dismissed list
- Each row in the Dismissed tab offers an action to **change the appraisal state** — e.g. from
  `dismissed` back to `shortlisted` (or `new` / whatever the existing appraisal states are).
- Reuse the existing state-transition mechanism (the same one the New/Review views use) — don't
  invent a parallel path.
- When a tender is moved out of `dismissed`, it leaves the Dismissed tab and reappears in the
  appropriate view. Keep the dismissal metadata on the record for history (an audit trail of the
  prior dismissal + who reinstated it, if an audit trail exists per CR-004/NEXT tenancy work).

---

## Data model

On the tender/appraisal record (additive):
- `dismissal_reason` — TEXT, required whenever appraisal state is `dismissed`.
- `dismissed_by` — TEXT (account name / user id), set from authenticated identity.
- `dismissed_at` — timestamp.

New columns on an existing populated table → needs a **real Alembic migration** (not the
`create_all` convention), per the deployment notes.

---

## API

- The state-change endpoint that sets `dismissed` must **require** a non-empty `reason` and reject
  the transition otherwise (400).
- It stamps `dismissed_by` from the authenticated user server-side and `dismissed_at = now()`.
- `GET /api/tenders?state=dismissed` (or the existing list endpoint's state filter) returns the
  dismissed set with the three metadata fields for the new tab.
- The reinstate action reuses the existing state-transition endpoint.

---

## Acceptance

- A **"Dismissed"** tab appears in the Scout and lists all dismissed tenders, newest first.
- Attempting to dismiss with an empty reason is blocked in the UI **and** rejected by the API.
- Every dismissed row shows the reason, the account name of who dismissed it, and when.
- From the Dismissed tab a reviewer can change a tender's state (e.g. dismissed → shortlisted); it
  then leaves the tab and appears in the correct view.
- Backend suite stays green (487 at last count) with new tests covering: empty-reason rejection,
  attribution capture, dismissed-list filtering, and reinstatement.
- Frontend typechecks + lints clean.

---

## Constraints

- Match the existing Scout tab layout/styling — this is a fourth tab, not a redesign.
- Don't trust client-supplied user identity; take `dismissed_by` from the session.
- Don't rebuild the scraping engine or the state-transition machinery — extend them.
- Naming stays **Tenderizer** (see `CLAUDE.md`).
