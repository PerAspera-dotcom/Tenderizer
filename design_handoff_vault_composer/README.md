# Handoff: Tenderizer — Vault & Composer (Phase 2)

## Overview

This package covers the **Vault** (technical-document library) and **Composer** (proposal
generation) apps inside **Tenderizer**. They are Phase-2 of the product; Phase-1 (Portal +
Scout) is specified separately in `TENDERIZER_HANDOFF.md` (included here for the shared
chrome, theme, and API conventions).

- **Vault** ingests a library of technical documents (datasheets, drawings, certificates),
  extracts structured metadata (material, water column, fire rating, linked CPV codes), and
  becomes the **evidence library** that Composer retrieves from.
- **Composer** wraps the existing Python proposal pipeline. It ingests a tender's documents,
  interprets each requirement, has a human validate them, generates a draft proposal grounded
  in Vault evidence, and tracks **gaps** until the proposal is submission-ready.

The single most important concept across Composer is the **gap-status system** (below).
Everything in the UI orients around driving the outstanding-gap count to zero.

## About the Design Files

The HTML files in this bundle are **design references** — interactive prototypes showing the
intended look and behaviour. **Do not ship the HTML directly.** Recreate these screens in the
target stack (the brief suggests **React + Vite** on the frontend, **FastAPI** wrapping the
existing Python pipeline) using its established component patterns.

`Tenderizer.dc.html` is the full mockup (all apps). It is authored in a "Design Component"
format and needs `support.js` (included) beside it to open in a browser. Open it, click the
**app switcher** under the logo (top-left), choose **Vault** or **Composer**, and walk the
left-nav. The Composer screens **Ingest & Config**, **Proposal Review**, and **Gaps Report**
and the Vault **Library** screen are the built, deepened previews this handoff documents.

Two functional specs sit behind the mockup, both included:
- `claude_design_brief_proposal_drafting.md` — the deep functional spec for the proposal tool
  (document prefixes, output files, gap statuses, the five pipeline scripts, screen-by-screen).
- `TENDERIZER_HANDOFF.md` — the product-wide build doc; **§4** has the Composer/Vault API
  endpoints, **§7** the design system, **§9** the phasing.

> **Palette note:** the proposal brief (`claude_design_brief…`) describes a *standalone*
> navy/gold light theme. **Ignore that palette.** The canonical visual for the integrated
> product is the **dark Tenderizer lock-up** used in the mockup and specified under *Design
> Tokens* below. Follow the mockup.

## Fidelity

**High-fidelity.** The mockup carries final colors, typography, spacing, and interaction
states. Recreate the UI to match, using the codebase's component library. Where the mockup
shows placeholder data, the live values come from the API / pipeline (see *Data: real vs.
illustrative* below).

## Screenshots

Reference renders of each built screen are in `screenshots/`. Open the live mockup for the
interactive states, but these show the intended composition at a glance:

- `screenshots/01-composer-ingest-and-config.png` — Screen A (workflow stepper, role-tagged
  document library, interpreted-requirements list + validation gate).
- `screenshots/02-composer-proposal-review.png` — Screen B (readiness banner, sorted
  requirement list, per-requirement detail with SOW text / response / citations / refine).
- `screenshots/03-composer-gaps-report.png` — Screen C (readiness + donut, run-progress
  chart, to-be-completed / to-be-linked groups).
- `screenshots/04-vault-library.png` — Screen D (indexed documents + dynamic extracted-
  metadata panel).

> The mockup is sized for a ~1280px+ workspace; the screenshots are captured at a narrower
> width, so some rows wrap more than they would at full size. Treat layout proportions and the
> token values below as canonical, not the exact wrap points in the PNGs.

---

## The two systems that drive everything

### 1. Gap-status system (Composer)

Every generated requirement gets a status from the retrieval similarity score:

| Status | Similarity | Color | Meaning |
|---|---|---|---|
| **Complete** | ≥ 0.35 | green `#34d399` | Strong evidence found. Full response generated with citations. |
| **To be linked** | 0.20 – 0.34 | blue `#60a5fa` | Weak evidence. Response written, but the cited document needs formal linking. |
| **To be completed** | < 0.20 | amber `#e3b341` | No evidence. Blank left. Must be filled before submission. |

