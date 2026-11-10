"""CR-013 Stage 4 (§5) — manual entry from documents.

The rule that matters most: nothing reaches `tenders` until a person
confirms. Also: text-layer pages are read locally and scanned pages go to
Vision within the agreed cap; low-confidence fields are suggested, never
pre-filled; the publishing platform is read from the document and matched to
the tenant's site list (never created silently); confirm goes through the
typed path and attaches the files to the tender's document list; the draft
keeps the model's proposal next to what the person submitted.
"""
import json
from datetime import datetime, timezone

import pytest

import api
import manual_drafts
import manual_tenders
import object_storage
import run
import store
from conftest import TEST_ACCOUNT_NAME, TEST_TENANT_ID, make_identity

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0}}
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _pdf(pages):
    """A real PDF; each item is page text, or None for an image-only page."""
    import fitz
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=10)
        else:
            page.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(0.2, 0.2, 0.2))
    data = doc.tobytes()
    doc.close()
    return data


def _fields_json(**over):
    f = {
        "tag_line": {"value": "Lieferung von Zelten", "confidence": 0.95, "source": "Lieferung von Zelten"},
        "description": {"value": "200 Zelte", "confidence": 0.9, "source": "200 Zelte"},
        "buyer": {"value": "BAAINBw", "confidence": 0.9, "source": "Auftraggeber: BAAINBw"},
        "country": {"value": "deu", "confidence": 0.8, "source": "Deutschland"},
        "place": {"value": None, "confidence": 0, "source": ""},
        "category": {"value": "supplies", "confidence": 0.85, "source": "Lieferleistung"},
        "procedure": {"value": "open", "confidence": 0.5, "source": "offenes Verfahren?"},
        "pub_date": {"value": "2026-07-10", "confidence": 0.9, "source": "10.07.2026"},
        "deadline": {"value": "2027-09-30T12:00", "confidence": 0.9, "source": "30.09.2027 12:00"},
        "cpv_codes": {"value": ["39522530-1", "bogus"], "confidence": 0.9, "source": "CPV 39522530"},
        "value": {"value": "1.200.000", "confidence": 0.4, "source": "ca. 1,2 Mio"},
        "value_currency": {"value": "eur", "confidence": 0.9, "source": "EUR"},
        "language": {"value": "deu", "confidence": 0.95, "source": ""},
        "url": {"value": "https://www.dtvp.de/Satellite/notice/123", "confidence": 0.9, "source": "dtvp.de"},
        "origin_site": {"value": {"name": "Deutsches Vergabeportal", "domain": "www.dtvp.de"},
                        "confidence": 0.9, "source": "veröffentlicht auf dtvp.de"},
    }
    f.update(over)
    return json.dumps({"fields": f})


