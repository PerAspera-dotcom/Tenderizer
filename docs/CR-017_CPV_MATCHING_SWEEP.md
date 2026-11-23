# CR-017: Is CPV matching consistent across the nine connectors?

**Status:** Findings only; nothing changed. Written 2026-11-23.
**Question:** CPV is hierarchical, and the tenant's 16 codes sit at different levels (from `35800000`, a 3-digit group, down to `39522530`, a full code). If some sources match a tenant code exactly and others also match the codes beneath it, coverage varies silently by source. Does it?

## Answer

**At admission, yes: the nine connectors follow four different rules.** But **for what reviewers actually see, the outcome is close to uniform.** The shared tagging step (`run._tag`) matches exactly for every source, and the `no_core_signal` exclusion hides any record without an exact CPV match or a distinctive keyword. Server-side child-code matching therefore mostly fills the database with rows that are stored and then hidden.

Three source-specific gaps are real:
1. **Bekanntmachungsservice stores some CPV codes with their check digit** (`39522530-1`). Those never match a tenant code, at admission or at tagging. 8 of 82 rows are affected, 2 of them tent notices that only got in through a keyword. A suffixed tent notice with no tent word in its text is dropped. **This is a bug.**
2. **BOAMP has no CPV filter at all.** Its API only supports full-text search, so a French below-threshold notice under a tenant code is fetched only if it also contains a distinctive keyword.
3. **CanadaBuys matches through a CPV→UNSPSC crosswalk** with class-level prefix matching, and 7 of the 16 tenant codes have no mapping. It is the one source where hierarchy affects what surfaces. In production none of its 1,326 rows carries a tenant CPV, so in practice it's keyword-only.

The child-code question itself turns out narrow. Only **20 codes** sit under the tenant's 16 without being listed. 16 of them belong to `35800000` (uniforms, helmets, gas masks, bullet-proof vests, flags): noise for a tent supplier, and exactly what TED's child-code matching pulls in. The other 4 are *sails*, *pneumatic mattresses*, *down-filled sleeping bags* and *profile sections*.

## How each connector admits a notice

| Source | Where CPV is checked | Rule | Keyword path |
|---|---|---|---|
| TED | server: `classification-cpv IN (…)` | **includes child codes** (measured) | OR distinctive keyword in the title (server) |
| e-Procurement | server: `cpvCodes` | **includes child codes** (measured) | none, keywords are not sent |
| TenderNed | local, after the per-notice detail call | exact | OR distinctive keyword (local, compounds on) |
| Bekanntmachungsservice | local | exact, **but codes keep their check digit**, see gap 1 | OR distinctive keyword |
| Find a Tender | local | exact | OR distinctive keyword |
| Contracts Finder | local | exact | OR distinctive keyword |
| CanadaBuys | local, through `config/canadabuys_unspsc.yaml` | UNSPSC class prefix (`…00` matches its children); 7 codes unmapped | keyword |
| BOAMP | none, the API has no CPV filter | n/a | distinctive keywords, server full text |
| PhilGEPS | none, the source has no CPV | n/a | keyword on title |
| **Every source, after fetch** | `run._tag` → `match_source`; `filters.check_no_core_signal` | **exact**: a record needs an exact tenant code or a distinctive keyword to surface | |

### Measured: TED and e-Procurement include child codes

Live and read-only, over a 90-day window (2026-08-25 to 2026-11-23). In every case, querying a parent plus its children returned exactly the parent's count, so the parent's results already contain the children's:

| Parent (tenant) | Children (not on the tenant list unless noted) | e-Procurement parent / children / both | TED parent / children / both |
|---|---|---|---|
| 35800000 | 35811300 military uniforms | 5 / 0 / 5 | 425 / 38 / 425 |
| 35800000 | 35813000 military helmets | 5 / 0 / 5 | 425 / 34 / 425 |
| 35800000 | 35821000 flags | 5 / 2 / 5 | 425 / 25 / 425 |
| 35800000 | 10 of its unlisted children together | 5 / 5 / 5 | 425 / **396** / 425 |
| 39522000 | 39522530 tents (listed) | 11 / 8 / 11 | 179 / 100 / 179 |
| 39522500 | 39522530 tents (listed) | 9 / 8 / 9 | 135 / 100 / 135 |
| 44112100 | 44112120 profile sections | 0 / 0 / 0 | 46 / 1 / 46 |

In 90 days, 396 of the 425 TED notices behind `35800000` are uniforms, helmets, masks, vests and flags.

## What production holds (read-only query, 2026-11-23)

Every stored row is classified against its tenant's codes. Both tenants have the same 16. "Child-only" means it carries a code under a tenant code but none equal to one.

