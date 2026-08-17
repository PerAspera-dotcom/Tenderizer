"""CR-007 Phase F — Composer depth: source-page verification for extracted
requirements (F-b), CPV-scoped Vault evidence blended into the automated
generate pass (F-c), and expiry down-ranking applied uniformly across every
Vault-evidence path, not just the manual search panel (F-a).
"""
from datetime import date, timedelta

import composer
import store
import vault
import api
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def _tender(pub_number="P-1", cpv_codes=None):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": cpv_codes or ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
            "first_seen": None, "status": "shortlisted"}


def _vault_doc(conn, filename="cert.pdf", cpv_codes=None, valid_until=None, confidence=0.8):
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, filename, "application/pdf", 100, "/p")
    store.update_vault_document_metadata(
        conn, TEST_TENANT_ID, doc_id, doc_type="Certificate", metadata={},
        cpv_codes=cpv_codes or [], confidence=confidence, fields_extracted=0,
        status="indexed", valid_until=valid_until)
    return doc_id


# ── F-b: composer._verify_source ─────────────────────────────────────────────

def test_verify_source_true_for_a_real_page():
    docs = [{"filename": "sow.pdf", "pages": ["p1", "p2", "p3"]}]
    assert composer._verify_source("sow.pdf §4.2 · p.2", docs) is True


def test_verify_source_false_for_an_out_of_range_page():
    docs = [{"filename": "sow.pdf", "pages": ["p1", "p2"]}]
    assert composer._verify_source("sow.pdf p.9", docs) is False


def test_verify_source_false_for_an_unknown_filename():
    docs = [{"filename": "sow.pdf", "pages": ["p1"]}]
    assert composer._verify_source("other.pdf p.1", docs) is False


def test_verify_source_false_when_no_page_number_present():
    docs = [{"filename": "sow.pdf", "pages": ["p1"]}]
    assert composer._verify_source("sow.pdf", docs) is False


def test_verify_source_false_for_blank_source():
    assert composer._verify_source("", [{"filename": "sow.pdf", "pages": ["p1"]}]) is False


def test_add_composer_requirements_stores_source_verified(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "sow.pdf p.1", "confidence": 0.9,
         "source_verified": True},
    ])[0]
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["source_verified"] is True


def test_add_composer_requirements_defaults_source_verified_to_none(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender())
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "sow.pdf p.1", "confidence": 0.9},
    ])[0]
    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["source_verified"] is None


# ── F-a: vault.is_expired / rank_chunks_by_expiry ────────────────────────────

def test_is_expired_true_for_a_past_date():
    past = (date.today() - timedelta(days=1)).isoformat()
    assert vault.is_expired(past) is True


def test_is_expired_false_for_a_future_date():
    future = (date.today() + timedelta(days=1)).isoformat()
    assert vault.is_expired(future) is False


def test_is_expired_false_when_unset():
    assert vault.is_expired(None) is False


def test_rank_chunks_by_expiry_sinks_expired_regardless_of_similarity():
    past = (date.today() - timedelta(days=1)).isoformat()
    chunks = [
        {"doc_id": 1, "similarity": 0.9},   # expired but "more similar"
        {"doc_id": 2, "similarity": 0.3},   # not expired
    ]
    vault.rank_chunks_by_expiry(chunks, {1: past, 2: None})
    assert [c["doc_id"] for c in chunks] == [2, 1]
    assert chunks[1]["expired"] is True


# ── F-c: run_generate blends CPV-scoped Vault evidence ───────────────────────

def test_run_generate_blends_vault_chunks_with_a_vault_prefixed_citation(monkeypatch):
    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    monkeypatch.setattr(
        vault, "search_vault",
        lambda tenant_id, doc_ids, query, top_k=8: [
            {"text": "cert text", "source": "cert.pdf", "doc_id": 1, "similarity": 0.5}])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    results = composer.run_generate(
        TEST_TENANT_ID, "P-1", [{"id": 1, "title": "Fire rating", "extracted": "Must be M2"}],
        vault_doc_ids=[1], valid_until_by_doc_id={})

    assert len(results) == 1
    citations = results[0]["citations"]
    assert citations == [{"doc": "Vault: cert.pdf", "score": 0.5}]
    assert results[0]["similarity"] == 0.5


def test_run_generate_without_vault_doc_ids_is_unaffected(monkeypatch):
    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    called = {"search_vault": False}

    def _fail_if_called(*a, **k):
        called["search_vault"] = True
        return []

    monkeypatch.setattr(vault, "search_vault", _fail_if_called)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    composer.run_generate(TEST_TENANT_ID, "P-1",
                           [{"id": 1, "title": "T", "extracted": "E"}])
    assert called["search_vault"] is False


def test_run_generate_down_ranks_an_expired_vault_chunk(monkeypatch):
    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    monkeypatch.setattr(
        vault, "search_vault",
        lambda tenant_id, doc_ids, query, top_k=8: [
            {"text": "old cert", "source": "old.pdf", "doc_id": 1, "similarity": 0.9},
            {"text": "new cert", "source": "new.pdf", "doc_id": 2, "similarity": 0.4},
        ])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    past = (date.today() - timedelta(days=1)).isoformat()

    results = composer.run_generate(
        TEST_TENANT_ID, "P-1", [{"id": 1, "title": "T", "extracted": "E"}],
        vault_doc_ids=[1, 2], valid_until_by_doc_id={1: past, 2: None})

    docs_in_order = [c["doc"] for c in results[0]["citations"]]
    assert docs_in_order == ["Vault: new.pdf", "Vault: old.pdf"]


# ── api._cpv_scoped_vault_doc_ids ────────────────────────────────────────────

def test_cpv_scoped_vault_doc_ids_matches_the_tenders_own_cpv(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender(cpv_codes=["39522530"]))
    matching_id = _vault_doc(conn, filename="tent_cert.pdf", cpv_codes=["39522530"])
    _vault_doc(conn, filename="unrelated.pdf", cpv_codes=["00000000"])

    ids = api._cpv_scoped_vault_doc_ids(conn, TEST_TENANT_ID, "P-1")
    assert ids == [matching_id]


def test_cpv_scoped_vault_doc_ids_empty_for_unknown_tender(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    assert api._cpv_scoped_vault_doc_ids(conn, TEST_TENANT_ID, "NOPE") == []


# ── F-a: refine's Vault merge respects expiry down-ranking ───────────────────

def test_run_composer_refine_down_ranks_an_expired_vault_citation(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "COMPOSER_OUTPUT_DIR", tmp_path / "composer_output")
    store.upsert(conn, TEST_TENANT_ID, _tender())
    past = (date.today() - timedelta(days=1)).isoformat()
    old_id = _vault_doc(conn, filename="old.pdf", valid_until=past)
    new_id = _vault_doc(conn, filename="new.pdf", valid_until=None)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]

    monkeypatch.setattr(composer, "retrieve_evidence", lambda *a, **k: [])
    monkeypatch.setattr(
        vault, "search_vault",
        lambda tenant_id, doc_ids, query, top_k=5: [
            {"text": "old", "source": "old.pdf", "doc_id": old_id, "similarity": 0.9},
            {"text": "new", "source": "new.pdf", "doc_id": new_id, "similarity": 0.2},
        ])
    monkeypatch.setattr(composer, "refine_section", lambda *a, **k: "refined text")

    api._run_composer_refine(TEST_TENANT_ID, "P-1", req_id, "feedback", [old_id, new_id])

    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    docs_in_order = [c["doc"] for c in req["citations"]]
    assert docs_in_order == ["Vault: new.pdf", "Vault: old.pdf"]
