"""CR-007 Phase C — cross-portal de-duplication (e.g. the same tender on
both TED and BOAMP).

dedup.find_cross_portal_duplicates(records, threshold) -> unit coverage of
the two signals (buyer-reference match, translated-description similarity).
store.py covers tender_duplicates CRUD + tenant_dedup_settings round-trip.
The API section confirms the duplicate flag surfaces symmetrically on both
records (never a one-sided link) and the settings endpoint round-trips.
"""
import json

import dedup
import normalize
import store
import api
from conftest import TEST_TENANT_ID, make_identity


# ── dedup.find_cross_portal_duplicates — unit tests ──────────────────────────

def _rec(pub_number, source, internal_identifier=None, cpv_codes=None,
         description_en=None, exclude_reason=""):
    return {"pub_number": pub_number, "source": source,
            "internal_identifier": internal_identifier,
            "cpv_codes": cpv_codes or [], "description_en": description_en,
            "exclude_reason": exclude_reason}


def test_reference_match_across_different_sources():
    a = _rec("TED-1", "TED", internal_identifier="2026-036")
    b = _rec("BOAMP-1", "BOAMP", internal_identifier="2026-036")
    matches = dedup.find_cross_portal_duplicates([a, b])
    assert matches == [("BOAMP-1", "TED-1", "reference", None)]


def test_reference_match_is_case_and_whitespace_insensitive():
    a = _rec("TED-1", "TED", internal_identifier=" 2026-036 ")
    b = _rec("BOAMP-1", "BOAMP", internal_identifier="2026-036")
    matches = dedup.find_cross_portal_duplicates([a, b])
    assert len(matches) == 1
    assert matches[0][2] == "reference"


def test_reference_match_ignored_within_the_same_source():
    a = _rec("TED-1", "TED", internal_identifier="2026-036")
    b = _rec("TED-2", "TED", internal_identifier="2026-036")
    assert dedup.find_cross_portal_duplicates([a, b]) == []


def test_no_reference_match_when_either_side_is_blank():
    a = _rec("TED-1", "TED", internal_identifier=None)
    b = _rec("BOAMP-1", "BOAMP", internal_identifier=None)
    assert dedup.find_cross_portal_duplicates([a, b]) == []


