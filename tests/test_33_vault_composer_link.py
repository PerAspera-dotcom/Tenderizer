"""CR-004 F3 — real Vault<->Composer linking (the audit's biggest finding:
Composer previously only reused Vault's parsing/chunking code, never
Vault's document library at runtime).

Covers: vault.search_vault (Chroma mocked, same convention as
test_28_vault.py), store.find_vault_documents/delete_vault_document/
update_vault_document_metadata_fields, the new /api/vault/* endpoints, and
Composer's regenerate path merging Vault evidence + citations.
"""
import io

from fastapi import BackgroundTasks, UploadFile

import api
import composer
import store
import vault
from conftest import TEST_TENANT_ID


# ── vault.search_vault ───────────────────────────────────────────────────────

class _FakeModel:
    def encode(self, texts):
        class _Arr(list):
            def tolist(self):
                return [[0.0, 0.1, 0.2] for _ in texts]
        return _Arr()


class _FakeSearchCollection:
    def __init__(self, count=1):
        self._count = count

    def count(self):
        return self._count

    def query(self, query_embeddings, n_results, include, where):
        self.last_where = where
        return {
            "documents": [["chunk about 600D polyester", "chunk about M2 fire rating"]],
            "metadatas": [[{"source": "tech_fabric.pdf", "doc_id": 1},
                            {"source": "tech_cert.pdf", "doc_id": 2}]],
            "distances": [[0.2, 0.9]],
        }


def test_search_vault_empty_doc_ids_returns_empty(monkeypatch):
    monkeypatch.setattr(vault, "_chroma_collection", lambda tid: _FakeSearchCollection())
    assert vault.search_vault(TEST_TENANT_ID, [], "fabric") == []


def test_search_vault_empty_collection_returns_empty(monkeypatch):
    monkeypatch.setattr(vault, "_chroma_collection", lambda tid: _FakeSearchCollection(count=0))
    assert vault.search_vault(TEST_TENANT_ID, [1, 2], "fabric") == []


def test_search_vault_ranks_by_similarity_and_filters_by_doc_ids(monkeypatch):
    fake = _FakeSearchCollection()
    monkeypatch.setattr(vault, "_chroma_collection", lambda tid: fake)
    monkeypatch.setattr(vault, "_embedding_model", lambda: _FakeModel())
    results = vault.search_vault(TEST_TENANT_ID, [1, 2], "polyester fabric", top_k=5)
    assert fake.last_where == {"doc_id": {"$in": [1, 2]}}
    assert results[0]["source"] == "tech_fabric.pdf"  # lower distance -> higher similarity -> first
    assert results[0]["similarity"] > results[1]["similarity"]


# ── store.find_vault_documents / delete / metadata validation ───────────────

def _add_doc(conn, filename, doc_type, metadata, cpv_codes, status="indexed", confidence=0.8):
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, filename, "application/pdf", 100, f"/p/{filename}")
    store.update_vault_document_metadata(conn, TEST_TENANT_ID, doc_id, doc_type, metadata,
                                          cpv_codes, confidence, len(metadata), status)
    return doc_id


def test_find_vault_documents_filters_by_cpv(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "600D PES"}, ["39522530"])
    _add_doc(conn, "tech_cert.pdf", "Certificate", {"issuer": "SGS"}, ["39522500"])
    results = store.find_vault_documents(conn, TEST_TENANT_ID, cpv="39522530")
    assert [d["filename"] for d in results] == ["tech_fabric.pdf"]


def test_find_vault_documents_filters_by_material_substring(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "600D polyester"}, [])
    _add_doc(conn, "tech_cert.pdf", "Certificate", {"issuer": "SGS"}, [])
    results = store.find_vault_documents(conn, TEST_TENANT_ID, material="polyester")
    assert [d["filename"] for d in results] == ["tech_fabric.pdf"]


