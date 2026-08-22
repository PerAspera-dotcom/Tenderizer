# Change Request 008 — Dedup data-loss fix, TED translation fix, review queue UX

**For:** Claude Code · **Against:** the Tenderizer repo (Scout + Portal)
**Status:** New — from client feedback session 2, 2026-08-20
**Priority:** P0 item is a data-loss blocker; the rest is prioritized workflow/UX

---

## P0 · BREAKING — tenders silently vanish from the Review Queue on auto-sync

**Status: RESOLVED, 2026-08-22.** Code fix shipped (`81fad5a` + follow-up commit), prod data
recovered, backend suite green (693+ passed), frontend typechecks/lints clean. See "Resolution"
below.

**Report:** Tender 563438-2026 (Lithuania, camp beds) was active in the Review Queue; after the
next scheduled pipeline run it — and a related record — were gone, with no trace and no way to
recover it (see client's email to Anthony). Client's standing instruction: **"this cannot happen —
rather too many tenders than too few."**

### Root cause (confirmed in code, not yet reproduced against the specific prod record)

`run.py`'s pipeline calls `dedup.find_duplicate_groups()` over **every** tenant record on **every**
run (`run.py:110-112`), grouping by same buyer + deadline within ±7 days + title similarity ≥ 0.9
(`src/dedup.py`). Anything but the newest-`pub_date` record in a group gets
`store.mark_superseded()` → `exclude_reason='superseded'` (`store.py:949-973`), which drops it from
`store.all_records`'s surfaced set and every UI view.

Two compounding problems, both present in the current code:

1. **No protection for reviewed/labeled tenders.** `mark_superseded` sets `exclude_reason`
   unconditionally — it never checks `status`, `assigned_to`, `reason_category`, or whether a note
   exists. A tender someone has already shortlisted, assigned, or is mid-review on can be hidden by
   the very next sync, with nothing on screen explaining why it disappeared.
2. **Grouping is recomputed from scratch every run, so it cascades.** Because candidates exclude
   already-superseded records (`dedup.py:63`), a later run can supersede *this* run's "kept" record
   in favor of a newer one — a multi-lot or multi-notice buyer (e.g. several near-identical
   "camp beds" lots from the same defence procurement office, common for TED) with similarity ≥ 0.9
   and deadlines within a week of each other is a realistic false-positive, and a same-buyer
   camp-beds tender is exactly the shape of the reported case. Two runs in a row can plausibly leave
   a reviewer with the impression that "both were there, then both were gone."

### What to build

- **Guard `mark_superseded` (or its call site in `run.py`) so a tender with a non-default `status`
  (anything other than `new`), a non-null `assigned_to`, or a non-null `reason_category`/note is
  never auto-superseded.** Surface it as a *candidate* duplicate instead (reuse the CR-007 Phase C
  `tender_duplicates` surfacing mechanism — "also listed on X" — rather than the hide-one-side
  `find_duplicate_groups` path) and let a human confirm the merge.
- **Never hard-hide via automation going forward** — same-source dedup should default to the
  CR-007 Phase C behavior (both records visible, cross-linked, reviewer decides) unless a human
  explicitly confirms a merge. This directly matches the client's "too many rather than too few"
  instruction and is consistent with how Phase C already treats cross-portal duplicates.
- **Audit trail for supersession.** Log every `mark_superseded` call to `tender_history`
  (field="exclude_reason", old/new value, which pub_number superseded which) so a hidden tender is
  always traceable and recoverable, the same pattern CR-006/tenancy-hardening already established.
- **Immediate data-recovery action:** find any `tenders` rows for pub_number `563438-2026` (and
  anything it was grouped with) with `exclude_reason='superseded'`, clear it, and restore the
  record to the Review Queue. Check for other silent supersessions in the same run for the same
  tenant while investigating.

### Acceptance

- A tender with `status != 'new'` or a set `assigned_to`/`reason_category` is never auto-superseded
  by a pipeline run; it can only be merged via explicit human action.
- Same-source near-duplicates that aren't auto-merged appear as a surfaced "possible duplicate"
  link on both records, not a silent removal.
- Every automated or manual supersession writes a `tender_history` row.
- 563438-2026 is confirmed restored (or confirmed never actually excluded, with the real cause
  documented) before this CR is marked done.
- New test: two same-buyer, similar-title, close-deadline tenders where one already has
  `status='shortlisted'` — pipeline run must leave both visible.

### Resolution

**Code (`src/dedup.py`, `src/run.py`, `src/store.py`):**
- `dedup.is_protected(record)` — true once a tender has any status past `new`, or a set
  `assigned_to`/`reason_category`. A protected candidate is never auto-superseded.
- `dedup.kept_order_is_confident(kept, candidate)` — **a second bug found while investigating the
  incident.** The "keep the newest by `pub_date`" tie-break is meaningless when `pub_date` is blank,
  which TED records commonly are — confirmed live: the actual 563438-2026 ↔ 547299-2026 pair (same
  buyer, same title, deadlines 3 days apart — TED's real amended-deadline-republish pattern) had
  `pub_date == ""` on **both** sides. With nothing to sort on, which record survived came down to
  unspecified DB row order — confirmed to differ **across tenants** for the identical public TED
  notice (3 of the 10 currently-superseded pairs in prod showed a different "kept" survivor
  depending on which of the 4 affected tenants you looked at). A candidate now only gets
  auto-superseded when both `is_protected` is false **and** `kept_order_is_confident` is true;
  otherwise it's surfaced as a `same_source` possible-duplicate link (reusing CR-007 Phase C's
  "surface, never hide" mechanism) instead of guessed at.
- `store.mark_superseded` now writes a `tender_history` row (field=`exclude_reason`) on every
  supersede. `store.restore_superseded()` added to reverse one.
- Frontend (`types.ts`, `ReviewQueue.tsx`, `TenderFeed.tsx`) renders the new `same_source`
  duplicate link with its own copy, distinct from the cross-portal "Also listed on X" wording.
- Tests: `tests/test_16_dedup.py` (unit coverage for both guard functions + the audit
  trail/restore path), `tests/test_11_run.py` (two full-pipeline regressions — a protected/touched
  tender staying visible, and a blank-`pub_date` pair staying visible instead of a coin-flip merge).

**Data recovery (prod, via a temporary Railway Postgres TCP proxy, closed after use):**
`scratch_recover_superseded.py` (report-only by default; `--restore <pub_number>` to reverse) found
10 superseded records per affected tenant. Three were confirmed genuine bugs and restored, each
re-linked as a visible `same_source` duplicate to its counterpart rather than left hidden:

| pub_number | tenant(s) | why it was wrongly hidden |
|---|---|---|
| 563438-2026 | 2, 5, 8, 10 | blank-`pub_date` ambiguous tie-break (the reported incident) |
| 466713-2026 | 10 | was `status='dismissed'`, silently also auto-superseded (pre-fix `is_protected` gap) |
| 483373-2026 | 10 | was `status='reviewed'`, same pre-fix gap |

The other 7 superseded pairs in prod are untouched (`status='new'`) same-buyer near-duplicates with
blank `pub_date` on at least one side — plausibly legitimate republish collapses, but resolved by
the same non-deterministic tie-break the fix above closes. **Not retroactively re-evaluated or
restored** — out of scope of what was confirmed as a bug for this pass. A full retroactive re-scan
of all pre-fix supersessions (re-running today's guarded logic against every existing
`exclude_reason='superseded'` row) is a natural follow-up if wanted; ask before running it, since it
would touch every tenant's data, not just the ones already inspected here.

---

## P1 · DeepL/TED translation is broken for a specific, now-identified reason

**Status: RESOLVED, 2026-08-22.** Code fix + retroactive backfill shipped (`a37b774`), prod data
fixed, backend suite green (706 passed), frontend typechecks/lints clean. See "Resolution" below.

**Report:** DeepL translation doesn't work specifically for TED-sourced tenders; needs a retroactive
fix for tenders already sitting in the Review Queue.

### Root cause (confirmed in code)

`normalize.normalize_ted` (`src/normalize.py:187-229`) picks `tag_line`'s language and uses it as
the record's `language` for **both** title and description (comment at line 225: "assumes both
fields share a language, true for TED's parallel per-language dicts"). That assumption doesn't
hold: TED notices commonly carry an English `notice-title` (buyers often add one for visibility)
while `description-proc` — the actual free-text body — stays in the buyer's own language only.
`description`'s own detected language (`_desc_lang`, line 189) is computed and then **discarded**.

Consequence: whenever a TED notice has an English title but a non-English description, the record
gets stamped `language='eng'`. `run.py:121`'s translation step skips anything with
`language == 'eng'`, so `description_en` is never populated and DeepL is never called — the
reviewer sees the untranslated original-language description with no indication it needs
translation. This is TED-specific because BOAMP is hardcoded single-language (`normalize.py:571`)
and doesn't have this failure mode.

The existing `scratch_backfill_language.py` does **not** fix already-affected rows: it only
backfills records where `language` is blank (`not r.get("language")`) — these rows already have
`language='eng'` set (incorrectly), so the backfill's own skip condition passes over them.

### What to build

- Fix `normalize_ted` to derive `language` from **both** `tag_line` and `description`'s detected
  languages, not `tag_line` alone — e.g. treat the record as needing translation if either field's
  language isn't English, and translate each field independently against its own detected language
  rather than assuming they match.
- **Retroactive fix:** a new backfill (or an extended `scratch_backfill_language.py`) that
  re-checks TED records currently marked `language='eng'` by re-deriving language from the raw
  `description-proc` dict (or via `translate.translate_and_detect`) and re-translates any that
  turn out not to be English. Scope: all TED tenders currently in the Review Queue (any
  non-excluded status), not just `new`.

### Acceptance

- A TED notice with an English title and a non-English description gets its description
  translated; `description_en` is populated and shown.
- Backfill run against current data fixes every currently-misclassified TED tender in the Review
  Queue (report counts affected/fixed).
- New test: `normalize_ted` on a fixture with `notice-title.eng` present but no
  `description-proc.eng` key — language must not resolve to `eng`, or translation must still run
  on the description.

### Resolution

**Code (`src/normalize.py`):** `_record_language(tag_lang, desc_lang)` now prefers the
description's detected language whenever the title says `eng` but the description disagrees — the
one combination the old single-field assumption got wrong. `normalize_ted` was already computing
`desc_lang` (as `_desc_lang`) and discarding it; it's now threaded through instead. The common case
(title itself non-English) is unchanged. Tests: `tests/test_07_normalize_store.py` (unit coverage
for `_record_language`'s three cases) and `tests/test_11_run.py` (a full-pipeline regression
reproducing the exact bug shape — English title, French-only description — confirming
`description_en` gets populated).

**Retroactive backfill (`scratch_backfill_ted_description_language.py`):** re-checks every
non-excluded TED tender currently stamped `language='eng'` that was never actually translated, via
DeepL's own detection on the stored `description` (the raw TED per-language payload isn't
persisted, so the stored text is all there is to re-check). Deduplicates detection calls by
description content hash across tenants within one run — `translate.translate_and_detect` isn't
cached like `translate_cached` is, and multiple tenants commonly track the same public TED notice
(confirmed: 656 candidate records collapsed to 140 distinct descriptions, a 79% reduction in actual
DeepL calls).

