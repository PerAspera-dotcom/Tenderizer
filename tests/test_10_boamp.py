"""Step 10 — BOAMP connector + cross-source schema consistency.
Interface:
  connectors.boamp.build_params(cpv_codes, keywords, since) -> dict
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
    p = boamp.build_params(["35521000"], ["tente"], date(2026,6,1))
    blob = str(p)
    assert "2026-06-01" in blob and "tente" in blob


def test_build_params_and_fetch_share_ted_connectors_argument_order():
    """Found during a repo audit: ted.fetch/build_query take
    (cpv_codes, keywords, since, ...) but boamp.fetch/build_params used to take
    (keywords, cpv_codes, since, ...) — same shape, swapped order. Nothing
    caught it because run.py's lambda happened to match each connector's own
    order. Locks in (cpv_codes, keywords, since, ...) for both connectors via
    inspect.signature, so a future connector can't silently reintroduce the
    swap.
    """
    import inspect
    from connectors import ted
    ted_params = list(inspect.signature(ted.fetch).parameters)[:3]
    boamp_params = list(inspect.signature(boamp.fetch).parameters)[:3]
    assert ted_params == boamp_params == ["cpv_codes", "keywords", "since"]


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


# ── CR-003 G4: structured award fields (verified live 2026-07 against a real
# ATTRIBUTION/"Résultat de marché" BOAMP notice) ─────────────────────────────

def test_no_titulaire_or_donnees_yields_none_award_info(raw_boamp_supply):
    r = normalize.normalize_boamp(raw_boamp_supply)
    assert r["raw_award_winner"] is None
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None

def test_titulaire_is_the_winner_name(raw_boamp_supply):
    raw = dict(raw_boamp_supply, titulaire=["EXA'RENT"])
    assert normalize.normalize_boamp(raw)["raw_award_winner"] == "EXA'RENT"

def test_extracts_notice_result_total_amount_from_donnees(raw_boamp_supply):
    donnees = {"EFORMS": {"ContractAwardNotice": {"ext:UBLExtensions": {"ext:UBLExtension": {
        "ext:ExtensionContent": {"efext:EformsExtension": {"efac:NoticeResult": {
            "cbc:TotalAmount": {"@currencyID": "EUR", "#text": "2557672"},
            "efac:LotResult": [{"cbc:ID": {"@schemeName": "result", "#text": "RES-0001"}}],
        }}}}}}}}
    raw = dict(raw_boamp_supply, titulaire=["EXA'RENT"], donnees=json.dumps(donnees))
    r = normalize.normalize_boamp(raw)
    assert r["raw_award_winner"] == "EXA'RENT"
    assert r["raw_award_value"] == "2557672"
    assert r["raw_award_currency"] == "EUR"

def test_ignores_per_lot_payable_amount_not_notice_result_total(raw_boamp_supply):
    # A per-lot cac:LegalMonetaryTotal.cbc:PayableAmount sitting outside
    # efac:NoticeResult must not be mistaken for the notice-level total.
    donnees = {"efac:LotTender": [{"cac:LegalMonetaryTotal": {
        "cbc:PayableAmount": {"@currencyID": "EUR", "#text": "1817043"}}}]}
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    r = normalize.normalize_boamp(raw)
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None

def test_malformed_donnees_json_yields_no_award_info_not_a_crash(raw_boamp_supply):
    raw = dict(raw_boamp_supply, donnees="{not valid json")
    r = normalize.normalize_boamp(raw)
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None


# ── Past-tenders data-coverage follow-up: winner/lot/contract detail (BOAMP)
# — the structure below is the REAL live `donnees` shape for a genuine BOAMP
# award notice (SAS EXHIBIT / ORG-0003, three organizations in the same
# notice: buyer ORG-0001, an unrelated ORG-0002, and the actual winner
# ORG-0003), fetched by searching BOAMP's live API for the winner name. ─────

def _real_exhibit_donnees():
    return {"EFORMS": {"ContractAwardNotice": {"ext:UBLExtensions": {"ext:UBLExtension": {
        "ext:ExtensionContent": {"efext:EformsExtension": {
            "efac:Organizations": {"efac:Organization": [
                {"efac:Company": {
                    "cac:PartyIdentification": {"cbc:ID": {"@schemeName": "organization", "#text": "ORG-0001"}},
                    "cac:PartyName": {"cbc:Name": {"@languageID": "FRA", "#text": "SPL Théâtre Communautaire"}},
                    "cac:PostalAddress": {"cbc:CityName": "Antibes", "cbc:PostalZone": "06600",
                                           "cac:Country": {"cbc:IdentificationCode": {"@listName": "country", "#text": "FRA"}}},
                    "cac:PartyLegalEntity": {"cbc:CompanyID": "75177766500025"},
                }},
                {"efac:Company": {
                    "cac:PartyIdentification": {"cbc:ID": {"@schemeName": "organization", "#text": "ORG-0002"}},
                    "cac:PartyName": {"cbc:Name": {"@languageID": "FRA", "#text": "Tribunal de Grande Instance"}},
                    "cac:PartyLegalEntity": {"cbc:CompanyID": "17130111200289"},
                }},
                {
                    "efbc:ListedOnRegulatedMarketIndicator": "true",
                    "efac:Company": {
                        "efbc:CompanySizeCode": {"@listName": "economic-operator-size", "#text": "medium"},
                        "cac:PartyIdentification": {"cbc:ID": {"@schemeName": "organization", "#text": "ORG-0003"}},
                        "cac:PartyName": {"cbc:Name": {"@languageID": "FRA", "#text": "SAS EXHIBIT"}},
                        "cac:PostalAddress": {
                            "cbc:CityName": "CARROS", "cbc:PostalZone": "06510",
                            "cbc:CountrySubentityCode": {"@listName": "nuts", "#text": "FRL03"},
                            "cac:Country": {"cbc:IdentificationCode": {"@listName": "country", "#text": "FRA"}},
                        },
                        "cac:PartyLegalEntity": {"cbc:CompanyID": "50233392500084"},
                    },
                },
            ]},
            "efac:NoticeResult": {
                "efbc:OverallMaximumFrameworkContractsAmount": {"@currencyID": "EUR", "#text": "200000"},
                "efac:LotResult": {
                    "cbc:ID": {"@schemeName": "result", "#text": "RES-0001"},
                    "efac:LotTender": {"cbc:ID": {"@schemeName": "tender", "#text": "TEN-0001"}},
                    "efac:TenderLot": {"cbc:ID": {"@schemeName": "Lot", "#text": "LOT-0001"}},
                },
                "efac:SettledContract": {
                    "cbc:ID": {"@schemeName": "contract", "#text": "CON-0001"},
                    "cbc:IssueDate": "2026-07-13+01:00",
                    "efac:ContractReference": {"cbc:ID": "Impression de documents divers Lot 4"},
                },
                "efac:TenderingParty": {
                    "cbc:ID": {"@schemeName": "tendering-party", "#text": "TPA-0001"},
                    "cbc:Name": "EXHIBIT",
                    "efac:Tenderer": {"cbc:ID": {"@schemeName": "organization", "#text": "ORG-0003"}},
                },
            },
        }}}}}}}


def test_boamp_award_detail_resolves_correct_org_among_several(raw_boamp_supply):
    # Three organizations in the same notice (buyer, unrelated, winner) —
    # must resolve ORG-0003 specifically via the TenderingParty->Tenderer
    # ID reference, not just grab the first/last org in the list.
    raw = dict(raw_boamp_supply, titulaire=["SAS EXHIBIT"], donnees=json.dumps(_real_exhibit_donnees()))
    detail = normalize.normalize_boamp(raw)["raw_award_detail"]
    assert detail == {
        "winner": {
            "registration_number": "50233392500084", "city": "CARROS", "postal_code": "06510",
            "nuts": "FRL03", "country": "FRA", "size": "medium", "regulated_market": True,
        },
        "lot": {"identifier": "LOT-0001"},
        "contract": {"identifier": "Impression de documents divers Lot 4",
                     "conclusion_date": "2026-07-13", "tender_identifier": "TEN-0001"},
        "framework_max_value": "200000", "framework_max_currency": "EUR",
    }


def test_boamp_award_detail_none_when_no_donnees(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["raw_award_detail"] is None


def test_boamp_award_detail_none_when_no_notice_result(raw_boamp_supply):
    raw = dict(raw_boamp_supply, donnees=json.dumps({"unrelated": "content"}))
    assert normalize.normalize_boamp(raw)["raw_award_detail"] is None


def test_boamp_award_detail_none_for_multi_lot_result(raw_boamp_supply):
    donnees = _real_exhibit_donnees()
    ext = donnees["EFORMS"]["ContractAwardNotice"]["ext:UBLExtensions"]["ext:UBLExtension"]["ext:ExtensionContent"]["efext:EformsExtension"]
    ext["efac:NoticeResult"]["efac:LotResult"] = [
        ext["efac:NoticeResult"]["efac:LotResult"], {"cbc:ID": {"#text": "RES-0002"}},
    ]
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["raw_award_detail"] is None


def test_boamp_award_detail_none_when_winner_org_not_found(raw_boamp_supply):
    donnees = _real_exhibit_donnees()
    ext = donnees["EFORMS"]["ContractAwardNotice"]["ext:UBLExtensions"]["ext:UBLExtension"]["ext:ExtensionContent"]["efext:EformsExtension"]
    ext["efac:NoticeResult"]["efac:TenderingParty"]["efac:Tenderer"]["cbc:ID"]["#text"] = "ORG-9999"
    raw = dict(raw_boamp_supply, donnees=json.dumps(donnees))
    assert normalize.normalize_boamp(raw)["raw_award_detail"] is None


def test_boamp_award_detail_malformed_json_not_a_crash(raw_boamp_supply):
    raw = dict(raw_boamp_supply, donnees="{not valid json")
    assert normalize.normalize_boamp(raw)["raw_award_detail"] is None
