"""Step 50 — Scout document translation, attach, and library
(translation_command.md).

translate_document.py: DeepL's async *document* API (upload -> poll ->
download), a different surface from translate.py's synchronous text
endpoint — requests.* is mocked throughout, same convention as
test_18_translate.py. Also covers the quota guard (a flat 50,000-character-
per-document estimate, a configured allowance since DeepL's own
character_limit doesn't reflect the real contractual ceiling, and a
configurable reserve protecting ingest translation) and the S3-compatible
storage helpers (mocked — no real S3 endpoint in the test suite).

store.py: translated_documents CRUD and document_tender_links' many-to-many
attach/detach, toggling the same row on re-attach like tender_duplicates'
resolve/unresolve rather than inserting a fresh row each cycle.

API section (background processing called directly, not via BackgroundTasks,
same pattern as test_28/test_29): upload/list/detail/download, quota
endpoint, attach/detach + the "attach to whole duplicate group" convenience,
and the async poll-download lifecycle (_process_translation) reaching
'ready' or 'failed'.

Composer integration: _composer_document_pages prefers a matching translated
document (by content hash) over the original when one exists and is ready —
Composer's extraction previously had no language handling at all.
"""
import asyncio
import hashlib
import io

import pytest
from fastapi import BackgroundTasks, UploadFile

import api
import store
import translate_document
import vault
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME, make_identity

OTHER_TENANT_ID = 999


# ── translate_document.py — pure DeepL calls (requests mocked) ──────────────

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json


def test_upload_document_success(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(200, {"document_id": "d1", "document_key": "k1"}))
    doc_id, doc_key, err = translate_document.upload_document(b"bytes", "f.pdf", "EN", api_key="fakekey")
    assert (doc_id, doc_key, err) == ("d1", "k1", None)


def test_upload_document_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    doc_id, doc_key, err = translate_document.upload_document(b"bytes", "f.pdf", "EN", api_key=None)
    assert doc_id is None and "not configured" in err


def test_upload_document_http_error_surfaces_message(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(413, {"message": "file too large"}))
    doc_id, doc_key, err = translate_document.upload_document(b"bytes", "f.pdf", "EN", api_key="fakekey")
    assert doc_id is None
    assert err == "file too large"


def test_check_status_translating(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(200, {"status": "translating", "seconds_remaining": 5}))
    result = translate_document.check_status("d1", "k1", api_key="fakekey")
    assert result == {"status": "translating", "seconds_remaining": 5,
                       "billed_characters": None, "error_message": None}


def test_check_status_done(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(200, {"status": "done", "billed_characters": 50000}))
    result = translate_document.check_status("d1", "k1", api_key="fakekey")
    assert result["status"] == "done"
    assert result["billed_characters"] == 50000


def test_check_status_deepl_error_surfaces_message(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(200, {"status": "error", "message": "no extractable text"}))
    result = translate_document.check_status("d1", "k1", api_key="fakekey")
    assert result == {"status": "error", "seconds_remaining": None,
                       "billed_characters": None, "error_message": "no extractable text"}


