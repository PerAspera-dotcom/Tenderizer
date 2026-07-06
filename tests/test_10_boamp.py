"""Step 10 — BOAMP connector + cross-source schema consistency.
Interface:
  connectors.boamp.build_params(keywords, cpv_codes, since) -> dict
  connectors.boamp.parse_response(json_data) -> list[dict]
  normalize.normalize_boamp(raw) -> normalised record (SAME schema as TED)
"""
import json
from datetime import date
import normalize
from connectors import boamp

def test_normalize_boamp_country_is_fr(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["country"] == "FR"

def test_normalize_boamp_fournitures_is_supply(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["category"] == "Supply"

def test_normalize_boamp_maps_objet_to_tagline(raw_boamp_supply):
    r = normalize.normalize_boamp(raw_boamp_supply)
    assert r["tag_line"] == "Fourniture de tentes pour la protection civile"

def test_boamp_and_ted_share_identical_schema(raw_boamp_supply, raw_ted_supply):
    # the whole pipeline depends on every source producing the same keys
    assert set(normalize.normalize_boamp(raw_boamp_supply)) == \
           set(normalize.normalize_ted(raw_ted_supply))

def test_build_params_includes_date_and_keyword():
    p = boamp.build_params(["tente"], ["35521000"], date(2026,6,1))
    blob = str(p)
    assert "2026-06-01" in blob and "tente" in blob


# ── CPV extraction from `donnees` (verified live 2026-07 — see
# normalize._boamp_cpv_codes's docstring). Contrary to this module's earlier
# assumption, BOAMP does carry CPV; it's just nested in a JSON-string field
# whose shape varies by notice schema/vintage, not at the flat top level.

def test_no_donnees_field_yields_no_cpv_codes(raw_boamp_supply):
    # the shared fixture has no 'donnees' key at all — legacy/absent case.
    assert normalize.normalize_boamp(raw_boamp_supply)["cpv_codes"] == []

def test_malformed_donnees_json_yields_no_cpv_codes_not_a_crash(raw_boamp_supply):
    raw = dict(raw_boamp_supply, donnees="{not valid json")
    assert normalize.normalize_boamp(raw)["cpv_codes"] == []

def test_eforms_shape_extracts_main_and_additional_cpv(raw_boamp_supply):
    donnees = {
        "EFORMS": {"ContractNotice": {"cac:ProcurementProject": {
            "cac:MainCommodityClassification": {
                "cbc:ItemClassificationCode": {"@listName": "cpv", "#text": "45111100"}},
            "cac:AdditionalCommodityClassification": [
                {"cbc:ItemClassificationCode": {"@listName": "cpv", "#text": "45262660"}},
            ],
        }}}
    }
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == ["45111100", "45262660"]

def test_eforms_shape_extracts_per_lot_cpv(raw_boamp_supply):
    donnees = {
        "EFORMS": {"ContractNotice": {"cac:ProcurementProjectLot": [
            {"cac:ProcurementProject": {"cac:MainCommodityClassification": {
                "cbc:ItemClassificationCode": {"@listName": "cpv", "#text": "45262660"}}}},
        ]}}
    }
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == ["45262660"]

def test_fnsimple_shape_extracts_cpv(raw_boamp_supply):
    donnees = {"FNSimple": {"initial": {"natureMarche": {
        "codeCPV": {"objetPrincipal": {"classPrincipale": "45421000"}}}}}}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == ["45421000"]

def test_fnsimple_shape_extracts_per_lot_cpv_too(raw_boamp_supply):
    donnees = {"FNSimple": {"initial": {
        "natureMarche": {"codeCPV": {"objetPrincipal": {"classPrincipale": "45210000"}}},
        "lots": {"lot": [
            {"codeCPV": {"objetPrincipal": {"classPrincipale": "45262522"}}},
            {"codeCPV": {"objetPrincipal": {"classPrincipale": "45410000"}}},
        ]},
    }}}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == \
        ["45210000", "45262522", "45410000"]

def test_mapa_shape_extracts_cpv(raw_boamp_supply):
    donnees = {"MAPA": {"rectificatif": {"description": {
        "CPV": {"objetPrincipal": {"classPrincipale": "45310000"}}}}}}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == ["45310000"]

def test_legacy_v230_shape_has_no_cpv_field_and_yields_none(raw_boamp_supply):
    # pre-2024 archived notices genuinely carry no CPV anywhere — permanent
    # gap for old data, not a bug; keyword/category matching is the fallback.
    donnees = {"IDENTITE": {"DENOMINATION": "Commune de Test"},
               "OBJET": {"TYPE_MARCHE": {"TRAVAUX": ""}, "OBJET_COMPLET": "Travaux de voirie"}}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == []

def test_empty_objet_principal_is_ignored_not_a_crash(raw_boamp_supply):
    # seen live: {"codeCPV": {"objetPrincipal": ""}} when the field is blank.
    donnees = {"FNSimple": {"initial": {"natureMarche": {
        "codeCPV": {"objetPrincipal": ""}}}}}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["cpv_codes"] == []
