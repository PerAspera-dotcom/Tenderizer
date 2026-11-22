# CR-016 — Scout feature audit

**Measured:** 2026-11-21 15:45 UTC, production Postgres (alembic head `a7c3e9f1b5d2`), via a single
read-only `SELECT` run inside the backend container (`default_transaction_read_only=on`).
**Code read at:** the working tree on the same date.
**Scope:** analysis only. Nothing has been changed except this document.

---

## 0 · Summary

- **The queue is small. The machinery behind it is not.** About 10 tenders reach the Review Queue each
  week. To show the 75 rows it currently holds, the server loads all 35,123 tenders and scores
  relevance on 34,921 of them, on every page load and again after every click.
- **More tenders expire undecided than get shortlisted.** 46 tenders reached the queue and expired
  with no decision. 15 have been shortlisted. The median time to a decision is 7 days, and the
  default sort is newest-first, not deadline.
- **Keywords never produced a shortlist on their own.** No keyword-only tender was ever
  shortlisted (0 of 29 decided). Keywords on top of a CPV match do help: 11% of those were
  shortlisted, against 2% for CPV alone.
- **TED is where the business comes from.** It produced 15 of the 16 shortlisted tenders.
  PhilGEPS is 85% of the database and has produced none.
- **Whole subsystems are still unused.** Vault and Composer have 0 rows in every table, there are
  0 screening answers, 0 inbound alert emails and 0 relevance corrections. The `reviewed` status
  has never been used. Claude has been called once since CR-015.
- **Nothing urgent.** I found no data loss, security problem or correctness bug that needs raising
  ahead of the ranking. Two minor data anomalies are noted in §2.6.

---

## 1 · Measurements (2026-11-21)

### 1.1 Volume and flow

| Metric | Value |
|---|---|
| Tenants | 2: tenant 5 is live; tenant 1 has 0 tenders |
| Tenders (non-deleted) | 35,123 (1 soft-deleted) |
| First seen, from / to | 2026-07-08 / 2026-11-21 |
| Excluded by a filter | 34,640 (98.6%) |
| Surfaced (visible, not past_tender) | 251 |
| Surfaced status | new 57 · dismissed 149 · dismissed_final 27 · shortlisted 15 · needs_review 3 · reviewed 0 |
| Left the queue (surfaced, status ≠ new) | 194 |
| Pipeline rows | 1 (not_started / pending); there are 16 shortlisted tenders |
| Live backlog (new + needs_review, not expired) | 14, oldest first seen 2026-10-30 (10 are 0–7 days old, 4 are 8–30) |
| **Surfaced, expired, never decided** | **46** |
| Rows the default `GET /api/tenders` returns | 75 |
| Arrivals per week | ~170 (Jul–early Oct); 4,744 / 11,037 / 13,297 / 2,393 (12 Oct – 2 Nov, PhilGEPS flood); ~335 since |
| Surfaced arrivals per week | 3–16, typically ~10 |

### 1.2 By source

| Source | Rows | Surfaced | Shortlisted | Dismissed* | Runs / failed (all time) | Avg pulled per run |
|---|---|---|---|---|---|---|
| TED | 1,334 | 195 | **15** | 130 | 137 / 1 | 238 |
| BOAMP | 2,706 | 26 | 1 | 24 | 137 / 1 | 484 |
| Bekanntmachungsservice | 82 | 17 | 0 | 15 | 41 / 1 | 31 |
| PhilGEPS | **29,686** | 5 | 0 | 3 | 40 / 4 | 3,463 |
| CanadaBuys | 1,296 | 1 | 0 | 1 | 40 / 0 | 491 |
| e-Procurement | 7 | 3 | 0 | 3 | 23 / 0 | 4 |
| TenderNed | 5 | 2 | 0 | 1 | 40 / 1 | 3 |
| Find a Tender | 6 | 1 | 0 | 1 | 40 / 2 | 0 |
| Contracts Finder | **0** | 0 | 0 | 0 | 40 / 0 | **0** |

\* dismissed + dismissed_final

### 1.3 Filters

