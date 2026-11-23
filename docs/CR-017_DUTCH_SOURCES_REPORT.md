# CR-017 §3: Why TenderNed and e-Procurement return so little

**Status:** Report only; nothing changed. Written 2026-11-22.
**Question (CR-017 §3, widened):** TenderNed stored 5 rows in 40 runs, and e-Procurement stored 7 in 23. For a Belgian client that's the home market. Why do these connectors return almost nothing? Is it a defect in the Dutch keyword list, or something else?

## Answer

**Neither connector is blind. They already see every notice their portal publishes, and they return little because the market inside the tenant's CPV scope is small:** roughly 3 to 5 notices a month per portal. The Dutch keyword list isn't the bottleneck.
- e-Procurement doesn't use keywords at all.
- TenderNed only uses the short "distinctive" list.
- In the notices these portals actually publish, the Dutch terms almost never occur. They fired 2 times in 1,843 TenderNed notices and 7 times in 4,000 e-Procurement notices.

CR-016 §2.2 found that no Dutch term ever matched a surfaced tender. That follows from how the pipeline is built, not from misspelled terms (see cause 5).

One real gap turned up: **CPV hierarchy**. e-Procurement's server counts child codes as matches, but TenderNed's check needs an exact code match, so a TenderNed notice filed under an unlisted child code would be missed. The size of that gap isn't measured yet; see "Open".

## Method

Live, read-only probes on 2026-11-22, covering the same 30-day window the daily run uses (2026-10-23 to 2026-11-22). They used the connectors' own User-Agent and code paths, at most 1 request per second, and stored nothing.
- **TenderNed:** public GETs.
- **e-Procurement:** the anonymous token POST and the search POST, both read-only. BOSA permission: ticket CS0227639.

The production figures come from the CR-016 measurement (`docs/cr-016/cr016_prod_output.tsv`, sections 2, 3 and 11).

## What each connector sees

| | TenderNed | e-Procurement |
|---|---|---|
| Notices published in the 30-day window | 2,430 | 4,228 |
| After the connector's own pre-filter | 1,843 (amendments, terminations and modifications skipped) | n/a (filtered on the server) |
| How a notice gets admitted | exact tenant CPV (from the per-notice detail call) **or** a distinctive keyword in title + description | tenant CPV only, sent as a server-side filter; keywords are not used |
| Admitted in the window | ~3 (production average pulled per run: 3) | **5** (live `totalCount`; production average: 4) |
| Keyword hits, distinctive list | 1 | 9 |
| Keyword hits, full Dutch list | 2 | 7 |
| Keyword hits, full library, all languages | 2 | 50 |
| Production runs / failures | 40 / 1 (one HTTP 500) | 23 / 0 |

**The 5 notices e-Procurement admitted in the window were all on target:**
- *Location de chapiteaux* (marquee rental)
- *shelter items*
- *tentes pliables* (folding tents)
- *buitenzonwering* (outdoor sun-shading)
- winter articles for mobile teams

Stored row counts (5 and 7) are small because every daily run re-pulls the same 30-day window. Forty runs times about 3 notices is mostly the same few notices, so few distinct rows accumulate.

## Candidate causes, tested

**1. The connectors aren't pulling notices. Ruled out.**
- TenderNed paged through all 2,430 notices in the window.
- Publication dates came back strictly newest-first, with zero out-of-order dates. That confirms the sort order the connector assumes, which `tenderned.py`'s docstring flags as inferred, not documented.
- The 1,843 candidates fit under the `max_records=2000` cap, though not by much. On a busy month the cap could cut off the oldest days. Each day is re-covered by later runs, so the risk is low.
- e-Procurement's server reported 4,228 notices for the window.
- Health over the measured period: 1 failure in 63 runs.

**2. Wrong, misspelled or mis-folded terms, case, or diacritics. Ruled out.**
- The matcher folds case and accents (`unidecode`) and handles French elision.
- The Dutch terms are the official CPV-label Dutch.
- Where they do occur, they match: *tent*, *tenten*, *zonwering*, *slaapzakken*, *veldbedden* all fired.

**3. Language detection. Not a factor for admission.**
- TenderNed is always labelled `nld`.
- e-Procurement notices are bilingual; `normalize` picks one language for the title (FR 1,977 and NL 1,948 of the 4,000 fetched).
- Matching runs on the stored title and description in that language. That only affects tagging after admission, never admission itself.

