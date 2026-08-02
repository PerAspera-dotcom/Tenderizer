"""Tenancy hardening: the pipeline_history/tender_history event-log pattern
extended to Vault document edits and Composer document/requirement edits —
the other half of NEXT.md's "extend the dismissal-attribution pattern (who/
when) to status, notes, and deadline changes across the app," applied to the
two feature areas that previously had zero edit history (only single-actor
creation timestamps).

update_composer_requirement_refined is deliberately excluded — it already has
its own purpose-built trail (version_history_json); the last test here
confirms that stays true rather than getting double-logged.
"""
import store
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


# ── Vault tags ────────────────────────────────────────────────────────────────

def test_vault_tag_change_is_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "spec.pdf", "application/pdf", 1, "/p1")

    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["fabric"])

    history = store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)
    assert len(history) == 1
    assert history[0]["field"] == "tags"
    assert history[0]["old_value"] == "[]"
    assert history[0]["new_value"] == '["fabric"]'


def test_vault_no_op_tag_set_is_not_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "spec.pdf", "application/pdf", 1, "/p1")
    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["fabric"])

    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["fabric"])  # same value

    assert len(store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)) == 1


def test_vault_tag_history_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "spec.pdf", "application/pdf", 1, "/p1")
    other_doc_id = store.add_vault_document(conn, OTHER_TENANT_ID, "spec2.pdf", "application/pdf", 1, "/p2")

    store.set_vault_document_tags(conn, TEST_TENANT_ID, doc_id, ["fabric"])
    store.set_vault_document_tags(conn, OTHER_TENANT_ID, other_doc_id, ["cert"])

    assert len(store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)) == 1
    assert len(store.get_vault_document_history(conn, OTHER_TENANT_ID, other_doc_id)) == 1
    # A doc id collision (both docs are id=1 in their own tenant) must not cross-leak history.
    assert store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)[0]["new_value"] == '["fabric"]'


# ── Vault metadata correction ────────────────────────────────────────────────

def test_vault_metadata_correction_is_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, "spec.pdf", "application/pdf", 1, "/p1")
    store.update_vault_document_metadata(conn, TEST_TENANT_ID, doc_id, doc_type="Datasheet",
                                          metadata={"Material": "PES"}, cpv_codes=[],
                                          confidence=0.8, fields_extracted=1, status="indexed")

    store.update_vault_document_metadata_fields(conn, TEST_TENANT_ID, doc_id, {"Material": "600D PES"})

    history = store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)
    assert len(history) == 1
    assert history[0]["field"] == "metadata"
    assert history[0]["old_value"] == '{"Material": "PES"}'
    assert history[0]["new_value"] == '{"Material": "600D PES"}'


# ── Composer document role override ──────────────────────────────────────────

def test_composer_role_override_is_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "mystery.pdf",
                                          "application/pdf", 1, "/p", "unknown")

    store.set_composer_document_role(conn, TEST_TENANT_ID, doc_id, "tech")

    history = store.get_composer_document_history(conn, TEST_TENANT_ID, doc_id)
    assert len(history) == 1
    assert history[0]["field"] == "role"
    assert history[0]["old_value"] is None
    assert history[0]["new_value"] == "tech"


def test_composer_role_history_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    doc_id = store.add_composer_document(conn, TEST_TENANT_ID, "P-1", "a.pdf",
                                          "application/pdf", 1, "/p1", "unknown")
    other_doc_id = store.add_composer_document(conn, OTHER_TENANT_ID, "P-1", "b.pdf",
                                                 "application/pdf", 1, "/p2", "unknown")

    store.set_composer_document_role(conn, TEST_TENANT_ID, doc_id, "tech")
    store.set_composer_document_role(conn, OTHER_TENANT_ID, other_doc_id, "background")

    assert [h["new_value"] for h in store.get_composer_document_history(conn, TEST_TENANT_ID, doc_id)] == ["tech"]
    assert [h["new_value"] for h in store.get_composer_document_history(conn, OTHER_TENANT_ID, other_doc_id)] == ["background"]


# ── Composer requirement validation ──────────────────────────────────────────

def test_composer_requirement_validation_change_is_logged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]

    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")

    history = store.get_composer_requirement_history(conn, TEST_TENANT_ID, req_id)
    assert len(history) == 1
    assert history[0]["field"] == "validation"
    assert history[0]["old_value"] == "pending"
    assert history[0]["new_value"] == "validated"


def test_composer_requirement_history_is_tenant_scoped(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    other_req_id = store.add_composer_requirements(conn, OTHER_TENANT_ID, "P-1", [
        {"title": "T2", "extracted": "E2", "source": "S2", "confidence": 0.5}])[0]

    store.update_composer_requirement_validation(conn, TEST_TENANT_ID, req_id, "validated")
    store.update_composer_requirement_validation(conn, OTHER_TENANT_ID, other_req_id, "not_applicable")

    assert [h["new_value"] for h in store.get_composer_requirement_history(conn, TEST_TENANT_ID, req_id)] == ["validated"]
    assert [h["new_value"] for h in store.get_composer_requirement_history(conn, OTHER_TENANT_ID, other_req_id)] == ["not_applicable"]


# ── Composer requirement resolved ────────────────────────────────────────────

def test_composer_requirement_resolved_is_logged_once(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]

    store.mark_composer_requirement_resolved(conn, TEST_TENANT_ID, req_id)
    store.mark_composer_requirement_resolved(conn, TEST_TENANT_ID, req_id)  # already resolved, no-op

    history = [h for h in store.get_composer_requirement_history(conn, TEST_TENANT_ID, req_id)
               if h["field"] == "resolved"]
    assert len(history) == 1
    assert history[0]["old_value"] == "False"
    assert history[0]["new_value"] == "True"


# ── No double-bookkeeping with the refine flow ───────────────────────────────

def test_composer_requirement_refine_does_not_duplicate_into_the_event_table(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    req_id = store.add_composer_requirements(conn, TEST_TENANT_ID, "P-1", [
        {"title": "T", "extracted": "E", "source": "S", "confidence": 0.5}])[0]
    store.update_composer_requirement_result(conn, TEST_TENANT_ID, req_id, "complete", 0.5, "v1 text", [])

    store.update_composer_requirement_refined(conn, TEST_TENANT_ID, req_id, "v2 text", "make it shorter")

    req = store.get_composer_requirement(conn, TEST_TENANT_ID, req_id)
    assert req["version"] == 2
    assert len(req["version_history"]) == 1  # its own purpose-built trail, unaffected
    assert store.get_composer_requirement_history(conn, TEST_TENANT_ID, req_id) == []  # not duplicated here