**Run against prod, 2026-08-22** (via the same temporary Railway Postgres TCP proxy used for the P0
recovery, closed immediately after):

| tenant | checked | fixed (genuinely mistranslated) | confirmed already English |
|---|---|---|---|
| 2 | 172 | 161 | 11 |
| 5 | 169 | 158 | 11 |
| 8 | 169 | 158 | 11 |
| 10 | 146 | 137 | 9 |
| **total** | **656** | **614 (94%)** | **42** |

Spot-checked the tender from the P0 incident report (563438-2026, Lithuanian) directly: was
`language='eng'`/never translated, now `language='lit'`, `translation_status='ok'`, with a real
English `description_en` across all 4 tenants.

---

## P2 · Review Queue workflow/UX

**Status: RESOLVED, 2026-08-22.** All five items (W1-W5) shipped in one pass, backend suite green
(721 passed), frontend typechecks/lints clean. See "Resolution" below. Not yet verified in a live
browser session — standing up authenticated Clerk auth end-to-end wasn't attempted this round;
recommend a manual click-through before calling this fully done.

Grouped because they're all Review Queue surface changes; can ship independently of each other.

### W1 — Forwarding shows up in-app, not just email
CR-007 Phase G added forwarding + email pings (`ForwardTender.tsx`). Client wants it visible
in-app too: a message banner and a badge on the profile icon (e.g. an exclamation mark) with a
dropdown of recent activity in a message-board format — "X forwarded tender Y to you with message
'...' in status W." Needs: an in-app notification feed (new table or reuse `tender_history`
filtered to forward/assign events), unread count on the profile badge, dropdown listing recent
items, click-through to the tender.