def test_similarity_match_above_threshold():
    a = _rec("TED-1", "TED", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    b = _rec("BOAMP-1", "BOAMP", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    matches = dedup.find_cross_portal_duplicates([a, b], similarity_threshold=0.75)
    assert len(matches) == 1
    pub_a, pub_b, match_type, similarity = matches[0]
    assert {pub_a, pub_b} == {"TED-1", "BOAMP-1"}
    assert match_type == "similarity"
    assert similarity == 1.0


def test_similarity_match_below_threshold_is_not_flagged():
    a = _rec("TED-1", "TED", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    b = _rec("BOAMP-1", "BOAMP", cpv_codes=["39522530"],
             description_en="Catering services for municipal school canteens.")
    assert dedup.find_cross_portal_duplicates([a, b], similarity_threshold=0.75) == []


def test_similarity_never_compared_without_a_shared_cpv_code():
    a = _rec("TED-1", "TED", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    b = _rec("BOAMP-1", "BOAMP", cpv_codes=["00000000"],
             description_en="Supply of military tents for field camps.")
    assert dedup.find_cross_portal_duplicates([a, b], similarity_threshold=0.75) == []


def test_excluded_records_never_participate():
    a = _rec("TED-1", "TED", internal_identifier="2026-036", exclude_reason="F4")
    b = _rec("BOAMP-1", "BOAMP", internal_identifier="2026-036")
    assert dedup.find_cross_portal_duplicates([a, b]) == []


def test_reference_signal_wins_over_a_redundant_similarity_signal():
    a = _rec("TED-1", "TED", internal_identifier="2026-036", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    b = _rec("BOAMP-1", "BOAMP", internal_identifier="2026-036", cpv_codes=["39522530"],
             description_en="Supply of military tents for field camps.")
    matches = dedup.find_cross_portal_duplicates([a, b], similarity_threshold=0.75)
    assert len(matches) == 1  # not double-recorded
    assert matches[0][2] == "reference"


# ── store.py: tender_duplicates + tenant_dedup_settings ──────────────────────

def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def test_upsert_tender_duplicate_stores_the_pair_sorted(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert len(dupes) == 1
    assert dupes[0]["pub_number_a"] < dupes[0]["pub_number_b"]


def test_upsert_tender_duplicate_is_idempotent_regardless_of_argument_order(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "BOAMP-1", "TED-1", "similarity", 0.9)
    dupes = store.get_tender_duplicates_for_tenant(conn, TEST_TENANT_ID)
    assert len(dupes) == 1
    assert dupes[0]["match_type"] == "similarity"  # the later upsert wins
    assert dupes[0]["similarity"] == 0.9


def test_dedup_settings_default_and_round_trip(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    assert store.get_dedup_settings(conn, TEST_TENANT_ID) == {"similarity_threshold": 0.75}
    store.set_dedup_settings(conn, TEST_TENANT_ID, {"similarity_threshold": 0.6})
    assert store.get_dedup_settings(conn, TEST_TENANT_ID) == {"similarity_threshold": 0.6}


# ── API: symmetric surfacing + settings endpoint ─────────────────────────────

def _tender(pub_number, source):
    return {"source": source, "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01T00:00:00+00:00", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv",
            "url": f"http://{source.lower()}.example/{pub_number}",
            "first_seen": None, "exclude_reason": ""}


def test_duplicate_surfaces_symmetrically_on_both_records(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", "TED"))
    store.upsert(conn, TEST_TENANT_ID, _tender("BOAMP-1", "BOAMP"))
    store.upsert_tender_duplicate(conn, TEST_TENANT_ID, "TED-1", "BOAMP-1", "reference")

    identity = make_identity()
    ted_rec = api.get_tender("TED-1", identity=identity)
    boamp_rec = api.get_tender("BOAMP-1", identity=identity)

    assert len(ted_rec["duplicates"]) == 1
    assert ted_rec["duplicates"][0]["pub_number"] == "BOAMP-1"
    assert ted_rec["duplicates"][0]["source"] == "BOAMP"
    assert ted_rec["duplicates"][0]["url"] == "http://boamp.example/BOAMP-1"

    assert len(boamp_rec["duplicates"]) == 1
    assert boamp_rec["duplicates"][0]["pub_number"] == "TED-1"
    assert boamp_rec["duplicates"][0]["source"] == "TED"


def test_tender_without_a_duplicate_has_an_empty_list_not_a_missing_key(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("TED-1", "TED"))
    rec = api.get_tender("TED-1", identity=make_identity())
    assert rec["duplicates"] == []


def test_dedup_settings_endpoint_round_trips(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    assert api.get_dedup_settings_config(tenant_id=TEST_TENANT_ID) == {"similarity_threshold": 0.75}
    api.put_dedup_settings_config(api.DedupSettingsBody(similarity_threshold=0.5),
                                   tenant_id=TEST_TENANT_ID)
    assert api.get_dedup_settings_config(tenant_id=TEST_TENANT_ID) == {"similarity_threshold": 0.5}


# ── normalize.py: internal_identifier extraction ─────────────────────────────
# Confirmed live (2026-08) against real TED/BOAMP data before writing this —
# see the CR-007 Phase C plan's Context section. Fixtures below mirror the
# actual shapes probed, not guessed ones.

def test_normalize_ted_extracts_internal_identifier(raw_ted_supply):
    raw_ted_supply["internal-identifier-proc"] = "2026-036"
    assert normalize.normalize_ted(raw_ted_supply)["internal_identifier"] == "2026-036"


def test_normalize_ted_internal_identifier_none_when_absent(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["internal_identifier"] is None


def test_normalize_boamp_extracts_internal_identifier_from_eforms(raw_boamp_supply):
    raw_boamp_supply["donnees"] = json.dumps({
        "EFORMS": {"ContractNotice": {
            "cac:ProcurementProject": {"cbc:ID": "26AO-V03"},
            "cac:ProcurementProjectLot": [{"cac:ProcurementProject": {"cbc:ID": "01"}}],
        }}
    })
    assert normalize.normalize_boamp(raw_boamp_supply)["internal_identifier"] == "26AO-V03"


def test_normalize_boamp_falls_back_to_legacy_reference_marche(raw_boamp_supply):
    raw_boamp_supply["donnees"] = json.dumps({
        "CONDITION_ADMINISTRATIVE": {"REFERENCE_MARCHE": "2015-S-1"},
    })
    assert normalize.normalize_boamp(raw_boamp_supply)["internal_identifier"] == "2015-S-1"


def test_normalize_boamp_internal_identifier_none_when_absent(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["internal_identifier"] is None
