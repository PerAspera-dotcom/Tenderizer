"""CR-013 §5 — manual tender entry from documents (the PDF path).

    upload (1..MAX_FILES PDFs, object storage area "manual_drafts")
      -> reading (read_documents): PyMuPDF per page, one shared budget of
         MAX_PAGES_PER_DRAFT pages per draft
           * a page with a text layer contributes its text (up to
             MAX_TEXT_CHARS in total)
           * a scanned page is re-rendered as a grayscale image into one PDF
             of scanned pages (kept under MAX_SCANNED_PDF_BYTES)
      -> field extraction: ONE Claude call gets the text plus the scanned
         pages as a native PDF document block and returns every field with a
         confidence and the passage it came from (no per-page transcription)
      -> a person reviews it on the same form as typed entry
      -> confirm: manual_tenders.create (the typed path, unchanged), then the
         files are attached to the new tender's document list

Anything past a limit is counted per file (skipped_pages) and shown on the
form, never silently dropped. Nothing is written to `tenders` before
confirm. The draft row keeps the model's extraction next to what the person
submitted.

Translation is not done here: on confirm the record goes through
run.translate_record like any other (DeepL + its content-hash cache), so
there is one translation route.
"""
import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import manual_tenders
import normalize
import object_storage
import store

CLAUDE_MODEL = "claude-sonnet-4-6"  # matches vault.CLAUDE_MODEL / composer.CLAUDE_MODEL
MAX_FILES = 10
MAX_PAGES_PER_DRAFT = 200       # agreed: pages read per draft, text-layer and scanned together
MAX_TEXT_CHARS = 400_000        # agreed: text-layer characters sent to the extraction call
MAX_SCANNED_PDF_BYTES = 80 * 1024 * 1024  # raw; base64 adds a third -> ~107 MB of the 128 MB request limit
SCANNED_PAGE_CHARS = 40         # a page with fewer text-layer chars counts as scanned
SCAN_DPI = 130                   # ~ what the model keeps of an A4 page (1,568 px long edge)
SCAN_JPEG_QUALITY = 60
EXTRACT_MAX_TOKENS = 8000
CONFIDENCE_THRESHOLD = 0.7      # agreed: below this a field is suggested, not pre-filled
DRAFT_RETENTION_DAYS = 30

# The fields the model may propose: the typed form's fields, with
# origin_site_id replaced by `origin_site` ({name, domain} of the platform the
# notice says it is published on). That is matched against the tenant's own
# site list in resolve_origin_site — a site is never created without a person.
EXTRACTED_FIELDS = tuple(f for f in manual_tenders.USER_FIELDS if f != "origin_site_id") + ("origin_site",)

_FIELD_GUIDE = {
    "tag_line": "the tender's title, as written in the document",
    "description": "a faithful summary of the scope and lots, in the document's own language, at most 1500 characters",
    "buyer": "the contracting authority's name",
    "country": "the buyer's country as an ISO 3166-1 alpha-3 code, e.g. DEU, FRA, GBR",
    "place": "place of performance or delivery",
    "category": "one of: supplies, services, works",
    "procedure": "the procedure type, e.g. open, restricted, negotiated, competitive dialogue",
    "pub_date": "publication date, YYYY-MM-DD",
    "deadline": "deadline for tenders/requests to participate, YYYY-MM-DD (date only)",
    "cpv_codes": "list of 8-digit CPV codes stated in the document; [] if none is stated — never invent codes",
    "value": "the estimated total value as a plain number without separators, e.g. 250000",
    "value_currency": "ISO 4217 currency of that value, e.g. EUR",
    "language": "the document's language as an ISO 639-2 code, e.g. eng, deu, fra, nld",
    "url": "a web address where the tender or its documents can be found, if one is stated",
    "origin_site": ('the e-procurement platform or website the document says this tender is published '
                    'or handled on, as an object {"name": ..., "domain": ...}, e.g. {"name": "DTVP", '
                    '"domain": "dtvp.de"}; null if the document does not say'),
}