`outstanding = (#to-be-linked) + (#to-be-completed)`. The proposal is **NOT
SUBMISSION-READY** until `outstanding === 0`. This count is surfaced on Proposal Review and
Gaps Report and should be visible from anywhere in Composer. Red text/flags
(`#f87171`) always mean "manual attention required before submission".

### 2. Document-role system (Composer ingest + Vault)

Source documents carry a **filename prefix** that sets their role and processing. The UI
auto-detects the role from the prefix (overridable) and color-codes it:

| Prefix | Role | Color | Processing |
|---|---|---|---|
| `sow_` | SOW (statement of work) | blue `#60a5fa` | Primary requirement driver. Full ingestion. |
| `tech_` | Technical doc / datasheet / cert | gold `#e3b341` | Evidence library — only these substantiate compliance. |
| `background_` | Company background | teal `#2EE6D4` | Loaded into proposal Section 2. |
| `parta_` | Capability / qualification form | purple `#c084fc` | "Part A" extracted; extra requirements source. |
| `example_` | Previous submitted proposals | grey `#8a97ac` | Style learning only — never retrieved. |

The **compliance matrix** (`compliance_matrix.xlsx`) sits apart from the document library as
its own upload (the mockup renders it as a separate card).

---

## Screens / Views

All screens share the global chrome from `TENDERIZER_HANDOFF.md §8`: 236px left sidebar
(app switcher → per-app menu → "Scout engine online" pill), 62px top bar (search, "Synced
HH:MM", avatar). Composer per-app accent is **purple `#c084fc`**, Vault is **blue `#60a5fa`**.
Each Phase-2 screen carries a `🚧 UNDER CONSTRUCTION` badge while these remain previews.

### Composer nav
`Ingest & Config` · `Proposal Review` · `Gaps Report` · `Style Guide` (🚧 stub) ·
`Settings` (🚧 stub). Default landing: `Ingest & Config`.

### Vault nav
`Library` · `Metadata Rules` (🚧 stub) · `Collections` (🚧 stub) · `Settings` (🚧 stub).
Default landing: `Library`.

---

### Screen A — Composer · Ingest & Config
**Purpose:** Load the tender's documents, watch them interpret into requirements, and have the
responsible person validate each before draft generation unlocks. Maps to the brief's
*Generation* pre-run + *Document Library* (tender side).

**Layout:** Page header (title + `COMPOSER` + 🚧 badges, then a one-line subtitle). A 4-step
**workflow stepper** (`Ingest → Interpret → Validate → Generate draft`), steps 1–2 done
(purple), 3 active (amber), 4 locked (grey). Below, a 2-column grid `300px / 1fr`:

- **Left column** (stacked, gap 16px):
  - **Drop zone** — dashed border `1.5px #3a3550`, purple-tinted bg, centered `⤓`, "Drop
    tender documents", sub: "Role auto-detected from filename prefix — `sow_ · tech_ ·
    background_ · parta_ · example_`" (the prefixes in IBM Plex Mono).
  - **Document library card** — header "Document library" + file count. One row per ingested
    doc: a colored **role tag** (uppercase, `color/bg/border` from the role color at
    `1a`/`44` alpha), filename in IBM Plex Mono (ellipsised), `N pages · N chunks` sub, and a
    status pill (`Ingested` green dot / `Pending` amber dot / `Style only` grey). Image-heavy
    PDFs that need enrichment show an inline amber warning row ("Image-heavy PDF — run
    datasheet enrichment to extract specs"). Footer: `⟳ Ingest all` (purple) +
    `✨ Enrich datasheets` (amber outline).
  - **Compliance-matrix card** — separate from the library: `▦`, `compliance_matrix.xlsx`,
    "Compliance matrix · N requirements", `Loaded` green pill.
- **Right column** — **Interpreted requirements** card. Header "Interpreted requirements" +
  "{validated} of {total} validated". A purple progress bar. One row per requirement:
  requirement title, the extracted snippet in quotes, a source ref chip (`CCTP §4.2 · p.12`,
  mono) + "{confidence}% extraction confidence", and two action buttons stacked on the right —
  **✓ Validate** (green) / **⚑ Flag** (amber); the chosen one fills in. Footer **GATE**: while
  not all validated, a disabled grey "✦ Proceed to draft generation" with "Validate every
  requirement to unlock… (n/total)"; once all validated, a green "All requirements validated"
  line + an enabled purple "✦ Proceed to draft generation →" that navigates to Proposal Review.

**Behavior / state:** per-requirement validation status `pending | validated | flagged`
(client state, persisted via `PATCH /api/composer/requirements/{id}`). Gate is purely derived:
`allValidated = validatedCount === total`. Validate/flag toggle the row's fill.

---

### Screen B — Composer · Proposal Review  *(centerpiece)*
**Purpose:** Review the generated proposal requirement-by-requirement, see evidence and
similarity, refine individual responses, and drive gaps down. Brief Screen 5.

**Layout:** Header (title + badges + subtitle "Every response traces to the SOW…"). A full-width
**readiness banner**, then a master/detail grid `340px / 1fr`.

- **Readiness banner** — amber-tinted (`bg rgba(227,179,65,0.07)`, `border
  rgba(227,179,65,0.3)`). Left: amber dot + **NOT SUBMISSION-READY** (800, amber) + a line
  naming the tender being drafted (BOAMP · deadline). Middle: three stat columns — **to
  complete** (amber), **to link** (blue), **complete** (green), each a big mono number + small
  uppercase label. Right: `View gaps →` (amber, navigates to Gaps Report), `⤓ .docx`,
  `⤓ matrix .xlsx`.
- **Left — requirement list** — a row of **filter chips** (`All · 9`, `To complete · 2`,
  `To link · 3`, `Complete · 4`); active chip is purple-filled. Below, a scrollable list
  (max-height ~560px) **sorted to-be-completed → to-be-linked → complete**. Each item: a
  status dot (status color), `num` (mono) + short title, then status label (status color) +
  `sim 0.NN` (mono). Selected row gets a purple left-border + tint.
- **Right — detail** for the selected requirement:
  - Header: `Requirement 3.6` (mono) + title (`text-wrap:pretty`), and on the right a status
    badge (status color at `1a`/`55` alpha) + the similarity score (big mono, status color)
    with "similarity" label. (The right group `flex-shrink:0`; title container `min-width:200px`
    so the badge wraps beneath on narrow widths.)
  - **Requirement · from SOW** — the SOW text, in a left-border-quoted block.
  - **Generated response** — for *complete/linked*: the generated prose. For *to-be-completed*:
    a red dashed box "⛔ To be completed" + the gap note (no prose). For *to-be-linked*: the
    prose **plus** a blue "🔗 To be linked" notice naming the document awaiting formal linking.
  - **Source citations · from Vault** — one row per citation: `↳` + document name (mono,
    ellipsised) + relevance score (mono, teal). Hidden when there are none.
  - **Refine this section** — a textarea (placeholder: "Type a refinement instruction — e.g.
    'reference the SGS certificate' / 'be more assertive' / 'make this shorter'"), a purple
    **⟳ Regenerate section** button, a "v3 · last regenerated 27 Jun" note, and a
    "⌄ Version history" affordance.

**Behavior / state:** selected requirement id; status filter (`all | completed | linked |
complete`). List sort is fixed (amber-first). Regenerate → `refine.py` via
`POST /api/composer/{pub}/generate` (section-scoped); version history shows prior draft + the
feedback that triggered it. Default-select the first outstanding (amber) requirement so the
worst gap is front-and-center.

---

### Screen C — Composer · Gaps Report
**Purpose:** The submission-readiness view. Brief Screen 6.

**Layout:** Header + subtitle. A 2-column top grid `1.5fr / 1fr`:
- **Readiness card** (amber-tinted): "Submission status" label, big **"{outstanding} items
  outstanding"** (amber), a line "{completed} to be completed · {linked} to be linked ·
  {complete} of {total} complete", and a **donut** (conic-gradient green vs `#1f2b40`) showing
  `{complete}/{total}` done. When `outstanding === 0`, flip to green **SUBMISSION READY**.
- **Run-progress card**: "Gaps closed across runs" — a small bar chart of successive runs
  (`Run 1 … Current`), each a bar (height ∝ gap count) + the count above + run label. Past
  runs slate `#3a4a66`, current amber `#e3b341`. Demonstrates the gap count trending down.

Then two grouped sections:
- **To be completed** (amber dot header + count): one row per gap — `num`, title + the
  "no documentation found" note, and a `+ Add document` action (→ Document library / Proposal
  Review).
- **To be linked** (blue dot header + count): one row per gap — `num`, title + the closest-doc
  note (blue), and `Link document` + `Mark resolved` actions.

Footer: `⤓ Download gaps_report.txt`.

**Behavior / state:** all derived from the requirement set (the same data as Proposal Review).
"Mark resolved" optimistically clears the item; "Add/Link document" routes into the relevant
screen.

---

### Screen D — Vault · Library
**Purpose:** Browse indexed technical documents and inspect the metadata Vault extracted from
each — the evidence pool Composer cites.

**Layout:** A search row (placeholder "Search specs, datasheets, certificates…") + a blue
`⤓ Ingest documents` button. Below, a 2-column auto-fit grid `minmax(380px,1fr)`:
- **Indexed documents** card — header + "1,240 total · N processing". One **clickable row**
  per doc: filename (ellipsised) + a type pill (`Datasheet`/`Drawing`/`Certificate`), a row of
  mono metadata chips (e.g. `600D PES`, `3000 mm`, `M2`), and a status (`Indexed` green dot /
  `Processing` amber dot). Selected row gets a **blue** left-border + tint.
- **Extracted metadata** card (sticky) — driven by the selected document. Header = the doc
  name. A 2-column field grid: each field is an uppercase label + value (mono for
  numeric/codes), e.g. Document type, Material, Water column, Fire rating, Weight, Language. A
  full-width **Linked CPV codes** row (teal, mono). Footer: "{N} fields extracted" +
  "{conf}% confidence" — or, for a *Processing* doc, "⏳ Extracting fields — not yet available
  for retrieval".

**Behavior / state:** selected document id drives the metadata panel. Processing docs have no
metadata yet and are excluded from retrieval until indexed.

### Stub screens (keep as 🚧 "Coming soon" previews)
Composer **Style Guide** (view/edit the extracted house style, `extract_style.py`) and
**Settings** (API key + credit balance, compliance-matrix column mapping, similarity
thresholds, model). Vault **Metadata Rules**, **Collections**, **Settings**. Render the simple
centered 🚧 card; do not build out yet.

---

## Interactions & Behavior

- **App switch / nav** — switching app sets the per-app accent and resets to its default page.
- **Validation gate** (Ingest) — "Proceed to draft generation" disabled until all interpreted
  requirements are validated; enabled state navigates to Proposal Review.
- **Requirement select + filter** (Proposal Review) — selecting a list row updates the detail
  panel; filter chips narrow the list (sort stays amber-first).
- **Regenerate** (Proposal Review) — section-scoped re-generation from the typed instruction;
  surfaces a new version + appends to version history.
- **Vault doc select** — drives the metadata panel; sticky panel stays in view while the list
  scrolls.
- **Pipeline triggers** — long-running script runs (`ingest`, `generate`, `enrich`,
  `extract_style`) should run as background tasks with a polled progress UI (requirement
  counter `N/total`, current item, ETA, cancel), never raw terminal output.

## State Management

- `app`, `page` — active app + screen.
- Composer ingest: `reqValid[reqId] = pending|validated|flagged`; derived `allValidated`.
- Proposal Review: `selectedReqId`, `statusFilter`. Derived: sorted/filtered list, counts,
  `outstanding`, `ready`.
- Gaps Report: derived entirely from the requirement set (+ optional `resolved[]`).
- Vault: `selectedDocId`. Derived: selected doc's metadata + confidence.
- Data fetching: see API below; pipeline triggers are background tasks the UI polls.

## Pipeline scripts → UI (from the brief §6)

| Script | Role | Surfaced in |
|---|---|---|
| `enrich_datasheets.py` | Claude-Vision spec extraction from image-heavy PDFs | Ingest "Enrich datasheets" + Vault processing |
| `ingest.py` | Parse/chunk/embed into local Chroma vector DB | Ingest "Ingest all" |
| `extract_style.py` | Build house style guide from `example_` docs | Style Guide (stub) |
| `generate.py` | Main run — full proposal + filled matrix | Ingest gate → Proposal Review |
| `refine.py` | Re-generate a single section from feedback | Proposal Review "Regenerate section" |

Outputs: `technical_proposal.docx`, `matrix_filled.xlsx` (only when the SOW requires a matrix),
`gaps_report.txt`. The UI is a thin read/trigger layer — **do not re-implement pipeline logic.**

## API (see `TENDERIZER_HANDOFF.md §4` — Composer block)

```
POST  /api/composer/ingest              # upload docs -> parsed requirements
                                        #   [{req, extracted, source, confidence}]
PATCH /api/composer/requirements/{id}   # {status: "validated" | "flagged"}
POST  /api/composer/{pub}/generate      # 403 until every requirement is validated;
                                        #   section-scoped for regenerate
```
Vault endpoints (library + extracted metadata + linked CPV) are additive on the same FastAPI
layer; mirror the `/api/tenders` conventions (serialize arrays as real JSON). The generate
endpoint must refuse (403) until all requirements are validated — the gate is enforced
server-side, not just in the UI.

## Data: real vs. illustrative

The mockup's documents, requirements, and scores are **placeholder content**. Real values come
from the pipeline: requirements + extracted snippets from `generate.py`/`ingest.py`, similarity
scores from Chroma cosine distance, Vault metadata from the enrichment step. Gap status is
**derived** from the similarity thresholds above — do not store a separate status the engine
doesn't produce. Country flags, type pills, and role tags are UI affordances derived from data.

## Design Tokens

**Type:** `Public Sans` (UI, weights 400–800), `IBM Plex Mono` (codes, IDs, dates, scores,
filenames). Base UI size ~13.5–14px; never below 10px for labels.

**Surfaces (dark):** page `#0f1623` · card `#151d2c` · inner panel `#121a28` · input/field
`#101826` · chip/btn-secondary `#16202f` · sidebar `#0b101a`.
**Borders/dividers:** `#222e44` (card) · `#1f2b40` / `#1b2536` (rows) · `#243049` · `#2a3650`.
**Text:** `#e7edf6` base · `#eaf0f8` strong · `#cdd6e3` / `#bcc7d6` body · `#8a97ac` muted ·
`#6b7990` / `#738197` labels.

**Accents:** Composer purple `#c084fc` (light `#d3b3fb`) · Vault blue `#60a5fa` (light
`#9cc1fb`) · Scout/Portal teal `#2EE6D4`.

**Gap statuses:** complete `#34d399` · to-be-linked `#60a5fa` · to-be-completed `#e3b341` ·
red flag `#f87171`. **Document roles:** SOW `#60a5fa` · Tech `#e3b341` · Background `#2EE6D4` ·
Part A `#c084fc` · Example `#8a97ac`. Tag fills use the role/status hex at `1a` (bg) and
`44`/`55` (border) alpha suffixes.

**Similarity thresholds:** complete ≥ `0.35` · linked `0.20`–`0.34` · completed < `0.20`.

**Radii:** cards 13–14px · panels/inputs/buttons 8–11px · chips/tags 5–7px · pills 6–20px.
**Status dots:** 6–9px circles (square for "warning"). **Shadows:** sparse; menus/slide-overs
use `0 22px 55px rgba(0,0,0,0.6)`.

## Assets

No external image assets. Icons are inline SVG stroke icons (1.6 stroke) defined in the
mockup's `navIcon()` map; recreate with the codebase's icon set. The few glyphs (`⤓ ✓ ⚑ ✦ ⟳
↳ ▦ 🚧`) can stay as characters or map to icons. No Anthropic brand assets are used.

## Files

- `Tenderizer.dc.html` — full interactive mockup (open with `support.js` beside it; use the app
  switcher → Vault / Composer).
- `support.js` — runtime needed to open the mockup.
- `claude_design_brief_proposal_drafting.md` — deep functional spec for the proposal tool.
- `TENDERIZER_HANDOFF.md` — product-wide build doc (chrome, theme §7, API §4, phasing §9).
- `screenshots/` — reference renders of the four built screens (see *Screenshots* above).
