"""Tenancy hardening: Vault/Composer isolation isn't only enforced by DB-row
tenant_id filters — Chroma vector stores and uploaded files are scoped by
filesystem path instead (see vault._chroma_collection, composer._chroma_
collection, and the upload endpoints in api.py). This file checks that
second, path-based isolation mechanism directly, plus a regression guard
that no client-suppliable request body can override tenant_id in the first
place — the actual attack surface this whole area depends on staying closed.
"""
import asyncio
import io

from fastapi import BackgroundTasks, UploadFile

import api
import composer
import store
import vault
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


def _file(name="spec.pdf", content=b"%PDF-1.4 fake pdf content"):
    return UploadFile(file=io.BytesIO(content), filename=name)


def _tender(pub_number, status="shortlisted"):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": status}


# ── Upload storage paths ─────────────────────────────────────────────────────

def test_vault_upload_storage_path_is_scoped_to_authenticated_tenant(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    vault_dir = tmp_path / "vault_uploads"
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", vault_dir)
    store.init_db(db_path)

    background = BackgroundTasks()
    result = asyncio.run(api.ingest_vault_document(background, file=_file(), tenant_id=TEST_TENANT_ID))
    doc = store.get_vault_document(api._db(), TEST_TENANT_ID, result["id"])
    storage_path = doc["storage_path"]

    assert str(vault_dir / str(TEST_TENANT_ID)) in storage_path
    assert str(vault_dir / str(OTHER_TENANT_ID)) not in storage_path


def test_composer_upload_storage_path_is_scoped_to_authenticated_tenant_and_pub_number(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    composer_dir = tmp_path / "composer_uploads"
    monkeypatch.setattr(api, "COMPOSER_UPLOAD_DIR", composer_dir)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _tender("P-1"))

    background = BackgroundTasks()
    result = asyncio.run(api.upload_composer_document(
        "P-1", background, file=_file(name="sow_tender.pdf"), role=None, tenant_id=TEST_TENANT_ID))
    doc = store.get_composer_document(conn, TEST_TENANT_ID, result["id"])
    storage_path = doc["storage_path"]

    assert str(composer_dir / str(TEST_TENANT_ID) / "P-1") in storage_path
    assert str(composer_dir / str(OTHER_TENANT_ID)) not in storage_path


# ── Chroma collection paths ──────────────────────────────────────────────────

class _CapturingClient:
    def __init__(self, path):
        self.path = path

    def get_or_create_collection(self, name, metadata=None):
        return self


def test_vault_chroma_path_is_scoped_to_authenticated_tenant(monkeypatch):
    captured = {}

    def _fake_client(path):
        captured["path"] = path
        return _CapturingClient(path)

    monkeypatch.setattr(vault.chromadb, "PersistentClient", _fake_client)

    vault._chroma_collection(TEST_TENANT_ID)
    assert captured["path"] == f"{vault.CHROMA_ROOT}/{TEST_TENANT_ID}"

    vault._chroma_collection(OTHER_TENANT_ID)
    assert captured["path"] == f"{vault.CHROMA_ROOT}/{OTHER_TENANT_ID}"
    assert f"{vault.CHROMA_ROOT}/{TEST_TENANT_ID}" != f"{vault.CHROMA_ROOT}/{OTHER_TENANT_ID}"


def test_composer_chroma_path_is_scoped_to_authenticated_tenant_and_pub_number(monkeypatch):
    captured = {}

    def _fake_client(path):
        captured["path"] = path
        return _CapturingClient(path)

    monkeypatch.setattr(composer.chromadb, "PersistentClient", _fake_client)

    composer._chroma_collection(TEST_TENANT_ID, "P-1")
    assert captured["path"] == f"{composer.CHROMA_ROOT}/{TEST_TENANT_ID}/P-1"

    composer._chroma_collection(OTHER_TENANT_ID, "P-1")
    assert captured["path"] == f"{composer.CHROMA_ROOT}/{OTHER_TENANT_ID}/P-1"


# ── Regression guard: tenant_id can never be client-supplied ────────────────

def test_no_request_body_accepts_a_client_supplied_tenant_id():
    for body_cls in (api.VaultTagsBody, api.VaultMetadataValidationBody, api.VaultRulesBody,
                      api.VaultSettingsBody, api.ComposerRoleBody, api.ComposerValidationBody,
                      api.ComposerGenerateBody, api.ComposerSettingsBody, api.ComposerStyleBody):
        assert "tenant_id" not in body_cls.model_fields
