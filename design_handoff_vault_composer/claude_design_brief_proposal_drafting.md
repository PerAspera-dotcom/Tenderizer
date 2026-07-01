# Proposal Generation Tool — Claude Design Brief

**For:** Claude Design / Claude Code  
**Purpose:** Design a browser-based UI for a government tender proposal generation tool  
**Current state:** Working Python CLI pipeline — this UI wraps and replaces the command-line workflow  

---

## 1. What This Tool Does

A company that supplies tents, camp structures, and shelter goods to EU government, defence, and humanitarian buyers needs to respond to government tenders. Each tender involves:

- A **Statement of Work (SOW)** from the contracting authority defining all requirements
- A **compliance matrix** (Excel) where the bidder must confirm compliance with each requirement and reference where in their proposal the evidence lives
- A **Technical Proposal** document the bidder submits as their formal response

The tool automates this process:
1. The user uploads their source documents (SOW, technical specs, company background, etc.)
2. The tool ingests them into a local vector database
3. For each requirement in the compliance matrix, the tool retrieves relevant evidence from the technical documents and uses Claude AI to write a compliant response
4. It outputs a filled compliance matrix and a structured technical proposal document
5. It flags gaps where documentation is missing, so the user knows exactly what to resolve before submission

**The critical design principle:** The SOW is the single source of truth. All proposal content traces back to it. Technical documents provide evidence that the company meets SOW requirements. The compliance matrix is an index into the proposal.

---

## 2. Process Flow

```
UPSTREAM (human, outside tool)
  Read bid documents → Bid/No bid decision → Proceed

TOOL SCOPE
  Upload documents → Ingest → Generate → Review gaps → Add missing docs → Re-run → Submit

DOWNSTREAM (human, outside tool)
  Human review & sign-off → Pricing document → Submission to authority
```

The tool covers everything between "document upload" and "submission-ready proposal."

After a tender closes, the submitted proposal is fed back into the tool as an example document, teaching it the company's writing style for future tenders.

---

## 3. Document System

All source documents have a **prefix** that determines how they are processed:

| Prefix | Role | Processing |
|--------|------|------------|
| `sow_` | Statement of Work from contracting authority | Primary requirement driver. Full ingestion. |
| `tech_` | Technical docs, datasheets, certifications, test reports | Evidence library. Only these substantiate compliance claims. |
| `background_` | Company background document | Loaded directly into proposal Section 2. |
| `parta_` | Capability & qualification form | Only "Part A" section extracted. Additional requirements source. |
| `example_` | Previous submitted proposals | Style learning only. Never used for retrieval. |

The **compliance matrix** (`compliance_matrix.xlsx`) sits in the root folder separately from the docs library.

---

## 4. Output Files

The tool generates three output files per run:

### 4.1 `technical_proposal.docx`
Structured Word document with:
- **Section 1 — Introduction:** Company responding to [authority] for [lots]. Has a red placeholder for manual completion of lot/tent type details.
- **Section 2 — Company Background:** Auto-populated from the `background_` document.
- **Section 3 — Scope of Requirements:** One numbered subsection per requirement. Each contains generated proposal prose grounded in retrieved technical documentation. Red "To be completed" or "To be linked" flags where evidence is missing.
- **Annex A — Referenced Technical Documents:** Auto-generated list of all cited documents. Documents that need formal linking are flagged in red.

### 4.2 `matrix_filled.xlsx`
The original compliance matrix returned with all response cells filled:
- YES/NO compliance answer
- Cross-reference string pointing to the exact proposal section
- Substantive remark per requirement per lot/tent size
- Colour-coded: **green** = complete, **blue** = to be linked, **amber** = to be completed

The compliance matrix output is only generated when the SOW explicitly requires one.

### 4.3 `gaps_report.txt`
Submission readiness report listing every outstanding item:
- TO BE COMPLETED: no supporting documentation found
- TO BE LINKED: document exists but not formally evidenced
- Shows total count — proposal is not submission-ready until both counts are 0

---

## 5. Gap Status System

Three statuses drive everything in the UI:

| Status | Colour | Meaning |
|--------|--------|---------|
| **COMPLETE** | Green | Strong evidence found (similarity ≥ 0.35). Full response generated with citations. |
| **TO BE LINKED** | Blue | Weak evidence (0.20–0.34). Response written but citation needs formal linking. |
| **TO BE COMPLETED** | Amber | No evidence (<0.20). Blank space left. Must be filled before submission. |

