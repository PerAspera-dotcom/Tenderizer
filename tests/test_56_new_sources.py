"""Step 56 — CR-011 §4: UK Find a Tender Service, UK Contracts Finder,
CanadaBuys, TenderNed connectors + normalize.

Interface (all four match the shared connector signature):
  connectors.<name>.fetch(cpv_codes, keywords, since, limit=100, max_records=2000) -> list[dict]
  normalize.normalize_<name>(raw) -> normalised record (SAME schema as every other source)

fetch() HTTP calls are mocked (no @pytest.mark.network test) — same reasoning
test_51_eprocurement.py/test_52_bekanntmachungsservice.py give: these are
live government services, not something to hit on every CI run.
"""
import inspect
from datetime import date

import normalize
from connectors import find_a_tender, contracts_finder, canadabuys, tenderned
from connectors import ted, boamp, eprocurement, bekanntmachungsservice

CPV = ["90910000"]
KW = ["cleaning"]
SINCE = date(2026, 10, 1)


class _FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ── interface parity ─────────────────────────────────────────────────────────

def test_all_eight_connectors_share_argument_order():
    names = [ted, boamp, eprocurement, bekanntmachungsservice,
             find_a_tender, contracts_finder, canadabuys, tenderned]
    signatures = [list(inspect.signature(m.fetch).parameters)[:3] for m in names]
    assert all(sig == ["cpv_codes", "keywords", "since"] for sig in signatures)


# ── find_a_tender ────────────────────────────────────────────────────────────

def _fts_release(cpv="90910000", title="Test cleaning contract", value=None):
    tender = {
        "title": title,
        "description": "A test description",
        "classification": {"scheme": "CPV", "id": cpv, "description": "x"},
        "tenderPeriod": {"endDate": "2026-11-16T12:00:00Z"},
        "mainProcurementCategory": "services",
        "procurementMethodDetails": "Open procedure",
        "items": [{"deliveryLocation": {"description": "North Lanarkshire"}}],
    }
    if value is not None:
        tender["value"] = value
    return {
        "id": "098127-2026", "date": "2026-10-16T15:52:30+01:00",
        "tender": tender,
        "parties": [{"name": "Clyde Valley Housing Association Limited", "roles": ["buyer"]}],
    }


def test_find_a_tender_build_params_uses_tender_stage_and_updated_from():
    p = find_a_tender.build_params(date(2026, 10, 5))
    assert p["stages"] == "tender"
    assert p["updatedFrom"] == "2026-10-05T00:00:00"


def test_find_a_tender_fetch_follows_cursor_pagination(monkeypatch):
    page1 = {"releases": [_fts_release()], "links": {"next": "https://example/page2"}}
    page2 = {"releases": [_fts_release(title="Second")], "links": {}}
    responses = iter([page1, page2])
    urls = []

    def fake_get(url, params=None, **kw):
        urls.append(url)
        return _FakeResponse(json_data=next(responses))

    monkeypatch.setattr(find_a_tender.requests, "get", fake_get)
    recs = find_a_tender.fetch(CPV, KW, SINCE)
    assert len(recs) == 2
    assert urls[1] == "https://example/page2"


def test_find_a_tender_fetch_filters_by_cpv(monkeypatch):
    data = {"releases": [_fts_release(cpv="99999999")], "links": {}}
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    assert find_a_tender.fetch(CPV, KW, SINCE) == []
    assert len(find_a_tender.fetch([], KW, SINCE)) == 1


def test_find_a_tender_fetch_falls_back_to_lot_value(monkeypatch):
    release = _fts_release()
    release["tender"]["lots"] = [{"value": {"amount": 1000, "currency": "GBP"}}]
    data = {"releases": [release], "links": {}}
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = find_a_tender.fetch([], KW, SINCE)
    assert recs[0]["valueAmount"] == 1000
    assert recs[0]["valueCurrency"] == "GBP"


def test_normalize_find_a_tender_fields(raw_find_a_tender_supply):
    r = normalize.normalize_find_a_tender(raw_find_a_tender_supply)
    assert r["source"] == "Find a Tender"
    assert r["country"] == "GBR"
    assert r["language"] == "eng"
    assert r["cpv_codes"] == ["90910000"]
    assert r["category"] == "Services"
    assert r["url"] == "https://www.find-tender.service.gov.uk/Notice/098127-2026"
    assert r["deadline"] == "2026-11-16"
    assert r["pub_date"] == "2026-10-16"
    assert r["value"] == ""  # no amount in the fixture


def test_normalize_find_a_tender_value_populated_when_present():
    raw = {"id": "1", "date": "2026-10-01", "title": "t", "cpvCodes": [],
           "valueAmount": 500000, "valueCurrency": "GBP"}
    r = normalize.normalize_find_a_tender(raw)
    assert r["value"] == "500000"
    assert r["value_currency"] == "GBP"


# ── contracts_finder ─────────────────────────────────────────────────────────