| exclude_reason | Rows |
|---|---|
| no_core_signal | 32,874 (29,107 of them PhilGEPS) |
| construction_works | 971 |
| below_value_floor | 319 (251 PhilGEPS, 65 TED, 3 Bekanntmachungsservice) |
| rental | 310 |
| container_modular_prefab | 166 |
| (kept) | 483 |

Every rule in `filters.CHECKS` fires. None is dead.

| match_source | All | Surfaced (decided-or-not) | Shortlisted | Dismissed* | New |
|---|---|---|---|---|---|
| both | 267 | 116 | **13** | 75 | 25 |
| cpv | 345 | 105 | 2 | 75 | 28 |
| keyword | 556 | 29 | **0** | 25 | 4 |
| none | 33,954 | 0 | — | — | — |

(The surfaced and outcome columns count non-past_tender rows only.)

**Keyword terms:** 32 distinct terms have ever matched a surfaced tender. Terms that appear on
shortlisted tenders: *tents* (7 of 40 hits), *sleeping bags* (4 of 5), *camp beds*, *tent*, *tentes*
(1 each). High-noise terms: *Zelt* (16 hits, 0 shortlisted, 15 dismissed), *Zelte* (7 hits, 0, 7),
*support equipment* (16 hits, 0), *chapiteaux* (9, 0), *shelters* (9, 0). No Dutch term ever matched
a surfaced tender. Keyword-only matches by language: French 14, German 7, English 7, Italian 1.
None of them was shortlisted.

### 1.4 Decisions

| Metric | Value |
|---|---|
| Dismissal rate among surfaced and decided | 176 / 194 = 91% |
| reason_category | wrong_sector 63 · excluded_type 31 · value_too_low 31 · duplicate 28 · other 8 · deadline_missed 2 · wrong_region 2 · null 18 |
| Repeated free-text reasons | "Rental"/"REntal"/"rental" 10 · "Too low"/"Value too low"/"Too low volume" 9 · "Duplicate" (+ linked-duplicate stamps) 9+ · "Event services" 2 · "Dynamic procurement" 2 |
| Days from first seen to first decision (n = 164) | p10 0 · p25 3 · **p50 7** · p75 15 · p90 23 · max 104 |
| Latency buckets | same day 19 · 1–2 days 19 · 3–7 days 49 · 8–30 days 69 · 30+ days 8 |
| Decisions by weekday | Mon 16 · **Tue 54** · Wed 27 · **Thu 64** · Fri 2 · Sat 1 |
| Who dismisses | two reviewers account for 165 of 183 dismissals (134 + 31); 17 have no recorded dismisser (pre-CR-006); 1 is stamped with a Clerk user ID instead of an email |
| Status transitions | new→dismissed 130 · dismissed→dismissed_final 29 · new→shortlisted 25 · shortlisted→dismissed 13 · needs_review→dismissed 8 · new→needs_review 7 · others ≤ 2 |
| Decided tenders with no `tender_history` | 36 (history starts 2026-08-20; first_seen starts 2026-07-08) |
| Surfaced by language | fra 59 · pol 44 · deu 38 · eng 27 · ces 10 · lit 10 · 17 more languages ≤ 8 each; **89% non-English** |
| translation_status on surfaced | ok 223 · blank 28 |

### 1.5 Duplicates (CR-007 / CR-008)

| Metric | Value |
|---|---|
| Pairs | 102 (same_source 63 · reference 36 · similarity 3), detected Oct 31 / Nov 71 |
| Pairs resolved as "not a duplicate" | **0** |
| Tenders carrying a link (all / surfaced) | 149 / 94 |
| Dismissed with category `duplicate` | 28 |
| Pair outcomes | both new 49 · both dismissed 36 · dismissed + new 9 · dismissed + final 3 · new + shortlisted 2 · others 1 each |

### 1.6 Everything else (trim list)