class FakeClaude:
    """Stands in for anthropic.Anthropic(): image requests are transcribed,
    the text request returns `reply`. Records what it was asked."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else _fields_json()
        self.vision_calls = 0
        self.prompts = []
        self.messages = self

    def create(self, model, max_tokens, messages):
        content = messages[0]["content"]
        if isinstance(content, list):  # vision
            self.vision_calls += 1
            text = "Gescannte Seite: Zelte und Planen"
        else:
            self.prompts.append(content)
            text = self.reply
        return type("M", (), {"content": [type("B", (), {"text": text})()]})()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(object_storage, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.delenv("DOCUMENTS_S3_BUCKET", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(manual_tenders, "_fx_rates", lambda: FX_RATES)
    monkeypatch.setattr(run.translate, "translate_cached", lambda _c, t: ("EN: " + (t or ""), "ok"))
    c = store.init_db(db_path)
    store.ensure_tenant(c, TEST_TENANT_ID)
    return c


def _upload(name, data):
    key = object_storage.new_key(TEST_TENANT_ID, "manual_drafts", name)
    object_storage.put(key, data, content_type="application/pdf")
    return {"key": key, "filename": name, "content_type": "application/pdf", "size": len(data)}


def _ready_draft(conn, client=None, pages=("Lieferung von Zelten. CPV 39522530. Frist 30.09.2027",)):
    draft_id = store.create_draft(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME)
    manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id, _upload("notice.pdf", _pdf(pages)))
    manual_drafts.start_extraction(conn, TEST_TENANT_ID, draft_id)
    manual_drafts.process(conn, TEST_TENANT_ID, draft_id, client=client or FakeClaude())
    return store.get_draft(conn, TEST_TENANT_ID, draft_id)


def _form(site, **over):
    f = {"origin_site_id": site["id"], "url": "https://www.dtvp.de/Satellite/notice/123",
         "tag_line": "Lieferung von Zelten", "description": "200 Zelte", "buyer": "BAAINBw",
         "country": "DEU", "place": "", "category": "Supply", "procedure": "open",
         "pub_date": "2026-07-10", "deadline": "2027-09-30", "cpv_codes": ["39522530"],
         "value": "1200000", "value_currency": "EUR", "language": "deu"}
    f.update(over)
    return f


# ── nothing reaches tenders without a confirm ────────────────────────────────

def test_a_ready_draft_writes_nothing_to_tenders(conn):
    d = _ready_draft(conn)
    assert d["status"] == "ready"
    assert store.all_records(conn, TEST_TENANT_ID, include_deleted=True) == []


def test_failed_and_discarded_drafts_write_nothing(conn):
    bad = _ready_draft(conn, client=FakeClaude(reply="not json"))
    assert bad["status"] == "failed" and "could not be read" in bad["error"]
    manual_drafts.discard(conn, TEST_TENANT_ID, bad["id"])
    assert store.get_draft(conn, TEST_TENANT_ID, bad["id"])["status"] == "discarded"
    assert store.all_records(conn, TEST_TENANT_ID, include_deleted=True) == []


# ── text extraction ──────────────────────────────────────────────────────────

def test_text_layer_pages_are_read_locally_and_scanned_pages_go_to_vision(conn):
    client = FakeClaude()
    d = _ready_draft(conn, client=client, pages=("Lieferung von Zelten fuer die Bundeswehr, 200 Stueck", None))
    assert client.vision_calls == 1
    (f,) = d["files"]
    assert (f["pages"], f["vision_pages"], f["skipped_pages"]) == (2, 1, 0)
    text = store.get_draft(conn, TEST_TENANT_ID, d["id"])["extracted_text"]
    assert "Lieferung von Zelten" in text and "Gescannte Seite" in text
    assert "[notice.pdf · p.2]" in text


def test_vision_stops_at_the_cap_and_counts_what_it_skipped(conn, monkeypatch):
    monkeypatch.setattr(manual_drafts, "VISION_PAGE_CAP", 2)
    client = FakeClaude()
    d = _ready_draft(conn, client=client, pages=("Titel der Ausschreibung: Lieferung von Zelten", None, None, None))
    assert client.vision_calls == 2
    assert (d["files"][0]["vision_pages"], d["files"][0]["skipped_pages"]) == (2, 1)


def test_no_api_key_fails_cleanly_so_the_user_can_type(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    draft_id = store.create_draft(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME)
    manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id, _upload("a.pdf", _pdf(["x" * 100])))
    manual_drafts.start_extraction(conn, TEST_TENANT_ID, draft_id)
    manual_drafts.process(conn, TEST_TENANT_ID, draft_id)  # no client injected
    d = store.get_draft(conn, TEST_TENANT_ID, draft_id)
    assert d["status"] == "failed" and "type the details" in d["error"]


def test_only_pdfs_and_at_most_ten_files(conn):
    draft_id = store.create_draft(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME)
    with pytest.raises(manual_drafts.DraftError):
        manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id,
                               {"key": object_storage.new_key(TEST_TENANT_ID, "manual_drafts", "a.docx"),
                                "filename": "a.docx", "content_type": "application/msword", "size": 3})
    for i in range(manual_drafts.MAX_FILES):
        manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id, _upload(f"{i}.pdf", _pdf(["x"])))
    with pytest.raises(manual_drafts.DraftError):
        manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id, _upload("11.pdf", _pdf(["x"])))


def test_unreadable_pdf_fails_the_draft(conn):
    draft_id = store.create_draft(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME)
    manual_drafts.add_file(conn, TEST_TENANT_ID, draft_id, _upload("broken.pdf", b"not a pdf at all"))
    manual_drafts.start_extraction(conn, TEST_TENANT_ID, draft_id)
    manual_drafts.process(conn, TEST_TENANT_ID, draft_id, client=FakeClaude())
    d = store.get_draft(conn, TEST_TENANT_ID, draft_id)
    assert d["status"] == "failed" and "broken.pdf" in d["error"]


# ── field extraction and the confidence threshold ────────────────────────────

def test_values_are_normalised_and_unusable_ones_dropped(conn):
    e = _ready_draft(conn)["extraction"]
    assert e["cpv_codes"]["value"] == ["39522530"]           # check digit stripped, junk dropped
    assert e["deadline"]["value"] == "2027-09-30"             # time dropped
    assert e["category"]["value"] == "Supply"                 # same map as scraped records
    assert e["value_currency"]["value"] == "EUR"
    assert e["value"]["value"] == ""                          # "1.200.000" is ambiguous -> not used
    assert e["value"]["confidence"] == 0.0
    assert e["place"]["value"] == "" and e["place"]["confidence"] == 0.0


def test_low_confidence_fields_are_suggested_not_prefilled(conn):
    v = manual_drafts.view(_ready_draft(conn))
    assert "tag_line" in v["prefill"] and "deadline" in v["prefill"]
    assert "procedure" in v["suggestions"] and "procedure" not in v["prefill"]   # 0.5 < 0.7
    assert "place" not in v["prefill"] and "place" not in v["suggestions"]       # nothing found
    assert v["confidence_threshold"] == 0.7
    assert "extracted_text" not in v


def test_prompt_lists_every_field_and_forbids_guessing(conn):
    client = FakeClaude()
    _ready_draft(conn, client=client)
    (prompt,) = client.prompts
    for f in manual_drafts.EXTRACTED_FIELDS:
        assert f"- {f}:" in prompt
    assert "never guess" in prompt


# ── origin site read from the document ───────────────────────────────────────

def test_known_site_is_matched_by_domain_and_prefilled(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    v = manual_drafts.view(_ready_draft(conn), store.get_origin_sites(conn, TEST_TENANT_ID))
    assert v["prefill"]["origin_site_id"]["value"] == site["id"]
    assert v["new_origin_site"] is None


def test_known_site_is_matched_by_name(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "Deutsches  Vergabeportal")
    v = manual_drafts.view(_ready_draft(conn), store.get_origin_sites(conn, TEST_TENANT_ID))
    assert v["prefill"]["origin_site_id"]["value"] == site["id"]


def test_url_domain_alone_can_match_a_site(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    client = FakeClaude(reply=_fields_json(origin_site={"value": None, "confidence": 0, "source": ""}))
    v = manual_drafts.view(_ready_draft(conn, client=client), store.get_origin_sites(conn, TEST_TENANT_ID))
    assert v["prefill"]["origin_site_id"]["value"] == site["id"]


def test_unknown_site_is_offered_never_created(conn):
    v = manual_drafts.view(_ready_draft(conn), store.get_origin_sites(conn, TEST_TENANT_ID))
    assert v["new_origin_site"]["name"] == "Deutsches Vergabeportal"
    assert v["new_origin_site"]["domain"] == "dtvp.de"
    assert "origin_site_id" not in v["prefill"]
    assert store.get_origin_sites(conn, TEST_TENANT_ID) == []  # nothing created behind the user's back


def test_low_confidence_site_match_is_only_suggested(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    client = FakeClaude(reply=_fields_json(
        origin_site={"value": {"name": "DTVP"}, "confidence": 0.3, "source": "?"},
        url={"value": None, "confidence": 0, "source": ""}))
    v = manual_drafts.view(_ready_draft(conn, client=client), store.get_origin_sites(conn, TEST_TENANT_ID))
    assert v["suggestions"]["origin_site_id"]["value"] == site["id"]
    assert "origin_site_id" not in v["prefill"]


# ── confirm ──────────────────────────────────────────────────────────────────

def test_confirm_creates_through_the_typed_path_and_attaches_files(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    d = _ready_draft(conn)
    draft_key = d["files"][0]["object_key"]
    t = manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], TEST_ACCOUNT_NAME, _form(site),
                              now=NOW, fx_rates=FX_RATES)
    assert (t["pub_number"], t["entry_method"], t["created_by"]) == ("MAN-2026-0001", "manual", TEST_ACCOUNT_NAME)
    assert t["description_en"] == "EN: 200 Zelte"   # translated by the shared route on create
    (listed,) = store.list_documents(conn, TEST_TENANT_ID, t["pub_number"])
    assert listed["filename"] == "notice.pdf"
    doc = store.get_document(conn, TEST_TENANT_ID, listed["id"])
    assert object_storage.row_read(doc).startswith(b"%PDF")
    assert doc["object_key"].startswith(f"{TEST_TENANT_ID}/tender_documents/MAN-2026-0001/")
    with pytest.raises(object_storage.ObjectNotFound):
        object_storage.get(draft_key)  # moved, not duplicated


def test_confirmed_draft_keeps_model_proposal_and_human_answer(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    d = _ready_draft(conn)
    manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], TEST_ACCOUNT_NAME,
                          _form(site, procedure="restricted"), now=NOW, fx_rates=FX_RATES)
    after = store.get_draft(conn, TEST_TENANT_ID, d["id"])
    assert after["status"] == "confirmed" and after["confirmed_pub_number"] == "MAN-2026-0001"
    assert after["extraction"]["procedure"]["value"] == "open"        # what the model said
    assert after["confirmed_values"]["procedure"] == "restricted"     # what the person asserted
    assert after["confirmed_by"] == TEST_ACCOUNT_NAME


def test_confirm_twice_creates_one_tender(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    d = _ready_draft(conn)
    manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], "a", _form(site), now=NOW, fx_rates=FX_RATES)
    with pytest.raises(manual_drafts.DraftError):
        manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], "a", _form(site), now=NOW, fx_rates=FX_RATES)
    assert len(store.all_records(conn, TEST_TENANT_ID)) == 1


def test_bad_input_on_confirm_leaves_the_draft_confirmable(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    d = _ready_draft(conn)
    with pytest.raises(manual_tenders.ManualTenderError):
        manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], "a", _form(site, tag_line=""), now=NOW, fx_rates=FX_RATES)
    assert store.get_draft(conn, TEST_TENANT_ID, d["id"])["status"] == "ready"
    assert store.all_records(conn, TEST_TENANT_ID) == []
    manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], "a", _form(site), now=NOW, fx_rates=FX_RATES)


def test_a_failed_draft_can_still_be_typed_and_confirmed(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    d = _ready_draft(conn, client=FakeClaude(reply="garbage"))
    t = manual_drafts.confirm(conn, TEST_TENANT_ID, d["id"], "a", _form(site), now=NOW, fx_rates=FX_RATES)
    assert store.list_documents(conn, TEST_TENANT_ID, t["pub_number"])  # files still attached


# ── tenancy, retention, API ──────────────────────────────────────────────────

def test_drafts_are_tenant_isolated(conn):
    store.ensure_tenant(conn, 2)
    d = _ready_draft(conn)
    assert store.get_draft(conn, 2, d["id"]) is None
    with pytest.raises(api.HTTPException) as e:
        api.get_manual_draft(d["id"], tenant_id=2)
    assert e.value.status_code == 404
    with pytest.raises(api.HTTPException):
        api.confirm_manual_draft(d["id"], api.ManualTenderBody(**_form({"id": 1})), identity=make_identity(tenant_id=2))


def test_stale_unconfirmed_drafts_are_purged_and_confirmed_ones_kept(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    stale = _ready_draft(conn)
    kept = _ready_draft(conn)
    manual_drafts.confirm(conn, TEST_TENANT_ID, kept["id"], "a", _form(site), now=NOW, fx_rates=FX_RATES)
    later = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    assert manual_drafts.purge_stale(conn, now=later) == 1
    assert store.get_draft(conn, TEST_TENANT_ID, stale["id"])["status"] == "discarded"
    assert store.get_draft(conn, TEST_TENANT_ID, stale["id"])["extracted_text"] is None
    with pytest.raises(object_storage.ObjectNotFound):
        object_storage.get(stale["files"][0]["object_key"])
    assert store.get_draft(conn, TEST_TENANT_ID, kept["id"])["status"] == "confirmed"


def test_api_flow_end_to_end(conn, monkeypatch):
    """create -> add file (direct upload) -> extract -> poll -> confirm."""
    from starlette.datastructures import UploadFile
    import asyncio
    import io
    store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    monkeypatch.setattr(manual_drafts, "_claude", lambda: FakeClaude())
    d = api.create_manual_draft(identity=make_identity())
    up = UploadFile(file=io.BytesIO(_pdf(["Lieferung von Zelten"])), filename="n.pdf",
                    headers={"content-type": "application/pdf"})
    d = asyncio.run(api.add_manual_draft_file(d["id"], file=up, object_key=None, filename=None,
                                              content_type=None, tenant_id=TEST_TENANT_ID))
    assert len(d["files"]) == 1

    class _BG:
        def add_task(self, fn, *a):
            fn(*a)

    api.extract_manual_draft(d["id"], _BG(), tenant_id=TEST_TENANT_ID)
    d = api.get_manual_draft(d["id"], tenant_id=TEST_TENANT_ID)
    assert d["status"] == "ready" and d["prefill"]["origin_site_id"]["site"]["name"] == "DTVP"
    form = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in d["prefill"].items()}
    body = api.ManualTenderBody(**{**_form({"id": form["origin_site_id"]}),
                                   **{k: v for k, v in form.items() if k in manual_tenders.USER_FIELDS}})
    t = api.confirm_manual_draft(d["id"], body, identity=make_identity())
    assert t["pub_number"].startswith("MAN-") and t["tag_line"] == "Lieferung von Zelten"
    assert api.list_manual_drafts(tenant_id=TEST_TENANT_ID) == []


@pytest.mark.parametrize("method, path, handler", [
    ("POST", "/api/manual-drafts", "create_manual_draft"),
    ("GET", "/api/manual-drafts", "list_manual_drafts"),
    ("GET", "/api/manual-drafts/7", "get_manual_draft"),
    ("POST", "/api/manual-drafts/7/files", "add_manual_draft_file"),
    ("POST", "/api/manual-drafts/7/extract", "extract_manual_draft"),
    ("POST", "/api/manual-drafts/7/confirm", "confirm_manual_draft"),
    ("POST", "/api/manual-drafts/7/discard", "discard_manual_draft"),
])
def test_routes_resolve(method, path, handler):
    from starlette.routing import Match
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "query_string": b"", "headers": []}
    full = [r for r in api.app.routes if getattr(r, "matches", None) and r.matches(scope)[0] == Match.FULL]
    assert full and full[0].endpoint.__name__ == handler


def test_presign_accepts_the_drafts_area_without_a_pub_number(conn):
    out = api.presign_upload(api.UploadPresignBody(area="manual_drafts", filename="n.pdf",
                                                   content_type="application/pdf", size=1000),
                             tenant_id=TEST_TENANT_ID)
    assert out["mode"] == "direct"
