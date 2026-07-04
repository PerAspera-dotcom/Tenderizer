import pytest

# Phase 2/3 multi-tenancy: any tenant_id works for tests since it's just a
# scoping key, but tests share this one constant for consistency/readability.
TEST_TENANT_ID = 1

@pytest.fixture
def raw_ted_supply():
    # real TED shape: multilingual dicts, list-valued fields
    return {
        "publication-number": "381972-2026",
        "notice-title": {
            "eng": "Sweden – Supply of military tents",
            "nld": "Zweden – Levering van militaire tenten",
            "fra": "Suède – Fourniture de tentes militaires",
        },
        "description-proc": {"eng": "Supply of all-season military tents for field camps."},
        "buyer-name": {"eng": ["FMV"]},
        "buyer-country": ["SWE"],
        "contract-nature": "supplies",
        "procedure-type": "open",
        "notice-type": "cn-standard",
        "publication-date": "20260603",
        "deadline-receipt-request": ["2026-08-31T23:59:00+02:00"],
        "place-of-performance": ["SE110", "SWE"],
        "classification-cpv": ["39522530", "39522500"],
        "links": {"html": {"ENG": "https://ted.europa.eu/en/notice/381972-2026/html"}},
    }

@pytest.fixture
def raw_ted_services():
    return {
        "publication-number": "381999-2026",
        "notice-title": {"eng": "France – Field camp management services"},
        "description-proc": {"eng": "Management of a temporary field camp."},
        "buyer-name": {"fra": ["Ministère des Armées"]},
        "buyer-country": ["FRA"],
        "contract-nature": "services",
        "procedure-type": "neg-w-call",
        "notice-type": "cn-standard",
        "publication-date": "20260603",
        "deadline-receipt-request": ["2026-09-30T23:59:00+02:00"],
        "place-of-performance": ["FR101", "FRA"],
        "classification-cpv": ["79993000"],
        "links": {"html": {"ENG": "https://ted.europa.eu/en/notice/381999-2026/html"}},
    }

@pytest.fixture
def sample_ted_api_json():
    return {"totalNoticeCount": 2, "notices": [
        {"publication-number": "1-2026", "notice-title": {"eng": "Tent supply"}},
        {"publication-number": "2-2026", "notice-title": {"eng": "Catering services"}}]}

@pytest.fixture
def raw_boamp_supply():
    # real BOAMP (OpenDataSoft) shape: flat, French, single-language
    return {
        "idweb": "26-12345",
        "objet": "Fourniture de tentes pour la protection civile",
        "nomacheteur": "Ministère de l'Intérieur",
        "dateparution": "2026-06-03",
        "datelimitereponse": "2026-08-15T12:00:00+02:00",
        "code_departement": ["75"],
        "procedure_libelle": "Procédure ouverte",
        "type_marche": "Fournitures",
    }