| Table / feature | Rows |
|---|---|
| vault_documents, vault_document_history | 0, 0 |
| composer_documents, composer_matrix, composer_requirements, composer_style_examples, tenant_style_guide, composer histories | all 0 |
| tender_screening_answers | **0** (5 questions configured per tenant) |
| tender_relevance_overrides | **0** |
| email_alerts (CR-012 inbound) | **0** |
| translated_documents | 3 (all ready); document_tender_links 0 |
| documents (CR-002 E upload) | 1 |
| translations (DeepL text cache) | 970 |
| Manual tenders | 1 (soft-deleted); drafts confirmed 1 / discarded 3; origin sites 1 |
| tender_presence | 319 heartbeats from 3 users, last on 2026-11-21 |
| tender_notifications | forward 3 (3 read) · needs_review_ping 8 (3 read) |
| assigned_to set | 11 |
| pipeline_history | 1 |
| llm_usage since CR-015 | **1 call** (manual_drafts, Sonnet 4.6, 2026-11) |
| Settings tables (vault, composer, dedup, scout) | 0 rows each: never changed from defaults |
| tenant_settings | 2 rows, both at the defaults (daily / 02:00–06:00 / notify off) |
| Keyword library (per tenant) | de 47 · en 23 · fr 23 · nl 22 (115) + 25 distinctive; 16 CPV codes |
| Retired `tender_reviews` table | still present in production |

---

## 2 · Findings

### 2.1 Review Queue: how much work it takes

**F1. Every queue load does the work of the whole database.** `list_tenders`
(`src/api.py:491-576`) calls `store.all_records` (`src/store.py:1219`), which loads all 35,123 rows and
JSON-parses three columns on each. `_attach_relevance` (`src/api.py:314-351`) then scores every
tender whose status is `new` or `needs_review`: 34,924 rows, each compared against 16 positive and 29
negative examples. After that it attaches duplicates, screening answers, presence and notifications.
Only then are 98.6% of the rows thrown away as excluded (`src/api.py:521-522`), leaving 75. The
frontend reloads this whole list after every action: `load()` at `ReviewQueue.tsx:264` (status),
`:317`/`:328` (duplicate resolve/unresolve) and `:379` (relevance). **This is where the queue breaks
down at volume.** The cost grows with total rows, not with queue size, and the PhilGEPS flood
(Oct 12 – Nov 2) multiplied total rows by about 6. I couldn't measure the latency (§4).

**F2. Clicks to clear one tender:**
- **Shortlist:** select the row, click ✓ Shortlist. That's 2 clicks plus a full reload (F1).
- **Dismiss (stage 1):** select, Dismiss, type a note, pick a category from the dropdown, Confirm.
  That's 4 clicks, typing, and a full reload. The note *and* the category are both required
  (`src/api.py:665-669`, `ReviewQueue.tsx:349-358`).
- **Dismiss permanently (stage 2):** select the same tender again, Dismiss permanently, Confirm
  (the note is pre-filled, `ReviewQueue.tsx:274-282`). That's 3 more clicks and a second reload.

So a complete dismissal takes about 7 clicks, 2 full reloads and the same reason entered twice. The
note-plus-category requirement is visibly redundant in the data: reviewers type "Rental", "Too low"
and "Duplicate" as notes next to categories that already say the same thing.

**F3. The second dismiss stage has mostly been abandoned.** There are 149 soft-dismissed tenders and
27 final ones. Only 29 dismissed→dismissed_final transitions ever happened. A soft-dismissed tender
stays in the list greyed out (`ReviewQueue.tsx:176-179`, `:513`, `:525`) until its deadline passes.
So the queue is cluttered with tenders that have already been decided.

**F4. The default sort works against the deadline.** The queue defaults to
`pub_date` newest first (`ReviewQueue.tsx:128`). Decisions take 7 days at the median and 23 at p90,
and they happen in batches on Tuesdays and Thursdays. 46 tenders reached the queue and expired
undecided, three times the number shortlisted. Expired tenders then drop out of the list without
a trace (`src/api.py:567-573`), so nobody sees that they were missed.

**F5. What's on the card when deciding, and what's missing:**
- *Missing: lot or relevant-part value.* Of the 31 `value_too_low` dismissals, only 12 tenders have
  a `value_eur` at all. Reviewers are judging value from the notice text, for example "Lot with
  relevant items (tents) was only valued 100.000 €".