Red text in documents = manual attention required before submission.

---

## 6. The Pipeline Scripts

Five scripts form the pipeline. The UI should wrap these as interface screens:

| Script | What it does | When to run |
|--------|-------------|-------------|
| `enrich_datasheets.py` | Uses Claude Vision to extract specs from image-heavy PDF datasheets | Once per new set of datasheets |
| `ingest.py` | Parses, chunks, embeds, stores all docs in local Chroma vector DB | After any document is added or changed |
| `extract_style.py` | Analyses example proposals and generates a style guide | Once per set of example proposals |
| `generate.py` | Main run — generates full proposal and fills matrix | Each tender cycle |
| `refine.py` | Re-generates individual sections based on typed feedback | During review |

---

## 7. Screens to Design

### Screen 1 — Dashboard / Home
**Purpose:** Central hub showing current tender status at a glance.

**Key elements:**
- Active tender name + contracting authority
- Submission readiness indicator (prominent — NOT READY / READY)
- Gap counts: X to be completed · Y to be linked
- Document library status: N documents ingested, last ingested [date]
- Quick action buttons: Run generation · Ingest documents · View gaps
- Recent run log (timestamp, requirements processed, gap count change)

---

### Screen 2 — Document Library
**Purpose:** Manage all source documents for the current tender.

**Key elements:**
- Document upload area (drag and drop)
- Role selector for each uploaded file (sow / tech / background / parta / example) — auto-detected from filename prefix but overridable
- Document list showing: filename · role tag · chunk count · last ingested date · status (ingested / pending)
- "Ingest all" button — triggers ingest.py
- "Enrich datasheets" button — triggers enrich_datasheets.py on image-heavy PDFs (flagged automatically)
- Warning badge on image-heavy PDFs that haven't been enriched yet
- Compliance matrix upload as a separate distinct section (not part of the docs library)

**Document role colour coding:**
- SOW → navy
- Tech → gold
- Background → teal
- Parta → purple
- Example → grey

---

### Screen 3 — Style Guide
**Purpose:** View and edit the extracted house writing style.

**Key elements:**
- Style guide status: Generated / Not generated / Outdated
- "Extract style" button — triggers extract_style.py on example_ documents
- Editable text area showing style_guide.txt content
- Sections visible: tone · compliance language · sentence patterns · phrases to use · phrases to avoid
- Save button

---

### Screen 4 — Generation
**Purpose:** Configure and run the proposal generation.

**Key elements:**
- Pre-run checklist: SOW ingested ✓ · Tech docs ingested ✓ · Style guide present ✓ · Compliance matrix loaded ✓
- Configuration panel: similarity thresholds (advanced, collapsible)
- "Generate proposal" button — triggers generate.py
- Live progress: requirement counter (N/35 complete), current requirement being processed, status of each as it completes
- Estimated time remaining
- Cancel button
- On completion: summary card showing total complete / to be linked / to be completed counts with links to outputs

---

### Screen 5 — Proposal Review
**Purpose:** Review the generated proposal section by section, refine individual responses.

**Key elements:**
- Left panel: requirement list sorted by status (amber first, then blue, then green)
  - Each item shows: requirement number · short title · status badge · similarity score
  - Filter by status (all / to be completed / to be linked / complete)
  - Search
- Right panel (detail view for selected requirement):
  - Requirement text (from SOW)
  - Generated proposal text
  - Status badge + similarity score
  - Source citations: which tech documents were used, with relevance scores
  - For TO BE LINKED: document name flagged + "to be linked" notice
  - For TO BE COMPLETED: red blank space + note
  - Feedback input: text box where user types refinement instruction ("make this shorter" / "reference the ISO cert" / "be more assertive")
  - "Regenerate this section" button — triggers refine.py logic
  - Version history toggle (shows previous draft + what feedback triggered it)
- "Export proposal" button at top right — downloads technical_proposal.docx
- "Export matrix" button — downloads matrix_filled.xlsx

---

### Screen 6 — Gaps Report
**Purpose:** Clear submission readiness view.

