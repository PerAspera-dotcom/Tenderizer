"""Step 34 — the five Vault/Composer secondary pages that were preview stubs
(Vault Rules/Collections/Settings, Composer Style Guide/Settings — see
CLAUDE_CODE_NEXT.md). Store-layer round trips + the /api/vault/* and
/api/composer/* endpoints they're built on, following the same
direct-function-call pattern as test_20_tenant_config.py / test_28_vault.py /
test_29_composer.py (no TestClient, no auth mocking — handlers are called
directly with tenant_id).
"""
import io

from fastapi import UploadFile

import composer
import store
import vault
import api
from conftest import TEST_TENANT_ID


# ── store: vault rules ───────────────────────────────────────────────────────

def test_get_vault_rules_defaults_to_empty_hints(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert store.get_vault_rules(conn, TEST_TENANT_ID) == {"hints": []}


def test_set_vault_rules_overwrites_hint_list(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_vault_rules(conn, TEST_TENANT_ID, ["always check fire rating"])
    store.set_vault_rules(conn, TEST_TENANT_ID, ["check water column"])
    assert store.get_vault_rules(conn, TEST_TENANT_ID) == {"hints": ["check water column"]}


def test_two_tenants_have_independent_vault_rules(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, 2)
    store.set_vault_rules(conn, 2, ["tenant 2 hint"])
    assert store.get_vault_rules(conn, 2) == {"hints": ["tenant 2 hint"]}
    assert store.get_vault_rules(conn, TEST_TENANT_ID) == {"hints": []}


# ── store: vault settings ────────────────────────────────────────────────────

def test_get_vault_settings_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert store.get_vault_settings(conn, TEST_TENANT_ID) == {"confidence_threshold": 0.6}


def test_set_vault_settings_merges_only_provided_keys(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_vault_settings(conn, TEST_TENANT_ID, {"confidence_threshold": 0.8})
    assert store.get_vault_settings(conn, TEST_TENANT_ID)["confidence_threshold"] == 0.8


# ── store: composer settings ─────────────────────────────────────────────────

def test_get_composer_settings_defaults(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert store.get_composer_settings(conn, TEST_TENANT_ID) == {
        "good_similarity": 0.35, "partial_similarity": 0.20, "top_k": 5,
    }


def test_set_composer_settings_merges_only_provided_keys(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_composer_settings(conn, TEST_TENANT_ID, {"top_k": 10})
    after = store.get_composer_settings(conn, TEST_TENANT_ID)
    assert after["top_k"] == 10
    assert after["good_similarity"] == 0.35  # untouched


def test_two_tenants_have_independent_composer_settings(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, 2)
    store.set_composer_settings(conn, 2, {"top_k": 20})
    assert store.get_composer_settings(conn, 2)["top_k"] == 20
    assert store.get_composer_settings(conn, TEST_TENANT_ID)["top_k"] == 5


# ── store: style guide + examples ────────────────────────────────────────────

def test_get_style_guide_defaults_to_none(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    assert store.get_style_guide(conn, TEST_TENANT_ID) == {
        "style_guide": None, "source_doc_count": 0, "generated_at": None,
    }


def test_set_style_guide_overwrites_wholesale(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.set_style_guide(conn, TEST_TENANT_ID, "Formal tone.", 2, "2026-01-01T00:00:00")
    assert store.get_style_guide(conn, TEST_TENANT_ID) == {
        "style_guide": "Formal tone.", "source_doc_count": 2, "generated_at": "2026-01-01T00:00:00",
    }


def test_style_example_crud_round_trip(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    ex_id = store.add_style_example(conn, TEST_TENANT_ID, "prior.docx",
                                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                     100, "/p1", "We hereby confirm compliance.")
    assert [e["filename"] for e in store.list_style_examples(conn, TEST_TENANT_ID)] == ["prior.docx"]
    assert store.get_style_example_texts(conn, TEST_TENANT_ID) == ["We hereby confirm compliance."]
    assert store.get_style_example(conn, TEST_TENANT_ID, ex_id)["storage_path"] == "/p1"

    store.delete_style_example(conn, TEST_TENANT_ID, ex_id)
    assert store.list_style_examples(conn, TEST_TENANT_ID) == []


# ── store: vault document tags ───────────────────────────────────────────────

def test_set_vault_document_tags_and_list_filters(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 1, "/p1")
    store.add_vault_document(conn, TEST_TENANT_ID, "b.pdf", "application/pdf", 1, "/p2")
    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["tents", "waterproof"])

    tagged = store.list_vault_documents(conn, TEST_TENANT_ID, tag="tents")
    assert [d["filename"] for d in tagged] == ["a.pdf"]
    assert store.list_vault_documents(conn, TEST_TENANT_ID)[0]["tags"] == ["tents", "waterproof"]


def test_list_vault_tags_returns_sorted_distinct_tags(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    d1 = store.add_vault_document(conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 1, "/p1")
    d2 = store.add_vault_document(conn, TEST_TENANT_ID, "b.pdf", "application/pdf", 1, "/p2")
    store.set_vault_document_tags(conn, TEST_TENANT_ID, d1, ["waterproof", "tents"])
    store.set_vault_document_tags(conn, TEST_TENANT_ID, d2, ["tents"])
    assert store.list_vault_tags(conn, TEST_TENANT_ID) == ["tents", "waterproof"]


def test_find_vault_documents_filters_by_tag(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 1, "/p1")
    store.update_vault_document_metadata(conn, TEST_TENANT_ID, doc_id, doc_type="Datasheet",
                                          metadata={}, cpv_codes=[], confidence=0.9,
                                          fields_extracted=0, status="indexed")
    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["tents"])
    assert len(store.find_vault_documents(conn, TEST_TENANT_ID, tag="tents")) == 1
    assert store.find_vault_documents(conn, TEST_TENANT_ID, tag="nope") == []


# ── vault.extract_metadata: extra_hints ──────────────────────────────────────

def test_extract_metadata_includes_extra_hints_in_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(vault, "_pdf_first_pages_as_images", lambda path: ["base64imagedata"])

    class _FakeContentBlock:
        text = '{"doc_type": "Other", "metadata": {}, "cpv_codes": [], "confidence": 0.1}'

    class _FakeMessage:
        content = [_FakeContentBlock()]

    captured = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage()

    class _FakeClient:
        messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _FakeClient())

    vault.extract_metadata("doc.pdf", "application/pdf", extra_hints=["always check EN 13501"])
    prompt_text = captured["messages"][0]["content"][-1]["text"]
    assert "always check EN 13501" in prompt_text


# ── composer.extract_style_guide ─────────────────────────────────────────────

def test_extract_style_guide_no_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert composer.extract_style_guide(["some text"]) is None


def test_extract_style_guide_no_examples_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    assert composer.extract_style_guide([]) is None


def test_extract_style_guide_returns_claude_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    class _FakeContentBlock:
        text = "Tone: formal.\nPhrases to use: 'we hereby confirm'."

    class _FakeMessage:
        content = [_FakeContentBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeMessage()

    class _FakeClient:
        messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _FakeClient())

    assert composer.extract_style_guide(["example one", "example two"]) == \
        "Tone: formal.\nPhrases to use: 'we hereby confirm'."


# ── composer._gap_status: configurable thresholds ────────────────────────────

def test_gap_status_uses_custom_thresholds():
    chunks = [{"similarity": 0.5}]
    assert composer._gap_status(chunks) == "complete"  # default GOOD_SIMILARITY=0.35
    assert composer._gap_status(chunks, good_similarity=0.9, partial_similarity=0.4) == "linked"
    assert composer._gap_status(chunks, good_similarity=0.9, partial_similarity=0.6) == "completed"


# ── API: vault rules / settings / tags ───────────────────────────────────────

def _seed_vault(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "VAULT_UPLOAD_DIR", tmp_path / "vault_uploads")
    return store.init_db(db_path)


def test_vault_rules_endpoints_round_trip(tmp_path, monkeypatch):
    _seed_vault(tmp_path, monkeypatch)
    assert api.get_vault_rules_config(tenant_id=TEST_TENANT_ID) == {"hints": []}
    api.put_vault_rules_config(api.VaultRulesBody(hints=["check fire rating"]), tenant_id=TEST_TENANT_ID)
    assert api.get_vault_rules_config(tenant_id=TEST_TENANT_ID) == {"hints": ["check fire rating"]}


def test_vault_settings_endpoint_includes_extraction_model(tmp_path, monkeypatch):
    _seed_vault(tmp_path, monkeypatch)
    body = api.get_vault_settings_config(tenant_id=TEST_TENANT_ID)
    assert body["confidence_threshold"] == 0.6
    assert body["extraction_model"] == vault.CLAUDE_MODEL

    api.put_vault_settings_config(api.VaultSettingsBody(confidence_threshold=0.75), tenant_id=TEST_TENANT_ID)
    assert api.get_vault_settings_config(tenant_id=TEST_TENANT_ID)["confidence_threshold"] == 0.75


def test_patch_vault_doc_tags_404_for_unknown_doc(tmp_path, monkeypatch):
    _seed_vault(tmp_path, monkeypatch)
    try:
        api.patch_vault_doc_tags(999, api.VaultTagsBody(tags=["x"]), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_patch_vault_doc_tags_and_filter_docs(tmp_path, monkeypatch):
    conn = _seed_vault(tmp_path, monkeypatch)
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "a.pdf", "application/pdf", 1, "/p1")
    api.patch_vault_doc_tags(doc_id, api.VaultTagsBody(tags=["tents"]), tenant_id=TEST_TENANT_ID)

    filtered = api.get_vault_docs(q=None, tag="tents", tenant_id=TEST_TENANT_ID)
    assert filtered["total"] == 1
    assert api.get_vault_tags(tenant_id=TEST_TENANT_ID) == {"tags": ["tents"]}


# ── API: composer settings / style ───────────────────────────────────────────

def _seed_composer(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(api, "STYLE_UPLOAD_DIR", tmp_path / "composer_style_uploads")
    return store.init_db(db_path)


def test_composer_settings_endpoint_includes_model(tmp_path, monkeypatch):
    _seed_composer(tmp_path, monkeypatch)
    body = api.get_composer_settings_config(tenant_id=TEST_TENANT_ID)
    assert body["top_k"] == 5
    assert body["model"] == composer.CLAUDE_MODEL

    api.put_composer_settings_config(api.ComposerSettingsBody(top_k=8), tenant_id=TEST_TENANT_ID)
    assert api.get_composer_settings_config(tenant_id=TEST_TENANT_ID)["top_k"] == 8


def test_composer_style_manual_edit_round_trips(tmp_path, monkeypatch):
    _seed_composer(tmp_path, monkeypatch)
    assert api.get_composer_style(tenant_id=TEST_TENANT_ID)["style_guide"] is None
    result = api.put_composer_style(api.ComposerStyleBody(style_guide="Be formal."), tenant_id=TEST_TENANT_ID)
    assert result["style_guide"] == "Be formal."
    assert api.get_composer_style(tenant_id=TEST_TENANT_ID)["style_guide"] == "Be formal."


def _style_file(name="prior.pdf", content=b"%PDF-1.4 fake pdf content"):
    return UploadFile(file=io.BytesIO(content), filename=name)


def test_style_example_upload_list_delete(tmp_path, monkeypatch):
    import asyncio
    _seed_composer(tmp_path, monkeypatch)
    monkeypatch.setattr(vault, "parse_document", lambda path, content_type: "We comply with EN 13501.")

    created = asyncio.run(api.upload_style_example(file=_style_file(), tenant_id=TEST_TENANT_ID))
    assert created["filename"] == "prior.pdf"

    listed = api.list_style_examples(tenant_id=TEST_TENANT_ID)
    assert len(listed["results"]) == 1

    api.delete_style_example(created["id"], tenant_id=TEST_TENANT_ID)
    assert api.list_style_examples(tenant_id=TEST_TENANT_ID)["results"] == []


def test_extract_composer_style_requires_examples(tmp_path, monkeypatch):
    _seed_composer(tmp_path, monkeypatch)
    try:
        api.extract_composer_style(tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409


def test_extract_composer_style_saves_result(tmp_path, monkeypatch):
    conn = _seed_composer(tmp_path, monkeypatch)
    store.add_style_example(conn, TEST_TENANT_ID, "prior.docx", "application/msword", 10, "/p1",
                             "We hereby confirm compliance.")
    monkeypatch.setattr(composer, "extract_style_guide", lambda texts: "Tone: formal.")

    result = api.extract_composer_style(tenant_id=TEST_TENANT_ID)
    assert result["style_guide"] == "Tone: formal."
    assert result["source_doc_count"] == 1
    assert api.get_composer_style(tenant_id=TEST_TENANT_ID)["style_guide"] == "Tone: formal."


def test_run_composer_generate_passes_style_guide_and_settings(tmp_path, monkeypatch):
    conn = _seed_composer(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "COMPOSER_OUTPUT_DIR", tmp_path / "composer_output")
    store.upsert(conn, TEST_TENANT_ID, {
        "source": "TED", "pub_number": "P-1", "tag_line": "Tent supply",
        "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
        "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
        "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
        "matched_terms": ["tent"], "match_source": "cpv", "url": "http://x",
        "first_seen": None, "status": "shortlisted"})
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")
    store.set_style_guide(conn, TEST_TENANT_ID, "Be formal.", 1, "2026-01-01T00:00:00")
    store.set_composer_settings(conn, TEST_TENANT_ID, {"top_k": 9})

    captured = {}

    def _fake_run_generate(tenant_id, pub_number, requirements, style_guide=None,
                            top_k=None, good_similarity=None, partial_similarity=None):
        captured["style_guide"] = style_guide
        captured["top_k"] = top_k
        return [{"id": req_id, "gap_status": "complete", "similarity": 0.6,
                  "response_text": "ok", "citations": []}]

    monkeypatch.setattr(composer, "run_generate", _fake_run_generate)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    api._run_composer_generate(TEST_TENANT_ID, "P-1")
    assert captured["style_guide"] == "Be formal."
    assert captured["top_k"] == 9