| Source | Rows | Exact tenant code | Child-only | of which hidden | No CPV |
|---|---|---|---|---|---|
| PhilGEPS | 29,690 | 0 | 0 | | 29,690 |
| BOAMP | 2,729 | 42 | 4 | 4 (`no_core_signal`) | 144 |
| TED | 1,349 | 545 | **772** | **770**: `no_core_signal` 717, value floor 43, container 7, rental 2, works 1 | 0 |
| CanadaBuys | 1,326 | 0 | 0 | | 1,326 |
| Bekanntmachungsservice | 83 | 16 | 2 (both suffixed codes, gap 1) | 0 (both got in by keyword) | 1 |
| e-Procurement | 7 | 6 | 1 | 1 (`no_core_signal`) | 0 |
| Find a Tender | 6 | 6 | 0 | | 0 |
| TenderNed | 6 | 4 | 0 | | 0 |

- **57% of TED's stored rows (772 of 1,349) are child-only,** and 770 of those are hidden. Top child codes: `35811200` police uniforms (216), `35815100` bullet-proof vests (139), `35811100` fire-brigade uniforms (96), `35811300` military uniforms (93), `35810000` individual equipment (79). Only 2 surfaced, both *camouflage jackets* (`35812100`), and they surfaced because "camouflage" is a distinctive keyword.
- **The relevant children are rare:** `39522510` pneumatic mattresses 10 rows, `39522541` down sleeping bags 9, `39522400` sails 6, `44112120` profile sections 1. All hidden today.
- PhilGEPS and CanadaBuys make up 88% of all stored rows (31,016 of 35,197) and carry no CPV. That isn't a hierarchy issue, but it's why the table holds 35,000 rows.

## TenderNed, every candidate swept

All 1,808 TenderNed candidates published 2026-10-24 to 2026-11-23, after the connector's own pre-filter, had their detail fetched (0 failures) and their CPV codes classified against the 16 tenant codes:

| Classification | Notices | What they are |
|---|---|---|
| Exact tenant code | 3 | all through `35800000`: *Overlevingskleding … redvesten* (survival clothing, life vests), *Specialized CBRN Suits*, *RF Handheld Jamming Capacity*. **No tent notice.** |
| Child-only (would match only with child codes) | 1 | *Raamovereenkomst Onderscheidingstekens en Emblemen* (insignia, `35811300` military uniforms): noise |
| Parent-only (broader than a tenant code) | 23 | furniture (`39000000`), medical (`33100000`), sports equipment (`37400000`), building materials (`44100000`): none relevant |
| Unrelated | 1,781 | |

**Exact matching on TenderNed misses nothing relevant in this window, and child-code matching would add one irrelevant notice.** It also confirms the §3 report: in this month the Dutch portal carried no notice under the tent codes at all, and its three CPV matches are equipment-group notices under `35800000`.

## What this means

- **Coverage of what reviewers see doesn't vary by source because of hierarchy.** The exact rule in `run._tag` and `no_core_signal` evens it out. The CR-017 §2 per-source rule (keyword-only admission on for CPV-less sources, off for the others) still describes the system correctly.
- **The cost of the inconsistency is storage and noise, not missed tenders.** TED fetches and stores about 130 uniform, helmet and flag notices a month through `35800000`, then hides them. Whether `35800000` belongs on the tenant list at all, at group level, is a question for the client. "Individual and support equipment" is mostly uniforms.
- **The one silent miss is gap 1,** check digits on Bekanntmachungsservice. It's small today: 2 tent notices, both caught by keyword. But it's the only place where a correctly coded tent notice can be dropped.
- **BOAMP's CPV-blindness is structural.** Above-threshold French notices also reach TED, which filters on CPV. Below-threshold ones depend on the distinctive keywords.

## Possible follow-ups (not done; each needs its own decision)

1. Strip the check digit in Bekanntmachungsservice's CPV parsing, as TenderNed and e-Procurement already do. Recheck the 8 affected rows.
2. Decide whether `35800000` stays at group level, narrows to the relevant codes beneath it, or stays as is and is accepted as noise.
3. Decide whether *down-filled sleeping bags* (`39522541`) and *pneumatic mattresses* (`39522510`) should join the list. Both sit under codes the tenant already has.

## Method

- **Code reading:** each connector's `fetch()` and query builder, `run._tag`, `filters.check_no_core_signal`, and `config/canadabuys_unspsc.yaml`.
- **Vocabulary:** the 9,454-code CPV list in `config/cpv_be_checksums.json`, used to find unlisted descendants. Labels came from e-Procurement's `/api/loc/cpvs` (one GET).
- **Live probes:** read-only, 1 request per second, the connectors' own User-Agent, nothing stored. TED's public search API, e-Procurement's search (BOSA ticket CS0227639), and TenderNed's public list and detail GETs (1,808 detail calls for the sweep).
- **Production:** two read-only queries through `railway ssh`, inside a `READ ONLY` transaction that was rolled back. Only aggregates and titles were printed.
