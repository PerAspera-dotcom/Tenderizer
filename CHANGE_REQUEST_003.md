# Change Request 003 — Client review-queue findings (in progress)

**For:** Claude Code · **Against:** the Tenderizer repo (engine `the_scout/` + `api/` + `web/`)
**Source:** ongoing client review of live Review Queue results, ongoing from 15 Jul 2026
**Read `TENDERIZER_HANDOFF.md`, `CLAUDE_CODE_BUILD.md`, `CHANGE_REQUEST_001.md`, and
`CHANGE_REQUEST_002.md` before starting.**

---

## Status

**Collecting feedback — not yet ready to build.** This CR accumulates findings from the client's
ongoing pass over live Review Queue results before Claude Code starts on it. Items get added here
as they come in.

---

## Reference: queue state as of 15 Jul 2026

Sample from the live Review Queue (all shown at 92% confidence), for context on what the client is
reviewing — untranslated titles still visible per-language (CR-001 R3 / CR-002 not yet built):

- Croatia – Tents – Šatori GRUPA 1/2… (TED · HRV)
- Estonia – Sleeping bags – Magamiskoti komplekti ostmine (TED · EST)
- List of service providers — canopies, tents, structures (CTS), and grandstand… (BOAMP · FR)
- France – Exhibition equipment – Référencement de prestataires pour chapiteaux, tentes… (TED · FRA)
- Portugal – Tents – Aquisição de 12 tendas insufláveis de alta pressão… (TED · PRT)
- Italy – Tarpaulins, awnings and sunblinds – Copertura para aree eventi… (TED · ITA)

## Findings log — session 2 (client review feedback, 15 Jul 2026)

Overall client read: **generally positive**, three specific issues below.

### G1 · False positive — "Norway – Individual and support equipment – Borehole instruments"

Added to the feed/queue but is not a relevant match (borehole/drilling instruments, not tent/shelter
supply). Likely a keyword false-positive: "support equipment" or an adjacent term firing without a
real CPV match. **Action:** pull this notice's `match_source`, `cpv_codes`, and `matched_terms` from
`tenders.db` and identify which keyword(s) fired. If it's a `keyword`-only match on an
over-broad/ambiguous term (e.g. a generic word like "support" or "equipment" alone), tighten that
term — require it in combination with a tent/shelter-domain term, or drop it from the distinctive
set. Don't blanket-suppress the whole keyword-only tier; fix the specific term(s) responsible.
*Acceptance:* re-running the matcher against this notice's stored text no longer flags it; existing
true positives that relied on the same term(s) are checked for regressions before merging.

**Investigated (CR-003 implementation, 2026-07-16) — root cause found, left as-is by customer
decision, no code change.** This isn't actually a keyword-only false positive: `match.py`'s
`classify_match` returns `"cpv"`/`"both"` whenever any active CPV code matches, and `filters.py`'s
`check_no_core_signal` (F5, CR-001) trusts a CPV-sourced match unconditionally — it never checks
which CPV code fired, so it never reaches the keyword/distinctive-term gate at all
(`tests/test_12_filters.py:274`, `test_cpv_match_is_always_core_regardless_of_terms`, locks this in
deliberately). The Norway notice is filed under CPV `35800000` ("Individual and support equipment",
added under CR-001 F7) — a broad parent category that also covers unrelated things like borehole/
drilling instruments. `config/keywords.yaml`'s "individual equipment"/"support equipment" terms
(EN/FR/NL/DE, also added under F7) are just that CPV code's own official label text copied into the
keyword lists, so they'd fire independently too, but they don't matter here — CPV alone already
guarantees the match via the F5 bypass.

Two fixes were considered — narrowing CPV `35800000` out of the active set, or requiring a
corroborating distinctive keyword for matches sourced only from broad/ambiguous CPV codes like this
one — but the customer's explicit call was to **leave current behavior as-is**: accept the
occasional false positive like this one rather than risk a stricter gate silently dropping a
genuine tent/shelter tender filed under the same broad code. No code changed for this item.

### G2 · Keyword library needs bulk enable/disable by language