def test_find_vault_documents_excludes_processing_docs(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "still_processing.pdf",
                                       "application/pdf", 100, "/p/x")
    assert store.get_vault_document(conn, TEST_TENANT_ID, doc_id) is not None  # sanity: row exists
    assert store.find_vault_documents(conn, TEST_TENANT_ID) == []  # default status='processing', excluded


def test_delete_vault_document_removes_row(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, [])
    store.delete_vault_document(conn, TEST_TENANT_ID, doc_id)
    assert store.get_vault_document(conn, TEST_TENANT_ID, doc_id) is None


def test_update_vault_document_metadata_fields_leaves_cpv_and_confidence(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, ["39522530"], confidence=0.6)
    store.update_vault_document_metadata_fields(conn, TEST_TENANT_ID, doc_id,
                                                 {"material": "600D PES", "water_column": "5000mm"})
    docs = store.list_vault_documents(conn, TEST_TENANT_ID)
    doc = docs[0]
    assert doc["metadata"] == {"material": "600D PES", "water_column": "5000mm"}
    assert doc["fields_extracted"] == 2
    assert doc["cpv_codes"] == ["39522530"]  # untouched
    assert doc["confidence"] == 0.6          # untouched


# ── API: vault detail / delete / validate-metadata / search ─────────────────

def _seed_vault_api(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", tmp_path / "vault_uploads")
    conn = store.init_db(db_path)
    return conn


def test_get_vault_doc_detail_404_for_unknown(tmp_path, monkeypatch):
    _seed_vault_api(tmp_path, monkeypatch)
    try:
        api.get_vault_doc_detail(999, tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_get_vault_doc_detail_returns_doc(tmp_path, monkeypatch):
    conn = _seed_vault_api(tmp_path, monkeypatch)
    doc_id = _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, [])
    result = api.get_vault_doc_detail(doc_id, tenant_id=TEST_TENANT_ID)
    assert result["filename"] == "tech_fabric.pdf"


def test_delete_vault_doc_removes_file_and_row(tmp_path, monkeypatch):
    conn = _seed_vault_api(tmp_path, monkeypatch)
    upload_dir = tmp_path / "vault_uploads"
    upload_dir.mkdir(parents=True)
    stored_file = upload_dir / "stored.pdf"
    stored_file.write_bytes(b"fake pdf")
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "tech_fabric.pdf", "application/pdf",
                                       8, str(stored_file))
    monkeypatch.setattr(vault, "_chroma_collection",
                         lambda tid: type("C", (), {"delete": lambda self, where=None: None})())
    result = api.delete_vault_doc(doc_id, tenant_id=TEST_TENANT_ID)
    assert result == {"deleted": True}
    assert store.get_vault_document(conn, TEST_TENANT_ID, doc_id) is None
    assert not stored_file.exists()


def test_validate_vault_metadata_updates_fields(tmp_path, monkeypatch):
    conn = _seed_vault_api(tmp_path, monkeypatch)
    doc_id = _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, [])
    body = api.VaultMetadataValidationBody(document_id=doc_id, metadata={"material": "600D PES, verified"})
    result = api.validate_vault_metadata(body, tenant_id=TEST_TENANT_ID)
    assert result == {"id": doc_id, "metadata": {"material": "600D PES, verified"}}
    assert store.list_vault_documents(conn, TEST_TENANT_ID)[0]["metadata"] == {"material": "600D PES, verified"}


def test_search_vault_endpoint_without_query_returns_doc_level_results(tmp_path, monkeypatch):
    conn = _seed_vault_api(tmp_path, monkeypatch)
    _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, ["39522530"], confidence=0.9)
    _add_doc(conn, "tech_cert.pdf", "Certificate", {"issuer": "SGS"}, ["39522500"], confidence=0.5)
    result = api.search_vault_endpoint(query=None, cpv="39522530", material=None, top_k=8,
                                        tenant_id=TEST_TENANT_ID)
    assert len(result["results"]) == 1
    assert result["results"][0]["filename"] == "tech_fabric.pdf"
    assert result["results"][0]["text"] is None