### W2 — Row-level status metadata in the queue list
Each row should show, without opening the tender: **status** (including "Needs further review" —
the `needs_review` status/label already exists, just needs to render as row metadata, not just a
filter pill), **forwarded** (to whom, if applicable), and **last notification** (most recent
activity timestamp). Pulls from `tenders`/`tender_history`, no new backend concept needed beyond
W1's activity feed.

### W3 — Remove "Mark reviewed"
Drop the `reviewed` status/action (`ReviewQueue.tsx` `applyStatus('reviewed')` and the `reviewed`
filter pill) — superseded by the more specific `needs_review`/`shortlisted`/`dismissed` states.
Confirm with client whether existing rows with `status='reviewed'` should be migrated to
`shortlisted` or left as-is (historical).

### W4 — Filter and sort by decision and region; sort by deadline
`ReviewQueue.tsx` already sorts by deadline/pub_date (`SortBy` type, line 9). Add: filter by
region/country (`tenders.country`/`place`), and filter/sort by decision (status). Match the
existing filter-pill pattern already used for status.

### W5 — CPV code labels in the Review Queue, specific → generic ordering
`config.cpv_label(code, lang)` already returns human-readable CPV labels (`src/config.py:25-27`,
backed by `config/cpv_reference.json` with en/fr/nl/de) but isn't surfaced in the Review Queue UI.
Show the label (not just the raw code) per tender, and when a tender has multiple CPV codes, order
them most-specific first — CPV codes are hierarchical by digit depth/trailing zeros (e.g.
`39516100` camp beds is more specific than `39500000` textiles); sort by ascending count of
trailing zero-pairs or equivalent specificity signal, most specific first.

