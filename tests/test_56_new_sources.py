"""Step 56 — CR-011 §4: UK Find a Tender Service, UK Contracts Finder,
CanadaBuys, TenderNed, PhilGEPS connectors + normalize.

Interface (all five match the shared connector signature):
  connectors.<name>.fetch(cpv_codes, keywords, since, limit=100, max_records=2000) -> list[dict]
  normalize.normalize_<name>(raw) -> normalised record (SAME schema as every other source)

fetch() HTTP calls are mocked (no @pytest.mark.network test) — same reasoning
test_51_eprocurement.py/test_52_bekanntmachungsservice.py give: these are
live government services, not something to hit on every CI run. (PhilGEPS'
own connector/normalize code WAS additionally verified against the real live
site during development — see connectors/philgeps.py's module docstring —
but that one-off verification isn't re-run here.)
"""
import inspect
from datetime import date
import pytest

import normalize
import store
from connectors import find_a_tender, contracts_finder, canadabuys, tenderned, philgeps
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

def test_all_nine_connectors_share_argument_order():
    names = [ted, boamp, eprocurement, bekanntmachungsservice,
             find_a_tender, contracts_finder, canadabuys, tenderned, philgeps]
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


def test_find_a_tender_build_params_sends_no_stage_filter():
    # stages=tender returned ~1/6 of new tender notices (live, 2026-10-18)
    p = find_a_tender.build_params(date(2026, 10, 5))
    assert "stages" not in p
    assert p["updatedFrom"] == "2026-10-05T00:00:00"


def test_find_a_tender_fetch_keeps_tender_and_planning_releases_only(monkeypatch):
    def rel(tag, title):
        return dict(_fts_release(title=title), tag=[tag], id=title)
    data = {"releases": [rel("tender", "t"), rel("planning", "p"), rel("award", "a"),
                         rel("contract", "c"), rel("tenderUpdate", "u")], "links": {}}
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = find_a_tender.fetch([], KW, SINCE)
    assert [(r["title"], r["formType"]) for r in recs] == [("t", "competition"), ("p", "planning")]


def test_find_a_tender_planning_notice_classifies_as_prior_information(monkeypatch):
    import classification
    data = {"releases": [dict(_fts_release(), tag=["planning"])], "links": {}}
    data["releases"][0]["tender"].pop("tenderPeriod")  # planning notices carry no deadline
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    rec = normalize.normalize_find_a_tender(find_a_tender.fetch([], KW, SINCE)[0])
    assert rec["form_type"] == "planning"
    assert classification.check_past_tender(rec) == "fbo"


def test_find_a_tender_fetch_waits_out_rate_limit(monkeypatch):
    class _Resp429(_FakeResponse):
        headers = {"Retry-After": "120"}
    responses = iter([_Resp429(status_code=429), _FakeResponse(json_data={"releases": [_fts_release()], "links": {}})])
    sleeps = []
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: next(responses))
    monkeypatch.setattr(find_a_tender.time, "sleep", sleeps.append)
    assert len(find_a_tender.fetch([], KW, SINCE)) == 1
    assert sleeps == [121]


def test_find_a_tender_fetch_caps_lookback(monkeypatch):
    seen = []
    def fake_get(url, params=None, **kw):
        seen.append(params)
        return _FakeResponse(json_data={"releases": [], "links": {}})
    monkeypatch.setattr(find_a_tender.requests, "get", fake_get)
    find_a_tender.fetch([], KW, date(2000, 1, 1))
    cap = date.today() - __import__("datetime").timedelta(days=find_a_tender.MAX_LOOKBACK_DAYS)
    assert seen[0]["updatedFrom"] == f"{cap.isoformat()}T00:00:00"


def test_find_a_tender_fetch_follows_cursor_pagination(monkeypatch):
    page1 = {"releases": [_fts_release()], "links": {"next": "https://example/page2"}}
    page2 = {"releases": [_fts_release(title="Second")], "links": {}}
    responses = iter([page1, page2])
    urls = []

    def fake_get(url, params=None, **kw):
        urls.append(url)
        return _FakeResponse(json_data=next(responses))

    monkeypatch.setattr(find_a_tender.requests, "get", fake_get)
    monkeypatch.setattr(find_a_tender.time, "sleep", lambda *_: None)
    recs = find_a_tender.fetch(CPV, KW, SINCE)
    assert len(recs) == 2
    assert urls[1] == "https://example/page2"


