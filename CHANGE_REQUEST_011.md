# Change Request 011 — Meeting 3: translation fix, dedup visibility, screening questions, new sources, new organization

**For:** Claude Code · **Against:** the Tenderizer repo
**Source:** Meeting on new sources + bugfixes (Oct 2026), with 4 Review Queue screenshots
**Status:** Bugfixes (urgent) + new features + source viability scoping

---

## Read first

Everything through CR-007 is live, plus the "assign + ping on needs review" feature. CR-008 (Belgian BDA)
is in progress. Confirm repo state first; don't rebuild anything already shipped. Keep the backend suite
green (678 at last count), add tests for every change, frontend typechecks + lints clean. Naming stays
**Tenderizer**. Altering a populated table needs a real Alembic migration.

Land as separate commits in the order below. Push to `master` auto-deploys.

---

## 1 · BUG — Translation is not working (urgent, was already requested in CR-003 G5 / CR-005)

**Evidence (Review Queue screenshots):** titles still show in the source language:
- BOAMP · FR · 90400000 — *"ACCORD-CADRE RELATIF AUX PRESTATIONS D'ENTRETIEN DES AIRES DE LAVAGE…"*
- TED · POL · 34144213 — *"Poland – Miscellaneous equipment – Dostawa sprzętu dla potrzeb projektu…"*
- BOAMP · FR · 44211300 — *"La présente consultation a pour objet l'achat et la maintenance de postes médicaux…"*
- TED · FRA · 44211300 — *"France – Tent for medical use – UniHA_Achat et maintenance de postes médicaux…"*

**Investigate and fix:**
- Check the **DeepL API** end to end in **production**: is `DEEPL_API_KEY` set on Railway, is it the right
  endpoint for the key type (Free keys → `api-free.deepl.com`, Pro → `api.deepl.com`), is the quota exhausted,
  are calls erroring and being **swallowed silently**?
- Check that the Review Queue / Tender Feed row title actually renders `tag_line_en` (not only the detail pane).
- Check language detection — TED titles are mixed ("Poland – Miscellaneous equipment – Dostawa…"): the
  English prefix must not cause the notice to be classed as English and skipped.
- Make failures **visible**: add DeepL status (ok / failing / quota %) to `/api/health` and the Portal Health
  panel; log + Sentry on translation errors; never fail silently again.
- **Backfill** every notice with a null/failed `*_en` once fixed.

*Acceptance:* all four screenshot notices show English titles in the queue row and detail; DeepL status is
visible in health; a forced DeepL failure shows up in health + Sentry.

---

## 2 · Dedup — make duplicates visible (and check detection)

**Evidence:** the two **44211300** notices above (BOAMP FR *"…postes médicaux avancés, polyvalents, mobiles…"*
and TED FRA *"France – Tent for medical use – UniHA_Achat et maintenance de postes médicaux avancés…"*) are
the **same tender**, yet both appear as separate, unflagged rows in the Review Queue. The client asked
*"how do these show up?"* — today they don't, visibly.