def test_check_status_network_failure_is_unavailable(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no network")
    monkeypatch.setattr(translate_document.requests, "post", boom)
    result = translate_document.check_status("d1", "k1", api_key="fakekey")
    assert result["status"] == "unavailable"


def test_download_result_success(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(200, content=b"translated bytes"))
    content, err = translate_document.download_result("d1", "k1", api_key="fakekey")
    assert (content, err) == (b"translated bytes", None)


def test_download_result_failure_surfaces_message(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "post",
                         lambda *a, **k: _FakeResponse(404, {"message": "document not found"}))
    content, err = translate_document.download_result("d1", "k1", api_key="fakekey")
    assert content is None
    assert err == "document not found"


def test_get_usage_success(monkeypatch):
    monkeypatch.setattr(translate_document.requests, "get",
                         lambda *a, **k: _FakeResponse(200, {"character_count": 100, "character_limit": 10**12}))
    usage = translate_document.get_usage(api_key="fakekey")
    assert usage == {"character_count": 100, "character_limit": 10**12}


def test_get_usage_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(translate_document.requests, "get", boom)
    assert translate_document.get_usage(api_key="fakekey") is None


# ── Quota guard ───────────────────────────────────────────────────────────────

def test_estimate_cost_is_flat_per_document_minimum():
    assert translate_document.estimate_cost(1) == 50_000
    assert translate_document.estimate_cost(20) == 1_000_000


def test_check_quota_allowed_with_headroom(monkeypatch):
    monkeypatch.setattr(translate_document, "get_usage",
                         lambda api_key=None, timeout=15: {"character_count": 1_000_000, "character_limit": 10**12})
    result = translate_document.check_quota(1, included_allowance=12_000_000, reserve=2_000_000)
    assert result["allowed"] is True
    assert result["estimated_cost"] == 50_000
    assert result["remaining_characters"] == 11_000_000
    assert result["remaining_documents"] == 220  # floor(11,000,000 / 50,000)


def test_check_quota_refused_within_reserve(monkeypatch):
    # 10.5M used, 12M allowance, 2M reserve -> only 1.5M headroom, refuse.
    monkeypatch.setattr(translate_document, "get_usage",
                         lambda api_key=None, timeout=15: {"character_count": 10_500_000, "character_limit": 10**12})
    result = translate_document.check_quota(1, included_allowance=12_000_000, reserve=2_000_000)
    assert result["allowed"] is False
    assert result["reason"]


def test_check_quota_fails_open_when_usage_unavailable(monkeypatch):
    monkeypatch.setattr(translate_document, "get_usage", lambda api_key=None, timeout=15: None)
    result = translate_document.check_quota(3, included_allowance=12_000_000, reserve=2_000_000)
    assert result["allowed"] is True
    assert result["remaining_characters"] is None
    assert result["reason"] == "usage_unavailable"


def test_check_quota_prefers_a_smaller_real_character_limit(monkeypatch):
    # DeepL starts actually enforcing a cap smaller than our configured
    # allowance — that's more trustworthy than the configured default.
    monkeypatch.setattr(translate_document, "get_usage",
                         lambda api_key=None, timeout=15: {"character_count": 900_000, "character_limit": 1_000_000})
    result = translate_document.check_quota(1, included_allowance=12_000_000, reserve=0)
    assert result["remaining_characters"] == 100_000  # 1,000,000 - 900,000, not 12M - 900k


# ── S3 storage helpers (mocked — no real endpoint in tests) ──────────────────

def test_storage_not_configured_raises_when_no_bucket_env(monkeypatch):
    monkeypatch.delenv("DOCUMENTS_S3_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    with pytest.raises(translate_document.StorageNotConfigured):
        translate_document.upload_to_s3("documents/1/abc/original.pdf", b"data")


def test_storage_key_uses_content_hash_or_id_and_extension():
    key = translate_document.storage_key(1, "abc123", "original", "SOW.pdf")
    assert key == "documents/1/abc123/original.pdf"


# ── In-memory fake S3 used by the store/API sections below ──────────────────

@pytest.fixture
def fake_s3(monkeypatch):
    blobs = {}
    monkeypatch.setattr(translate_document, "upload_to_s3", lambda key, content: blobs.__setitem__(key, content))
    monkeypatch.setattr(translate_document, "download_from_s3", lambda key: blobs[key])
    return blobs


# ── store.py — translated_documents CRUD ─────────────────────────────────────

def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def test_create_and_get_translated_document(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["filename"] == "sow.pdf"
    assert doc["status"] == "queued"
    assert doc["uploaded_by"] == TEST_ACCOUNT_NAME
    assert store.get_translated_document(conn, OTHER_TENANT_ID, doc_id) is None


def test_update_translated_document_status_is_sticky(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "failed", error_message="boom")
    # A later status-only update (poll loop) must not blank the error out.
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "failed")
    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["error_message"] == "boom"


def test_get_translated_document_by_hash_only_when_ready(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    assert store.get_translated_document_by_hash(conn, TEST_TENANT_ID, "h") is None  # still queued
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key="documents/1/1/translated.pdf")
    found = store.get_translated_document_by_hash(conn, TEST_TENANT_ID, "h")
    assert found["id"] == doc_id
    assert store.get_translated_document_by_hash(conn, OTHER_TENANT_ID, "h") is None


def test_list_translated_documents_filters(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    ready_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h1/original.pdf",
        "h1", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    store.update_translated_document_status(conn, TEST_TENANT_ID, ready_id, "ready",
                                              translated_storage_key="documents/1/1/translated.pdf")
    store.create_translated_document(
        conn, TEST_TENANT_ID, "specs.docx", "application/msword", 50, "documents/1/h2/original.docx",
        "h2", "DE", "EN", "d2", "k2", TEST_ACCOUNT_NAME)  # stays queued

    assert len(store.list_translated_documents(conn, TEST_TENANT_ID)) == 2
    assert [d["id"] for d in store.list_translated_documents(conn, TEST_TENANT_ID, status="ready")] == [ready_id]
    assert [d["id"] for d in store.list_translated_documents(conn, TEST_TENANT_ID, language="FR")] == [ready_id]
    assert store.list_translated_documents(conn, TEST_TENANT_ID, attached=True) == []


def test_attach_detach_and_reattach_toggles_same_row(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)

    store.attach_document_to_tender(conn, TEST_TENANT_ID, doc_id, "P-1", TEST_ACCOUNT_NAME)
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == ["P-1"]
    assert store.get_active_document_links_for_tender(conn, TEST_TENANT_ID, "P-1") == [doc_id]

    store.detach_document_from_tender(conn, TEST_TENANT_ID, doc_id, "P-1", "colleague@example.com")
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == []

    # Re-attach reuses the same row (no duplicate rows) and clears the detach.
    store.attach_document_to_tender(conn, TEST_TENANT_ID, doc_id, "P-1", TEST_ACCOUNT_NAME)
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == ["P-1"]
    with conn.connect() as c:
        from schema import document_tender_links
        rows = c.execute(document_tender_links.select().where(
            document_tender_links.c.document_id == doc_id)).fetchall()
    assert len(rows) == 1


def test_detach_unknown_pair_raises(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    with pytest.raises(ValueError):
        store.detach_document_from_tender(conn, TEST_TENANT_ID, doc_id, "P-NEVER", TEST_ACCOUNT_NAME)


# ── API endpoints ─────────────────────────────────────────────────────────────

def _file(name="sow.pdf", content=b"%PDF-1.4 fake pdf content"):
    return UploadFile(file=io.BytesIO(content), filename=name)


def _tender(pub_number, source="TED", pub_date="2026-06-01", deadline="2030-01-01", status="new"):
    return {"source": source, "pub_number": pub_number, "tag_line": "Tent supply", "description": "",
            "buyer": "Ministry X", "country": "SWE", "place": "", "category": "Supply", "procedure": "open",
            "pub_date": pub_date, "deadline": deadline, "cpv_codes": ["39522530"], "matched_terms": ["tent"],
            "match_source": "cpv", "url": f"http://{source.lower()}.example/{pub_number}",
            "first_seen": None, "exclude_reason": "", "status": status}


def test_quota_endpoint(monkeypatch):
    monkeypatch.setattr(translate_document, "get_usage",
                         lambda api_key=None, timeout=15: {"character_count": 0, "character_limit": 10**12})
    result = api.get_translation_quota(document_count=2, tenant_id=TEST_TENANT_ID)
    assert result["estimated_cost"] == 100_000
    assert result["allowed"] is True


def test_upload_endpoint_happy_path(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 50000,
                                                   "remaining_characters": 1000000,
                                                   "remaining_documents": 20, "reason": None})
    monkeypatch.setattr(translate_document, "upload_document",
                         lambda *a, **k: ("d1", "k1", None))
    background = BackgroundTasks()
    result = asyncio.run(api.upload_translated_document(
        background, file=_file(), target_lang="EN", source_lang=None, identity=make_identity()))
    assert result["filename"] == "sow.pdf"
    assert result["status"] == "queued"
    assert "attached_tenders" in result
    assert len(background.tasks) == 1  # queued, not run inline
    assert "documents/" in list(fake_s3.keys())[0]


def test_upload_endpoint_refused_when_quota_guard_says_no(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": False, "estimated_cost": 50000,
                                                   "remaining_characters": 10000,
                                                   "remaining_documents": 0, "reason": "too close to the reserve"})
    background = BackgroundTasks()
    with pytest.raises(Exception) as exc:
        asyncio.run(api.upload_translated_document(
            background, file=_file(), target_lang="EN", source_lang=None, identity=make_identity()))
    assert getattr(exc.value, "status_code", None) == 409


def test_process_translation_reaches_ready(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", None, "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    monkeypatch.setattr(translate_document, "check_status",
                         lambda *a, **k: {"status": "done", "seconds_remaining": None,
                                           "billed_characters": 50000, "error_message": None})
    monkeypatch.setattr(translate_document, "download_result", lambda *a, **k: (b"translated pdf", None))

    api._process_translation(TEST_TENANT_ID, doc_id)

    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["status"] == "ready"
    assert doc["billed_characters"] == 50000
    assert doc["translated_storage_key"]
    assert fake_s3[doc["translated_storage_key"]] == b"translated pdf"


def test_process_translation_deepl_error_marks_failed_with_message(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "scan.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", None, "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    monkeypatch.setattr(translate_document, "check_status",
                         lambda *a, **k: {"status": "error", "seconds_remaining": None,
                                           "billed_characters": None,
                                           "error_message": "this document contains no extractable text"})

    api._process_translation(TEST_TENANT_ID, doc_id)

    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["status"] == "failed"
    assert doc["error_message"] == "this document contains no extractable text"


def test_process_translation_download_failure_marks_failed(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", None, "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    monkeypatch.setattr(translate_document, "check_status",
                         lambda *a, **k: {"status": "done", "seconds_remaining": None,
                                           "billed_characters": 50000, "error_message": None})
    monkeypatch.setattr(translate_document, "download_result", lambda *a, **k: (None, "gone"))

    api._process_translation(TEST_TENANT_ID, doc_id)

    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["status"] == "failed"
    assert doc["error_message"] == "gone"


def test_list_get_and_download_endpoints(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original bytes"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key="documents/1/x/translated.pdf")
    fake_s3["documents/1/x/translated.pdf"] = b"translated bytes"

    listed = api.list_translated_documents(tenant_id=TEST_TENANT_ID)
    assert len(listed) == 1 and "deepl_document_id" not in listed[0]

    detail = api.get_translated_document(doc_id, tenant_id=TEST_TENANT_ID)
    assert detail["status"] == "ready"

    original = api.download_translated_document(doc_id, version="original", tenant_id=TEST_TENANT_ID)
    assert original.body == b"original bytes"
    translated = api.download_translated_document(doc_id, version="translated", tenant_id=TEST_TENANT_ID)
    assert translated.body == b"translated bytes"


def test_attach_and_detach_endpoints(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)

    result = api.attach_translated_document(doc_id, "P-1", identity=make_identity())
    assert result["attached"] is True
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == ["P-1"]

    result = api.detach_translated_document(doc_id, "P-1", identity=make_identity())
    assert result["attached"] is False
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == []


def test_attach_endpoint_404_for_unknown_tender(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    with pytest.raises(Exception) as exc:
        api.attach_translated_document(doc_id, "NEVER-EXISTED", identity=make_identity())
    assert getattr(exc.value, "status_code", None) == 404


def test_attach_to_duplicate_group_attaches_every_active_member(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A", pub_date="2026-06-01"))
    store.upsert(conn, TEST_TENANT_ID, _tender("B", pub_date="2026-06-15"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "A", "B", "same_source")
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)

    result = api.attach_translated_document_to_group(doc_id, "A", identity=make_identity())
    assert set(result["tenders"]) == {"A", "B"}
    assert store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id) == ["A", "B"] or \
        set(store.get_active_tender_links_for_document(conn, TEST_TENANT_ID, doc_id)) == {"A", "B"}


def test_attach_to_duplicate_group_falls_back_to_solo_tender_without_a_group(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("A"))
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)

    result = api.attach_translated_document_to_group(doc_id, "A", identity=make_identity())
    assert result["tenders"] == ["A"]


# ── Composer integration: prefers a translated document when available ──────

def test_composer_interpret_prefers_translated_text(tmp_path, monkeypatch, fake_s3):
    # vault.parse_document's real PDF/DOCX parsing isn't the point here — it
    # already has its own coverage in test_28_vault.py. This test is about
    # the content-hash lookup + S3 fetch choosing the translated file's text
    # over the original's, so the parse step itself is stubbed to just read
    # whatever file path it's given.
    monkeypatch.setattr(vault, "parse_document", lambda path, content_type: open(path, encoding="utf-8").read())
    conn = _db(tmp_path, monkeypatch)
    original_bytes = b"Objet du marche: fourniture de tentes"
    sow_path = tmp_path / "sow.docx"
    sow_path.write_bytes(original_bytes)
    content_hash = hashlib.sha256(original_bytes).hexdigest()

    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.docx", "application/msword", len(original_bytes),
        f"documents/1/{content_hash}/original.docx", content_hash, "FR", "EN", "d1", "k1", TEST_ACCOUNT_NAME)
    translated_key = f"documents/1/{doc_id}/translated.docx"
    fake_s3[translated_key] = b"Subject of the contract: supply of tents"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key=translated_key)

    full = {"pub_number": "P-1", "filename": "sow.docx", "content_type": "application/msword",
            "storage_path": str(sow_path), "role": "sow"}
    pages = api._composer_document_pages(TEST_TENANT_ID, full)
    assert pages == ["Subject of the contract: supply of tents"]


def test_composer_interpret_falls_back_to_original_without_a_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "parse_document", lambda path, content_type: open(path, encoding="utf-8").read())
    conn = _db(tmp_path, monkeypatch)
    original_bytes = b"Objet du marche: fourniture de tentes"
    sow_path = tmp_path / "sow.docx"
    sow_path.write_bytes(original_bytes)

    full = {"pub_number": "P-1", "filename": "sow.docx", "content_type": "application/msword",
            "storage_path": str(sow_path), "role": "sow"}
    pages = api._composer_document_pages(TEST_TENANT_ID, full)
    assert pages == [original_bytes.decode()]
