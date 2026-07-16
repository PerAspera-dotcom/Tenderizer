"""Vault — tenant-wide technical-document library.

Parses, chunks, and embeds uploaded documents into a per-tenant Chroma
collection (the same approach `proposal_tool/ingest.py` already validated —
sentence-transformers `all-MiniLM-L6-v2`, cosine similarity), then extracts
structured metadata (doc type, technical specs, linked CPV codes) via Claude
Vision (same approach as `proposal_tool/enrich_datasheets.py`, generalized to
return structured JSON instead of free text). Composer's later generation
step is meant to query the same per-tenant collection this module populates —
see `design_handoff_vault_composer/README.md`.

PDF and DOCX only for now (matches what `proposal_tool` actually parses).
`ANTHROPIC_API_KEY` must be a real env var; if unset, metadata extraction is
skipped (chunk/embed still happens) rather than crashing.
"""
import base64
import json
import os

import chromadb
import pdfplumber
from docx import Document as DocxDocument
from sentence_transformers import SentenceTransformer

CHUNK_SIZE = 400
OVERLAP = 50
CHROMA_ROOT = "data/vault_chroma"
COLLECTION_NAME = "vault_docs"
CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_METADATA_PAGES = 3

_model = None


def _embedding_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    """Word-based sliding window, dropping sub-80-char chunks — same
    algorithm as `proposal_tool/ingest.py`'s `chunk_text()`.
    """
    words = text.split()
    step = size - overlap
    chunks = []
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + size])
        if len(chunk.strip()) > 80:
            chunks.append(chunk)
    return chunks


def _is_pdf(path, content_type):
    return content_type == "application/pdf" or path.lower().endswith(".pdf")


def _is_docx(path, content_type):
    return (content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or path.lower().endswith(".docx"))


def parse_document(path, content_type):
    """Full extracted text, or None for an unsupported type."""
    if _is_pdf(path, content_type):
        text = ""
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        return text
    if _is_docx(path, content_type):
        d = DocxDocument(path)
        return "\n".join(p.text for p in d.paragraphs if p.text.strip())
    return None


def _chroma_collection(tenant_id):
    client = chromadb.PersistentClient(path=f"{CHROMA_ROOT}/{tenant_id}")
    return client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def ingest_and_embed(tenant_id, doc_id, path, content_type):
    """Parse -> chunk -> embed -> upsert into this tenant's Chroma
    collection. Returns the number of chunks stored (0 for an unsupported
    file type or a file with no extractable text — not an error, just
    nothing to index).
    """
    text = parse_document(path, content_type)
    if not text or not text.strip():
        return 0
    chunks = chunk_text(text)
    if not chunks:
        return 0
    embeddings = _embedding_model().encode(chunks).tolist()
    ids = [f"doc{doc_id}_chunk{i}" for i in range(len(chunks))]
    metadatas = [{"source": os.path.basename(path), "doc_id": doc_id} for _ in chunks]
    collection = _chroma_collection(tenant_id)
    collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    return len(chunks)


_METADATA_PROMPT = """You are extracting structured information from a technical document \
(a datasheet, drawing, or certificate for tents/shelters/camping or related equipment).

Return ONLY a JSON object (no markdown fences, no other text) with this exact shape:
{
  "doc_type": "Datasheet" | "Drawing" | "Certificate" | "Other",
  "metadata": { "<field name>": "<value, with units where applicable>", ... },
  "cpv_codes": ["<8-digit CPV code>", ...],
  "confidence": <your own confidence in this extraction, 0.0 to 1.0>
}

Extract whatever technical specs are actually present (e.g. material, water column, fire rating, \
weight, dimensions, standard, issuer, valid until, language) — do not invent fields that aren't \
shown. cpv_codes should only include codes explicitly present in the document or unambiguously \
implied by its stated scope; leave the list empty if none apply. If the document contains no \
extractable technical content, still return the JSON shape with an empty "metadata" object and a \
low confidence.
"""


def _pdf_first_pages_as_images(path, max_pages=MAX_METADATA_PAGES, dpi=150):
    import fitz  # PyMuPDF
    images = []
    with fitz.open(path) as doc:
        for page in doc[:max_pages]:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            images.append(base64.standard_b64encode(pix.tobytes("png")).decode("utf-8"))
    return images


def _parse_metadata_response(text):
    """Best-effort JSON parse — strips a ```json fence if Claude added one
    despite the prompt. Returns None on anything that isn't the expected
    shape (caller treats that the same as "couldn't extract", never fabricates).
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "metadata" not in data:
        return None
    return data


def extract_metadata(path, content_type):
    """(doc_type, metadata_dict, cpv_codes, confidence) via Claude Vision —
    PDF only (no visual content to extract from a DOCX the same way).
    Returns None if ANTHROPIC_API_KEY isn't configured, the file isn't a PDF,
    or the response couldn't be parsed — caller leaves the doc `processing`
    rather than treating any of these as a crash.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    if not _is_pdf(path, content_type):
        return None

    import anthropic
    images = _pdf_first_pages_as_images(path)
    if not images:
        return None
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}
               for img in images]
    content.append({"type": "text", "text": _METADATA_PROMPT})
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    parsed = _parse_metadata_response(message.content[0].text)
    if parsed is None:
        return None
    doc_type = parsed.get("doc_type") or "Other"
    metadata = parsed.get("metadata") or {}
    cpv_codes = parsed.get("cpv_codes") or []
    confidence = parsed.get("confidence")
    confidence = confidence if isinstance(confidence, (int, float)) else None
    return doc_type, metadata, cpv_codes, confidence


def process_upload(tenant_id, doc_id, path, content_type):
    """The full background-task pipeline for one uploaded document: embed
    for retrieval, then extract display metadata. Returns the fields
    `store.update_vault_document_metadata` needs; always ends in a terminal
    status ('indexed') — there's no retry queue in this slice, so a doc that
    got no metadata (no API key, unsupported type, or an unparseable
    response) still becomes indexed with empty metadata, not stuck
    'processing' forever.
    """
    ingest_and_embed(tenant_id, doc_id, path, content_type)
    extracted = extract_metadata(path, content_type)
    if extracted is None:
        return {"doc_type": None, "metadata": {}, "cpv_codes": [], "confidence": None,
                "fields_extracted": 0, "status": "indexed"}
    doc_type, metadata, cpv_codes, confidence = extracted
    return {"doc_type": doc_type, "metadata": metadata, "cpv_codes": cpv_codes,
            "confidence": confidence, "fields_extracted": len(metadata), "status": "indexed"}
