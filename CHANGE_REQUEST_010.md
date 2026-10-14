# Change Request 010 — Two new Scout connectors: Belgium e-Procurement and Germany Bekanntmachungsservice

**For:** Claude Code · **Against:** the Tenderizer repo (Scout)
**Status:** New — raised 2026-10-14, following the portal longlist review
**Priority:** P1 · BE first, DE second. Neither goes live without the legal gate in §4.

---

## Why this exists

Scout runs two sources: TED and BOAMP. The product already tells users there should be more —
`TENDERIZER_HANDOFF.md` has the dashboard KPI reading **"2 / 4 · BE planned · DE paused"**, and the
Portals page renders e-Procurement with a grey "Planned" dot. This CR closes both gaps.

- **Belgium e-Procurement** — the client's home market. Today Belgian tenders reach them only via
  TED, which means above-threshold only. Every below-threshold Belgian contract is currently
  invisible to a Belgian company. Highest business value of anything on the longlist.
- **Germany Bekanntmachungsservice** (`oeffentlichevergabe.de`) — the official federal CC0
  open-data service that supersedes DTVP. DTVP stays blocked (its ToS prohibits automated use);
  this replaces it rather than reopening it. The business is currently trialling the source
  manually, so treat go-live as gated on their verdict, not on this build.

`CLAUDE_CODE_BUILD.md` §4 already defines what adding a portal costs:

> Adding "Belgium e-Procurement" = one new connector file + a registry entry + a portal config
> row. No changes to normalize / match / store.

Hold to that. If either connector seems to require changes to `normalize` core, `match` or
`store`, **stop and report** rather than making them — that is a signal the contract is wrong,
and it is a conversation, not a patch.

---

## §0 · Before anything else — confirm the base you are branching from

The public remote's `master` was last pushed **2026-08-26** (`7fbcb39`). A clone of it contains no
`translated_documents`, `translate_document` or `document_tender_links` anywhere in `src/` — which
means the September/October work (DeepL document translation, duplicate resolution, the associated
schema changes) is **not on `master`**.

Confirm where that work actually lives before writing a line of connector code. If it is local or
unpushed, push it first. Branching two new connectors off a two-month-stale base and merging later
is avoidable pain, and `run.py`/`store.py` are exactly the files both efforts touch.

Report what you find. Do not start §1 until this is resolved.

---

## §1 · Belgium — e-Procurement (BDA)

### Investigate first

Verified already, by reading the platform's own traffic and terms on 2026-10-14 — you do not need
to re-derive these, but do confirm anything you depend on:

- There is a live REST API at `https://www.publicprocurement.be/api/…`, unauthenticated.
  Observed returning 200 anonymously: `/api/loc/cpvs`, `/api/loc/nuts`, `/api/loc/cities`,
  `/api/loc/countries`, `/api/loc/currencies`, `/api/dos/organisations/tree-view/`,
  `/api/lex/translations/simple?language=NL|FR|DE|EN`.
- The BDA search UI exposes **CPV code, NUTS code, Main nature and Publication language** as
  native filters — CPV is first-class and server-side queryable, unlike BOAMP.
- Notice detail pages are at `/publication-workspaces/{uuid}`.
- Belgium runs **eForms SDK 1.8** with three national forms — E1 (PMC), E3 (CN), E4 (CAN),
  derived from eForms subtypes 4, 16 and 29. National forms exist, so below-threshold notices are
  in eForms, the same standard family as TED.
- Coverage is genuinely national: the platform's terms define its users as "l'Etat, les Régions,
  les Communautés, les autorités locales". No regional fragmentation to chase.

**Not yet known, and the first thing to find out:** the search endpoint itself. The reference
endpoints above were captured on page load; the query call was not. Discover its path, method,
request shape and response shape before designing anything.

This is the platform's own backend, **not a documented open-data API**. There is no Swagger, no
versioning promise, no stability guarantee. Treat every field as something to verify live and
document in the module docstring, the way `ted.py` and `boamp.py` already do.

### Then propose

Post the proposed connector shape — endpoint, params, pagination, field mapping — and wait for
approval before implementing. Include anything that surprised you.

### Then implement

**`src/connectors/eprocurement.py`**, interface parity with the existing two:

```python
build_params(cpv_codes, keywords, since, limit=100, offset=0) -> dict
parse_response(json_data) -> list[dict]
fetch(cpv_codes, keywords, since, limit=100, max_records=2000) -> list[dict]
```