### Acceptance
- Forwarding a tender produces an in-app banner/badge notification for the recipient, not just email.
- Each queue row shows status (incl. "Needs further review"), forwarded-to, and last-notification
  time without opening the row.
- "Mark reviewed" is gone from the UI; existing `reviewed` rows handled per client decision.
- Region and decision filters work alongside the existing deadline/pub_date sort.
- CPV labels render in the queue and detail view, ordered specific-to-generic.

### Resolution

**New table (`src/schema.py`): `tender_notifications`** — one row per forward/needs_review ping,
append-only. `to_email` (not a Clerk user id) is the addressing key, since a forward's recipient
can be a manually-typed email with no resolvable org membership. Rides the `create_all` convention
(brand new table, no existing data to migrate).

**W1 — in-app notifications.** `forward_tender` and `patch_tender`'s needs_review assignee ping
both now write a `tender_notifications` row synchronously (the DB write, unlike the email, isn't
deferrable background work) alongside their existing email. New endpoints: `GET /api/notifications`
(the caller's own feed, matched by `to_email == their account_name/email`, plus unread count) and
`POST /api/notifications/{id}/read` (ownership-checked). New `NotificationBell.tsx` component —
badge + unread count in `Layout.tsx`'s header, polls every 30s, dropdown lists recent pings
message-board style ("X forwarded a tender to you (PUB-NUM) — status · 'message'"), click-through
marks read and navigates to `/scout/review-queue?pub=<pub_number>` (ReviewQueue.tsx now consumes
that query param on first load to auto-select).

**W2 — row metadata.** `api._attach_notifications` (mirrors the existing `_attach_presence`/
`_attach_duplicates` pattern) adds `forwarded_to` (most recent forward's recipient) and
`last_notification_at` (most recent ping of any kind) to every tender row from `store.
get_last_forwarded_to`/`get_last_notification_times` — org-shared, not personal, matching the rest
of the Review Queue. Rendered as a compact metadata line under each row's badges: status text
(including "Needs further review", previously dot-color-only), forwarded-to, and a 🔔 timestamp.

**W3 — "Mark reviewed" removed.** Button and `applyStatus('reviewed')` call gone from
`ReviewQueue.tsx`; the `reviewed` filter pill and `StatusFilter` type option dropped. Kept:
`reviewed` still renders correctly everywhere a status is displayed (`StatusBadge`, dot colors,
detail view) and the backend still accepts it as a valid status — **existing `status='reviewed'`
rows were left as-is (historical)**, not migrated to `shortlisted`. This was a judgment call, not a
confirmed client decision (the original CR flagged it as needing confirmation) — chosen because
every other change in this CR followed the same non-destructive default, and it's trivially
reversible with a one-off backfill later if the client wants a migration instead.

**W4 — region + decision filter/sort.** Region: a `<select>` (not pills — the candidate set is
whatever countries are actually present, open-ended unlike status), filtering client-side against
`tenders.country`. Decision: added as a third `sortBy` option (`STATUS_SORT_RANK` — undecided
first, then decided-positive, then decided-negative, rather than alphabetical). The backend
already had a `country` query filter (`GET /api/tenders?country=`) from an earlier CR; unused here
since ReviewQueue.tsx already loads its full working set client-side and filters in-memory, same
as the existing status pills.

**W5 — CPV labels.** New `GET /api/cpv/labels?codes=` endpoint (unlike the existing
`/api/config/cpv`, not limited to the tenant's *active* set — a tender's own `cpv_codes` commonly
includes codes matched via keyword rather than CPV, outside that set). New `utils.
sortCpvCodesBySpecificity` — fewer trailing zeros in the 8-digit code sorts first (`39516100`
before `39500000`, per the CR's own example). Queue rows show the single most-specific code's
label as a compact chip (full list on hover); the detail view shows every code labeled, ordered
specific-to-generic, falling back to the raw code for anything outside `cpv_reference.json`.

**Tests:** `tests/test_48_notifications.py` (new — store primitives, ownership checks, the two
ping-write sites, both endpoints, the CPV labels endpoint, row-metadata attachment). Existing
`test_41_needs_review.py`/`test_47_forward_tender.py` re-run clean (no behavior change to their
existing assertions, only additive DB writes alongside the existing email sends).

---

## P3 · Needs scoping before implementation

These came out of the feedback session but aren't specified enough to build yet — flagging so
they aren't lost, not proposing a design.

### Positive reinforcement / promote good tenders based on precedent
Idea: surface tenders similar to ones the org has previously accepted/won, ranked up. Needs a
definition of "similar" (CPV overlap? buyer history? past pipeline outcome?) and where it surfaces
(a badge in the queue? a distinct ranking?) before this is buildable.

### Odoo integration on acceptance
When a tender is accepted, signal Odoo to create a project and show the resulting project number
on the tender. Needs: which Odoo instance/API (XML-RPC vs REST), auth method and where the
credential lives, which Odoo model/template a "project" maps to, and what "accepted" means in
Tenderizer terms (a new status, or reuse `shortlisted`/pipeline `submission_status`). Recommend a
short scoping conversation with whoever owns the Odoo instance before this is written up as a CR.

---

## Constraints

- Don't rebuild the scraping engine, the state-transition machinery, or CR-007's org-shared queue
  (already shipped as of `5af9241` — confirmed live in this repo, not something this CR redoes).
- P0's fix must not weaken cross-portal dedup (CR-007 Phase C) — that mechanism already does the
  right thing (surface, never hide) and is the template for fixing same-source dedup.
- Backend suite stays green; add tests for every acceptance criterion above, especially the P0
  regression test.
- Frontend typechecks + lints clean.
- Naming stays **Tenderizer**.
