import pytest

import api

# Phase 2/3 multi-tenancy: any tenant_id works for tests since it's just a
# scoping key, but tests share this one constant for consistency/readability.
TEST_TENANT_ID = 1

# CR-006: api.patch_tender takes an Identity (tenant_id + account_name, both
# normally resolved from a verified Clerk token) rather than a bare
# tenant_id — tests construct one directly rather than going through
# get_current_identity's JWT verification, mirroring how TEST_TENANT_ID
# already bypasses get_current_tenant_id.
TEST_ACCOUNT_NAME = "tester@example.com"


def make_identity(tenant_id=TEST_TENANT_ID, account_name=TEST_ACCOUNT_NAME):
    return api.Identity(tenant_id=tenant_id, account_name=account_name)


@pytest.fixture(autouse=True)
def _no_database_url_env(monkeypatch):
    """Force every test onto its own isolated SQLite file regardless of what's
    in the developer's environment/.env — store.init_db() switches to
    DATABASE_URL when it's configured (step 4: Postgres cutover), and tests
    must never share a real database or lose per-test isolation because a
    dev happens to have Postgres configured locally.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

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
