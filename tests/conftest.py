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

# CR-007 Phase A: the Clerk user id behind TEST_ACCOUNT_NAME. A second
# constant (below) stands in for a colleague sharing the same TEST_TENANT_ID
# org — post-CR-007, every Review Queue action either of them takes is
# visible to the other (see schema.py's tender_reviews retirement comment).
TEST_CLERK_USER_ID = "user_test_1"
TEST_ACCOUNT_NAME_B = "colleague@example.com"
TEST_CLERK_USER_ID_B = "user_test_2"


def make_identity(tenant_id=TEST_TENANT_ID, account_name=TEST_ACCOUNT_NAME,
                   clerk_user_id=TEST_CLERK_USER_ID):
    return api.Identity(tenant_id=tenant_id, account_name=account_name, clerk_user_id=clerk_user_id)


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

@pytest.fixture
def raw_eprocurement_supply():
    # Real BE e-Procurement (BDA) shape, captured live 2026-10-14 against
    # POST /api/sea/search/publications — a genuine below-threshold, single-
    # language (NL) notice: a school board's roof repair, never published to
    # TED (tedPublished: false, publicationReferenceNumbersTED: []). See
    # connectors/eprocurement.py's docstring for the endpoint/auth discovery.
    return {
        "cpvAdditionalCodes": [
            {"code": "45261210-9", "descriptions": [
                {"language": "NL", "text": "Dakdekkerswerkzaamheden"},
                {"language": "FR", "text": "Travaux de couverture"},
                {"language": "DE", "text": "Dachdeckarbeiten"},
                {"language": "EN", "text": "Roof-covering work"},
            ]},
        ],
        "cpvMainCode": {"code": "45261910-6", "descriptions": [
            {"language": "NL", "text": "Reparatie van daken"},
            {"language": "FR", "text": "Réparation de toiture"},
            {"language": "DE", "text": "Dachreparatur"},
            {"language": "EN", "text": "Roof repair"},
        ]},
        "dispatchDate": "2026-10-13",
        "dossier": {
            "accreditations": {},
            "descriptions": [{"language": "NL", "text": "Bakgoot voorgevel Handelsschool"}],
            "enterpriseCategories": [],
            "legalBasis": "D24",
            "number": "PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot",
            "procurementProcedureType": "OPEN",
            "referenceNumber": "PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot",
            "titles": [{"language": "NL", "text": "Bakgoot voorgevel Handelsschool"}],
        },
        "insertionDate": "2026-10-13",
        "lots": [{
            "descriptions": [{"language": "NL", "text": "Bakgoot voorgevel Handelsschool"}],
            "reservedExecution": ["NO"], "reservedParticipation": ["NONE"],
            "titles": [{"language": "NL", "text": "Bakgoot voorgevel Handelsschool"}],
        }],
        "natures": ["WORKS"],
        "noticeIds": ["c70edaf6-b46f-4030-849b-4633330da46b"],
        "noticeSubType": "16",
        "nutsCodes": ["BE231"],
        "organisation": {
            "organisationId": 4885,
            "organisationNames": [
                {"language": "FR", "text": "Scholengroep 19 'Dender' (3P)"},
                {"language": "EN", "text": "Scholengroep 19 'Dender' (3P)"},
                {"language": "NL", "text": "Scholengroep 19 'Dender'"},
                {"language": "DE", "text": "Scholengroep 19 'Dender' (3P)"},
            ],
            "tree": "200004.200026.1133.4885",
        },
        "procedureId": "f2a385b4-8b34-49ab-a35e-31841450fc98",
        "publicationDate": "2026-10-13",
        "publicationLanguages": ["NL"],
        "publicationReferenceNumbersBDA": ["PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot_001"],
        "publicationReferenceNumbersTED": [],
        "publicationType": "ACTIVE",
        "publicationWorkspaceId": "1b5e777f-e009-401b-8a04-96737a274165",
        "publishedAt": ["2026-10-13T09:41:45.489776"],
        "referenceNumber": "PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot_001",
        "sentAt": ["2026-10-13T09:41:45.489776"],
        "tedPublished": False,
        "vaultSubmissionDeadline": "2026-11-13T11:00:00",
    }

