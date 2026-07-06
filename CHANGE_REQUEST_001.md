# Change Request 001 — Post-customer-meeting refinements

**For:** Claude Code · **Against:** the Tenderizer repo (engine `the_scout/` + `api/` + `web/`)
**Source:** customer meeting notes, 1 Jul 2026
**Read `TENDERIZER_HANDOFF.md` and `CLAUDE_CODE_BUILD.md` before starting.**

---

## How to work this CR (do this, in order)

1. **Read first — don't code yet.** Open the affected modules (matching, keywords/CPV config,
   dedup, the record schema, the review-queue frontend, Composer ingest) and confirm where each
   change lands. Report back a short plan + anything ambiguous (see §Decisions) before editing.
2. **Branch:** `git checkout -b cr-001-meeting-refinements`.
3. **Implement one item at a time**, smallest/safest first (config-list changes before logic before
   new dependencies). Most of these are *engine filtering* changes — additive filters, not schema
   changes.
4. **Test as you go.** After each item: run `pytest -q` in `the_scout` (must stay green — 52+),
   and add/extend a unit test for the new behaviour. Do not batch all changes then test once.
5. **Commit per item** with a message referencing the item ID (e.g. `cr-001 F3: drop container/modular terms`).
6. When all green, summarise what changed, which tests were added, and the count.

**Golden rule:** the 52 existing engine tests must never go red. If a change breaks one, that's a
signal — pause and reconcile, don't delete the test.

---

## A · Filtering & scope (engine — `the_scout` matching stage)

These narrow what Scout surfaces. Implement as a **post-match filter stage** (or extend the existing
match filter) so a notice can be matched, then excluded with a recorded reason — don't silently drop
it. Prefer a single `exclude_reason` field on the record (null = kept) over deleting rows, so the
reason is auditable and testable.

**F1 · Deadline lead-time floor.**
Exclude any tender whose submission deadline is **less than 72 hours in the future** (proposal
lead-time). Compare against run time in the tender's timezone if available, else UTC.
*Acceptance:* a notice due in 48h is excluded with reason `deadline_too_soon`; one due in 96h is kept.
*Test:* fixtures at 48h / 72h / 96h relative to a frozen "now".

**F2 · Drop rental tenders (all languages).**
Exclude tenders whose title/subject matches rental terms: **rental (EN), location (FR), miete (DE),
huur (NL)**. Word-boundary match, case-insensitive, diacritic-insensitive. Keep the term list in
config (`config/exclusions.yaml` or similar) so it's extendable — add IT `noleggio`, PL `wynajem`
as commented candidates for the customer to confirm.
*Watch:* `location` (FR) is a common word — scope it to procurement context or require it near
tent/shelter terms, and add a test that a French tent *purchase* ("acquisition") is NOT excluded.
*Acceptance:* "Location de tentes" excluded (`rental`); "Acquisition de tentes" kept.

**F3 · Exclude container / modular / prefabricated entirely (not just de-list). (customer: hard exclude)**
Two parts:
1. **Remove** "container", "modular", "prefabricated structures" and their translations from the
   keyword list, and remove the corresponding CPV codes from the active set (44211xxx prefabricated
   buildings, 44211100 modular/portable, 34221xxx mobile containers — confirm exact codes against
   `cpv_reference.json`).
2. **Actively exclude** them (like rental in F2): add container / modular / prefabricated + all-
   language translations to the exclusion term list, AND treat their CPV codes as exclusion codes, so
   any notice scoped to containers / modular / prefabricated structures is **dropped with reason
   `container_modular_prefab`** even if it also trips a tent term. These must not surface at all.
*Translations (confirm/extend):* container — conteneur (FR) / container (NL) / Container (DE);
modular — modulaire (FR) / modulair (NL) / modular (DE); prefabricated — préfabriqué (FR) /
geprefabriceerd (NL) / vorgefertigt / Fertig- (DE).
*Acceptance:* a "modular container" notice is excluded (`container_modular_prefab`); a notice that
mentions both "tente" and "préfabriqué" is still excluded; a pure tent notice is kept.
*Test:* fixtures — modular/prefab notice → excluded; tent+prefab notice → excluded; tent-only → kept;
assert the terms/codes are gone from the active match lists too.

**F4 · Filter out construction-work tenders. (D3 ✅ hard exclude)**
Exclude **all** tenders scoped to construction works — CPV division **45 (construction work)** and its
subgroups — as a **hard exclude**, regardless of other signals (customer confirmed). A notice carrying
any 45xxx code is dropped with reason `construction_works`.
*Acceptance:* a 45000000 works notice is excluded (`construction_works`); excluding it does not remove
tent supply notices that carry only tent CPV/terms (no 45xxx code).
*Test:* fixture with a 45xxx code → excluded; a tent-only notice → kept.