**Key elements:**
- Large readiness indicator: SUBMISSION READY / X ITEMS OUTSTANDING
- Two sections:
  - **To be completed** (amber): grouped by requirement, shows requirement text + which tent sizes are missing. Action: "Add document" links to document library.
  - **To be linked** (blue): grouped by requirement, shows closest matched document name. Action: "Add formal document" or "Mark as resolved".
- Progress tracker: shows how gap count has changed across runs (run 1: 245 gaps → run 2: 87 gaps → run 3: 12 gaps)
- Download gaps_report.txt button

---

### Screen 7 — Settings
**Purpose:** Configure API key, column mappings, similarity thresholds.

**Key elements:**
- Anthropic API key field (masked, with credit balance indicator)
- Compliance matrix column mapping: shows current COL_ index values with descriptions, editable
- Similarity thresholds: GOOD (default 0.35) and PARTIAL (default 0.20) sliders
- Model selection (currently claude-sonnet-4-6)
- Run history log

---

## 8. Design Language

### Colour Palette
```
Navy (primary)      #1A2E4A   — headers, primary actions, nav
Gold (accent)       #C09A3A   — highlights, active states, progress
White               #FFFFFF   — card backgrounds
Off-white           #F4F1EC   — page background
Light grey          #F9F7F4   — secondary backgrounds

Status colours:
Complete (green)    #1A5C2E / background #E8F5EC
To be linked (blue) #2A5298 / background #EEF3FA
To be completed     #8B6500 / background #FFF8E8 (amber)
Red flags           #8B1A1A / background #FFF0F0
```

### Typography
- **Headings:** Libre Baskerville (serif) — formal, government-appropriate
- **Body / UI:** Source Sans 3 (sans-serif) — readable, professional
- **Code / IDs / dates:** DM Mono or Courier New — monospaced

### Tone
Formal-professional. This tool is used for government procurement — every screen should feel like enterprise software, not a consumer app. Dense information is fine; the user is a procurement professional scanning many items.

### Card style
White cards with subtle `1px` border `#C8C0B0`, minimal shadow. Navy header bar with gold accent border. No rounded corners on cards larger than 4px.

### Status badges
Small pill badges, same colour system as gap statuses. Bold, uppercase, small.

---

## 9. Key UX Principles

1. **Gaps drive the workflow** — the gap count is the most important number in the tool. It should be visible from every screen.

2. **Iterative document adding** — not all technical documents exist at project start. The UI must make it easy to add a document and re-run without losing context.

3. **SOW is the source of truth** — every requirement displayed in the UI traces back to the SOW. Source citations should always show which document a claim comes from.

4. **Red flags are explicit** — "to be completed" items must be impossible to miss. They should not blend into the background.

5. **Non-technical user** — the primary user is a procurement professional, not a developer. Command-line operations must be fully wrapped. No terminal output should be visible in the final UI.

6. **Submission checklist mentality** — the user's mental model is "I need all items green before I submit." The UI should reinforce this at every step.

---

## 10. What Is NOT in Scope for This Tool

- Pricing / commercial proposal (entirely separate)
- Bid/no-bid decision (upstream, human judgment)
- Capture planning (internal only, not fed into tool)
- Legal review
- SharePoint integration (planned but not current)

---

## 11. Current Technical Stack (for Claude Code context)

| Layer | Technology |
|-------|-----------|
| Backend scripts | Python (Windows, runs locally) |
| Language model | Anthropic Claude API (claude-sonnet-4-6) |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2, local) |
| Vector store | Chroma (local, cosine similarity) |
| PDF parsing | pdfplumber |
| Vision extraction | Claude Vision via Anthropic API |
| Word output | python-docx |
| Excel output | openpyxl |
| Suggested UI stack | React + Vite, FastAPI backend |

The Python scripts already work and produce correct output. The UI calls them via a FastAPI API layer. No rebuilding of the pipeline logic — the UI is a thin read/trigger layer over what already exists.

---

## 12. Reference Artefacts Available

The following files exist and can be used as design reference:

- **`wireframe.html`** — interactive system architecture diagram showing full process flow, document relationships, and pipeline steps
- **`spec_document.docx`** — full technical specification covering all components, configuration, thresholds, and planned features
- **`user_manual.docx`** — step-by-step user guide covering all commands, troubleshooting, and workflow

These were generated as part of the same build session and reflect the current production state of the tool.