@pytest.fixture
def raw_eprocurement_multilingual():
    # Real BE e-Procurement shape, captured live 2026-10-14 — a notice
    # published in two languages (FR + EN) rather than the single-language
    # norm, used to exercise _eprocurement_lang_dict/_record_language's
    # preference logic. (This particular live example happens to be an
    # EU-funded notice also carrying a TED publication reference — useful
    # here purely for its genuine multilingual shape, not as a typical
    # national BDA notice.)
    return {
        "cpvAdditionalCodes": [{"code": "45000000-7", "descriptions": [
            {"language": "NL", "text": "Bouwwerkzaamheden"},
            {"language": "FR", "text": "Travaux de construction"},
            {"language": "DE", "text": "Bauarbeiten"},
            {"language": "EN", "text": "Construction work"},
        ]}],
        "cpvMainCode": {"code": "45000000-7", "descriptions": [
            {"language": "NL", "text": "Bouwwerkzaamheden"},
            {"language": "FR", "text": "Travaux de construction"},
            {"language": "DE", "text": "Bauarbeiten"},
            {"language": "EN", "text": "Construction work"},
        ]},
        "dispatchDate": "2026-10-13",
        "dossier": {
            "accreditations": {},
            "descriptions": [
                {"language": "FR", "text": "Construction de l'école verte pour filles Al-Bakri à Hébron"},
                {"language": "EN", "text": "Construction of Al-Bakri Green Girls' School in Hebron"},
            ],
            "enterpriseCategories": [],
            "legalBasis": "D24",
            "number": "PSE22001-10075",
            "procurementProcedureType": "OPEN",
            "referenceNumber": "PSE22001-10075",
            "titles": [
                {"language": "FR", "text": "PSE22001-10075 - 1"},
                {"language": "EN", "text": "PSE22001-10075 - 1"},
            ],
        },
        "insertionDate": "2026-07-15",
        "lots": [{
            "descriptions": [
                {"language": "FR", "text": "Construction de l'école verte pour filles Al-Bakri à Hébron"},
                {"language": "EN", "text": "Construction of Al-Bakri Green Girls' School in Hebron"},
            ],
            "reservedExecution": ["NO"], "reservedParticipation": ["NONE"],
            "titles": [
                {"language": "FR", "text": "PSE22001-10075 - 1"},
                {"language": "EN", "text": "PSE22001-10075 - 1"},
            ],
        }],
        "natures": ["WORKS"],
        "noticeIds": [
            "517be104-c2d8-4a8b-86f5-683c0a8e74cd", "ecfe8501-8b78-4037-8953-b83ee7c95380",
            "235aa6ed-462b-40ba-9cdb-8c18680383f5", "f542b2c2-5519-4a27-aef8-04feb74fe438",
            "b9be25a6-2212-42e8-aebd-b04cc1553b31",
        ],
        "noticeSubType": "16",
        "nutsCodes": [],
        "organisation": {
            "organisationId": 200014,
            "organisationNames": [
                {"language": "FR", "text": "Enabel"}, {"language": "EN", "text": "Enabel"},
            ],
            "tree": "200002.200014.139",
        },
        "procedureId": "d12a63a4-d89c-4361-b53f-b99a5ad1a31e",
        "publicationDate": "2026-10-14",
        "publicationLanguages": ["FR", "EN"],
        "publicationReferenceNumbersBDA": [],
        "publicationReferenceNumbersTED": ["PSE22001-10075_pub01"],
        "publicationType": "ACTIVE",
        "publicationWorkspaceId": "f11c3188-a2c2-4dee-aa78-cd16bdc2bb15",
        "publishedAt": ["2026-07-15T04:23:07.185971"],
        "referenceNumber": "PSE22001-10075_pub01",
        "sentAt": ["2026-07-14T14:17:00.291345"],
        "tedPublished": True,
        "vaultSubmissionDeadline": "2026-11-02T15:00:00",
    }

@pytest.fixture
def raw_bekanntmachungsservice_supply():
    # Real notice, fields assembled the way connectors/bekanntmachungsservice.
    # py's own CSV join produces them (captured live 2026-10-13 export: a
    # multi-lot Fraunhofer IWES pipeline/control-system award — awarded on
    # one lot, unsuccessful on the other, both carried in the same notice).
    return {
        "noticeIdentifier": "6a06759e-fe07-4383-800c-b6236d96f2a0",
        "noticeVersion": "02",
        "formType": "result",
        "noticeType": "can-standard",
        "procedureLegalBasis": "de-vob",
        "publicationDate": "2026-10-13T02:22:05Z",
        "procedureType": "open",
        "title": "Sleepertrasse",
        "description": "Das Fraunhofer-Institut fur Windenergiesysteme IWES plant mit der Verlangerung der bestehenden Medien Pipeline den Standortausbau am Hydrogen Lab Leuna (HLL).",
        "internalIdentifier": "",
        "estimatedValue": "",
        "estimatedValueCurrency": "",
        "mainNature": "supplies",
        "cpvCodes": ["42961000", "44160000"],
        "nutsCodes": ["DEE0B"],
        "buyerName": "Fraunhofer-Institut fur Windenergiesysteme IWES",
        "winnerName": "SPIE IMO Service GmbH",
        "noticeValue": "23790",
        "noticeValueCurrency": "EUR",
    }