**F5 · Narrow scope — drop mechanical / non-tent noise. (D4 ✅ core tent list confirmed)**
Too many mechanical / unrelated tenders surface. Tighten by (a) requiring at least one *core tent/
shelter* signal (CPV or distinctive keyword) rather than only an adjacent/fuzzy term, and (b)
demoting keyword-only matches on generic terms. Do F3/F4/F7 first, then tighten against the
**confirmed core tent/shelter list** (the codes tagged Keep/Add in the CPV Scope Worksheet + their
synced terms from F8 — customer confirmed this set is fine to gate on).
*Acceptance:* sample set from the meeting no longer shows the mechanical tenders; known-good tent
tenders remain. Capture a before/after count in the commit message.

**F6 · Value floor (when value is present).**
When a tender exposes an estimated value, exclude those **below €200,000 (or equivalent)**. If value
is absent, keep the tender (don't exclude on missing data). Non-EUR values must be converted — see
Decision D2.
*Acceptance:* €150k excluded (`below_value_floor`); €250k kept; value-absent kept.
*Test:* fixtures at 150k/200k/250k EUR and one non-EUR value.

**F7 · CPV scope changes — add these codes to the active list.**
Add the following CPV codes to the CPV config (`cpv.yaml` / per-tenant CPV set), confirm each
resolves against `cpv_reference.json` (EN/FR/NL/DE labels), and set match tier consistently with the
other tent/shelter codes:
- `35800000` — Individual and support equipment
- `39511100` — Blankets
- `39522520-8` — Field bedding
- `39522540-1` — Sleeping bags

Also **remove `44210000` (Structures and parts of structures)** here (in addition to the
container/modular/prefab removals in F3) — it drags in construction/mechanical noise. Confirmed keeps
(do NOT remove): `39522100` (awnings/tarpaulins), `35000000` (defence parent).
*Acceptance:* the four codes above are present and match sample notices; `44210000` no longer matches;
config-writer round-trip preserves all changes.
*Test:* extend the CPV config test to assert the added codes are present and `44210000` is absent.

**F8 · Sync keyword terms to the CPV changes (all languages) + terms↔codes consistency check.**
For every CPV code added in F7, add the corresponding **keyword terms in all four languages
(EN/FR/NL/DE)** to `keywords.yaml` — e.g. blankets (EN) / couvertures (FR) / dekens (NL) / Decken (DE);
sleeping bags / sacs de couchage / slaapzakken / Schlafsäcke; field bedding / literie de campagne /
veldbedden / Feldbetten. Then run a **consistency pass**: every active CPV code should have matching
keyword coverage and vice-versa — report any term with no related code, or any code with no term, so
the two lists stay aligned. Remove terms orphaned by F3 (container/modular/prefabricated) in the same
pass.
*Acceptance:* the new codes each have EN/FR/NL/DE terms; the consistency check reports zero orphaned
terms/codes (or lists them for review); removed-term languages are all gone.
*Test:* a config test asserting term/code parity for the changed entries.

---

## B · Deduplication (engine — dedup stage)

**D-DUP · Treat re-published updates as duplicates.**
The list shows the same tender published under different codes — these are **updated versions of the
same tender**, not distinct opportunities (see the two identical "Romania – Tents – Corturi și
Generatoare" rows, both due 10 Jul). Extend the dedup key so near-identical notices collapse to one,
keeping the **most recently published** version and recording the superseded `pub_number`(s).
*Approach:* add a secondary dedup pass beyond exact `pub_number`: match on (same buyer) + (fuzzy-equal
title, e.g. normalized ≥ ~0.9 similarity) + (same or close deadline). Keep latest `publication_date`;
attach `supersedes: [old_pub_numbers]` so the UI can show version history.
*Acceptance:* the two Romania rows collapse to one; the kept record lists the other pub_number under
`supersedes`; two genuinely different tenders from the same buyer are NOT merged.
*Test:* a fixture pair (same buyer/title/deadline, different codes) → 1 record; a control pair
(same buyer, different title) → 2 records. This is the highest-risk change — write the test first.

---

## C · Review Queue (frontend — `web`, review-queue screen)

**R1 · Link to the tender's source page on the overview card.**
The "Open ↗" affordance on each review-queue card must link to the tender's **canonical portal URL**
(TED/BOAMP notice page), opening in a new tab. Ensure the engine persists that URL per notice (it
should already have a source link; if not, capture it in the connector). No dead/placeholder links.
*Acceptance:* clicking Open on a TED row opens that notice on ted.europa.eu in a new tab.

**R2 · De-duplicate CPV codes in the summary card.**
Some results repeat the same 4–5 CPV codes several times, bloating the card. **Dedupe the CPV code
list per tender** before rendering (unique codes only, stable order). This is a display fix but
verify the *stored* codes aren't duplicated either — if they are, dedupe at ingest.
*Acceptance:* a card that previously showed a code 3× shows it once; count of chips = count of
distinct codes.

**R3 · Translate non-English titles/summaries to English.**
In the review queue, tenders whose language ≠ English show an **English translation** of the title
(and short description) with the original available (tooltip or toggle). See Decision D1 for the
translation mechanism — do not hard-block the rest of the CR on it; ship R1/R2 first, then R3.
*Acceptance:* the Romanian "Corturi și Generatoare…" row shows an English title; original preserved.

---

## D · Composer (Vault/Composer — ingest step, Phase 2)

**C1 · Built-in translation on document ingest.**
When Composer ingests tender documents not in English, translate the extracted text to English so
requirement interpretation runs on English. Preserve the source-language original alongside the
translation (Composer already cites source `§`/page — keep those anchors on the original).
*Note:* Phase 2 feature — implement behind the existing Composer scaffolding; shares the translation
mechanism from D1. Don't block Phase 1 on it.

---

## Decisions needed (raise these before/at implementation)

- **D1 · Translation service (R3, C1). ✅ DECIDED — DeepL API.** Use the DeepL API for all
  translation (review-queue titles/summaries + Composer document ingest). Adds an external dependency
  and a per-tenant API key. Cache translations by content hash so the same notice/document isn't
  re-translated on each run. Confirm budget/plan (DeepL Free vs Pro) with the customer.
- **D2 · Currency conversion (F6). ✅ DECIDED — ECB daily snapshot.** Convert non-EUR values to EUR
  using the **European Central Bank daily reference rates** (free). Fetch once per run and cache the
  day's snapshot; do not call per tender. Store the rate/date used so a conversion is reproducible.
- **D3 · Construction exclude strictness (F4). ✅ DECIDED — hard exclude.** Hard-exclude every CPV
  45xxx (construction work) notice, no tent-signal override.
- **D4 · Scope-narrowing allowlist (F5). ✅ DECIDED — core tent list confirmed.** Gate F5 on the
  Keep/Add codes in the CPV Scope Worksheet + their F8-synced terms; customer confirmed this set.
- **D5 · Exact CPV codes for the container/modular/prefab exclusion (F3).** Confirm the specific
  codes against `cpv_reference.json` before wiring them as exclusion codes. Not a blocker — Claude
  Code confirms inline. ⚠ Note: a legitimately "modular tent" notice would also be excluded under this
  rule — customer accepted the hard exclusion.

---

## Status log

- **2026-07-06 — Audit + §5.4 test-coverage gap closed.** Reconciled this
  file's static text against actual repo state (git history + code): F1–F8,
  D-DUP, R1, R2, R3 were all already implemented and committed (`git log
  --oneline | grep cr-001`), each with its own test coverage — this section
  had simply never been kept up to date. Only C1 (Composer ingest
  translation) remains not started, correctly deferred (phase 2, needs D1).
  One process gap found: TENDERIZER_HANDOFF.md §5.4's Portal `pipeline` table
  (`store.ensure_pipeline_entry`/`set_pipeline_entry`/`get_pipeline_entries`/
  `get_followup_entries`, `/api/pipeline`, `/api/followup`) was fully built
  and wired into the frontend's PortalPipeline screen, but had never gotten
  its own unit tests. Added `tests/test_22_pipeline.py` (17 tests: default
  row creation, idempotency, invalid-field guarding, shortlisted/submitted
  scoping, tenant isolation at both the store and API layer) — no behaviour
  changed. `pytest -q`: 243 passed (226 + 17 new). Open question surfaced
  earlier by the F5 commit (`900d341`) still stands: the CR references a
  "CPV Scope Worksheet" that doesn't exist in this repo — F5 mapped "core"
  onto `config.cpv_codes()` + `distinctive_keywords()` instead; worth
  confirming with the customer that matches their intent.

## Suggested order (safest first)

1. R2 (CPV display dedupe) — pure frontend, low risk.
2. F7, F8 (CPV scope + term sync) — additive config + consistency check, low risk.
3. F2, F3 (config-list exclusions) — additive, test-covered.
4. F1, F6 (deadline + value filters) — additive filters.
4. F4, F5 (construction + scope narrowing) — needs D3/D4, higher judgment.
5. D-DUP (version dedup) — highest risk, test-first.
6. R1 (source link) — needs URL persisted.
7. R3, C1 (translation) — needs D1 dependency; do last.

Keep `pytest -q` green after every step.
