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


# Review follow-up to CR-012 §1: the document API used to hardcode the Pro
# host and skip key cleaning, so a Free (":fx") or whitespace-dirty key 403'd
# every document call even after translate.py's own fix.

def _capture_post(monkeypatch, captured, response):
    def fake_post(url, **kwargs):
        captured.append((url, kwargs["headers"]["Authorization"]))
        return response
    monkeypatch.setattr(translate_document.requests, "post", fake_post)


def test_document_calls_use_free_host_for_fx_key_and_clean_the_key(monkeypatch):
    captured = []
    _capture_post(monkeypatch, captured, _FakeResponse(200, {"document_id": "d1", "document_key": "k1",
                                                              "status": "done"}, content=b"x"))
    translate_document.upload_document(b"bytes", "f.pdf", "EN-GB", api_key=' "fakekey:fx"\n')
    translate_document.check_status("d1", "k1", api_key="fakekey:fx ")
    translate_document.download_result("d1", "k1", api_key="fakekey:fx\n")
    assert [u for u, _ in captured] == [
        translate_document.FREE_DOCUMENT_ENDPOINT,
        f"{translate_document.FREE_DOCUMENT_ENDPOINT}/d1",
        f"{translate_document.FREE_DOCUMENT_ENDPOINT}/d1/result",
    ]
    assert {a for _, a in captured} == {"DeepL-Auth-Key fakekey:fx"}


def test_document_calls_use_pro_host_for_pro_key(monkeypatch):
    captured = []
    _capture_post(monkeypatch, captured, _FakeResponse(200, {"document_id": "d1", "document_key": "k1"}))
    translate_document.upload_document(b"bytes", "f.pdf", "EN-GB", api_key="fakekey")
    assert captured[0][0] == translate_document.PRO_DOCUMENT_ENDPOINT


def test_get_usage_picks_host_by_key_type_and_cleans_key(monkeypatch):
    import translate
    captured = []
    def fake_get(url, **kwargs):
        captured.append((url, kwargs["headers"]["Authorization"]))
        return _FakeResponse(200, {"character_count": 1, "character_limit": 2})
    monkeypatch.setattr(translate_document.requests, "get", fake_get)
    translate_document.get_usage(api_key="fakekey:fx\n")
    translate_document.get_usage(api_key="'fakekey'")
    assert captured == [(translate.FREE_USAGE_ENDPOINT, "DeepL-Auth-Key fakekey:fx"),
                        (translate.PRO_USAGE_ENDPOINT, "DeepL-Auth-Key fakekey")]


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