Keep the `(cpv_codes, keywords, since)` argument order even if one of them goes unused in the
query — `boamp.py` already sets that precedent explicitly, so a caller can invoke any connector
the same way.

**Use the CPV filter.** Unlike BOAMP, this platform supports it server-side. CPV is the primary
net; keywords stay the safeguard.

**`normalize.normalize_eprocurement(raw)`** in `src/normalize.py` — must return the **identical
key set** to `normalize_ted` and `normalize_boamp`:

```
source, pub_number, tag_line, description, buyer, country, place, category, procedure,
pub_date, deadline, cpv_codes, matched_terms, match_source, url, first_seen,
internal_identifier, value, value_currency, language,
raw_award_winner, raw_award_value, raw_award_currency, raw_award_detail
```

Specifics for this source:

- `source` = `"e-Procurement"` — match the string already used in the handoff docs and UI.
- `country` = `"BE"`.
- `language` — **do not hardcode.** BOAMP hardcodes `"fra"` because BOAMP is monolingual. Belgium
  is not: notices appear in NL, FR and DE. Derive it per notice from the publication language,
  using the existing `_pick_lang` / `_record_language` helpers. Getting this wrong sends the wrong
  text to DeepL and corrupts the translation path.
- `internal_identifier` — look for the buyer's own procurement reference, the same cross-portal
  join key `_boamp_internal_identifier` and TED's `internal-identifier-proc` provide. If it isn't
  exposed, return empty and say so; don't invent one.

**Registry wiring** — `_default_sources` in `src/run.py`, following the existing pattern exactly.

**`config/portals.yaml`** — add the row with **`enabled: false`**. See §4.

### Be a good guest

This is an undocumented internal API on a government service, and the platform's terms do prohibit
one thing specifically: overloading its servers or hindering other users. So:

- rate-limit the pull, with a real delay between requests
- set a descriptive `User-Agent` identifying the client
- keep `max_records` as the hard safety cap, as the other connectors do
- fail soft — `run_pipeline` already captures a failing source in `health` without aborting the run

---

## §2 · Germany — Bekanntmachungsservice (oeffentlichevergabe.de)

### Investigate first

This one is fully documented and the facts below come from the live OpenAPI spec at
`https://oeffentlichevergabe.de/documentation/api/opendata`. Read it yourself before building.

- **One endpoint:** `GET /api/notice-exports`
- **Params:** `pubMonth` (YYYY-MM) **or** `pubDay` (YYYY-MM-DD), mutually exclusive, plus
  `format` ∈ `eforms.zip` | `ocds.zip` | `csv.zip`. Returns a ZIP.
- **Licence:** CC0 1.0. No key, no registration.
- **No server-side filtering at all** — no search, no CPV, no keyword. You pull a whole day and
  filter locally.
- **T+1 only.** Requesting today or a future `pubDay` returns **HTTP 400**; only notices processed
  before midnight the previous day are retrievable. History goes back to 2022-12-01.
- **All notice versions are returned**, keyed `noticeIdentifier` + `noticeVersion`. Corrigenda
  arrive as additional versions.
- **Twelve schema variants coexist:** eForms-DE 1.0–2.1 over eForms-EU base 1.0–1.14.

### The two design decisions to propose, not assume

**1. Format.** OCDS 1.1 normalises the twelve eForms variants and gives one JSON file per notice.
The CSV export gives relational tables (`notice.csv`, `lot.csv`, `classification.csv`,
`contract.csv`) with CPV carried at **both notice and lot level** — rows with a `lotIdentifier`
belong to that lot, rows without belong to the notice. Lot-level CPV is valuable here: a multi-lot
tender where only one lot is shelter should match on that lot. Probe both against a real day and
recommend one, with the reason.

**2. The `since` contract.** TED and BOAMP both take `since` and query forward from it. This
source cannot do that — it only serves whole days, in the past. Propose how `fetch` reconciles
that (most likely: iterate `pubDay` from `since` to yesterday, skipping today), and flag the
consequence plainly: **German notices arrive a day later than TED's.** That is a product fact the
client should hear, not an implementation detail to bury.

### Then implement

**`src/connectors/bekanntmachungsservice.py`** with the same three-function interface and the same
argument order. `cpv_codes` is accepted but unused in the request — filtering happens after the
unzip, exactly as `boamp.py` documents for its own case.

