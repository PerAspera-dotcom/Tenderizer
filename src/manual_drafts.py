"""CR-013 §5 — manual tender entry from documents (the PDF path).

    upload (1..MAX_FILES PDFs, object storage area "manual_drafts")
      -> text extraction: PyMuPDF per page; a page with almost no text layer
         is rendered and read by Claude Vision (capped at VISION_PAGE_CAP
         pages per draft)
      -> field extraction: one Claude call maps the text onto the manual-entry
         fields, each with a confidence and the snippet it came from
      -> a person reviews it on the same form as typed entry
      -> confirm: manual_tenders.create (the typed path, unchanged), then the
         files are attached to the new tender's document list

Nothing is written to `tenders` before confirm. The draft row keeps the
model's extraction next to what the person submitted.

Translation is not done here: on confirm the record goes through
run.translate_record like any other (DeepL + its content-hash cache), so
there is one translation route.
"""
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import manual_tenders
import normalize
import object_storage
import store

CLAUDE_MODEL = "claude-sonnet-4-6"  # matches vault.CLAUDE_MODEL / composer.CLAUDE_MODEL
MAX_FILES = 10
VISION_PAGE_CAP = 20            # agreed cap: pages per draft sent to Claude Vision
SCANNED_PAGE_CHARS = 40         # a page with fewer text-layer chars counts as scanned
VISION_DPI = 150
MAX_PROMPT_CHARS = 60_000       # text sent to the field-extraction call
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

_EXTRACT_PROMPT = """You are reading a public procurement notice or tender document so that a \
person can register the tender. Map the document text below onto these fields:

{guide}

Return ONLY a JSON object (no markdown fences, no other text) shaped exactly:
{{"fields": {{"<field>": {{"value": <string, or list of strings for cpv_codes, or null>, \
"confidence": <number 0..1>, "source": "<the short passage the value came from, quoted>"}}}}}}

Rules: include every field listed. If the document does not state a field, use null with \
confidence 0 — never guess or fill in a plausible value. Confidence reflects how clearly the \
document states the value, not how plausible it is. Quote "source" from the text verbatim.

Document text (page markers in [brackets]):
{text}
"""

_VISION_PROMPT = ("Transcribe all text on this page of a procurement document, verbatim, in its "
                  "original language. Preserve headings and table cells as plain lines. Output "
                  "only the transcription.")


class DraftError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


# ── text extraction ──────────────────────────────────────────────────────────

def _claude():
    import anthropic
    return anthropic.Anthropic()


def _vision_page_text(client, page):
    import fitz  # PyMuPDF
    mat = fitz.Matrix(VISION_DPI / 72, VISION_DPI / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    b64 = base64.standard_b64encode(pix.tobytes("png")).decode("utf-8")
    msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=4000, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": _VISION_PROMPT},
    ]}])
    return msg.content[0].text.strip()


def extract_text(files, vision_budget=None, client=None):
    """Text of every file, page-marked. Returns (text, per_file_stats).
    Scanned pages go to Claude Vision while the shared budget lasts; past it
    they are skipped and counted, so the reviewer knows text is missing."""
    import fitz  # PyMuPDF
    parts, stats = [], []
    budget = VISION_PAGE_CAP if vision_budget is None else vision_budget  # read at call time
    for f in files:
        data = object_storage.get(f["object_key"])
        st = {"object_key": f["object_key"], "pages": 0, "vision_pages": 0, "skipped_pages": 0}
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as e:  # not a readable PDF
            raise DraftError(f"Could not read {f.get('filename') or 'a file'} as a PDF ({type(e).__name__})")
        try:
            with doc:
                st["pages"] = len(doc)
                for i, page in enumerate(doc, start=1):
                    text = page.get_text().strip()
                    if len(text) < SCANNED_PAGE_CHARS:
                        if budget > 0 and os.getenv("ANTHROPIC_API_KEY"):
                            client = client or _claude()
                            text = _vision_page_text(client, page)
                            budget -= 1
                            st["vision_pages"] += 1
                        else:
                            st["skipped_pages"] += 1
                            continue
                    if text:
                        parts.append(f"[{f.get('filename') or 'document'} · p.{i}]\n{text}")
        except DraftError:
            raise
        except Exception as e:
            raise DraftError(f"Could not read {f.get('filename') or 'a file'} ({type(e).__name__})")
        stats.append(st)
    return "\n\n".join(parts), stats


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


def extract_fields(text, client=None):
    """{field: {value, confidence, source}} for EXTRACTED_FIELDS."""
    client = client or _claude()
    guide = "\n".join(f"- {f}: {_FIELD_GUIDE[f]}" for f in EXTRACTED_FIELDS)
    msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=4000, messages=[{
        "role": "user", "content": _EXTRACT_PROMPT.format(guide=guide, text=text[:MAX_PROMPT_CHARS])}])
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
        text, stats = extract_text(draft["files"], client=client)
        files = [dict(f, **next((s for s in stats if s["object_key"] == f["object_key"]), {}))
                 for f in draft["files"]]
        if not text.strip():
            raise DraftError("No readable text was found in the documents")
        extraction = extract_fields(text, client=client)
        store.update_draft(conn, tenant_id, draft_id, expect_status="extracting", status="ready",
                           files=files, extracted_text=text, extraction=extraction, model=CLAUDE_MODEL,
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