- First, **check detection**: run the CR-007 C1 logic on this pair. Did it link them? If not, why (missing
  source link on the TED notice, similarity computed on untranslated text because of bug #1, threshold too high)?
  Fix, and re-run detection after the translation backfill.
- **Show it in the list, not just the detail:** a duplicate badge on the queue row (e.g. "⧉ also on BOAMP"),
  and **group** the pair — one primary row (prefer the national listing) with the TED twin collapsed under it,
  so reviewers triage it once.
- Triage once, apply to both: shortlisting/dismissing the primary carries over to its linked duplicate.
- Reviewer can **unlink** a false positive.

*Acceptance:* the 44211300 pair appears as one grouped row with a visible duplicate badge; deciding on it
updates both; unlinking works.

---

## 3 · Screening questions in the Review Queue (new feature)

The client wants a **list of filter/screening questions** that are **answered automatically from the tender
text** and shown on each tender in the Review Queue, so a reviewer can decide go/no-go without opening the documents.

- Currently surfaced: **value**, **CPV**, **summary**. Target: a set of **~7 questions**.
- Make the question set **configurable per organization** (tenant settings: add / edit / reorder / disable).
- Each answer is extracted by the LLM from notice text (+ documents where available), with a **source
  snippet** and "not stated" when absent — never invent an answer.
- **The remaining questions are not yet defined by the client.** Seed these defaults (editable), and flag
  for client confirmation:
  1. Estimated value (existing)
  2. CPV / scope (existing)
  3. Summary (existing)
  4. Delivery location / place of performance
  5. Quantities / lots
  6. Required certifications or standards (e.g. fire rating, ISO, NATO)
  7. Eligibility / qualification criteria (turnover, references)
  8. Contract duration / framework vs one-off
- Answers are computed at ingest (after translation) and cached; re-computed if questions change.

*Acceptance:* each Review Queue tender shows the configured questions with answers + source snippets;
an org admin can edit the question list and answers refresh.

---

## 4 · New sources — scope viability first, then build the green-lit ones

For **each** site: assess access (public API / open-data feed / public HTML / login-walled), ToS + robots,
fields available (title, buyer, value, deadline, classification code, detail URL), language, and volume.
Produce a **viability table** (green / amber / red + reason) in `SOURCES_VIABILITY.md`, then build the
**green** ones as adapters behind the existing engine interface (same pattern as CR-008). Amber/red: register
as *planned* / *paused* in Portals config with the reason, like DTVP.

Preliminary hypotheses to **verify** (not confirmed):

| Source | Likely access path | Prelim. |
|---|---|---|
| **TenderNed** (NL) | TenderNed open-data API; Dutch → translate | green |
| **UK — MyTenders + contractsmod.uk** | Prefer **Find a Tender Service** (OCDS API) + **Contracts Finder** API — under the Procurement Act 2023 central platform, MOD/defence notices should be published there; MyTenders/MOD portals may need registration | green via FTS, verify MOD coverage |
| **Saudi MOD** — supplier.mod.gov.sa | Login required → don't scrape. Assess alternatives: (1) **Etimad** (tenders.etimad.sa) public tender listing — check whether MOD/defence entities publish there and what's visible without login; (2) **GAMI** (General Authority for Military Industries) procurement announcements; (3) a commercial tender aggregator covering KSA. If none give login-free access, mark *paused* with reason | amber/red |
| **Australia** — tenders.gov.au (AusTender) | Public listing; check for API/OCDS feed; open-tender details may require login | amber |
| **Canada** — canada.ca defence-marine | That page is informational; use **CanadaBuys** tender-notice open data | green |
| **Singapore** — gebiz.gov.sg | Public opportunity listing; check ToS; no known open API | amber |
| **Malaysia** — eperolehan.gov.my quotation/tender notices | Public listing; Malay → translate; check ToS | amber |
| **Philippines** — philgeps.gov.ph | Public bid-notice search; check for open data | amber |

**Important — classification codes:** non-EU sources don't use **CPV** (AusTender/Canada/PhilGEPS use
UNSPSC/GSIN-style codes; GeBIZ has its own categories). Our relevance model is CPV-first. For each new source:
map its codes to our 22 CPV codes where a mapping exists (store a `source_code` + mapped `cpv`), otherwise fall
back to **keyword matching**, and tag clearly so the confidence model stays honest.

Also: English sources (UK, AU, CA, SG, PH) skip translation; NL + MY go through DeepL. Every new source joins
per-source health logging, the daily schedule, and **dedup** (e.g. TenderNed ↔ TED, like BOAMP ↔ TED).

*Acceptance:* `SOURCES_VIABILITY.md` with a verdict per site; each green source live in the Scout with health
logging, translation where needed, and CPV-mapped or keyword-tagged matches; amber/red registered with reasons.

---

## 5 · New, empty organization

Create an **additional organization**, separate from **WoT**, with access to the **full portal** (Overview,
Scout, Vault, Composer) — but **no structure and no content**: no CPV config, no keywords, no screening
questions, no Vault documents or tags, no pipeline, no members beyond the admin. It will be filled in later.

- Must be fully isolated from WoT (no WoT data visible; verify with a cross-org test).
- Empty-state screens must render cleanly (no errors on zero CPV codes / zero sources configured).
- **Organization name: "Alpinter".**

*Acceptance:* new org exists, all modules open with clean empty states, nothing from WoT leaks in.

---

## Suggested order
1. Translation bug (#1) — blocks dedup accuracy and screening questions.
2. Dedup visibility (#2) — re-run after translation backfill.
3. New empty organization (#5) — small, isolated.
4. Screening questions (#3).
5. Source viability scoping → build green sources (#4).