def test_find_a_tender_fetch_filters_by_cpv(monkeypatch):
    data = {"releases": [_fts_release(cpv="99999999")], "links": {}}
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    assert find_a_tender.fetch(CPV, [], SINCE) == []  # no keywords: CPV alone decides
    assert len(find_a_tender.fetch([], KW, SINCE)) == 1


def test_find_a_tender_fetch_keeps_keyword_match_with_unmatched_cpv(monkeypatch):
    data = {"releases": [_fts_release(cpv="79952000", title="Marquee hire for county show"),
                         _fts_release(cpv="79952000", title="Event catering")], "links": {}}
    monkeypatch.setattr(find_a_tender.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = find_a_tender.fetch(CPV, ["marquee"], SINCE)
    assert [r["title"] for r in recs] == ["Marquee hire for county show"]


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
    assert contracts_finder.fetch(CPV, [], SINCE) == []  # no keywords: CPV alone decides


def test_contracts_finder_fetch_keeps_keyword_match_with_unmatched_cpv(monkeypatch):
    release = _cf_release(cpv="11111111")
    release["tender"]["title"] = "Supply of tents and gazebos"
    data = {"releases": [release, _cf_release(cpv="11111111")], "links": {}}
    monkeypatch.setattr(contracts_finder.requests, "get", lambda *a, **kw: _FakeResponse(json_data=data))
    recs = contracts_finder.fetch(CPV, ["tents"], SINCE)
    assert [r["title"] for r in recs] == ["Supply of tents and gazebos"]


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
    "solicitationNumber-numeroSollicitation", "noticeType-avisType-eng",
    "tradeAgreements-accordsCommerciaux-eng", "amendmentNumber-numeroModification",
    "contactInfoName-informationsContactNom", "contactInfoEmail-informationsContactCourriel",
    "contactInfoPhone-contactInfoTelephone",
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


def test_canadabuys_notice_url_falls_back_to_canadabuys_page_for_cb_and_ssc_references():
    own = dict(_ca_row(), **{"referenceNumber-numeroReference": "cb-395-95380568", "noticeURL-URLavis-eng": ""})
    merx = _ca_row()
    unknown = dict(_ca_row(), **{"referenceNumber-numeroReference": "WS5894216590", "noticeURL-URLavis-eng": ""})
    ssc = dict(_ca_row(), **{"referenceNumber-numeroReference": "SSC-26-00035079:T", "noticeURL-URLavis-eng": ""})
    recs = canadabuys.parse_response(_csv_bytes([own, merx, unknown, ssc]))
    assert recs[0]["noticeURL"] == "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-395-95380568"
    assert recs[1]["noticeURL"] == "https://www.merx.com/x"
    assert recs[2]["noticeURL"] == ""
    assert recs[3]["noticeURL"] == "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/ssc-26-00035079t"


def test_canadabuys_notice_detail_reads_extra_columns():
    row = dict(_ca_row(), **{
        "solicitationNumber-numeroSollicitation": "W8486-270836/B",
        "noticeType-avisType-eng": "Request for Proposal",
        "tradeAgreements-accordsCommerciaux-eng": "*Canadian Free Trade Agreement (CFTA)\n*World Trade Organization (WTO-AGP)",
        "amendmentNumber-numeroModification": "000",
        "contactInfoName-informationsContactNom": "Jane Doe",
        "contactInfoEmail-informationsContactCourriel": "jane.doe@forces.gc.ca",
        "contactInfoPhone-contactInfoTelephone": "",
    })
    detail = canadabuys.parse_response(_csv_bytes([row]))[0]["noticeDetail"]
    assert detail["solicitation_number"] == "W8486-270836/B"
    assert detail["trade_agreements"] == "Canadian Free Trade Agreement (CFTA), World Trade Organization (WTO-AGP)"
    assert detail["contact_email"] == "jane.doe@forces.gc.ca"
    assert "amendment_number" not in detail and "contact_phone" not in detail
    assert normalize.normalize_canadabuys({"noticeDetail": detail})["notice_detail"] == detail


def test_canadabuys_notice_detail_none_when_empty():
    assert canadabuys.notice_detail({}) is None


def test_notice_detail_round_trips_through_store(tmp_path):
    conn = store.init_db(str(tmp_path / "t.db"))
    rec = normalize.normalize_canadabuys({"referenceNumber": "cb-1-2", "title": "Tents",
                                          "noticeDetail": {"solicitation_number": "W1"}})
    store.upsert(conn, 1, rec)
    other = dict(rec, pub_number="cb-1-3", notice_detail=None)
    store.upsert(conn, 1, other)
    got = {r["pub_number"]: r["notice_detail"] for r in store.all_records(conn, 1)}
    assert got == {"cb-1-2": {"solicitation_number": "W1"}, "cb-1-3": None}


def test_canadabuys_fetch_filters_locally_by_publication_date(monkeypatch):
    rows = [_ca_row(pub_date="2026-09-01", title="Too old"),
            _ca_row(pub_date="2026-10-10", title="In range")]
    monkeypatch.setattr(canadabuys.requests, "get",
                         lambda *a, **kw: _FakeResponse(content=_csv_bytes(rows)))
    recs = canadabuys.fetch([], KW, date(2026, 10, 1))
    assert [r["title"] for r in recs] == ["In range"]


def test_normalize_canadabuys_no_cpv_codes_without_a_crosswalk_match(raw_canadabuys_supply):
    r = normalize.normalize_canadabuys(raw_canadabuys_supply)
    assert r["cpv_codes"] == []
    assert r["value"] == ""  # confirmed absent from the source entirely


# ── CanadaBuys UNSPSC crosswalk ─────────────────────────────────────────────

def test_canadabuys_parse_unspsc_live_format():
    assert canadabuys.parse_unspsc("*49121503\n*95131700") == ["49121503", "95131700"]
    assert canadabuys.parse_unspsc("") == []


def test_canadabuys_matched_cpv_codes_exact_and_hierarchical():
    wanted = {"39522530": ["49121503", "95131700"], "39522500": ["49121500"], "39522540": ["49121504"]}
    # 49121503 (Tents) hits tents exactly and camping goods via its class 49121500
    assert canadabuys.matched_cpv_codes("*49121503", wanted) == ["39522530", "39522500"]
    # a commodity under class 95131700 matches it
    assert canadabuys.matched_cpv_codes("*95131701", wanted) == ["39522530"]
    # unrelated codes, and a sibling class sharing only the family prefix, don't
    assert canadabuys.matched_cpv_codes("*72121400\n*49121600", {"39522530": ["49121503"]}) == []


def test_canadabuys_crosswalk_codes_are_well_formed_and_cpv_keys_known():
    import config
    crosswalk = config.canadabuys_unspsc()
    assert crosswalk, "crosswalk must not be empty"
    reference = config.cpv_reference()
    for cpv, codes in crosswalk.items():
        assert cpv in reference, f"unknown CPV {cpv}"
        assert codes and all(len(c) == 8 and c.isdigit() for c in codes)


def test_canadabuys_fetch_maps_unspsc_to_tenant_cpv_and_keeps_unmatched(monkeypatch):
    tent = dict(_ca_row(pub_date="2026-10-10", title="Field tents"), unspsc="*49121503")
    other = dict(_ca_row(pub_date="2026-10-10", title="Office chairs"), unspsc="*56101500")
    monkeypatch.setattr(canadabuys.requests, "get",
                         lambda *a, **kw: _FakeResponse(content=_csv_bytes([tent, other])))
    recs = canadabuys.fetch(["39522530", "39522110"], KW, date(2026, 10, 1))
    by_title = {r["title"]: r for r in recs}
    assert by_title["Field tents"]["cpvCodes"] == ["39522530"]  # 39522110 has no UNSPSC equivalent
    assert by_title["Office chairs"]["cpvCodes"] == []           # still returned: keywords decide
    assert normalize.normalize_canadabuys(by_title["Field tents"])["cpv_codes"] == ["39522530"]


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


def test_tenderned_parse_list_accepts_wrapped_page_shape():
    # Live shape since at least 2026-10-17 — the bare-array-only parser
    # silently returned [] for it, so every run reported "ok, 0".
    item = {"publicatieId": 1, "publicatieDatum": "2026-10-17"}
    assert tenderned.parse_list_response({"content": [item], "last": False}) == [item]
    assert tenderned.parse_list_response([item]) == [item]
    assert tenderned.parse_list_response({"unexpected": True}) == []


def test_tenderned_fetch_handles_wrapped_pages_and_stops_on_last(monkeypatch):
    pages = iter([
        {"content": [{"publicatieId": 2, "publicatieDatum": "2026-10-16", "link": {"href": "u2"}}], "last": False},
        {"content": [{"publicatieId": 1, "publicatieDatum": "2026-10-15", "link": {"href": "u1"}}], "last": True},
    ])
    list_calls = []

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url == tenderned.LIST_ENDPOINT:
                list_calls.append(params["page"])
                return _FakeResponse(json_data=next(pages))
            return _FakeResponse(json_data={"cpvCodes": [], "nutsCodes": []})

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    recs = tenderned.fetch([], KW, date(2026, 10, 1))
    assert [r["publicatieId"] for r in recs] == [2, 1]
    assert list_calls == [0, 1]  # no request past the page marked last


def test_tenderned_fetch_skips_amendment_types_without_a_detail_call(monkeypatch):
    page = {"content": [
        {"publicatieId": 1, "publicatieDatum": "2026-10-16", "typePublicatie": {"code": "AAO"}, "link": {"href": "u1"}},
        {"publicatieId": 2, "publicatieDatum": "2026-10-16", "typePublicatie": {"code": "REC"}, "link": {"href": "u2"}},
        {"publicatieId": 3, "publicatieDatum": "2026-10-16", "typePublicatie": {"code": "VBE"}, "link": {"href": "u3"}},
        {"publicatieId": 4, "publicatieDatum": "2026-10-16", "typePublicatie": {"code": "AGO"}, "link": {"href": "u4"}},
    ], "last": True}
    detail_calls = []

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url == tenderned.LIST_ENDPOINT:
                return _FakeResponse(json_data=page)
            detail_calls.append(url)
            return _FakeResponse(json_data={"cpvCodes": [], "nutsCodes": []})

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    recs = tenderned.fetch([], KW, date(2026, 10, 1))
    assert [r["publicatieId"] for r in recs] == [1, 4]
    assert len(detail_calls) == 2


def test_tenderned_fetch_keeps_keyword_match_with_unmatched_cpv(monkeypatch):
    page = {"content": [
        {"publicatieId": 1, "publicatieDatum": "2026-10-16", "aanbestedingNaam": "Levering schuiltent", "link": {"href": "u1"}},
        {"publicatieId": 2, "publicatieDatum": "2026-10-16", "aanbestedingNaam": "Schoonmaakdiensten", "link": {"href": "u2"}},
    ], "last": True}

    class _Sess:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if url == tenderned.LIST_ENDPOINT:
                return _FakeResponse(json_data=page)
            return _FakeResponse(json_data={"cpvCodes": [{"code": "90910000-9"}], "nutsCodes": []})

    monkeypatch.setattr(tenderned.requests, "Session", lambda: _Sess())
    monkeypatch.setattr(tenderned.time, "sleep", lambda *_: None)
    recs = tenderned.fetch(["39522530"], ["schuiltent"], date(2026, 10, 1))
    assert [r["publicatieId"] for r in recs] == [1]


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
    assert recs[0]["noticeDetail"] == {"reference": "IUC25-711"}
    # a non-matching CPV filter excludes it
    recs_none = tenderned.fetch(["99999999"], KW, date(2026, 10, 1))
    assert recs_none == []


def test_tenderned_notice_detail_from_live_shape():
    # trimmed from the live detail response for notice 444954 (2026-10-18)
    detail = {
        "kenmerk": 619056, "referentieNummer": "3002228a-dc72-4fae-9211-812440f9f48a",
        "aankondigingCode": {"code": "AAO", "omschrijving": "Aankondiging opdracht"},
        "procedureCode": {"code": "OPE", "omschrijving": "Openbaar"},
        "nationaalOfEuropeesCode": {"code": "EU", "omschrijving": "Europees"},
        "juridischKaderCode": {"code": "NAW", "omschrijving": "Aanbestedingswet 2012"},
        "urlTsenderNaam": "Mercell", "urlTsenderWebsite": "https://s2c.mercell.com/today/235977",
        "links": {"pdf": {"href": "/papi/tenderned-rs-tns/v2/publicaties/444954/pdf"}},
    }
    assert tenderned.notice_detail(detail) == {
        "reference": "3002228a-dc72-4fae-9211-812440f9f48a",
        "tenderned_kenmerk": "619056",
        "notice_type": "Contract notice",
        "procedure": "Open",
        "scope": "European (above EU threshold)",
        "legal_framework": "Aanbestedingswet 2012",
        "tender_platform": "Mercell",
        "tender_platform_url": "https://s2c.mercell.com/today/235977",
        "notice_pdf_url": "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties/444954/pdf",
    }
    assert tenderned.notice_detail({}) is None
    unknown = {"procedureCode": {"code": "XYZ", "omschrijving": "Iets nieuws"}}
    assert tenderned.notice_detail(unknown) == {"procedure": "Iets nieuws"}


def test_normalize_tenderned_carries_notice_detail():
    rec = normalize.normalize_tenderned({"publicatieId": 1, "noticeDetail": {"procedure": "Open"}})
    assert rec["notice_detail"] == {"procedure": "Open"}
    assert normalize.normalize_tenderned({"publicatieId": 2})["notice_detail"] is None


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


# ── philgeps ─────────────────────────────────────────────────────────────────

_PHILGEPS_ROW_HTML = """
<tr class="GridAltItem">
    <td align="center" valign="middle" style="width:5%;">2</td><td class="GridItemTD" align="center" valign="middle" style="width:5%;">{pub}</td><td class="GridItemTD" align="center" valign="middle" style="width:10%;">{close}</td><td class="GridItemTD">
        <a id="dgSearchResult_ctl03_hyLinkTitle" href="SplashBidNoticeAbstractUI.aspx?menuIndex=3&amp;refID={ref}&amp;Result=3">{title}</a>
        <span id="dgSearchResult_ctl03_lblOrgAndBusCat">, {category} ,{agency}</span>
    </td>
</tr>
"""


def _philgeps_page(rows, viewstate="VS1", validation="EV1", generator="G1"):
    body = "".join(
        _PHILGEPS_ROW_HTML.format(
            pub=r.get("pub", "16/10/2026"), close=r.get("close", "20/10/2026 09:00 AM"),
            ref=r["ref"], title=r.get("title", "Test tender"),
            category=r.get("category", "Catering Services"), agency=r["agency"],
        )
        for r in rows
    )
    return (
        f'<input type="hidden" id="__VIEWSTATE" value="{viewstate}" />'
        f'<input type="hidden" id="__VIEWSTATEGENERATOR" value="{generator}" />'
        f'<input type="hidden" id="__EVENTVALIDATION" value="{validation}" />'
        f"<table id=\"dgSearchResult\">{body}</table>"
    )


def test_philgeps_parse_response_splits_category_and_comma_in_agency():
    html = _philgeps_page([{"ref": "1", "agency": "CITY OF MANDAUE, CEBU"}])
    recs = philgeps.parse_response(html)
    assert len(recs) == 1
    assert recs[0]["category"] == "Catering Services"
    assert recs[0]["agency"] == "CITY OF MANDAUE, CEBU"  # internal comma preserved
    assert recs[0]["refID"] == "1"


def test_philgeps_parse_response_converts_dates_to_iso():
    html = _philgeps_page([{"ref": "1", "agency": "X", "pub": "16/10/2026", "close": "20/10/2026 09:00 AM"}])
    recs = philgeps.parse_response(html)
    assert recs[0]["publicationDate"] == "2026-10-16"
    assert recs[0]["closingDate"] == "2026-10-20"


class _FakeResponse2:
    status_code = 200  # read by connectors.retry

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeSessionText:
    """Mimics requests.Session closely enough for philgeps.fetch: .get/.post
    return an object with a `.text` attribute (philgeps.py reads resp.text,
    unlike the JSON-based connectors' resp.json())."""
    def __init__(self, pages):
        self._pages = iter(pages)

    def get(self, url, headers=None, timeout=None):
        return _FakeResponse2(next(self._pages))

    def post(self, url, data=None, headers=None, timeout=None):
        return _FakeResponse2(next(self._pages))


def test_philgeps_fetch_pages_until_older_than_since(monkeypatch):
    page1 = _philgeps_page([{"ref": "2", "agency": "X", "pub": "16/10/2026"}])
    page2 = _philgeps_page([{"ref": "1", "agency": "Y", "pub": "09/01/2020"}])  # older than since
    monkeypatch.setattr(philgeps.requests, "Session", lambda: _FakeSessionText([page1, page2]))
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    recs = philgeps.fetch([], [], date(2026, 10, 1))
    assert len(recs) == 1
    assert recs[0]["refID"] == "2"


def test_philgeps_fetch_stops_on_repeated_page(monkeypatch):
    page = _philgeps_page([{"ref": "1", "agency": "X"}])
    monkeypatch.setattr(philgeps.requests, "Session", lambda: _FakeSessionText([page, page, page]))
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    recs = philgeps.fetch([], [], date(2026, 10, 1))
    assert len(recs) == 1  # second page repeats refID "1" -> loop stops, no duplicate


def test_philgeps_fetch_respects_max_records(monkeypatch):
    page1 = _philgeps_page([{"ref": "1", "agency": "A"}, {"ref": "2", "agency": "B"}])
    monkeypatch.setattr(philgeps.requests, "Session", lambda: _FakeSessionText([page1]))
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    recs = philgeps.fetch([], [], date(2026, 10, 1), max_records=1)
    assert len(recs) == 1


def _redirect_loop():
    """A TooManyRedirects shaped like requests raises it: .response.history
    holds the hops."""
    hop = philgeps.requests.Response()
    hop.status_code, hop.url = 302, philgeps.SEARCH_URL
    hop.headers["Location"] = "/GEPSNONPILOT/Error.aspx"
    last = philgeps.requests.Response()
    last.history = [hop]
    return philgeps.requests.TooManyRedirects("Exceeded 30 redirects.", response=last)


def test_philgeps_fetch_keeps_earlier_pages_when_a_later_page_redirect_loops(monkeypatch):
    page1 = _philgeps_page([{"ref": "2", "agency": "X"}])

    class _Sess(_FakeSessionText):
        def post(self, url, data=None, headers=None, timeout=None):
            raise _redirect_loop()

    monkeypatch.setattr(philgeps.requests, "Session", lambda: _Sess([page1]))
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    recs = philgeps.fetch([], [], date(2026, 10, 1))
    assert [r["refID"] for r in recs] == ["2"]
    assert recs.warning.startswith("stopped after page 1 (1 notices): Exceeded 30 redirects.")
    assert "-> /GEPSNONPILOT/Error.aspx" in recs.warning  # the hop that bounced is named


def test_philgeps_fetch_page_one_redirect_loop_fails_the_source(monkeypatch):
    class _Sess(_FakeSessionText):
        def get(self, url, headers=None, timeout=None):
            raise _redirect_loop()

    monkeypatch.setattr(philgeps.requests, "Session", lambda: _Sess([]))
    with pytest.raises(RuntimeError, match=r"^page 1: Exceeded 30 redirects\. \(first hop: 302 "):
        philgeps.fetch([], [], date(2026, 10, 1))


def test_philgeps_fetch_clean_run_has_no_warning(monkeypatch):
    page = _philgeps_page([{"ref": "1", "agency": "X"}])
    monkeypatch.setattr(philgeps.requests, "Session", lambda: _FakeSessionText([page, page]))
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    assert philgeps.fetch([], [], date(2026, 10, 1)).warning is None


_PHILGEPS_ABSTRACT = (
    "<td>Solicitation Number:</td><td>2026-010</td>"
    "<td>Approved Budget for the Contract:</td><td><span>PHP 100,000.00</span></td>"
    "<td>Delivery Period:</td><td>15 Day/s</td>"
)


def test_philgeps_parse_approved_budget():
    assert philgeps.parse_approved_budget(_PHILGEPS_ABSTRACT) == ("PHP", "100000.00")
    assert philgeps.parse_approved_budget("<p>no budget here</p>") == (None, None)


def test_philgeps_parse_estimated_budget():
    # Negotiated purchases (live, 2026-10-29) say "Estimated", with an &nbsp;
    # between currency and amount.
    html = ("<td>Estimated Budget for the Contract:</td>"
            "<td><span>PHP &nbsp; 100,000.00</span></td>")
    assert philgeps.parse_approved_budget(html) == ("PHP", "100000.00")


def test_philgeps_fetch_enriches_budget_only_for_keyword_rows(monkeypatch):
    page = _philgeps_page([{"ref": "1", "agency": "A", "title": "Purchase of steel pipe tent"},
                           {"ref": "2", "agency": "B", "title": "Office supplies"}])
    detail_urls = []

    class _Sess(_FakeSessionText):
        def get(self, url, headers=None, timeout=None):
            if "SplashBidNoticeAbstractUI" in url:
                detail_urls.append(url)
                return _FakeResponse2(_PHILGEPS_ABSTRACT)
            return super().get(url, headers=headers, timeout=timeout)

    monkeypatch.setattr(philgeps.requests, "Session", lambda: _Sess([page, page]))  # repeat ends paging
    monkeypatch.setattr(philgeps.time, "sleep", lambda *_: None)
    recs = {r["refID"]: r for r in philgeps.fetch([], ["tent"], date(2026, 10, 1))}
    assert len(detail_urls) == 1 and "refID=1" in detail_urls[0]
    assert (recs["1"]["budgetCurrency"], recs["1"]["approvedBudget"]) == ("PHP", "100000.00")
    assert "approvedBudget" not in recs["2"]
    norm = normalize.normalize_philgeps(recs["1"])
    assert (norm["value"], norm["value_currency"]) == ("100000.00", "PHP")


def test_normalize_philgeps_fields(raw_philgeps_supply):
    r = normalize.normalize_philgeps(raw_philgeps_supply)
    assert r["source"] == "PhilGEPS"
    assert r["country"] == "PHL"
    assert r["language"] == "eng"
    assert r["cpv_codes"] == []
    assert r["category"] == "Other"
    assert r["buyer"] == "MUNICIPALITY OF ESPERANZA, MASBATE"
    assert r["pub_number"] == "13275928"
    assert r["url"] == (
        "https://notices.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashBidNoticeAbstractUI.aspx"
        "?menuIndex=3&refID=13275928&Result=3"
    )
    assert r["deadline"] == "2026-10-26"
    assert r["value"] == ""


# ── schema parity — now across all nine sources ─────────────────────────────

def test_all_nine_sources_share_identical_schema(
    raw_ted_supply, raw_boamp_supply, raw_eprocurement_supply, raw_bekanntmachungsservice_supply,
    raw_find_a_tender_supply, raw_contracts_finder_supply, raw_canadabuys_supply, raw_tenderned_supply,
    raw_philgeps_supply,
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
        "PhilGEPS": set(normalize.normalize_philgeps(raw_philgeps_supply)),
    }
    # form_type is an optional classification hint (eForms sources only), not
    # a stored column; notice_detail is a nullable column only sources with
    # extra facts set (store.upsert stores NULL when absent). The only keys
    # allowed to differ.
    optional = {"form_type", "notice_detail"}
    canonical = keysets["TED"] - optional
    for name, keys in keysets.items():
        assert keys - optional == canonical, f"{name} schema diverges: {(keys - optional) ^ canonical}"
