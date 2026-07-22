"""Step 29 — Composer: per-tender proposal-drafting pipeline. Role detection
and Part A clipping (real), embed/extract (mocked — no live model download or
Anthropic call in the test suite, same reasoning as test_28_vault.py), store
round-trips, and the /api/composer/* endpoints (background processing called
directly, not via BackgroundTasks, same pattern as test_26/test_28).
"""
import asyncio
import io

from fastapi import BackgroundTasks, UploadFile

import composer
import store
import api
import vault
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


# ── get_role / extract_parta_section ─────────────────────────────────────────

def test_get_role_detects_all_prefixes():
    assert composer.get_role("sow_tender.pdf") == "sow"
    assert composer.get_role("TECH_spec.pdf") == "tech"
    assert composer.get_role("background_company.docx") == "background"
    assert composer.get_role("parta_form.pdf") == "parta"
    assert composer.get_role("example_old-proposal.pdf") == "example"
    assert composer.get_role("readme.txt") == "unknown"


def test_extract_parta_section_clips_to_markers():
    text = ("intro fluff " * 5 +
            "Part A - Technical Proposal/Capability and Qualification Form " +
            "actual part a content here " * 10 +
            "Part B - Pricing " + "pricing stuff " * 10)
    extracted = composer.extract_parta_section(text)
    assert extracted.startswith("Part A")
    assert "pricing stuff" not in extracted


def test_extract_parta_section_falls_back_to_full_text_without_marker():
    text = "no recognisable header here, just prose"
    assert composer.extract_parta_section(text) == text


# ── ingest_document (embedding model + Chroma mocked, same as vault's tests) ─

class _FakeModel:
    def encode(self, texts):
        class _Arr(list):
            def tolist(self):
                return [[0.0, 0.1, 0.2] for _ in texts]
        return _Arr()


class _FakeCollection:
    def __init__(self):
        self.upserted = None
        self._count = 0

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upserted = {"ids": ids, "documents": documents,
                          "embeddings": embeddings, "metadatas": metadatas}
        self._count += len(ids)

    def count(self):
        return self._count

    def query(self, query_embeddings, n_results, include, where):
        # One matching chunk at a fixed distance -> similarity 0.9
        return {"documents": [["some tech evidence text"]],
                "metadatas": [[{"source": "tech_spec.pdf", "role": "tech"}]],
                "distances": [[0.2]]}


def test_ingest_document_upserts_chunks(monkeypatch):
    fake_collection = _FakeCollection()
    monkeypatch.setattr(vault, "_embedding_model", lambda: _FakeModel())
    monkeypatch.setattr(composer, "_chroma_collection", lambda tenant_id, pub_number: fake_collection)
    monkeypatch.setattr(vault, "parse_document", lambda path, ct: " ".join(f"w{i}" for i in range(500)))

    n = composer.ingest_document(TEST_TENANT_ID, "P-1", doc_id=1, path="whatever.pdf",
                                  content_type="application/pdf", role="tech")
    assert n == 2
    assert fake_collection.upserted["ids"] == ["doc1_chunk0", "doc1_chunk1"]
    assert fake_collection.upserted["metadatas"][0]["role"] == "tech"


def test_ingest_document_clips_parta_role_before_chunking(monkeypatch):
    monkeypatch.setattr(vault, "_embedding_model", lambda: _FakeModel())
    fake_collection = _FakeCollection()
    monkeypatch.setattr(composer, "_chroma_collection", lambda tenant_id, pub_number: fake_collection)

    full_text = ("Part A - Technical Proposal/Capability and Qualification Form " +
                 "qualifying content word " * 200 + "Part B - Pricing " + "pricing " * 200)
    monkeypatch.setattr(vault, "parse_document", lambda path, ct: full_text)

    composer.ingest_document(TEST_TENANT_ID, "P-1", doc_id=2, path="form.pdf",
                              content_type="application/pdf", role="parta")
    stored_text = " ".join(fake_collection.upserted["documents"])
    assert "pricing" not in stored_text


def test_ingest_document_no_text_stores_nothing(monkeypatch):
    monkeypatch.setattr(vault, "parse_document", lambda path, ct: "")
    n = composer.ingest_document(TEST_TENANT_ID, "P-1", doc_id=1, path="whatever",
                                  content_type="text/plain", role="tech")
    assert n == 0