- *Missing in practice: screening answers.* The card has a screening section
  (`ReviewQueue.tsx:882-915`), but there are 0 answers in production. Since CR-015, screening only
  runs when someone requests it (`src/screening.py`, module docstring), and nobody ever has.
- *On screen but unused:* the relevance-correction control (`ReviewQueue.tsx:946-990`), with 0
  overrides ever, and the `reviewed` status colouring (`:102`), with 0 tenders ever in that status.

**F6. Bulk actions.** Dismissal reasons repeat: "Rental" ×10, "Too low" ×9, "Duplicate" ×9+. There's
no multi-select. At about 10 new tenders a week that costs less than it would at TED's full
volume, but it's the repetitive action the data shows.

**F7. Duplicate handling saves work, but the safety valve is never used.** 94 surfaced tenders
carry a link, and a decision on one applies to the whole group (`ReviewQueue.tsx:1001-1009`).
28 dismissals are categorised as duplicate. The "not a duplicate" resolve action has been used 0
times out of 102 pairs. Either detection has had no false positives, or reviewers don't use the
control. 9 pairs have one side dismissed and the other side still new. That's consistent with pairs
detected after the decision, but I haven't verified it (§4).

### 2.2 Filters and matching

**F8. Keyword-only matching surfaces noise and has never found anything.** Keyword-only tenders:
53 surfaced, 29 decided, 0 shortlisted, 25 dismissed. Keywords as a corroborator do earn their keep:
13 of 116 `both` matches were shortlisted (11%), against 2 of 105 CPV-only (2%). That's an argument
for using keywords to rank, not to admit tenders.

**F9. The hard filters leak in predictable ways.** Reviewers typed "rental" 10 times even though the
`rental` filter exists (`src/filters.py:43-53`). 31 dismissals are `excluded_type`. "Event services"
and "Dynamic procurement" recur. This dismissal text is free labelled data for tightening the
filters.

**F10. Sources.** TED produced 15 of 16 shortlists from 4% of rows. PhilGEPS: 29,686 rows, 0
shortlists. CanadaBuys: 1,296 rows, 1 surfaced. BOAMP: 2,706 rows, 1 shortlist. Contracts Finder:
40 daily runs, 0 notices ever. Find a Tender: 0 pulled per run on average, 6 rows in total.

### 2.3 The three open questions (§5)

**Q1: Value floor.** Production runs **€200,000 globally and €50,000 for PhilGEPS only**. The
config says so (`config/exclusions.yaml:53-56`), and the data boundaries confirm it: the highest TED
value excluded is €197,476 and the lowest kept is €206,459; for PhilGEPS those are €40,354 and
€57,103. CR-011's global change to €50k **did not land**. Only the PhilGEPS override did. On what
the floor *should* be: none of the 7 shortlisted tenders with a value is below €500k (p10 €742,706).
The €200–500k band surfaced 19 tenders, shortlisted 0 and dismissed 14. Lowering the global floor
to €50k would have surfaced 56 more tenders over 4.5 months (15 at €50–100k and 41 at €100–200k
that were excluded as below the floor), about 3 a week. That's in a band where nothing above it has
been shortlisted. **The data does not support lowering the floor.** The sample is small (7), so it
doesn't strongly support raising it either. The bigger problem is F5: value is often absent or at
the wrong level (whole contract rather than the relevant lot).

**Q2: Manual tenders below the floor.** The code path exists: `visibility.is_hidden` never hides a
manual tender (`src/visibility.py`), and `ManualTenderPanel.tsx:16-20` renders "would be excluded:
<reason>". It has tests (`tests/test_65_manual_marker.py`, `tests/test_62_manual_identity.py`).
**It is still unverified end to end in production.** The only manual tender ever created was kept
and later soft-deleted, so no below-floor manual record has ever existed.

**Q3: Keyword library.** See F8. Of 115 terms, 32 have ever matched a surfaced tender, 5 have
matched a shortlisted one, and no Dutch term has ever matched. Keyword-only matching adds review
load without adding shortlists, in every language. Adding more non-English sources means adding
more term lists of this kind, and the evidence says those mostly produce dismissals. CPV plus a few
corroborating terms scales. The full multilingual library does not.

