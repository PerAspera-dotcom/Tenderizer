"""Step 27 — CR-003 G3: rescore stale `status='new'` rows after a config
change (`store.rescore_pending`), without touching triaged records.

Root cause this guards against: `store.upsert()` is deliberately insert-only,
so a row ingested before cpv.yaml/keywords.yaml gained coverage for it stays
tagged unmatched forever unless something revisits it (the investigation
found exactly this pattern behind a client-reported missed tender, CR-003
finding G3).
"""
import store
from conftest import TEST_TENANT_ID


def _stale_rec(pub_number, status="new"):
    # cpv_codes=["39522530"] ("Tents") and tag_line containing "tent" both
    # match tenant 1's YAML-seeded default config (see test_20's
    # test_ensure_tenant_seeds_cpv_from_yaml_defaults) — so under current
    # config this record *should* match, but is stored as if it never did,
    # mirroring a row ingested before that config existed.
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": [], "match_source": None, "url": "http://x",
            "first_seen": None, "exclude_reason": "", "status": status}


def test_rescore_pending_updates_stale_new_row_that_now_matches(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _stale_rec("STALE-1"))

    stats = store.rescore_pending(conn, TEST_TENANT_ID)

    assert stats["updated"] == 1
    updated = store.all_records(conn, TEST_TENANT_ID)[0]
    assert updated["match_source"] == "both"
    assert "tent" in updated["matched_terms"]
    assert updated["exclude_reason"] == ""


def test_rescore_pending_leaves_already_matched_rows_unchanged(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _stale_rec("STALE-1"))
    store.rescore_pending(conn, TEST_TENANT_ID)

    stats = store.rescore_pending(conn, TEST_TENANT_ID)

    assert stats["updated"] == 0
    assert stats["unchanged"] == 1


def test_rescore_pending_ignores_non_new_status(tmp_path):
    """Governance boundary: a triaged (e.g. shortlisted) row keeps its tagging
    as it was when the customer acted on it, even if stale under current
    config — only pending review-queue rows get re-tagged.
    """
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, _stale_rec("SHORTLISTED-1", status="shortlisted"))

    stats = store.rescore_pending(conn, TEST_TENANT_ID)

    assert stats.get("total", 0) == 0
    unchanged = store.all_records(conn, TEST_TENANT_ID)[0]
    assert unchanged["match_source"] is None
    assert unchanged["matched_terms"] == []


def test_rescore_pending_no_new_rows_returns_empty_counter(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    stats = store.rescore_pending(conn, TEST_TENANT_ID)
    assert not stats