_EXTRACT_PROMPT = """You are reading a public procurement notice or tender documents so that a \
person can register the tender. Map the documents onto these fields:

{guide}

Return ONLY a JSON object (no markdown fences, no other text) shaped exactly:
{{"fields": {{"<field>": {{"value": <string, or list of strings for cpv_codes, or null>, \
"confidence": <number 0..1>, "source": "<the short passage the value came from, quoted>"}}}}}}

Rules: include every field listed. If the documents do not state a field, use null with \
confidence 0 — never guess or fill in a plausible value. Confidence reflects how clearly the \
documents state the value, not how plausible it is. Quote "source" verbatim.
{scanned}
Document text (page markers in [brackets]):
{text}
"""


class DraftError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _claude():
    import anthropic
    return anthropic.Anthropic()


# ── reading ──────────────────────────────────────────────────────────────────

@dataclass
class Reading:
    text: str = ""
    scanned_pdf: bytes = b""
    scanned_pages: list = field(default_factory=list)  # [(filename, page_no)] in attached-PDF order
    stats: list = field(default_factory=list)          # per file: pages/text_pages/scanned_pages/skipped_pages

    @property
    def empty(self):
        return not self.text.strip() and not self.scanned_pages


def _scan_image(page):
    import fitz  # PyMuPDF
    mat = fitz.Matrix(SCAN_DPI / 72, SCAN_DPI / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return pix.tobytes("jpeg", jpg_quality=SCAN_JPEG_QUALITY)


def read_documents(files):
    """Read every file within the draft's limits (all read at call time).
    Pages past a limit are counted in that file's skipped_pages."""
    import fitz  # PyMuPDF
    parts, chars = [], 0
    reading = Reading()
    scanned_doc, scanned_bytes = fitz.open(), 0
    budget = MAX_PAGES_PER_DRAFT
    try:
        for f in files:
            name = f.get("filename") or "document"
            data = object_storage.get(f["object_key"])
            st = {"object_key": f["object_key"], "pages": 0, "text_pages": 0, "scanned_pages": 0,
                  "skipped_pages": 0}
            try:
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception as e:  # not a readable PDF
                raise DraftError(f"Could not read {name} as a PDF ({type(e).__name__})")
            try:
                with doc:
                    st["pages"] = len(doc)
                    for i, page in enumerate(doc, start=1):
                        if budget <= 0:
                            st["skipped_pages"] += 1
                            continue
                        text = page.get_text().strip()
                        if len(text) >= SCANNED_PAGE_CHARS:
                            chunk = f"[{name} · p.{i}]\n{text}"
                            if chars + len(chunk) > MAX_TEXT_CHARS:
                                st["skipped_pages"] += 1
                                continue
                            parts.append(chunk)
                            chars += len(chunk) + 2
                            st["text_pages"] += 1
                        else:
                            img = _scan_image(page)
                            if scanned_bytes + len(img) > MAX_SCANNED_PDF_BYTES:
                                st["skipped_pages"] += 1
                                continue
                            out = scanned_doc.new_page(width=page.rect.width, height=page.rect.height)
                            out.insert_image(out.rect, stream=img)
                            scanned_bytes += len(img)
                            reading.scanned_pages.append((name, i))
                            st["scanned_pages"] += 1
                        budget -= 1
            except DraftError:
                raise
            except Exception as e:
                raise DraftError(f"Could not read {name} ({type(e).__name__})")
            reading.stats.append(st)
        reading.text = "\n\n".join(parts)
        if reading.scanned_pages:
            reading.scanned_pdf = scanned_doc.tobytes(garbage=3, deflate=True)
    finally:
        scanned_doc.close()
    return reading


# ── field extraction ─────────────────────────────────────────────────────────

def _parse_json(text):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("fields"), dict) else None