# ── extract_requirements / _parse_requirements_response ──────────────────────

def test_extract_requirements_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert composer.extract_requirements([{"filename": "sow.pdf", "role": "sow", "pages": ["text"]}]) == []


def test_extract_requirements_no_pages_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    assert composer.extract_requirements([{"filename": "sow.pdf", "role": "sow", "pages": ["", "  "]}]) == []


def test_extract_requirements_parses_claude_json_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    class _FakeContentBlock:
        text = ('[{"title": "Fire resistance", "extracted": "M2 required", '
                '"source": "sow.pdf §4.2 · p.12", "confidence": 0.9}]')

    class _FakeMessage:
        content = [_FakeContentBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeMessage()

    class _FakeClient:
        messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _FakeClient())

    result = composer.extract_requirements([{"filename": "sow.pdf", "role": "sow", "pages": ["p1 text"]}])
    assert len(result) == 1
    assert result[0]["title"] == "Fire resistance"
    assert result[0]["confidence"] == 0.9


def test_parse_requirements_response_handles_code_fence():
    text = '```json\n[{"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}]\n```'
    parsed = composer._parse_requirements_response(text)
    assert parsed[0]["title"] == "T"


def test_parse_requirements_response_invalid_json_returns_empty():
    assert composer._parse_requirements_response("not json") == []


def test_parse_requirements_response_skips_items_without_extracted():
    text = '[{"title": "T"}, {"extracted": "E2"}]'
    parsed = composer._parse_requirements_response(text)
    assert len(parsed) == 1
    assert parsed[0]["extracted"] == "E2"


# ── gap status thresholds ─────────────────────────────────────────────────────

def test_gap_status_thresholds():
    assert composer._gap_status([]) == "completed"
    assert composer._gap_status([{"similarity": 0.1}]) == "completed"
    assert composer._gap_status([{"similarity": 0.25}]) == "linked"
    assert composer._gap_status([{"similarity": 0.4}]) == "complete"


# ── build_proposal_docx / build_gaps_report ──────────────────────────────────

def _req(id, title, gap_status, response=None, citations=None):
    return {"id": id, "title": title, "extracted": f"requirement text for {title}",
            "gap_status": gap_status, "similarity": 0.5, "response": response,
            "citations": citations or []}


def test_build_gaps_report_counts_and_readiness(tmp_path):
    reqs = [
        _req(1, "Req A", "completed"),
        _req(2, "Req B", "linked", response="draft", citations=[{"doc": "tech_x.pdf", "score": 0.25}]),
        _req(3, "Req C", "complete", response="draft", citations=[{"doc": "tech_y.pdf", "score": 0.5}]),
    ]
    out = tmp_path / "gaps_report.txt"
    composer.build_gaps_report(reqs, str(out))
    text = out.read_text(encoding="utf-8")
    assert "SUBMISSION READINESS: NOT READY" in text
    assert "Total requirements:  3" in text
    assert "Req A" in text and "Req B" in text
    assert "tech_x.pdf" in text


def test_build_gaps_report_ready_when_no_gaps(tmp_path):
    reqs = [_req(1, "Req A", "complete", response="draft")]
    out = tmp_path / "gaps_report.txt"
    composer.build_gaps_report(reqs, str(out))
    assert "SUBMISSION READINESS: READY" in out.read_text(encoding="utf-8")


def test_build_proposal_docx_creates_file_with_sections(tmp_path):
    from docx import Document
    reqs = [
        _req(1, "Req A", "completed"),
        _req(2, "Req B", "complete", response="Our fabric meets the spec.",
             citations=[{"doc": "tech_x.pdf", "score": 0.5}]),
    ]
    out = tmp_path / "technical_proposal.docx"
    composer.build_proposal_docx(reqs, str(out), background_text="We are a great company.")
    assert out.exists()
    doc = Document(str(out))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "We are a great company." in full_text
    assert "Our fabric meets the spec." in full_text
    assert "tech_x.pdf" in full_text


# ── store round-trips ─────────────────────────────────────────────────────────