**`normalize.normalize_bekanntmachungsservice(raw)`** — identical key set again. `country` =
`"DE"`, `language` = German (this source genuinely is monolingual, so hardcoding is correct here,
unlike Belgium).

**Within-source deduplication.** This is new and neither existing connector has it: the same
notice legitimately appears multiple times across versions. Collapse to the latest
`noticeVersion` per `noticeIdentifier` inside the connector, before the record reaches the
pipeline. Do not route this through the cross-source dedup in `run.py` — that solves a different
problem and conflating them will cause exactly the silent-disappearance class of bug CR-008 P0
was about.

**Report one number with the deliverable:** the **CPV fill rate on a real day's export, split
above vs below threshold.** Below-threshold German notices use a sparser eForms profile and it is
unconfirmed whether CPV is mandatory there. Above-threshold German notices already reach the
client through TED, so below-threshold is the entire reason this source exists. If CPV is thinly
populated there, the business case changes and they need to know before go-live, not after.

**`config/portals.yaml`** — add with **`enabled: false`**.

---

## §3 · Shared requirements

**Schema parity test.** `tests/test_10_boamp.py` has
`test_boamp_and_ted_share_identical_schema`. Extend that idea to all four sources — the whole
pipeline depends on every source emitting the same keys. One test, four normalizers, pairwise or
against a canonical key set.

**Fixtures.** Follow the existing convention: real captured payloads as fixtures, not hand-written
dicts. A fixture built from imagination will pass tests and fail live.

**Cross-source duplication.** Both new sources republish their own above-threshold notices, which
already arrive via TED. The cross-source dedup was tuned on TED↔BOAMP. **Check the new pairings
against real data rather than assuming they carry over** — report the observed duplicate rate per
pairing. Germany is a large TED publisher, so expect a high rate there.

**Translation.** No keyword-library work is needed: `config/keywords.yaml` already covers
EN/FR/NL/DE, which spans both new sources. But ingest translation volume will rise. Report the
expected matched-notice volume per source so the DeepL allowance can be modelled before go-live
rather than discovered in a bill.

**Source health.** `run_pipeline` already records per-source health and alerts after
`SOURCE_FAILURE_ALERT_THRESHOLD` consecutive failures without auto-pausing. Nothing new required —
just confirm both new sources flow through it.

**Commits.** One per connector, independently verifiable, suite green at each step. Not one
combined commit.

---

## §4 · The legal gate — neither source goes live in this CR

Ship both with `enabled: false`. From `CLAUDE_CODE_BUILD.md` §4:

> Respect ToS: DTVP stays paused; e-Procurement (BE) is planned, not built. Per-portal legal
> review (handled with the co-dev company) gates turning any source live.

Where each stands:

- **Germany is clean.** CC0 1.0, explicitly published for reuse. The licence question is settled.
- **Belgium is grey, not green.** I read the full terms of use (version 31.03.2025). Free public
  access to notices is explicit (§3.1), and "inappropriate use" is defined narrowly — damaging or
  overloading servers, hindering other users. **There is no prohibition on automated access**, so
  this is materially unlike DTVP. But §11 claims IP in the site's content including data files,
  and no reuse licence is granted anywhere — no CC0, no OGL equivalent. Not prohibited, not
  licensed.

That makes Belgium a legal-review item, not a technical blocker. Worth asking BOSA directly in
parallel — a government service with an API-first design and a staffed helpdesk may simply grant
written permission, which converts grey into documented.

---

## §5 · Out of scope

- Do not fetch or store tender **documents** from either source. This CR is Scout only. Composer's
  document path is a separate conversation.
- Do not change `normalize` core helpers, `match`, or `store` beyond adding the two normalizers
  and the registry entries.
- Do not enable either portal.
- Do not touch DTVP. It stays paused; a later CR can retire it formally once Germany is live.
- Do not wire outcomes into the relevance loop — still explicitly deferred.

---

## §6 · Answer these rather than guessing

1. Where does the September/October work live, and is `master` safe to branch from? (§0)
2. What is the Belgian search endpoint's path, method, request and response shape?
3. Does the Belgian payload expose a buyer's own procurement reference for `internal_identifier`?
4. OCDS or CSV for Germany, and why?
5. What is the German below-threshold CPV fill rate on a real day?
6. What is the observed cross-source duplicate rate for TED↔BE and TED↔DE?

