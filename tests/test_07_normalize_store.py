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