@pytest.fixture
def raw_bekanntmachungsservice_below_threshold():
    # Real notice, below-threshold national form (de-uvgo — the explicit
    # below-threshold ordinance), active/competition (not yet awarded) —
    # no winner/value yet, no internalIdentifier populated on this one
    # (confirmed live: only ~50% of notices carry it).
    return {
        "noticeIdentifier": "42d98adf-f195-4aef-a7c8-7063b771caaa",
        "noticeVersion": "1",  # confirmed live: unpadded "1" and padded "01"
        "formType": "competition",                    # both occur for the same semantic version — never compare as strings.
        "noticeType": "cn-standard",
        "procedureLegalBasis": "de-uvgo",
        "publicationDate": "2026-10-13T02:24:04Z",
        "procedureType": "open",
        "title": "MSR Technik",
        "description": "Mess-, Steuerungs-, und Regelungstechnikarbeiten fur die Sanierung der Patientenzimmer.",
        "internalIdentifier": "",
        "estimatedValue": "",
        "estimatedValueCurrency": "",
        "mainNature": "works",
        "cpvCodes": ["45315700", "45315000"],
        "nutsCodes": ["DE212"],
        "buyerName": "Artemed Klinikum Munchen Sud",
        "winnerName": None,
        "noticeValue": "",
        "noticeValueCurrency": "",
    }


# ── CR-011 §4: new source fixtures ──────────────────────────────────────────
# Each shaped the way its own connector's parse_response/_flatten produces it
# (NOT the raw source API payload 1:1) — captured against real, live-fetched
# responses on 2026-10-16 (see each connector's own module docstring for the
# confirmed nested paths these were flattened from).

@pytest.fixture
def raw_find_a_tender_supply():
    return {
        "id": "098127-2026",
        "date": "2026-10-16T15:52:30+01:00",
        "title": "CVG Close & Cleaning Contract 2027 - 2029",
        "description": "Close Cleaning along with an Office premises",
        "mainProcurementCategory": "services",
        "procurementMethodDetails": "Open procedure",
        "tenderPeriodEndDate": "2026-11-16T12:00:00Z",
        "cpvCodes": ["90910000"],
        "place": "North Lanarkshire",
        "buyerName": "Clyde Valley Housing Association Limited",
        "valueAmount": None,
        "valueCurrency": None,
    }


@pytest.fixture
def raw_contracts_finder_supply():
    return {
        "id": "7eeb3a8e-8f79-46c3-92bf-a032bedc89d7-918207",
        "date": "2026-10-16T15:24:18+01:00",
        "title": "CA18576 - Electrical Maintenance. 4 Year Opportunity",
        "description": "New College Swindon invites tenders for Electrical Maintenance Services.",
        "mainProcurementCategory": "services",
        "procurementMethodDetails": "Open procedure",
        "tenderPeriodEndDate": "2026-11-19T10:00:00Z",
        "cpvCodes": ["45310000", "50711000", "71314100"],
        "buyerName": "New College Swindon",
        "valueAmount": 400000,
        "valueCurrency": "GBP",
        "noticeUrl": "https://www.contractsfinder.service.gov.uk/Notice/7eeb3a8e-8f79-46c3-92bf-a032bedc89d7",
    }


@pytest.fixture
def raw_canadabuys_supply():
    return {
        "title": "Open Construction Source List for CFB Halifax",
        "description": "Construction source list for Canadian Forces Base Halifax.",
        "referenceNumber": "MX-443841357513",
        "publicationDate": "2026-01-23",
        "closingDate": "2029-03-31T13:00:00",
        "gsin": "",
        "unspsc": "",
        "procurementCategory": "*CNST",
        "procurementMethod": "Competitive - Open bidding",
        "regionsOfDelivery": "Nova Scotia",
        "contractingEntityName": "Defence Construction Canada - Atlantic Region",
        "noticeURL": "https://www.merx.com/public/solicitations/3776296409/abstract?language=EN",
    }


@pytest.fixture
def raw_tenderned_supply():
    return {
        "publicatieId": 445074,
        "publicatieDatum": "2026-10-16T16:18:00.558847",
        "aanbestedingNaam": "Representatie artikelen",
        "opdrachtBeschrijving": "De Opdracht betreft het leveren van representatieartikelen.",
        "opdrachtgeverNaam": "Belastingdienst, IUC Belastingdienst",
        "sluitingsDatum": "2026-11-20T12:00:00",
        "typeOpdracht": "L",
        "procedure": "Openbaar",
        "cpvCodes": ["18530000"],
        "nuts": "NL",
        "referentieNummer": "IUC25-711",
        "url": "https://www.tenderned.nl/aankondigingen/overzicht/445074",
    }