---

## Findings (answers to §6, both connectors built and committed)

**1. §0 base.** The Sept/Oct work (DeepL document translation, duplicate-resolution
follow-up, an expired-deadline test fix) was local and 2 months unpushed —
`origin/master` was still at `7fbcb39` (2026-08-26). Ran the full suite (775 passed)
and pushed before touching either connector; `origin/master` is now current. One
unrelated thing surfaced and flagged, not fixed (out of scope): a Railway API
token + Postgres password committed in plaintext back in July are still live in
this repo's history, which is public on GitHub — needs rotating and scrubbing
separately.

**2. Belgian endpoint.** `POST https://www.publicprocurement.be/api/sea/search/publications`,
body `{terms?, cpvCodes?, nutsCodes?, publicationLanguages?, publicationDateFrom?,
publicationDateTo?, includeOrganisationChildren, page (1-indexed), pageSize}`,
response `{publications: [...], totalCount}`. The real surprise: every call needs
`Authorization: Bearer <JWT>` — an anonymous Keycloak client-credentials token the
public frontend mints silently from credentials shipped in its own public
`env.config.js`. Not a real secret (every visitor's browser gets it), but still
wired through `EPROCUREMENT_AUTH_*` env vars rather than hardcoded, since the
platform can rotate it without notice. `pageSize=100` is now confirmed live (the
UI offers 10/25/50/100 natively; see finding 6 below, which used it) —
`build_params`'s `limit=100` default is no longer a guess.

**3. Belgian `internal_identifier`.** Yes — `dossier.referenceNumber` (e.g.
`"PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot"`), distinct from the
publication-level `referenceNumber` used as `pub_number` (which changes per
republish/corrigendum). No extra call needed.

**4. OCDS vs CSV for Germany — CSV**, reversing the first instinct. OCDS's single
nested object per notice looked simpler, but `purpose.csv`'s `internalIdentifier`
(BT-22, the named cross-portal join key) is completely absent from OCDS —
confirmed by grepping 8 real populated values against their matching OCDS JSON,
zero hits every time. Losing a named join key permanently was the wrong trade for
a one-time connector-build cost. Surfaced along the way: submission deadline is
absent from **both** CSV and OCDS (checked 30 real active notices plus the raw
eForms XML, which does have it) — deferred, `deadline` is always `""` for this
source, a visible product gap flagged rather than silently built around.

**5. German CPV fill rate** — good news, not thin as feared. Joined `notice.csv` ×
`classification.csv` across a full week (2026-10-07–13, 5003 notices):

| | notices | with CPV | fill rate |
|---|---|---|---|
| above-threshold (EU directive) | 2607 | 2607 | **100%** |
| below-threshold (national: VOB/VOL/UVgO) | 1713 | 1616 | **94.3%** |

(Single-day check during the build, 2026-10-13 only: 494/494 above, 352/379 = 92.9%
below — the week-long sample confirms it wasn't a lucky day.)

**6. Cross-source duplicate rate — observed, both pairings:**

| Pairing | Rate | Basis |
|---|---|---|
| TED ↔ Belgium | **33.7%** (202/600) | Direct field — `publicationReferenceNumbersTED` / `tedPublished`, both agreed on every record, zero disagreements, across a 600-notice live sample (6×100, unfiltered, most-recent-first) |
| TED ↔ Germany | **52.2%** (2612/5003) | Legal-basis inference over the same 6-day, 5003-notice sample — a `32xxLxxxx`/`32xxRxxxx` EU-directive legal basis is a legal *requirement* to also publish on TED, not a guess. No direct cross-reference field exists in Germany's export (checked). Spot-checked against TED's live API: the mechanism holds (German buyers do turn up), but exact notice-by-notice matching was noisy — TED's indexing lag meant a same-day notice wasn't found yet, and buyer-name variants didn't match exactly (e.g. "Stadt Norderstedt" vs. its own utility subsidiary "Stadtwerke Norderstedt"). **Means whatever eventually tunes TED↔DE cross-portal dedup will need fuzzy buyer matching and a lag tolerance, not exact-string matching** — flagging now so it isn't assumed later.

Both above/below-threshold splits and both duplicate rates are higher than
TED↔BOAMP's historically — worth factoring into DeepL volume modelling and how
aggressively dedup gets tuned before either source goes live.