def test_add_and_list_composer_document_defaults_to_processing(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "sow.pdf", "application/pdf",
                                          123, "/fake/path", "sow")
    docs = store.list_composer_documents(conn, TEST_TENANT_ID, "P-1")
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id
    assert docs[0]["status"] == "processing"
    assert docs[0]["role"] == "sow"


def test_set_composer_document_role_overrides_effective_role(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "mystery.pdf", "application/pdf",
                                          1, "/p", "unknown")
    store.set_composer_document_role(conn, TEST_TENANT_ID, doc_id, "tech")
    doc = store.list_composer_documents(conn, TEST_TENANT_ID, "P-1")[0]
    assert doc["role"] == "tech"


def test_composer_matrix_round_trip_replaces_existing(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_composer_matrix(conn, TEST_TENANT_ID, "P-1", "matrix.xlsx", "/p1", 35)
    store.set_composer_matrix(conn, TEST_TENANT_ID, "P-1", "matrix2.xlsx", "/p2", 40)
    matrix = store.get_composer_matrix(conn, TEST_TENANT_ID, "P-1")
    assert matrix["filename"] == "matrix2.xlsx"
    assert matrix["requirement_count"] == 40


def test_composer_requirements_round_trip_and_validation(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    ids = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "Fire resistance", "extracted": "M2 required", "source": "§4.2", "confidence": 0.9},
    ])
    req_id = ids[0]
    reqs = store.list_composer_requirements(conn, TEST_TENANT_ID, "P-1")
    assert reqs[0]["validation"] == "pending"
    assert reqs[0]["gap_status"] is None

    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")
    assert store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)["validation"] == "validated"

    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "complete", 0.5,
                                              "Great response", [{"doc": "tech.pdf", "score": 0.5}])
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["gap_status"] == "complete"
    assert req["response"] == "Great response"
    assert req["version"] == 1


def test_update_composer_requirement_refined_bumps_version_and_history(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "complete", 0.5, "v1 text", [])

    store.update_composer_requirement_refined(conn, TEST_TENANT_ID, req_id, "v2 text", "make it shorter")
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["response"] == "v2 text"
    assert req["version"] == 2
    assert req["version_history"] == [{"text": "v1 text", "feedback": "make it shorter",
                                        "at": req["version_history"][0]["at"]}]


def test_mark_composer_requirement_resolved(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.mark_composer_requirement_resolved(conn, TEST_TENANT_ID, req_id)
    assert store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)["resolved"] is True


def test_get_composer_document_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "sow.pdf", "application/pdf",
                                          1, "/p1", "sow")
    assert store.get_composer_document(conn, TEST_TENANT_ID, doc_id)["filename"] == "sow.pdf"
    assert store.get_composer_document(conn, 999, doc_id) is None


# ── API endpoints ─────────────────────────────────────────────────────────────

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
    monkeypatch.setattr(api, "COMPOSER_UPLOAD_DIR", tmp_path / "composer_uploads")
    monkeypatch.setattr(api, "COMPOSER_OUTPUT_DIR", tmp_path / "composer_output")
    conn = store.init_db(db_path)
    store.upsert(conn, TEST_TENANT_ID, _tender(pub_number, status=status))
    return conn


def _file(name="sow_tender.pdf", content=b"%PDF-1.4 fake pdf content"):
    return UploadFile(file=io.BytesIO(content), filename=name)