def test_storage_falls_back_to_local_volume_when_no_bucket_env(tmp_path, monkeypatch):
    # Production had no S3 variables, so every upload 503'd; the persistent
    # volume is the fallback now.
    monkeypatch.delenv("DOCUMENTS_S3_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    monkeypatch.setattr(translate_document, "LOCAL_STORAGE_ROOT", tmp_path)
    key = "documents/1/abc/original.docx"
    translate_document.upload_to_s3(key, b"data")
    assert (tmp_path / key).read_bytes() == b"data"
    assert translate_document.download_from_s3(key) == b"data"
    translate_document.delete_from_s3(key)
    assert not (tmp_path / key).exists()
    translate_document.delete_from_s3(key)  # idempotent


def test_local_storage_rejects_keys_escaping_the_root(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCUMENTS_S3_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    monkeypatch.setattr(translate_document, "LOCAL_STORAGE_ROOT", tmp_path)
    with pytest.raises(ValueError):
        translate_document.upload_to_s3("../../etc/evil", b"x")


def test_storage_key_uses_content_hash_or_id_and_extension():
    key = translate_document.storage_key(1, "abc123", "original", "SOW.pdf")
    assert key == "documents/1/abc123/original.pdf"


# ── In-memory fake S3 used by the store/API sections below ──────────────────

@pytest.fixture
def fake_s3(monkeypatch):
    blobs = {}
    monkeypatch.setattr(translate_document, "upload_to_s3", lambda key, content: blobs.__setitem__(key, content))
    monkeypatch.setattr(translate_document, "download_from_s3", lambda key: blobs[key])
    monkeypatch.setattr(translate_document, "delete_from_s3", lambda key: blobs.pop(key, None))
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
    result = api.upload_translated_document(
        background, file=_file(), source_lang=None, identity=make_identity())
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
        api.upload_translated_document(
            background, file=_file(), source_lang=None, identity=make_identity())
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


# ---- CR-012 2: Translate dropbox additions ----

# ---- already-English detection ----

def test_detect_already_english_true_for_english_sample(tmp_path, monkeypatch):
    sample_path = tmp_path / "doc.txt"
    sample_path.write_text("This is an English sentence.", encoding="utf-8")
    monkeypatch.setattr(translate_document.translate, "translate_and_detect",
                         lambda text, api_key=None: (text, "eng", "ok"))
    assert translate_document.detect_already_english(str(sample_path), "text/plain") is True


def test_detect_already_english_false_for_non_english_sample(tmp_path, monkeypatch):
    sample_path = tmp_path / "doc.txt"
    sample_path.write_text("Ceci est une phrase francaise.", encoding="utf-8")
    monkeypatch.setattr(translate_document.translate, "translate_and_detect",
                         lambda text, api_key=None: (text, "fra", "ok"))
    assert translate_document.detect_already_english(str(sample_path), "text/plain") is False


def test_detect_already_english_false_when_detection_unavailable(tmp_path, monkeypatch):
    sample_path = tmp_path / "doc.txt"
    sample_path.write_text("Some text.", encoding="utf-8")
    monkeypatch.setattr(translate_document.translate, "translate_and_detect",
                         lambda text, api_key=None: (None, None, "unavailable"))
    assert translate_document.detect_already_english(str(sample_path), "text/plain") is False


def test_detect_already_english_false_for_unsupported_extension(tmp_path, monkeypatch):
    # .pptx isn't in _TEXT_EXTRACTABLE_EXTENSIONS -- never even attempts detection
    called = []
    sample_path = tmp_path / "doc.pptx"
    sample_path.write_bytes(b"fake pptx bytes")
    monkeypatch.setattr(translate_document.translate, "translate_and_detect",
                         lambda *a, **k: called.append(1) or (None, None, "ok"))
    assert translate_document.detect_already_english(str(sample_path), "application/vnd.ms-powerpoint") is False
    assert called == []


def test_detect_already_english_false_for_empty_file(tmp_path, monkeypatch):
    sample_path = tmp_path / "doc.txt"
    sample_path.write_text("   ", encoding="utf-8")
    assert translate_document.detect_already_english(str(sample_path), "text/plain") is False


# ---- upload endpoint: new validation + already-English path ----

def test_upload_endpoint_rejects_unsupported_extension(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    background = BackgroundTasks()
    with pytest.raises(Exception) as exc:
        api.upload_translated_document(
            background, file=_file(name="malware.exe"), source_lang=None, identity=make_identity())
    assert getattr(exc.value, "status_code", None) == 422


def test_upload_endpoint_rejects_oversized_file(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    big = b"x" * (translate_document.MAX_DOCUMENT_SIZE_BYTES + 1)
    background = BackgroundTasks()
    with pytest.raises(Exception) as exc:
        api.upload_translated_document(
            background, file=_file(content=big), source_lang=None, identity=make_identity())
    assert getattr(exc.value, "status_code", None) == 413


def test_upload_endpoint_already_english_skips_deepl_and_background_task(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 50000,
                                                   "remaining_characters": 1000000,
                                                   "remaining_documents": 20, "reason": None})
    monkeypatch.setattr(translate_document, "detect_already_english", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(translate_document, "upload_document", lambda *a, **k: called.append(1) or ("d1", "k1", None))
    background = BackgroundTasks()
    result = api.upload_translated_document(
        background, file=_file(), source_lang=None, identity=make_identity())
    assert result["status"] == "already_english"
    assert called == []  # never called DeepL's document upload at all
    assert len(background.tasks) == 0  # nothing to poll


def test_upload_endpoint_target_lang_is_always_en_gb(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 50000,
                                                   "remaining_characters": 1000000,
                                                   "remaining_documents": 20, "reason": None})
    monkeypatch.setattr(translate_document, "detect_already_english", lambda *a, **k: False)
    captured = {}
    def fake_upload(content, filename, target_lang, source_lang=None, api_key=None, timeout=30):
        captured["target_lang"] = target_lang
        return "d1", "k1", None
    monkeypatch.setattr(translate_document, "upload_document", fake_upload)
    background = BackgroundTasks()
    api.upload_translated_document(background, file=_file(), source_lang=None, identity=make_identity())
    assert captured["target_lang"] == "EN-GB"


# ---- retry endpoint ----

def test_retry_endpoint_resubmits_a_failed_document(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original bytes"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "failed", error_message="boom")
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 50000,
                                                   "remaining_characters": 1000000,
                                                   "remaining_documents": 20, "reason": None})
    monkeypatch.setattr(translate_document, "upload_document", lambda *a, **k: ("d2", "k2", None))
    background = BackgroundTasks()
    result = api.retry_translated_document(doc_id, background, tenant_id=TEST_TENANT_ID)
    assert result["status"] == "queued"
    assert result["error_message"] is None
    assert len(background.tasks) == 1
    doc = store.get_translated_document(conn, TEST_TENANT_ID, doc_id)
    assert doc["deepl_document_id"] == "d2"


def test_retry_endpoint_refuses_a_non_failed_document(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    background = BackgroundTasks()
    with pytest.raises(Exception) as exc:
        api.retry_translated_document(doc_id, background, tenant_id=TEST_TENANT_ID)
    assert getattr(exc.value, "status_code", None) == 409


# ---- delete endpoint ----

def test_delete_endpoint_removes_document_and_s3_objects(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original"
    translated_key = "documents/1/1/translated.pdf"
    fake_s3[translated_key] = b"translated"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key=translated_key)

    result = api.delete_translated_document(doc_id, tenant_id=TEST_TENANT_ID)
    assert result == {"deleted": True}
    assert store.get_translated_document(conn, TEST_TENANT_ID, doc_id) is None
    assert "documents/1/h/original.pdf" not in fake_s3
    assert translated_key not in fake_s3


def test_delete_endpoint_404_for_missing_document(tmp_path, monkeypatch, fake_s3):
    _db(tmp_path, monkeypatch)
    with pytest.raises(Exception) as exc:
        api.delete_translated_document(9999, tenant_id=TEST_TENANT_ID)
    assert getattr(exc.value, "status_code", None) == 404


def test_delete_endpoint_cross_tenant_cannot_delete_another_orgs_document(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 100, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    with pytest.raises(Exception) as exc:
        api.delete_translated_document(doc_id, tenant_id=OTHER_TENANT_ID)
    assert getattr(exc.value, "status_code", None) == 404
    assert store.get_translated_document(conn, TEST_TENANT_ID, doc_id) is not None  # untouched


# ---- download-all (zip) ----

def test_download_all_zips_ready_documents(tmp_path, monkeypatch, fake_s3):
    import zipfile, io as _io
    conn = _db(tmp_path, monkeypatch)
    id1 = store.create_translated_document(
        conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 10, "documents/1/a/original.pdf",
        "a", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/a/translated.pdf"] = b"translated A"
    store.update_translated_document_status(conn, TEST_TENANT_ID, id1, "ready",
                                              translated_storage_key="documents/1/a/translated.pdf")
    id2 = store.create_translated_document(
        conn, TEST_TENANT_ID, "b.pdf", "application/pdf", 10, "documents/1/b/original.pdf",
        "b", "FR", "EN-GB", "d2", "k2", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/b/translated.pdf"] = b"translated B"
    store.update_translated_document_status(conn, TEST_TENANT_ID, id2, "ready",
                                              translated_storage_key="documents/1/b/translated.pdf")

    resp = api.download_all_translated_documents(ids=f"{id1},{id2}", tenant_id=TEST_TENANT_ID)
    zf = zipfile.ZipFile(_io.BytesIO(resp.body))
    assert set(zf.namelist()) == {"a_EN.pdf", "b_EN.pdf"}
    assert zf.read("a_EN.pdf") == b"translated A"


def test_download_all_skips_unready_and_foreign_ids(tmp_path, monkeypatch, fake_s3):
    import zipfile, io as _io
    conn = _db(tmp_path, monkeypatch)
    queued_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 10, "documents/1/a/original.pdf",
        "a", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)  # still queued, no translated_storage_key

    resp = api.download_all_translated_documents(ids=f"{queued_id},9999", tenant_id=TEST_TENANT_ID)
    zf = zipfile.ZipFile(_io.BytesIO(resp.body))
    assert zf.namelist() == []  # neither entry produced a file, zip is just empty


# ---- save to vault ----

def test_save_to_vault_creates_original_and_en_entries(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 10, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original bytes"
    translated_key = "documents/1/1/translated.pdf"
    fake_s3[translated_key] = b"translated bytes"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key=translated_key)
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", tmp_path / "vault_uploads")
    monkeypatch.setattr(api, "_run_vault_processing", lambda *a, **k: None)

    background = BackgroundTasks()
    result = api.save_translated_document_to_vault(doc_id, background, identity=make_identity())
    assert len(result["saved_vault_document_ids"]) == 2
    vault_docs = store.list_vault_documents(conn, TEST_TENANT_ID)
    assert len(vault_docs) == 2
    names = {d["filename"] for d in vault_docs}
    assert "sow.pdf" in names
    assert "sow_EN.pdf" in names


def test_save_to_vault_refuses_an_unfinished_document(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 10, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)  # still queued
    background = BackgroundTasks()
    with pytest.raises(Exception) as exc:
        api.save_translated_document_to_vault(doc_id, background, identity=make_identity())
    assert getattr(exc.value, "status_code", None) == 409


def test_save_to_vault_already_english_only_saves_one_copy(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 10, "documents/1/h/original.pdf",
        "h", "eng", "EN-GB", None, None, TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original bytes"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "already_english")
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", tmp_path / "vault_uploads")
    monkeypatch.setattr(api, "_run_vault_processing", lambda *a, **k: None)

    background = BackgroundTasks()
    result = api.save_translated_document_to_vault(doc_id, background, identity=make_identity())
    assert len(result["saved_vault_document_ids"]) == 1  # no separate EN copy -- nothing to translate


# ---- retention purge ----

def test_retention_purge_deletes_old_documents_and_keeps_recent_ones(tmp_path, monkeypatch, fake_s3):
    from datetime import datetime, timedelta, timezone
    conn = _db(tmp_path, monkeypatch)
    old_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "old.pdf", "application/pdf", 10, "documents/1/old/original.pdf",
        "old", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/old/original.pdf"] = b"old bytes"
    recent_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "recent.pdf", "application/pdf", 10, "documents/1/recent/original.pdf",
        "recent", "FR", "EN-GB", "d2", "k2", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/recent/original.pdf"] = b"recent bytes"

    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with conn.begin() as c:
        from schema import translated_documents
        from sqlalchemy import update as sa_update
        c.execute(sa_update(translated_documents).where(translated_documents.c.id == old_id)
                  .values(uploaded_at=old_ts))

    monkeypatch.setattr(api, "DOCUMENT_RETENTION_DAYS", 30)
    api._run_translation_retention_purge()

    assert store.get_translated_document(conn, TEST_TENANT_ID, old_id) is None
    assert store.get_translated_document(conn, TEST_TENANT_ID, recent_id) is not None
    assert "documents/1/old/original.pdf" not in fake_s3
    assert "documents/1/recent/original.pdf" in fake_s3


# ---- quota wired into Portal Health ----

def test_health_endpoint_includes_document_translation_quota(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 0,
                                                   "remaining_characters": 5_000_000,
                                                   "remaining_documents": 100, "reason": None})
    result = api.get_health(tenant_id=TEST_TENANT_ID)
    deepl_entry = next(e for e in result if e["name"] == "DeepL")
    assert deepl_entry["quota"]["remaining_documents"] == 100


# ── Review follow-ups (CR-012 §2 bugs) ───────────────────────────────────────

def test_upload_endpoint_is_sync_so_blocking_io_runs_in_the_threadpool():
    import inspect
    assert not inspect.iscoroutinefunction(api.upload_translated_document)


def test_duplicate_uploads_get_separate_originals_so_deleting_one_keeps_the_other(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    monkeypatch.setattr(translate_document, "check_quota",
                         lambda n, api_key=None: {"allowed": True, "estimated_cost": 0, "remaining_characters": None,
                                                   "remaining_documents": None, "reason": None})
    monkeypatch.setattr(translate_document, "detect_already_english", lambda *a, **k: False)
    monkeypatch.setattr(translate_document, "upload_document", lambda *a, **k: ("d1", "k1", None))
    first = api.upload_translated_document(BackgroundTasks(), file=_file(), source_lang=None, identity=make_identity())
    second = api.upload_translated_document(BackgroundTasks(), file=_file(), source_lang=None, identity=make_identity())
    key_1 = store.get_translated_document(conn, TEST_TENANT_ID, first["id"])["original_storage_key"]
    key_2 = store.get_translated_document(conn, TEST_TENANT_ID, second["id"])["original_storage_key"]
    assert key_1 != key_2

    api.delete_translated_document(first["id"], tenant_id=TEST_TENANT_ID)
    resp = api.download_translated_document(second["id"], version="original", tenant_id=TEST_TENANT_ID)
    assert resp.body == b"%PDF-1.4 fake pdf content"


def test_content_disposition_handles_non_latin1_and_quote_characters():
    header = api._content_disposition('umowa_łódź "x".docx')
    header.encode("latin-1")  # must not raise — Starlette encodes headers as latin-1
    assert "filename*=UTF-8''umowa_%C5%82%C3%B3d%C5%BA%20%22x%22.docx" in header
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback and "\\" not in fallback


def test_download_translated_uses_en_suffix_and_survives_polish_filename(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "umowa_ł.docx", "application/octet-stream", 10, "documents/1/p/original.docx",
        "p", "PL", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/p/original.docx"] = b"oryginal"
    fake_s3["documents/1/p/translated.docx"] = b"translated"
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key="documents/1/p/translated.docx")

    translated = api.download_translated_document(doc_id, version="translated", tenant_id=TEST_TENANT_ID)
    assert "umowa_%C5%82_EN.docx" in translated.headers["content-disposition"]
    original = api.download_translated_document(doc_id, version="original", tenant_id=TEST_TENANT_ID)
    assert "umowa_%C5%82.docx" in original.headers["content-disposition"]


def test_download_all_dedupes_colliding_filenames(tmp_path, monkeypatch, fake_s3):
    import zipfile, io as _io
    conn = _db(tmp_path, monkeypatch)
    ids = []
    for n in range(2):
        doc_id = store.create_translated_document(
            conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 10, f"documents/1/{n}/original.pdf",
            str(n), "FR", "EN-GB", f"d{n}", f"k{n}", TEST_ACCOUNT_NAME)
        fake_s3[f"documents/1/{n}/translated.pdf"] = f"translated {n}".encode()
        store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                                  translated_storage_key=f"documents/1/{n}/translated.pdf")
        ids.append(doc_id)
    resp = api.download_all_translated_documents(ids=",".join(map(str, ids)), tenant_id=TEST_TENANT_ID)
    assert sorted(zipfile.ZipFile(_io.BytesIO(resp.body)).namelist()) == ["sow_EN (2).pdf", "sow_EN.pdf"]


def test_download_all_rejects_non_integer_ids():
    with pytest.raises(Exception) as exc:
        api.download_all_translated_documents(ids="1,abc", tenant_id=TEST_TENANT_ID)
    assert getattr(exc.value, "status_code", None) == 422


def test_save_to_vault_writes_nothing_when_the_translated_copy_cannot_be_read(tmp_path, monkeypatch, fake_s3):
    conn = _db(tmp_path, monkeypatch)
    doc_id = store.create_translated_document(
        conn, TEST_TENANT_ID, "sow.pdf", "application/pdf", 10, "documents/1/h/original.pdf",
        "h", "FR", "EN-GB", "d1", "k1", TEST_ACCOUNT_NAME)
    fake_s3["documents/1/h/original.pdf"] = b"original bytes"
    # translated key recorded but the object is missing — fake_s3 raises KeyError on read
    store.update_translated_document_status(conn, TEST_TENANT_ID, doc_id, "ready",
                                              translated_storage_key="documents/1/1/translated.pdf")
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", tmp_path / "vault_uploads")
    monkeypatch.setattr(api, "_run_vault_processing", lambda *a, **k: None)

    with pytest.raises(Exception) as exc:
        api.save_translated_document_to_vault(doc_id, BackgroundTasks(), identity=make_identity())
    assert getattr(exc.value, "status_code", None) == 502
    assert store.list_vault_documents(conn, TEST_TENANT_ID) == []