Current Keywords screen (phase 2, per handoff §"Keywords") requires scrolling/toggling terms one at
a time. Client wants to **enable or disable an entire language's terms in one action** instead of
hunting through the full 112-term list. Add a per-language toggle (EN/FR/NL/DE) above the term list
that flips every term in that language on/off at once; individual term toggles remain for
exceptions. Persists via the existing `PUT /api/config/keywords` write path — no new storage shape,
just a bulk-apply control in the UI.
*Acceptance:* toggling a language switches all its terms' active state in one click; individual
terms can still be re-enabled/disabled after; save behaves exactly as today (validate → write YAML).

### G3 · Missed tender — TED notice 394609-2026 not found by Scout, but found by client

https://ted.europa.eu/en/notice/-/detail/394609-2026 — client found this manually; Scout's run(s)
never surfaced it. **Needs root-cause before a fix, not a guess.** Investigation steps for Claude
Code:
1. Confirm the notice falls inside the scraper's query window (date range, TED search API params in
   `connectors/ted.py`) — if it predates the lookback window or was published between runs and TED's
   own indexing lag pushed it outside the query, that's a window/scheduling issue, not a matching one.
2. If it was fetched but not stored: check `normalize.py` / dedup hash in `store.upsert` — could be
   silently dropped as a false duplicate.
3. If it was fetched and stored but not matched: pull its CPV code(s) and full text, and check
   against `cpv.yaml` (22 active codes) and `keywords.yaml` (112 terms) — this may surface a genuine
   coverage gap (a CPV code or term this notice needed that isn't in either list yet), which is a
   config fix, not a code fix.
4. Report back which of the three it was before changing anything — each has a different fix and
   this determines whether other notices are silently missed the same way.
*Acceptance:* root cause identified and stated (window / dedup / coverage gap); the specific fix
applied; a regression check confirms this notice (or an equivalent test fixture) is now caught by a
clean re-run.

### G4 · Past-tender award extraction (CR-002 A1) not firing — winner/value stay empty

Past Tenders view (CR-002 §B1) correctly identifies past/awarded notices (empty deadline) but the
`awarded_to` / `awarded_value` extraction from CR-002 A1 isn't populating — rows show `—` for both
fields. Confirmed example: TED **391890-2026** (Greece — Specialist vehicles), whose notice body
carries a standard TED "Results" section:

> "Value of all contracts awarded in this notice: 45 290,32 EUR"

with winner **"Ι. ΚΑΤΣΙΔΩΝΙΩΤΑΚΗΣ Α.Τ.Ε.Β.Ε ΚΑΤΑΣΚΕΥΑΣΤΙΚΗ ΔΙΑΣ ΑΤΕΒΕ"** — both present in source
text, neither extracted. CR-002 A1 scoped this as "when the award fields are present in source
text, they populate" — this notice shows that path isn't working yet, at least for TED's
structured "Results" block, and possibly not for non-Latin-script buyer/winner names (Greek here).
**Action:** fix the TED award-section parser to read the standard "Results" / "Value of all
contracts awarded in this notice" field pattern (this appears to be a consistent TED template
string across languages/notices, not per-country prose to regex-hunt) and populate `awarded_value`
+ currency from it; extract `awarded_to` from the adjacent winner-name field in the same section.
Verify non-Latin scripts (Greek, and likely Cyrillic/other EU alphabets) pass through intact —
don't transliterate or drop them. Re-check against the BOAMP "attributaire"/"montant" path from
A1 too — confirm it wasn't only ever tested on French notices.
*Acceptance:* re-processing TED 391890-2026 populates both `awarded_to` and `awarded_value` (with
correct currency) on the Past Tenders row; a small batch of other past-tender notices across TED
and BOAMP, multiple languages, checked for the same gap before calling this closed.

---

## Suggested order

1. G3 investigation (§1–3) — highest priority, could indicate a systemic miss affecting other
   notices too; investigate before shipping other CR-003 changes.
2. G4 — award extraction is a shipped CR-002 feature currently not working; fix before other polish.
3. G1 — bounded fix, single term/rule adjustment.
4. G2 — UI-only addition to the existing (phase 2) Keywords screen; build alongside it.
