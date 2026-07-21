"""Step 7 — normalisation + storage (against the REAL multilingual TED structure)."""
import normalize, store
from conftest import TEST_TENANT_ID

REQUIRED_KEYS = {"source","pub_number","tag_line","buyer","country","place","category",
                 "procedure","pub_date","deadline","cpv_codes","matched_terms",
                 "match_source","url","first_seen","value","value_currency"}

def test_normalize_ted_has_all_schema_keys(raw_ted_supply):
    assert REQUIRED_KEYS <= set(normalize.normalize_ted(raw_ted_supply))

def test_picks_english_title_over_others(raw_ted_supply):
    # LANG_PREF prefers eng even though nld/fra are present
    assert normalize.normalize_ted(raw_ted_supply)["tag_line"] == "Sweden – Supply of military tents"

def test_flattens_buyer_name_list(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["buyer"] == "FMV"

def test_falls_back_to_available_language(raw_ted_services):
    # only 'fra' present for buyer-name -> still extracted
    assert normalize.normalize_ted(raw_ted_services)["buyer"] == "Ministère des Armées"

def test_country_from_buyer_country(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["country"] == "SWE"

def test_place_excludes_country_keeps_nuts(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["place"] == "SE110"

def test_deadline_unwrapped_from_list(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["deadline"] == "2026-08-31T23:59:00+02:00"

def test_supplies_maps_to_supply_category(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["category"] == "Supply"

def test_services_maps_to_services_category(raw_ted_services):
    assert normalize.normalize_ted(raw_ted_services)["category"] == "Services"

def test_map_category_handles_dutch_and_french():
    assert normalize.map_category("Leveringen") == "Supply"
    assert normalize.map_category("FOURNITURES") == "Supply"
    assert normalize.map_category("Diensten") == "Services"
    assert normalize.map_category("works") == "Works"

def test_map_category_unknown_is_other():
    assert normalize.map_category("banana") == "Other"

def test_url_built_from_pub_number(raw_ted_supply):
    url = normalize.normalize_ted(raw_ted_supply)["url"]
    assert "381972-2026" in url and url.endswith("/html")

def test_record_hash_stable(raw_ted_supply):
    r = normalize.normalize_ted(raw_ted_supply)
    assert normalize.record_hash(r) == normalize.record_hash(r)

def test_record_hash_differs_by_pub_number(raw_ted_supply, raw_ted_services):
    a = normalize.normalize_ted(raw_ted_supply); b = normalize.normalize_ted(raw_ted_services)
    assert normalize.record_hash(a) != normalize.record_hash(b)

# ── CR-003 G4: structured award fields (verified live 2026-07 against TED
# 391890-2026 — see connectors/ted.py's FIELDS comment) ─────────────────────

def test_no_award_fields_yields_none_award_info(raw_ted_supply):
    r = normalize.normalize_ted(raw_ted_supply)
    assert r["raw_award_winner"] is None
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None

def test_extracts_winner_name_and_notice_level_award_value(raw_ted_supply):
    raw = dict(raw_ted_supply, **{
        "winner-name": {"ell": ["Κατασκευαστική Διάς ΑΤΕΒΕ"]},
        "result-value-notice": "45290.32",
        "result-value-cur-notice": "EUR",
    })
    r = normalize.normalize_ted(raw)
    assert r["raw_award_winner"] == "Κατασκευαστική Διάς ΑΤΕΒΕ"
    assert r["raw_award_value"] == "45290.32"
    assert r["raw_award_currency"] == "EUR"

def test_falls_back_to_per_lot_tender_value_when_notice_level_absent(raw_ted_supply):
    raw = dict(raw_ted_supply, **{"tender-value": ["43720"], "tender-value-cur": ["EUR"]})
    r = normalize.normalize_ted(raw)
    assert r["raw_award_value"] == "43720"
    assert r["raw_award_currency"] == "EUR"

def test_notice_level_award_value_preferred_over_per_lot(raw_ted_supply):
    raw = dict(raw_ted_supply, **{
        "result-value-notice": "45290.32", "result-value-cur-notice": "EUR",
        "tender-value": ["43720"], "tender-value-cur": ["EUR"],
    })
    r = normalize.normalize_ted(raw)
    assert r["raw_award_value"] == "45290.32"


# ── Past-tenders data-coverage follow-up: winner/lot/contract detail (TED) —
# field values below are the REAL live response for TED 391890-2026 (the
# exact notice CR-003/CR-005's screenshots used), fetched via the
# UNSUPPORTED_VALUE field-probe technique connectors/ted.py's FIELDS
# comment documents. ────────────────────────────────────────────────────────

def _ted_391890_award_fields():
    return {
        "winner-name": {"ell": ["Ι. ΚΑΤΣΙΔΩΝΙΩΤΑΚΗΣ Α.Τ.Ε.Β.Ε ΚΑΤΑΣΚΕΥΑΣΤΙΚΗ ΔΙΑΣ ΑΤΕΒΕ"]},
        "winner-city": ["Ηράκλειο "],
        "winner-country": ["GRC"],
        "winner-country-sub": ["EL431"],
        "winner-post-code": ["71409 "],
        "winner-identifier": ["094338244"],
        "winner-size": ["medium"],
        "winner-decision-date": ["2026-03-03+02:00"],
        "result-lot-identifier": ["LOT-0001"],
        "title-lot": {"ell": ["Τμήμα 10: Φορητός φωτισμός"]},
        "contract-duration-period-lot": [{"unit": "MONTH", "value": "6"}],
        "contract-identifier": ["2820/2026"],
        "contract-conclusion-date": ["2026-05-28+03:00"],
        "tender-identifier": ["355935"],
        "result-value-notice": "45290.32", "result-value-cur-notice": "EUR",
    }


def test_ted_award_detail_full_single_lot(raw_ted_supply):
    raw = dict(raw_ted_supply, **_ted_391890_award_fields())
    detail = normalize.normalize_ted(raw)["raw_award_detail"]
    assert detail == {
        "winner": {
            "registration_number": "094338244", "city": "Ηράκλειο", "postal_code": "71409",
            "nuts": "EL431", "country": "GRC", "size": "medium", "decision_date": "2026-03-03",
        },
        "lot": {"identifier": "LOT-0001", "title": "Τμήμα 10: Φορητός φωτισμός", "duration": "6 months"},
        "contract": {"identifier": "2820/2026", "conclusion_date": "2026-05-28", "tender_identifier": "355935"},
    }


def test_ted_award_detail_trims_trailing_whitespace(raw_ted_supply):
    # winner-city/-post-code come back with real trailing whitespace live.
    raw = dict(raw_ted_supply, **_ted_391890_award_fields())
    detail = normalize.normalize_ted(raw)["raw_award_detail"]
    assert detail["winner"]["city"] == "Ηράκλειο"
    assert detail["winner"]["postal_code"] == "71409"


def test_ted_award_detail_none_when_no_lot_identifier(raw_ted_supply):
    assert normalize.normalize_ted(raw_ted_supply)["raw_award_detail"] is None


def test_ted_award_detail_none_for_multi_lot_notice(raw_ted_supply):
    # Real multi-lot notices have mismatched array lengths/order across
    # winner-name vs. result-lot-identifier (verified live) — never guessed.
    raw = dict(raw_ted_supply, **{
        "result-lot-identifier": ["LOT-0001", "LOT-0002"],
        "winner-name": {"nld": ["LITES", "AMPLI"]},
        "winner-identifier": ["001", "002"],
    })
    assert normalize.normalize_ted(raw)["raw_award_detail"] is None


def test_ted_award_detail_partial_fields_still_populate(raw_ted_supply):
    # Only some fields present (e.g. no contract identifier disclosed) —
    # populates what's there, omits the rest, never fabricates.
    raw = dict(raw_ted_supply, **{
        "result-lot-identifier": ["LOT-0001"],
        "winner-city": ["Paris"],
    })
    detail = normalize.normalize_ted(raw)["raw_award_detail"]
    assert detail == {"winner": {"city": "Paris"}, "lot": {"identifier": "LOT-0001"}}


def test_upsert_new_returns_true(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    assert store.upsert(conn, TEST_TENANT_ID, normalize.normalize_ted(raw_ted_supply)) is True

def test_upsert_duplicate_returns_false_and_adds_no_row(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, TEST_TENANT_ID, rec)
    assert store.upsert(conn, TEST_TENANT_ID, rec) is False
    assert len(store.all_records(conn, TEST_TENANT_ID)) == 1

def test_first_seen_preserved_on_reinsert(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply); rec["first_seen"] = "2026-01-15"
    store.upsert(conn, TEST_TENANT_ID, rec); store.upsert(conn, TEST_TENANT_ID, rec)
    assert store.all_records(conn, TEST_TENANT_ID)[0]["first_seen"] == "2026-01-15"

def test_stored_record_roundtrips_cpv_list(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    store.upsert(conn, TEST_TENANT_ID, normalize.normalize_ted(raw_ted_supply))
    assert store.all_records(conn, TEST_TENANT_ID)[0]["cpv_codes"] == ["39522530", "39522500"]


# ── Past-tenders data-coverage follow-up: award_detail is nullable JSON
# (real SQL NULL when absent, never a fabricated "{}") ──────────────────────

def test_award_detail_roundtrips_when_present(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path / "t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    rec["award_detail"] = {"winner": {"city": "Paris"}, "lot": {"identifier": "LOT-0001"}}
    store.upsert(conn, TEST_TENANT_ID, rec)
    stored = store.all_records(conn, TEST_TENANT_ID)[0]
    assert stored["award_detail"] == {"winner": {"city": "Paris"}, "lot": {"identifier": "LOT-0001"}}


def test_award_detail_is_none_not_fabricated_when_absent(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path / "t.db"))
    store.upsert(conn, TEST_TENANT_ID, normalize.normalize_ted(raw_ted_supply))
    assert store.all_records(conn, TEST_TENANT_ID)[0]["award_detail"] is None


def test_update_classification_sets_award_detail(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path / "t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, TEST_TENANT_ID, rec)
    store.update_classification(conn, TEST_TENANT_ID, rec["pub_number"], "past_tender",
                                 "Winner Co", "45290.32", "EUR",
                                 award_detail={"winner": {"city": "Heraklion"}})
    stored = store.all_records(conn, TEST_TENANT_ID)[0]
    assert stored["award_detail"] == {"winner": {"city": "Heraklion"}}


def test_update_classification_award_detail_defaults_to_none(tmp_path, raw_ted_supply):
    # Existing callers (scratch_backfill_notice_type.py) that don't pass
    # award_detail must keep working unchanged.
    conn = store.init_db(str(tmp_path / "t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, TEST_TENANT_ID, rec)
    store.update_classification(conn, TEST_TENANT_ID, rec["pub_number"], "past_tender",
                                 "Winner Co", "45290.32", "EUR")
    assert store.all_records(conn, TEST_TENANT_ID)[0]["award_detail"] is None

def test_cpv_codes_deduped_at_ingest(raw_ted_supply):
    # CR-001 R2: TED can list the same CPV code twice (e.g. main + additional
    # classification) — dedupe at ingest, preserving first-seen order.
    raw_ted_supply["classification-cpv"] = ["39522530", "39522500", "39522530"]
    assert normalize.normalize_ted(raw_ted_supply)["cpv_codes"] == ["39522530", "39522500"]

def test_value_extracted_when_present(raw_ted_supply):
    # CR-001 F6: BT-27-Procedure (estimated-value-proc / -cur-proc)
    raw_ted_supply["estimated-value-proc"] = "5000000"
    raw_ted_supply["estimated-value-cur-proc"] = "SEK"
    r = normalize.normalize_ted(raw_ted_supply)
    assert r["value"] == "5000000" and r["value_currency"] == "SEK"

def test_value_absent_when_not_disclosed(raw_ted_supply):
    # most notices don't carry these fields at all — value disclosure is optional
    r = normalize.normalize_ted(raw_ted_supply)
    assert r["value"] == "" and r["value_currency"] == ""

def test_boamp_value_always_absent(raw_boamp_supply):
    # BOAMP's live schema has no value/amount field at all (verified live)
    r = normalize.normalize_boamp(raw_boamp_supply)
    assert r["value"] == "" and r["value_currency"] == ""

def test_language_is_eng_when_ted_provides_english(raw_ted_supply):
    # CR-001 R3: 'eng' present in notice-title -> no translation needed
    assert normalize.normalize_ted(raw_ted_supply)["language"] == "eng"

def test_language_falls_back_when_no_english_translation(raw_ted_supply):
    # TED didn't provide an English title for this notice -> falls back to
    # whatever LANG_PREF/first-available finds; here that's 'fra'
    del raw_ted_supply["notice-title"]["eng"]
    assert normalize.normalize_ted(raw_ted_supply)["language"] == "fra"

def test_boamp_language_always_fra(raw_boamp_supply):
    # BOAMP is French-only, single-language (per the connector's own docs)
    assert normalize.normalize_boamp(raw_boamp_supply)["language"] == "fra"


# ── Phase 2/3 step 3: tenant isolation ───────────────────────────────────────

def test_two_tenants_can_store_the_same_notice_independently(tmp_path, raw_ted_supply):
    # Two different tenant companies legitimately watching the SAME public TED
    # notice would produce the same hash — the PK is (tenant_id, hash), not
    # hash alone, specifically so this doesn't collide.
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    assert store.upsert(conn, 1, rec) is True
    assert store.upsert(conn, 2, rec) is True  # same hash, different tenant -> not a duplicate
    assert len(store.all_records(conn, 1)) == 1
    assert len(store.all_records(conn, 2)) == 1

def test_tenant_never_sees_another_tenants_records(tmp_path, raw_ted_supply, raw_ted_services):
    conn = store.init_db(str(tmp_path/"t.db"))
    store.upsert(conn, 1, normalize.normalize_ted(raw_ted_supply))
    store.upsert(conn, 2, normalize.normalize_ted(raw_ted_services))
    tenant_1_pubs = {r["pub_number"] for r in store.all_records(conn, 1)}
    tenant_2_pubs = {r["pub_number"] for r in store.all_records(conn, 2)}
    assert tenant_1_pubs == {"381972-2026"}
    assert tenant_2_pubs == {"381999-2026"}
    assert tenant_1_pubs.isdisjoint(tenant_2_pubs)

def test_set_status_only_affects_the_calling_tenants_row(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, 1, rec)
    store.upsert(conn, 2, rec)
    store.set_status(conn, 1, rec["pub_number"], "shortlisted")
    t1 = store.all_records(conn, 1)[0]
    t2 = store.all_records(conn, 2)[0]
    assert t1["status"] == "shortlisted"
    assert t2["status"] == "new"

def test_update_tagging_rewrites_an_existing_records_match_fields(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    rec["cpv_codes"] = []
    rec["match_source"] = None
    store.upsert(conn, 1, rec)

    store.update_tagging(conn, 1, rec["pub_number"],
                          cpv_codes=["45111100"], matched_terms=["tent"],
                          match_source="both", exclude_reason="construction_works")

    updated = store.all_records(conn, 1)[0]
    assert updated["cpv_codes"] == ["45111100"]
    assert updated["matched_terms"] == ["tent"]
    assert updated["match_source"] == "both"
    assert updated["exclude_reason"] == "construction_works"

def test_update_language_backfills_a_row_predating_language_tagging(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    rec["language"] = ""  # simulates a row inserted before CR-001 R3 existed
    store.upsert(conn, 1, rec)
    assert store.all_records(conn, 1)[0]["language"] == ""

    store.update_language(conn, 1, rec["pub_number"], "fra")
    assert store.all_records(conn, 1)[0]["language"] == "fra"

def test_update_language_only_affects_the_calling_tenants_row(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    rec["language"] = ""
    store.upsert(conn, 1, rec)
    store.upsert(conn, 2, rec)
    store.update_language(conn, 1, rec["pub_number"], "fra")
    assert store.all_records(conn, 2)[0]["language"] == ""

def test_update_tagging_only_affects_the_calling_tenants_row(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, 1, rec)
    store.upsert(conn, 2, rec)
    store.update_tagging(conn, 1, rec["pub_number"],
                          cpv_codes=["45111100"], matched_terms=[],
                          match_source="cpv", exclude_reason="construction_works")
    t2 = store.all_records(conn, 2)[0]
    assert t2["cpv_codes"] == rec["cpv_codes"]
    assert t2["exclude_reason"] == ""