### 2.4 Unused subsystems

**F11. Vault and Composer: 0 rows in all 9 tables, 4.5 months after shipping**
(CR-002 E, CR-007 E/F). Their settings have never been changed and there have been 0 Claude calls.
Cost: 33 API routes (vault 13, composer 20), `src/vault.py` + `src/composer.py` (1,152 lines),
about 2,000 lines of frontend pages, 7 test files, 9 tables + 4 settings tables, plus
`proposal_tool/`. Product priorities put Vault/Composer "later". So this is **parked**, not
unwanted or undiscoverable.

**F12. Screening: 0 answers.** Since CR-015, screening only runs when requested. Unused, and probably
undiscoverable: the answers would sit on the card, but someone has to click Screen on each tender.
At about 10 surfaced tenders a week, screening all of them would cost about 40 Claude calls a month.

**F13. Relevance correction: 0 rows.** Unwanted, as far as the data shows. The relevance score
itself is computed on every request (F1), but whether anyone reads it can't be measured.

**F14. Inbound email alerts (CR-012): 0 rows ever.** Deferred by decision, so parked rather than
failed. Cost: 3 routes, `src/email_alerts.py` (99 lines), 1 table.

**F15. Smaller dead items:**
- the `reviewed` status (0 uses; `src/api.py:616`, `ReviewQueue.tsx:102`)
- scheduler and notify fields in `tenant_settings`, documented as read by nothing (`src/schema.py:732-736`)
- tenant 1: a legacy tenant with 0 tenders but full config (16 CPV codes, 115 keywords, 10 portals, 5 questions)
- the retired `tender_reviews` table still present in production (`src/schema.py:305-324`)
- endpoints nothing in the frontend calls: `POST /api/config/rescore`, `POST /api/vault/validate-metadata`
- the `sort` parameter on `/api/tenders`, which is accepted and ignored (`src/api.py:505`, `:575`)
- document attachment: 3 translated documents, 0 attached to any tender (12 translated-document routes)

**F16. Features that are used.** Translation: 970 cached strings, 223 surfaced tenders translated,
and 89% of surfaced tenders are non-English. Presence: 3 users. Notifications: 11.
needs_review/assignment: 11. Duplicate detection (F7). All of these earn their keep.

### 2.5 Pipeline

**F17.** 16 tenders are shortlisted and the pipeline has 1 row, edited once. A pipeline row is only
created when someone edits it (`PATCH /api/pipeline/{pub}`, `src/api.py:1729`), so this measures
edits, not views. Pipeline tracking has effectively never been used. Whether the page gets looked
at can't be measured.

### 2.6 Minor anomalies (not urgent)
- One `dismissed_by` value is a Clerk user ID rather than an email, so attribution is inconsistent.
- A `manual_test` source row (match_source `manual`, entry_method `scraped`) is in the production
  queue data, dismissed.
- The `pg_stat_user_tables` live-row estimates are far off: `tenders` shows 424 against 35,123
  actual, which means the table statistics are stale. A manual `ANALYZE` is harmless and worth doing.

---

## 3 · Recommendations, ranked by value against effort

Review Queue items first. Effort: S = under a day, M = a few days, L = a week or more.

