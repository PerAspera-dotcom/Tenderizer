"""Step 7 — normalisation + storage (against the REAL multilingual TED structure)."""
import normalize, store

REQUIRED_KEYS = {"source","pub_number","tag_line","buyer","country","place","category",
                 "procedure","pub_date","deadline","cpv_codes","matched_terms",
                 "match_source","url","first_seen"}

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

def test_upsert_new_returns_true(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    assert store.upsert(conn, normalize.normalize_ted(raw_ted_supply)) is True

def test_upsert_duplicate_returns_false_and_adds_no_row(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, rec)
    assert store.upsert(conn, rec) is False
    assert len(store.all_records(conn)) == 1

def test_first_seen_preserved_on_reinsert(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    rec = normalize.normalize_ted(raw_ted_supply); rec["first_seen"] = "2026-01-15"
    store.upsert(conn, rec); store.upsert(conn, rec)
    assert store.all_records(conn)[0]["first_seen"] == "2026-01-15"

def test_stored_record_roundtrips_cpv_list(tmp_path, raw_ted_supply):
    conn = store.init_db(str(tmp_path/"t.db"))
    store.upsert(conn, normalize.normalize_ted(raw_ted_supply))
    assert store.all_records(conn)[0]["cpv_codes"] == ["39522530", "39522500"]