def _cf_release(cpv="45310000"):
    return {
        "id": "7eeb3a8e-8f79-46c3-92bf-a032bedc89d7-918207",
        "date": "2026-10-16T15:24:18+01:00",
        "tender": {
            "title": "CA18576 - Electrical Maintenance", "description": "desc",
            "classification": {"scheme": "CPV", "id": cpv},
            "value": {"amount": 400000, "currency": "GBP"},
            "tenderPeriod": {"endDate": "2026-11-19T10:00:00Z"},
            "procurementMethodDetails": "Open procedure",
            "mainProcurementCategory": "services",
            "documents": [{"documentType": "tenderNotice",
                            "url": "https://www.contractsfinder.service.gov.uk/Notice/7eeb3a8e-8f79-46c3-92bf-a032bedc89d7"}],
        },
        "buyer": {"name": "New College Swindon"},
    }


def test_contracts_finder_fetch_uses_buyer_name_top_level(monkeypatch):
    data = {"releases": [_cf_release()], "links": {}}
    monkeypatch.setattr(contracts_finder.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = contracts_finder.fetch([], KW, SINCE)
    assert recs[0]["buyerName"] == "New College Swindon"


def test_contracts_finder_fetch_uses_document_url_not_constructed(monkeypatch):
    data = {"releases": [_cf_release()], "links": {}}
    monkeypatch.setattr(contracts_finder.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = contracts_finder.fetch([], KW, SINCE)
    # GUID in the real notice URL has no "-918207" suffix, unlike release id
    assert recs[0]["noticeUrl"] == "https://www.contractsfinder.service.gov.uk/Notice/7eeb3a8e-8f79-46c3-92bf-a032bedc89d7"


def test_contracts_finder_fetch_filters_by_cpv(monkeypatch):
    data = {"releases": [_cf_release(cpv="11111111")], "links": {}}
    monkeypatch.setattr(contracts_finder.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    assert contracts_finder.fetch(CPV, KW, SINCE) == []


def test_normalize_contracts_finder_fields(raw_contracts_finder_supply):
    r = normalize.normalize_contracts_finder(raw_contracts_finder_supply)
    assert r["source"] == "Contracts Finder"
    assert r["country"] == "GBR"
    assert r["place"] == ""  # see normalize's docstring — not guessed
    assert r["value"] == "400000"
    assert r["value_currency"] == "GBP"
    assert r["url"] == "https://www.contractsfinder.service.gov.uk/Notice/7eeb3a8e-8f79-46c3-92bf-a032bedc89d7"
    assert r["cpv_codes"] == ["45310000", "50711000", "71314100"]


# ── canadabuys ───────────────────────────────────────────────────────────────

_CSV_HEADER = [
    "title-titre-eng", "tenderDescription-descriptionAppelOffres-eng",
    "referenceNumber-numeroReference", "publicationDate-datePublication",
    "tenderClosingDate-appelOffresDateCloture", "gsin-nibs", "unspsc",
    "procurementCategory-categorieApprovisionnement",
    "procurementMethod-methodeApprovisionnement-eng",
    "regionsOfDelivery-regionsLivraison-eng",
    "contractingEntityName-nomEntitContractante-eng", "noticeURL-URLavis-eng",
]


def _csv_bytes(rows):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADER)
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in _CSV_HEADER})
    return buf.getvalue().encode("utf-8-sig")


def _ca_row(pub_date="2026-10-05", title="Test tender"):
    return {
        "title-titre-eng": title,
        "tenderDescription-descriptionAppelOffres-eng": "A test description",
        "referenceNumber-numeroReference": "MX-123",
        "publicationDate-datePublication": pub_date,
        "tenderClosingDate-appelOffresDateCloture": "2026-12-01T13:00:00",
        "procurementCategory-categorieApprovisionnement": "*CNST",
        "procurementMethod-methodeApprovisionnement-eng": "Competitive",
        "regionsOfDelivery-regionsLivraison-eng": "Ontario",
        "contractingEntityName-nomEntitContractante-eng": "Test Entity",
        "noticeURL-URLavis-eng": "https://www.merx.com/x",
    }


def test_canadabuys_parse_response_reads_english_columns():
    recs = canadabuys.parse_response(_csv_bytes([_ca_row()]))
    assert len(recs) == 1
    assert recs[0]["title"] == "Test tender"
    assert recs[0]["referenceNumber"] == "MX-123"


def test_canadabuys_fetch_filters_locally_by_publication_date(monkeypatch):
    rows = [_ca_row(pub_date="2026-09-01", title="Too old"),
            _ca_row(pub_date="2026-10-10", title="In range")]
    monkeypatch.setattr(canadabuys.requests, "get",
                         lambda *a, **kw: _FakeResponse(content=_csv_bytes(rows)))
    recs = canadabuys.fetch([], KW, date(2026, 10, 1))
    assert [r["title"] for r in recs] == ["In range"]