def _normalise_value(field, value):
    """Shape a model value like the form expects. Returns "" (or []) for
    anything unusable, so a malformed value can never be pre-filled."""
    if value is None:
        return [] if field == "cpv_codes" else ""
    if field == "origin_site":
        if isinstance(value, str):
            value = {"name": value}
        if not isinstance(value, dict):
            return ""
        name = " ".join(str(value.get("name") or "").split())
        domain = _domain(value.get("domain") or "")
        if not name and not domain:
            return ""
        return {"name": name or domain, "domain": domain}
    if field == "cpv_codes":
        codes = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value))
        out = []
        for c in codes:
            m = re.match(r"^(\d{8})(?:-\d)?$", str(c).strip())
            if m and m.group(1) not in out:
                out.append(m.group(1))
        return out
    value = str(value).strip()
    if field in ("pub_date", "deadline"):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
        return m.group(1) if m else ""
    if field == "category":
        return normalize.map_category(value) if value else ""
    if field == "value":
        v = value.replace(",", "").replace(" ", "")
        return v if re.fullmatch(r"\d+(\.\d+)?", v) else ""
    if field == "value_currency":
        return value.upper() if re.fullmatch(r"[A-Za-z]{3}", value) else ""
    if field == "language":
        return value.lower() if re.fullmatch(r"[A-Za-z]{3}", value) else ""
    if field == "country":
        return value.upper()[:3]
    return value