def test_upload_composer_document_requires_shortlisted(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status="new")
    background = BackgroundTasks()
    try:
        asyncio.run(api.upload_composer_document("P-1", background, file=_file(), role=None,
                                                  tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409


def test_upload_composer_document_403s_on_another_tenants_tender(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "COMPOSER_UPLOAD_DIR", tmp_path / "composer_uploads")
    conn = store.init_db(db_path)
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.upsert(conn, OTHER_TENANT_ID, _tender("P-1"))  # exists, but not TEST_TENANT_ID's

    background = BackgroundTasks()
    try:
        asyncio.run(api.upload_composer_document("P-1", background, file=_file(), role=None,
                                                  tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_upload_composer_document_auto_detects_role_and_queues_background_task(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    result = asyncio.run(api.upload_composer_document(
        "P-1", background, file=_file(name="tech_spec.pdf"), role=None, tenant_id=TEST_TENANT_ID))
    assert result["role"] == "tech"
    assert result["status"] == "processing"
    assert len(background.tasks) == 1


def test_upload_composer_document_rejects_invalid_role(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    try:
        asyncio.run(api.upload_composer_document("P-1", background, file=_file(),
                                                  role="not-a-real-role", tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


def test_patch_composer_document_role(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    result = asyncio.run(api.upload_composer_document(
        "P-1", background, file=_file(name="mystery.pdf"), role=None, tenant_id=TEST_TENANT_ID))
    body = api.ComposerRoleBody(role="background")
    updated = api.patch_composer_document_role("P-1", result["id"], body, tenant_id=TEST_TENANT_ID)
    assert updated["role"] == "background"


def test_run_composer_ingest_skips_example_role(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "example_old.pdf",
                                          "application/pdf", 1, "/fake/path", "example")
    api._run_composer_ingest(TEST_TENANT_ID, "P-1", doc_id, "/fake/path", "application/pdf", "example")
    doc = store.list_composer_documents(api._db(), TEST_TENANT_ID, "P-1")[0]
    assert doc["status"] == "style_only"


def test_upload_composer_matrix_parses_requirement_count(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(composer, "_load_matrix_requirements", lambda path: [{"num": 1}, {"num": 2}])
    result = asyncio.run(api.upload_composer_matrix(
        "P-1", file=_file(name="compliance_matrix.xlsx", content=b"fake xlsx"), tenant_id=TEST_TENANT_ID))
    assert result["requirement_count"] == 2
    assert result["filled"] is False


def test_upload_composer_matrix_422s_on_unparseable_file(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    def _raise(path):
        raise ValueError("bad matrix")
    monkeypatch.setattr(composer, "_load_matrix_requirements", _raise)
    try:
        asyncio.run(api.upload_composer_matrix("P-1", file=_file(content=b"not really xlsx"),
                                                tenant_id=TEST_TENANT_ID))
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


def test_run_composer_interpret_persists_requirements(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "sow.pdf", "application/pdf",
                                          1, str(tmp_path / "sow.pdf"), "sow")
    (tmp_path / "sow.pdf").write_bytes(b"fake")
    monkeypatch.setattr(api, "_is_pdf_path", lambda path: False)
    monkeypatch.setattr(vault, "parse_document", lambda path, ct: "some SOW text")
    monkeypatch.setattr(composer, "extract_requirements", lambda docs: [
        {"title": "Fire resistance", "extracted": "M2 required", "source": "§4.2", "confidence": 0.9}])

    api._run_composer_interpret(TEST_TENANT_ID, "P-1")
    reqs = store.list_composer_requirements(conn, TEST_TENANT_ID, "P-1")
    assert len(reqs) == 1
    assert reqs[0]["title"] == "Fire resistance"


def test_patch_composer_requirement_validation_and_undo(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]

    api.patch_composer_requirement(req_id, api.ComposerValidationBody(status="validated"),
                                    tenant_id=TEST_TENANT_ID)
    assert store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)["validation"] == "validated"

    api.patch_composer_requirement(req_id, api.ComposerValidationBody(status="pending"),
                                    tenant_id=TEST_TENANT_ID)
    assert store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)["validation"] == "pending"


def test_patch_composer_requirement_rejects_invalid_status(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    try:
        api.patch_composer_requirement(req_id, api.ComposerValidationBody(status="bogus"),
                                        tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


def test_resolve_composer_requirement(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    result = api.resolve_composer_requirement(req_id, tenant_id=TEST_TENANT_ID)
    assert result["resolved"] is True
    assert store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)["resolved"] is True


def test_get_composer_session_aggregates_docs_matrix_and_requirements(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "sow.pdf", "application/pdf", 1, "/p", "sow")
    store.set_composer_matrix(conn, TEST_TENANT_ID, "P-1", "matrix.xlsx", "/m", 10)
    store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])

    session = api.get_composer_session("P-1", tenant_id=TEST_TENANT_ID)
    assert session["tender_title"] == "Tent supply"
    assert len(session["docs"]) == 1
    assert session["matrix"]["requirement_count"] == 10
    assert "storage_path" not in session["matrix"]  # never leak server-local paths
    assert len(session["requirements"]) == 1


def test_generate_403s_when_not_all_validated(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])
    background = BackgroundTasks()
    try:
        api.post_composer_generate("P-1", background, api.ComposerGenerateBody(), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


def test_generate_409s_with_no_requirements(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    background = BackgroundTasks()
    try:
        api.post_composer_generate("P-1", background, api.ComposerGenerateBody(), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409


def test_generate_queues_background_task_once_validated(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")

    background = BackgroundTasks()
    result = api.post_composer_generate("P-1", background, api.ComposerGenerateBody(), tenant_id=TEST_TENANT_ID)
    assert result == {"status": "started"}
    assert len(background.tasks) == 1


def test_run_composer_generate_persists_results_and_writes_docx(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "Fire resistance", "extracted": "M2 required", "source": "§4.2", "confidence": 0.9}])[0]
    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")

    monkeypatch.setattr(composer, "run_generate", lambda tenant_id, pub_number, requirements, style_guide=None, top_k=None, good_similarity=None, partial_similarity=None: [
        {"id": req_id, "gap_status": "complete", "similarity": 0.6,
         "response_text": "We comply.", "citations": [{"doc": "tech.pdf", "score": 0.6}]},
    ])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # skip the matrix-fill path (no matrix anyway)

    api._run_composer_generate(TEST_TENANT_ID, "P-1")

    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["gap_status"] == "complete"
    assert req["response"] == "We comply."

    docx_path = api._composer_output_path(TEST_TENANT_ID, "P-1", "technical_proposal.docx")
    gaps_path = api._composer_output_path(TEST_TENANT_ID, "P-1", "gaps_report.txt")
    assert docx_path.exists()
    assert gaps_path.exists()


def test_generate_section_scoped_regenerate_requires_feedback(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    background = BackgroundTasks()
    try:
        api.post_composer_generate("P-1", background, api.ComposerGenerateBody(requirement_id=req_id),
                                    tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422


def test_generate_section_scoped_regenerate_queues_refine_task(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    background = BackgroundTasks()
    result = api.post_composer_generate(
        "P-1", background, api.ComposerGenerateBody(requirement_id=req_id, feedback="be shorter"),
        tenant_id=TEST_TENANT_ID)
    assert result == {"status": "started", "requirement_id": req_id}
    assert len(background.tasks) == 1


def test_run_composer_refine_bumps_version(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "complete", 0.5, "v1 text", [])

    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    monkeypatch.setattr(composer, "refine_section", lambda *a, **k: "v2 text")

    api._run_composer_refine(TEST_TENANT_ID, "P-1", req_id, "make it shorter")
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["response"] == "v2 text"
    assert req["version"] == 2


def test_download_endpoints_404_before_generate(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    for fn in (api.download_composer_proposal, api.download_composer_gaps):
        try:
            fn("P-1", tenant_id=TEST_TENANT_ID)
            assert False, "expected HTTPException"
        except Exception as e:
            assert getattr(e, "status_code", None) == 404
    try:
        api.download_composer_matrix("P-1", tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_download_endpoints_404_for_unknown_pub_number(tmp_path, monkeypatch):
    """Tenancy hardening: proposal/gaps downloads must validate pub_number
    against a real, owned tender before touching the filesystem — matches
    every other pub_number-scoped composer endpoint (and matrix download,
    which already did this via a DB-scoped query).
    """
    _seed(tmp_path, monkeypatch)
    for fn in (api.download_composer_proposal, api.download_composer_gaps):
        try:
            fn("NOT-A-REAL-PUB", tenant_id=TEST_TENANT_ID)
            assert False, "expected HTTPException"
        except Exception as e:
            assert getattr(e, "status_code", None) == 404


def test_download_proposal_after_generate(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")
    monkeypatch.setattr(composer, "run_generate", lambda tenant_id, pub_number, requirements, style_guide=None, top_k=None, good_similarity=None, partial_similarity=None: [
        {"id": req_id, "gap_status": "complete", "similarity": 0.6, "response_text": "ok", "citations": []}])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    api._run_composer_generate(TEST_TENANT_ID, "P-1")
    response = api.download_composer_proposal("P-1", tenant_id=TEST_TENANT_ID)
    assert response.filename == "technical_proposal.docx"
