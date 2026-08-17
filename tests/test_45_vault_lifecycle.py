"""CR-007 Phase E — Vault document lifecycle (E1: expiry/"needs replacement"
+ down-ranking) and auto file-naming (E2: suggestion + accept/rename).
"""
from datetime import date, timedelta

import store
import vault
import api
from conftest import TEST_TENANT_ID

OTHER_TENANT_ID = 999


def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def _seed_doc(conn, filename="spec.pdf", doc_type="Certificate", metadata=None,
              cpv_codes=None, confidence=0.8, valid_until=None, status="indexed"):
    doc_id = store.add_vault_document(conn, TEST_TENANT_ID, filename, "application/pdf", 100, "/p")
    store.update_vault_document_metadata(
        conn, TEST_TENANT_ID, doc_id, doc_type=doc_type, metadata=metadata or {},
        cpv_codes=cpv_codes or [], confidence=confidence, fields_extracted=len(metadata or {}),
        status=status, valid_until=valid_until)
    return doc_id


# ── E1: valid_until extraction validation ────────────────────────────────────

def test_valid_iso_date_accepts_real_dates():
    assert vault._valid_iso_date_or_none("2027-01-15") == "2027-01-15"


def test_valid_iso_date_rejects_non_dates():
    assert vault._valid_iso_date_or_none("2027") is None
    assert vault._valid_iso_date_or_none("5 years") is None
    assert vault._valid_iso_date_or_none(None) is None
    assert vault._valid_iso_date_or_none("") is None


# ── E1: expiry computation (api._attach_expiry) ──────────────────────────────

def test_attach_expiry_flags_a_past_date_as_expired():
    past = (date.today() - timedelta(days=5)).isoformat()
    docs = [{"valid_until": past}]
    api._attach_expiry(docs)
    assert docs[0]["expired"] is True
    assert docs[0]["expiring_soon"] is False


def test_attach_expiry_flags_a_near_date_as_expiring_soon():
    soon = (date.today() + timedelta(days=10)).isoformat()
    docs = [{"valid_until": soon}]
    api._attach_expiry(docs)
    assert docs[0]["expired"] is False
    assert docs[0]["expiring_soon"] is True


def test_attach_expiry_leaves_a_far_future_date_unflagged():
    far = (date.today() + timedelta(days=365)).isoformat()
    docs = [{"valid_until": far}]
    api._attach_expiry(docs)
    assert docs[0]["expired"] is False
    assert docs[0]["expiring_soon"] is False


def test_attach_expiry_leaves_a_doc_with_no_valid_until_unflagged():
    docs = [{"valid_until": None}]
    api._attach_expiry(docs)
    assert docs[0]["expired"] is False
    assert docs[0]["expiring_soon"] is False


# ── E1: store round-trip ─────────────────────────────────────────────────────

def test_valid_until_round_trips_through_store(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn, valid_until="2027-06-01")
    doc = next(d for d in store.list_vault_documents(conn, TEST_TENANT_ID) if d["id"] == doc_id)
    assert doc["valid_until"] == "2027-06-01"


def test_valid_until_defaults_to_none(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn)
    doc = next(d for d in store.list_vault_documents(conn, TEST_TENANT_ID) if d["id"] == doc_id)
    assert doc["valid_until"] is None


def test_get_vault_doc_detail_includes_expiry_flags(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    past = (date.today() - timedelta(days=1)).isoformat()
    doc_id = _seed_doc(conn, valid_until=past)
    doc = api.get_vault_doc_detail(doc_id, tenant_id=TEST_TENANT_ID)
    assert doc["expired"] is True


# ── E1: down-ranking in the search endpoint ──────────────────────────────────

def test_search_endpoint_ranks_expired_docs_after_non_expired_regardless_of_confidence(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    past = (date.today() - timedelta(days=1)).isoformat()
    expired_id = _seed_doc(conn, filename="old.pdf", confidence=0.99, valid_until=past)
    fresh_id = _seed_doc(conn, filename="new.pdf", confidence=0.4, valid_until=None)

    # top_k is a FastAPI Query(...) default — must be passed explicitly when
    # calling the route as a plain function (same reason limit/offset need
    # explicit values elsewhere in this test suite).
    results = api.search_vault_endpoint(top_k=8, tenant_id=TEST_TENANT_ID)["results"]
    ids = [r["doc_id"] for r in results]
    assert ids.index(fresh_id) < ids.index(expired_id)
    assert next(r for r in results if r["doc_id"] == expired_id)["expired"] is True


# ── E2: suggest_filename ─────────────────────────────────────────────────────

def test_suggest_filename_combines_doc_type_and_metadata():
    name = vault.suggest_filename("Certificate", {"Material": "600D PES"}, ["39522530"], "scan.pdf")
    assert name == "Certificate_600D_PES_39522530.pdf"


def test_suggest_filename_falls_back_to_original_when_nothing_extracted():
    assert vault.suggest_filename(None, {}, [], "scan.pdf") == "scan.pdf"
    assert vault.suggest_filename("Other", {}, [], "scan.pdf") == "scan.pdf"


def test_suggest_filename_preserves_the_original_extension():
    name = vault.suggest_filename("Datasheet", {"Material": "steel"}, [], "spec.docx")
    assert name.endswith(".docx")


def test_attach_suggested_filename_omits_when_unchanged(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    docs = [{"filename": "scan.pdf", "doc_type": None, "metadata": {}, "cpv_codes": []}]
    api._attach_suggested_filename(docs)
    assert docs[0]["suggested_filename"] is None


def test_attach_suggested_filename_present_when_it_would_change(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    docs = [{"filename": "scan.pdf", "doc_type": "Certificate",
             "metadata": {"Material": "600D PES"}, "cpv_codes": []}]
    api._attach_suggested_filename(docs)
    assert docs[0]["suggested_filename"] == "Certificate_600D_PES.pdf"


# ── E2: rename endpoint ───────────────────────────────────────────────────────

def test_rename_endpoint_accepts_the_suggested_name(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn, filename="scan.pdf", doc_type="Certificate", metadata={"Material": "PES"})

    result = api.patch_vault_doc_filename(
        doc_id, api.VaultFilenameBody(filename="Certificate_PES.pdf"), tenant_id=TEST_TENANT_ID)
    assert result["filename"] == "Certificate_PES.pdf"

    doc = api.get_vault_doc_detail(doc_id, tenant_id=TEST_TENANT_ID)
    assert doc["filename"] == "Certificate_PES.pdf"
    assert doc["suggested_filename"] is None  # now matches, nothing left to suggest


def test_rename_endpoint_rejects_blank_filename(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn)
    try:
        api.patch_vault_doc_filename(doc_id, api.VaultFilenameBody(filename="   "),
                                      tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 400


def test_rename_endpoint_404s_for_another_tenant(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn)
    try:
        api.patch_vault_doc_filename(doc_id, api.VaultFilenameBody(filename="new.pdf"),
                                      tenant_id=OTHER_TENANT_ID)
        assert False, "expected HTTPException"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_rename_logs_history(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    doc_id = _seed_doc(conn, filename="old.pdf")
    store.rename_vault_document(conn, TEST_TENANT_ID, doc_id, "new.pdf")
    history = store.get_vault_document_history(conn, TEST_TENANT_ID, doc_id)
    assert any(h["field"] == "filename" and h["old_value"] == "old.pdf" and h["new_value"] == "new.pdf"
               for h in history)