def test_normalize_canadabuys_no_cpv_codes(raw_canadabuys_supply):
    # see connectors/canadabuys.py's module docstring — no verified GSIN/
    # UNSPSC -> CPV mapping, so this source is keyword-matched only.
    r = normalize.normalize_canadabuys(raw_canadabuys_supply)
    assert r["cpv_codes"] == []
    assert r["value"] == ""  # confirmed absent from the source entirely


def test_normalize_canadabuys_category_from_construction_code(raw_canadabuys_supply):
    assert normalize.normalize_canadabuys(raw_canadabuys_supply)["category"] == "Works"


def test_normalize_canadabuys_fields(raw_canadabuys_supply):
    r = normalize.normalize_canadabuys(raw_canadabuys_supply)
    assert r["source"] == "CanadaBuys"
    assert r["country"] == "CAN"
    assert r["language"] == "eng"
    assert r["pub_number"] == "MX-443841357513"
    assert r["place"] == "Nova Scotia"


# ── tenderned ────────────────────────────────────────────────────────────────

def test_tenderned_strip_checksum():
    assert tenderned._strip_checksum("18530000-3") == "18530000"
    assert tenderned._strip_checksum("") == ""


def test_tenderned_fetch_pages_until_older_than_since(monkeypatch):
    page0 = [{"publicatieId": 2, "publicatieDatum": "2026-10-16", "link": {"href": "u2"}}]
    page1 = [{"publicatieId": 1, "publicatieDatum": "2026-09-01", "link": {"href": "u1"}}]  # older than since
    pages = iter([page0, page1])

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url == tenderned.LIST_ENDPOINT:
                return _FakeResponse(json_data=next(pages))
            return _FakeResponse(json_data={"cpvCodes": [], "nutsCodes": []})

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    recs = tenderned.fetch([], KW, date(2026, 10, 1))
    assert len(recs) == 1
    assert recs[0]["publicatieId"] == 2


def test_tenderned_fetch_merges_detail_cpv_and_filters(monkeypatch):
    list_page = [{"publicatieId": 445074, "publicatieDatum": "2026-10-16",
                  "aanbestedingNaam": "Representatie artikelen",
                  "link": {"href": "https://www.tenderned.nl/aankondigingen/overzicht/445074"}}]
    detail = {"cpvCodes": [{"code": "18530000-3"}], "nutsCodes": [{"code": "NL"}],
              "referentieNummer": "IUC25-711"}
    calls = {"n": 0}

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url == tenderned.LIST_ENDPOINT:
                calls["n"] += 1
                return _FakeResponse(json_data=list_page if calls["n"] == 1 else [])
            return _FakeResponse(json_data=detail)

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    recs = tenderned.fetch(["18530000"], KW, date(2026, 10, 1))
    assert len(recs) == 1
    assert recs[0]["cpvCodes"] == ["18530000"]
    assert recs[0]["referentieNummer"] == "IUC25-711"
    # a non-matching CPV filter excludes it
    recs_none = tenderned.fetch(["99999999"], KW, date(2026, 10, 1))
    assert recs_none == []


def test_normalize_tenderned_fields(raw_tenderned_supply):
    r = normalize.normalize_tenderned(raw_tenderned_supply)
    assert r["source"] == "TenderNed"
    assert r["country"] == "NLD"
    assert r["language"] == "nld"
    assert r["cpv_codes"] == ["18530000"]
    assert r["category"] == "Supply"  # typeOpdracht "L" = Leveringen
    assert r["internal_identifier"] == "IUC25-711"
    assert r["value"] == ""
    assert r["pub_number"] == "445074"


# ── schema parity — now across all eight sources ────────────────────────────

def test_all_eight_sources_share_identical_schema(
    raw_ted_supply, raw_boamp_supply, raw_eprocurement_supply, raw_bekanntmachungsservice_supply,
    raw_find_a_tender_supply, raw_contracts_finder_supply, raw_canadabuys_supply, raw_tenderned_supply,
):
    keysets = {
        "TED": set(normalize.normalize_ted(raw_ted_supply)),
        "BOAMP": set(normalize.normalize_boamp(raw_boamp_supply)),
        "e-Procurement": set(normalize.normalize_eprocurement(raw_eprocurement_supply)),
        "Bekanntmachungsservice": set(normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)),
        "Find a Tender": set(normalize.normalize_find_a_tender(raw_find_a_tender_supply)),
        "Contracts Finder": set(normalize.normalize_contracts_finder(raw_contracts_finder_supply)),
        "CanadaBuys": set(normalize.normalize_canadabuys(raw_canadabuys_supply)),
        "TenderNed": set(normalize.normalize_tenderned(raw_tenderned_supply)),
    }
    canonical = keysets["TED"]
    for name, keys in keysets.items():
        assert keys == canonical, f"{name} schema diverges: {keys ^ canonical}"
