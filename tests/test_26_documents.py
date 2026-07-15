"""Step 26 — CR-002 E: minimal document upload slice (D-C decided).

Upload + store only, scoped to shortlisted tenders — no requirement
parsing/translation (that's Composer's Phase 2 Ingest & Config, deliberately
not built here). Route handlers called directly, same pattern as
test_17_api.py; upload_document is async, so tests drive it via asyncio.run.
"""
import asyncio
import io

from fastapi import UploadFile

import store
import api
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


def _tender(pub_number, status="shortlisted"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": status}


def _seed(tmp_path, monkeypatch, status="shortlisted", pub_number="P-1"):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _tender(pub_number, status=status))
    return conn


def _file(name="spec.pdf", content=b"%PDF-1.4 fake pdf content"):
    return UploadFile(file=io.BytesIO(content), filename=name)


def test_upload_requires_shortlisted_status(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status="new")
    try:
        asyncio.run(api.upload_document("P-1", file=_file(), tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409


def test_upload_404s_on_unknown_pub_number(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    try:
        asyncio.run(api.upload_document("NEVER-EXISTED", file=_file(), tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_upload_403s_on_another_tenants_tender(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))  # exists, but not TEST_TENANT_ID's

    try:
        asyncio.run(api.upload_document("P-1", file=_file(), tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_upload_then_list_and_download(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    result = asyncio.run(api.upload_document("P-1", file=_file(name="tech-spec.pdf"),
                                              tenant_id=TEST_TENANT_ID))
    assert result["filename"] == "tech-spec.pdf"
    assert result["size"] > 0

    docs = api.list_documents("P-1", tenant_id=TEST_TENANT_ID)
    assert len(docs) == 1
    assert docs[0]["filename"] == "tech-spec.pdf"
    assert docs[0]["id"] == result["id"]

    response = api.download_document(result["id"], tenant_id=TEST_TENANT_ID)
    assert response.filename == "tech-spec.pdf"


def test_download_404s_for_another_tenant(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    result = asyncio.run(api.upload_document("P-1", file=_file(), tenant_id=TEST_TENANT_ID))

    store.ensure_tenant(conn, OTHER_TENANT_ID)
    try:
        api.download_document(result["id"], tenant_id=OTHER_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_upload_rejects_oversized_file(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "MAX_UPLOAD_SIZE", 10)
    try:
        asyncio.run(api.upload_document("P-1", file=_file(content=b"x" * 100), tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 413


def test_stored_filename_never_uses_user_supplied_path(tmp_path, monkeypatch):
    """A malicious filename with path-traversal segments must not affect
    where the file lands on disk — storage_path is always uuid-based."""
    _seed(tmp_path, monkeypatch)
    result = asyncio.run(api.upload_document(
        "P-1", file=_file(name="../../evil.pdf"), tenant_id=TEST_TENANT_ID))
    docs = api.list_documents("P-1", tenant_id=TEST_TENANT_ID)
    doc = store.get_document(api._db(), TEST_TENANT_ID, result["id"])
    assert (tmp_path / "uploads").resolve() in __import__("pathlib").Path(doc["storage_path"]).resolve().parents
    assert doc["filename"] == "../../evil.pdf"  # kept as display metadata only
    assert docs[0]["filename"] == "../../evil.pdf"