def test_search_vault_endpoint_with_query_returns_chunk_level_results(tmp_path, monkeypatch):
    conn = _seed_vault_api(tmp_path, monkeypatch)
    doc_id = _add_doc(conn, "tech_fabric.pdf", "Datasheet", {"material": "PES"}, ["39522530"])
    fake = _FakeSearchCollection()
    monkeypatch.setattr(vault, "_chroma_collection", lambda tid: fake)
    monkeypatch.setattr(vault, "_embedding_model", lambda: _FakeModel())
    result = api.search_vault_endpoint(query="fabric", cpv=None, material=None, top_k=8,
                                        tenant_id=TEST_TENANT_ID)
    assert result["results"][0]["doc_id"] == doc_id
    assert result["results"][0]["text"] == "chunk about 600D polyester"


def test_search_vault_endpoint_no_candidates_returns_empty(tmp_path, monkeypatch):
    _seed_vault_api(tmp_path, monkeypatch)
    result = api.search_vault_endpoint(query=None, cpv="00000000", material=None, top_k=8,
                                        tenant_id=TEST_TENANT_ID)
    assert result == {"results": []}


# ── Composer regenerate merges Vault evidence + citations ───────────────────

def _tender(pub_number="P-1", status="shortlisted"):
    return {"pub_number": pub_number, "source": "TED", "tag_line": "Tents", "description": "d",
            "buyer": "b", "country": "SE", "place": "p", "category": "Supply", "procedure": "open",
            "pub_date": "2026-06-01", "deadline": "2026-12-31", "cpv_codes": [], "matched_terms": [],
            "match_source": "cpv", "url": "u", "status": status}


def _seed_composer(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    return conn


def test_run_composer_refine_with_vault_documents_merges_evidence_and_citations(tmp_path, monkeypatch):
    conn = _seed_composer(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "Fabric spec", "extracted": "600D polyester required", "source": "S", "confidence": 0.5}
    ])[0]
    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "completed", None, None, [])

    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    captured_chunks = {}

    def fake_refine(requirement_text, current_response, feedback, evidence_chunks):
        captured_chunks["chunks"] = evidence_chunks
        return "v2 text grounded in vault evidence"

    monkeypatch.setattr(composer, "refine_section", fake_refine)
    monkeypatch.setattr(vault, "search_vault", lambda tenant_id, doc_ids, query, top_k=5: [
        {"text": "600D PES, 5000mm water column", "source": "tech_fabric.pdf", "doc_id": 1, "similarity": 0.42}
    ])

    api._run_composer_refine(TEST_TENANT_ID, "P-1", req_id, "reference the fabric datasheet",
                              vault_document_ids=[1])

    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["response"] == "v2 text grounded in vault evidence"
    assert req["citations"] == [{"doc": "Vault: tech_fabric.pdf", "score": 0.42}]
    assert captured_chunks["chunks"][0]["source"] == "tech_fabric.pdf"  # vault evidence passed through


def test_run_composer_refine_without_vault_documents_unchanged(tmp_path, monkeypatch):
    conn = _seed_composer(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "complete", 0.5, "v1 text", [])

    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    monkeypatch.setattr(composer, "refine_section", lambda *a, **k: "v2 text")

    api._run_composer_refine(TEST_TENANT_ID, "P-1", req_id, "make it shorter")
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["citations"] == []  # no vault_document_ids given -> citations untouched


def test_generate_body_accepts_vault_document_ids(tmp_path, monkeypatch):
    conn = _seed_composer(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    background = BackgroundTasks()
    result = api.post_composer_generate(
        "P-1", background,
        api.ComposerGenerateBody(requirement_id=req_id, feedback="cite the datasheet", vault_document_ids=[5, 9]),
        tenant_id=TEST_TENANT_ID)
    assert result == {"status": "started", "requirement_id": req_id}
    assert len(background.tasks) == 1