**4. Dutch compound words. Real, but tiny.**
- In 1,843 TenderNed notices, the only Dutch words ending in a library stem were noise (*assistent*, *content*, *consistent*, *studentenassistenten*) plus one real one: *buitenzonwering*.
- The whole-word matcher misses *buitenzonwering*, because *zonwering* isn't in the `compounds.head` list.
- It changes nothing in practice: that e-Procurement notice was already admitted by CPV.
- This is consistent with the standing decision not to add Dutch compound keywords (memory: `dutch-matching-cpv-only`).

**5. The keyword list is never reached. True by design, and the explanation for CR-016 §2.2.**
- **Admission:** TenderNed sees only the 25-term `distinctive` list, five of whose terms are Dutch or shared (*tent*, *dekzeil*, *dekzeilen*, *schuiltent*, *camouflage*). e-Procurement sees no keywords at all (`eprocurement.py`: `keywords` "accepted for interface parity but NOT sent").
- **Tagging:** the full 22-term Dutch list only runs in `run._tag`, on records already admitted.
- **Result:** only notices the CPV filter let in can be tagged with a Dutch term, and those few (6 surfaced `nld` tenders in total) happen to be worded with compounds like *buitenzonwering* or with no library term at all.
- So "no Dutch term ever matched" is what this design produces at this volume. It isn't evidence that the Dutch terms are wrong.

**6. The market is small. The main cause.**
- About 3 TenderNed and 5 e-Procurement notices a month fall inside the 16 tenant CPV codes.
- A broad exploratory regex over all 1,843 TenderNed titles and descriptions (tent, paviljoen, overkapping, luifel, zeil, evenement and so on) turned up no tent, marquee or shelter tender. It matched substrings like *content*, *opvang* and *Tentamen*.
- Above-threshold Belgian and Dutch notices are also published on TED, which this probe didn't measure.

## What widening the net would buy

These are measured from the same window. They're not proposals; §6 of the CR rules out changing the library or filters here.
- **Keyword admission on e-Procurement** (full library, all languages): about **45 extra notices a month**. Nearly all are roofing (*couverture*: 22), station canopies (*auvent*), building works and communication services.
  - Two are plausible, and both are tent rental for events: *Raamovereenkomst voor bouw en huur tent jobbeurs De Nayer* and *Services d'organisation d'actions de terrain*.
  - The rental-exclusion filter would hide the first anyway.
- **Full Dutch list instead of the distinctive list on TenderNed:** **+1 a month**, *Zonwering*, a sun-shading works contract filed under CPV 44115900 and 45212190.
  - The one distinctive-list hit was itself a false positive: *Onbemenste Doorbraaksystemen* (unmanned breaching systems), which contains "industriële tent" in an equipment list.
- **Server-side free text on e-Procurement** (`terms`, never sent): `tent` returned 268 notices, against 1 whole-word *tent* hit in 4,000 fetched titles and descriptions. So the server's matching is clearly fuzzy or substring-based, and sending it would flood the queue. This confirms the connector's own reasons for not using it.

**This supports the CR-017 §2 rule:** keyword-only admission should stay off for sources with CPV coverage. Both of these sources have CPV coverage, and keyword admission would add noise, not tenders.

## Open: worth a decision

1. **CPV hierarchy on TenderNed.**
   - `tenderned.fetch` admits a notice only when one of its codes *equals* a tenant code. The same exact match is used in `run._tag`.
   - e-Procurement's server includes child codes: `39522000` alone returned the same 5 notices as its listed children together.
   - A TenderNed notice coded with an unlisted child of a tenant code would be missed. That's quiet under-matching on the client's neighbouring market.
   - **Measuring it** means one read-only sweep of the detail endpoint for all ~1,850 candidates: about 30 minutes at 1 request per second, which is what the connector already does every night at 2 per second.
2. **e-Procurement is switched off for tenant 1** in production (`portals`: tenant 1 / e-Procurement / false); only tenant 5 ingests it. If tenant 1 still matters (CR-016 T6 proposes removing it), that's a one-row setting, not a connector problem.
3. **The `max_records=2000` cap on TenderNed.** 1,843 candidates per 30 days is about 92% of the cap. Worth watching rather than changing.