def _extract_content(reading):
    """The single request's user content: the scanned pages as a native PDF
    document block (if any), then the instructions with the text layer."""
    guide = "\n".join(f"- {f}: {_FIELD_GUIDE[f]}" for f in EXTRACTED_FIELDS)
    scanned, content = "", []
    if reading.scanned_pages:
        content.append({"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(reading.scanned_pdf).decode("ascii")}})
        index = "; ".join(f"PDF page {n} = {name} p.{p}"
                          for n, (name, p) in enumerate(reading.scanned_pages, start=1))
        scanned = ("\nThe attached PDF holds the scanned pages, which have no text layer; read them "
                   f"as part of the documents. Page map: {index}.\n")
    content.append({"type": "text", "text": _EXTRACT_PROMPT.format(
        guide=guide, scanned=scanned, text=reading.text or "(no text layer; see the attached PDF)")})
    return content


def extract_fields(reading, client=None):
    """{field: {value, confidence, source}} for EXTRACTED_FIELDS, in one call."""
    client = client or _claude()
    msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=EXTRACT_MAX_TOKENS,
                                 messages=[{"role": "user", "content": _extract_content(reading)}])
    if getattr(msg, "stop_reason", None) == "max_tokens":
        raise DraftError("The documents could not be read into fields (the response was cut off)")
    data = _parse_json(msg.content[0].text)
    if data is None:
        raise DraftError("The document could not be read into fields (unexpected model output)")
    out = {}
    for f in EXTRACTED_FIELDS:
        raw = data["fields"].get(f) or {}
        try:
            conf = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        value = _normalise_value(f, raw.get("value"))
        if value in ("", []):
            conf = 0.0
        out[f] = {"value": value, "confidence": round(conf, 2), "source": str(raw.get("source") or "")[:500]}
    return out


def split_by_confidence(extraction):
    """(prefill, suggestions): fields at/above CONFIDENCE_THRESHOLD are
    pre-filled; anything below with a value is only suggested, so an
    uncertain value is never quietly put in the form."""
    prefill, suggestions = {}, {}
    for f, e in (extraction or {}).items():
        if f == "origin_site" or e.get("value") in ("", [], None):
            continue
        (prefill if e.get("confidence", 0) >= CONFIDENCE_THRESHOLD else suggestions)[f] = e
    return prefill, suggestions


def _domain(v):
    """'https://www.DTVP.de/foo' -> 'dtvp.de'; '' if it isn't a host."""
    v = str(v or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", v) else ""


def resolve_origin_site(extraction, sites):
    """Turn the extracted origin_site (and the extracted url's domain) into
    the form's origin_site_id. Returns (match, new_site):
      match    — {value: site id, confidence, source, site} for an existing
                 site of this tenant (by normalised name or domain), else None
      new_site — {name, domain, confidence, source} when the document names a
                 platform the tenant hasn't added yet, for a one-click "add",
                 else None."""
    extraction = extraction or {}
    e = extraction.get("origin_site") or {}
    found = e.get("value") or {}
    url_domain = _domain((extraction.get("url") or {}).get("value") or "")
    by_key = {store._origin_site_key(s["name"]): s for s in sites}
    by_domain = {s["domain"]: s for s in sites if s.get("domain")}
    candidates = []
    if found:
        candidates += [(by_key.get(store._origin_site_key(found.get("name"))), e.get("confidence", 0), e.get("source", "")),
                       (by_domain.get(found.get("domain") or ""), e.get("confidence", 0), e.get("source", ""))]
    if url_domain:
        url_e = extraction.get("url") or {}
        candidates.append((by_domain.get(url_domain), url_e.get("confidence", 0), url_e.get("source", "")))
    for site, conf, source in candidates:
        if site:
            return {"value": site["id"], "confidence": conf, "source": source, "site": site}, None
    if found:
        return None, {"name": found.get("name"), "domain": found.get("domain") or "",
                      "confidence": e.get("confidence", 0), "source": e.get("source", "")}
    return None, None


# ── draft lifecycle ──────────────────────────────────────────────────────────

def add_file(conn, tenant_id, draft_id, upload):
    """Record one uploaded file ({key, filename, content_type, size}) on a
    draft that is still collecting files."""
    draft = store.get_draft(conn, tenant_id, draft_id)
    if draft is None:
        raise LookupError(draft_id)
    if draft["status"] != "uploading":
        raise DraftError("This draft is no longer accepting files")
    if len(draft["files"]) >= MAX_FILES:
        raise DraftError(f"At most {MAX_FILES} files per tender")
    is_pdf = (upload.get("content_type") == "application/pdf"
              or (upload.get("filename") or "").lower().endswith(".pdf"))
    if not is_pdf:
        object_storage.delete(upload["key"])
        raise DraftError("Only PDF files can be read")
    files = draft["files"] + [{"object_key": upload["key"], "filename": upload["filename"],
                               "content_type": upload.get("content_type") or "application/pdf",
                               "size": upload["size"]}]
    if not store.update_draft(conn, tenant_id, draft_id, expect_status="uploading", files=files):
        object_storage.delete(upload["key"])
        raise DraftError("This draft is no longer accepting files")
    return store.get_draft(conn, tenant_id, draft_id)


def start_extraction(conn, tenant_id, draft_id):
    draft = store.get_draft(conn, tenant_id, draft_id)
    if draft is None:
        raise LookupError(draft_id)
    if not draft["files"]:
        raise DraftError("Add at least one PDF first")
    if not store.update_draft(conn, tenant_id, draft_id, expect_status="uploading", status="extracting"):
        raise DraftError("Extraction has already started")
    return store.get_draft(conn, tenant_id, draft_id)


def process(conn, tenant_id, draft_id, client=None):
    """Background job: text, then fields. Ends in 'ready' or 'failed';
    never raises (a failed draft can still be typed in and confirmed)."""
    draft = store.get_draft(conn, tenant_id, draft_id)
    if draft is None or draft["status"] != "extracting":
        return
    try:
        if not os.getenv("ANTHROPIC_API_KEY") and client is None:
            raise DraftError("Reading documents is not configured (no Anthropic API key). "
                             "You can still type the details.")
        reading = read_documents(draft["files"])
        files = [dict(f, **next((s for s in reading.stats if s["object_key"] == f["object_key"]), {}))
                 for f in draft["files"]]
        if reading.empty:
            raise DraftError("No readable pages were found in the documents")
        extraction = extract_fields(reading, client=client)
        record = reading.text
        if reading.scanned_pages:
            record += ("\n\n[Scanned pages, read from images: "
                       + ", ".join(f"{n} p.{p}" for n, p in reading.scanned_pages) + "]")
        store.update_draft(conn, tenant_id, draft_id, expect_status="extracting", status="ready",
                           files=files, extracted_text=record, extraction=extraction, model=CLAUDE_MODEL,
                           error=None)
    except Exception as e:
        logging.exception("manual draft %s extraction failed", draft_id)
        msg = str(e) if isinstance(e, DraftError) else f"Reading the documents failed ({type(e).__name__})"
        store.update_draft(conn, tenant_id, draft_id, expect_status="extracting", status="failed", error=msg)


def view(draft, sites=()):
    """API shape: the draft plus prefill/suggestions split at the threshold,
    with the extracted platform resolved against this tenant's `sites`."""
    prefill, suggestions = split_by_confidence(draft.get("extraction"))
    match, new_site = resolve_origin_site(draft.get("extraction"), list(sites))
    if match:
        (prefill if match["confidence"] >= CONFIDENCE_THRESHOLD else suggestions)["origin_site_id"] = match
    out = {k: v for k, v in draft.items() if k != "extracted_text"}
    out.update({"prefill": prefill, "suggestions": suggestions, "new_origin_site": new_site,
                "confidence_threshold": CONFIDENCE_THRESHOLD})
    return out


def confirm(conn, tenant_id, draft_id, confirmed_by, fields, now=None, fx_rates=None):
    """A person's confirmation: create the tender exactly as typed entry
    does, then attach the draft's files to it. Returns the tender."""
    draft = store.get_draft(conn, tenant_id, draft_id)
    if draft is None:
        raise LookupError(draft_id)
    if draft["status"] not in ("ready", "failed"):
        raise DraftError("This draft can't be confirmed (it is " + draft["status"] + ")")
    # claim it first, so a double submit can't create two tenders
    if not store.update_draft(conn, tenant_id, draft_id, expect_status=("ready", "failed"),
                              status="confirming"):
        raise DraftError("This draft is already being confirmed")
    try:
        tender = manual_tenders.create(conn, tenant_id, confirmed_by, fields, now=now, fx_rates=fx_rates)
    except Exception:
        store.update_draft(conn, tenant_id, draft_id, expect_status="confirming", status=draft["status"])
        raise
    pub = tender["pub_number"]
    for f in draft["files"]:
        try:
            data = object_storage.get(f["object_key"])
            key = object_storage.new_key(tenant_id, "tender_documents", f["filename"], pub_number=pub)
            object_storage.put(key, data, content_type=f.get("content_type"))
            store.add_document(conn, tenant_id, pub, f["filename"], f.get("content_type"), len(data), "",
                               object_key=key)
            object_storage.delete(f["object_key"])
        except Exception:
            logging.exception("attaching %s to %s failed", f.get("filename"), pub)
    store.update_draft(conn, tenant_id, draft_id, expect_status="confirming", status="confirmed",
                       confirmed_by=confirmed_by, confirmed_at=_now().isoformat(timespec="seconds"),
                       confirmed_pub_number=pub,
                       confirmed_values={k: fields.get(k) for k in manual_tenders.USER_FIELDS})
    return tender


def discard(conn, tenant_id, draft_id):
    draft = store.get_draft(conn, tenant_id, draft_id)
    if draft is None:
        raise LookupError(draft_id)
    if not store.update_draft(conn, tenant_id, draft_id,
                              expect_status=("uploading", "extracting", "ready", "failed"),
                              status="discarded", extracted_text=None):
        raise DraftError("This draft can't be discarded (it is " + draft["status"] + ")")
    _delete_files(draft)


def _delete_files(draft):
    for f in draft["files"]:
        try:
            object_storage.delete(f["object_key"])
        except Exception:
            logging.exception("deleting draft file %s failed", f.get("object_key"))


def purge_stale(conn, now=None):
    """Retention: unconfirmed drafts untouched for DRAFT_RETENTION_DAYS are
    discarded and their files deleted. Confirmed drafts are kept — they are
    the record of what the model proposed versus what a person asserted."""
    cutoff = ((now or _now()) - timedelta(days=DRAFT_RETENTION_DAYS)).isoformat(timespec="seconds")
    n = 0
    for d in store.stale_open_drafts(conn, cutoff):
        if store.update_draft(conn, d["tenant_id"], d["id"],
                              expect_status=("uploading", "extracting", "ready", "failed"),
                              status="discarded", extracted_text=None):
            _delete_files(d)
            n += 1
    return n