| # | Type | Recommendation | Evidence | Effort |
|---|---|---|---|---|
| 1 | Improvement | **Filter in SQL before enriching.** Select only rows the queue can show (visible, not past_tender, not expired), then score relevance and attach on those 75 instead of 35k. Keep the full set only for the relevance training pools (shortlisted + dismissed_final: 45 rows). | F1 | S |
| 2 | Improvement | **Default the sort to deadline**, with closing-soon at the top. | F4: 46 expired undecided vs 15 shortlisted; p50 7 days | S |
| 3 | Addition | **Show "expired without decision"** as a count and a tab, instead of a silent drop. | F4: 46 | S |
| 4 | Improvement | **Update in place after an action** instead of re-fetching the whole list. | F1, F2 | S |
| 5 | Improvement / trim | **Make dismissal one stage.** Remove the soft stage, or auto-finalise it after N days. | F3: 149 soft vs 27 final; F2: ~7 clicks | S |
| 6 | Improvement | **Require the category and make the note optional**, with one-click presets for the top reasons (Rental, Value too low, Duplicate, Wrong sector). | F2, F6 | S |
| 7 | Improvement | **Stop surfacing keyword-only matches.** Use keywords to rank CPV matches (a both-match boost) instead of admitting tenders. | F8: 0 / 29 shortlisted | S |
| 8 | Improvement | **Tighten the rental and excluded-type filters** using the dismissal texts as test cases. | F9: "rental" typed ×10, excluded_type 31 | S–M |
| 9 | Addition | **Auto-screen surfaced tenders at ingest** (about 40 calls/month), or trim screening (T4). Your call: surface it or remove it. | F12: 0 answers; F5 | S |
| 10 | Addition | **Bulk select plus dismiss-with-reason.** | F6 | M |
| 11 | Addition | **Lot-level or relevant-part value on the card**, where the source carries lots. | F5: 19 of 31 value_too_low dismissals had no EUR value | M |
| T1 | Trim | **Stop storing no_core_signal rows**, or move them to an archive table. That's 32,874 rows (94%), never shown and never reviewed. Item 1 removes the request cost either way; this removes the storage and backup weight. | F1, F10 | M |
| T2 | Trim | **Remove the Contracts Finder connector.** | F10: 40 runs, 0 notices | S |
| T3 | Trim (decision) | **Review PhilGEPS and CanadaBuys.** 31k rows between them, 6 surfaced, 0 shortlisted. Disable, or keep them on probation with a review date. | F10 | S |
| T4 | Trim | **Remove relevance correction** (UI, endpoint, table) and the `reviewed` status. | F13, F15: 0 uses | S |
| T5 | Trim | **Hide Vault and Composer from navigation behind a flag** until the "later" date. Delete them if that date isn't within two quarters. | F11: 0 rows anywhere | S (hide) / L (delete) |
| T6 | Trim | **Clean-up batch:** drop `tender_reviews`; remove tenant 1; remove the inert scheduler/notify settings fields (or wire them up); remove `/api/config/rescore` and `/api/vault/validate-metadata`; remove the ignored `sort` parameter. | F15 | S |
| T7 | Trim (decision) | **Inbound email alerts:** leave dormant (cheap) or remove. 0 rows. | F14 | S |
| — | Keep | Translation, duplicate detection, presence, notifications, needs_review / assignment. | F16 | — |
| — | Decision | **Value floor:** keep €200k global and €50k for PhilGEPS. Don't lower it to €50k. Revisit once there are 30+ shortlisted tenders with values. | Q1 | — |
| — | Verify | **Manual tender below the floor:** create one real record in production and check that the "would be excluded" badge shows. | Q2 | S |

---

## 4 · What couldn't be measured, and what measuring it would take

| Question | Why not | What it would take |
|---|---|---|
| Latency of `GET /api/tenders` at 35k rows | No request timing is recorded | Railway HTTP logs if they hold durations, or a timing log line (instrumentation, which is a separate decision) |
| Clicks and time per decision in practice | No UI event logging | Frontend analytics (separate decision) |
| Whether reviewers read the relevance score or the screening section | No view tracking | As above |
| False negatives inside no_core_signal (32,874 rows) | Excluded rows are never reviewed | A one-off manual sample (e.g. 200 random excluded TED/BOAMP rows) reviewed by a domain expert |
| Who made non-dismiss decisions | `tender_history` has no `changed_by` (`src/schema.py:286-292`) | Add the column (it's additive) |
| Decisions before 2026-08-20 | History starts then; 36 decided tenders have none | Not recoverable |
| Whether the 9 split duplicate pairs (one dismissed, one new) are a propagation bug | Needs `detected_at` compared with each side's decision time, row by row | A follow-up query joining `tender_history` to `tender_duplicates.detected_at` |
| Whether the pipeline page is viewed | Rows appear only on edit | View tracking |
| Exact table sizes on disk | Not queried | `pg_total_relation_size` per table (read-only; add to a future measurement block) |
