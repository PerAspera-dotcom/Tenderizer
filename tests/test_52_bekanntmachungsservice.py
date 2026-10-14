"""Step 52 — CR-010 §2: Germany Bekanntmachungsservice connector.
Interface:
  connectors.bekanntmachungsservice.build_params(cpv_codes, keywords, since, limit=100, offset=0) -> dict
  connectors.bekanntmachungsservice.parse_response(zip_bytes) -> list[dict]  # one day's worth
  connectors.bekanntmachungsservice.fetch(cpv_codes, keywords, since) -> list[dict]  # live, multi-day
  normalize.normalize_bekanntmachungsservice(raw) -> normalised record (SAME schema as the other three)

parse_response's input is real ZIP bytes (not JSON, unlike every other connector
— see module docstring) — built in-memory here via zipfile/io rather than
captured live, since a real day's csv.zip is far too large to fix as a test
fixture; the CSV *content* itself mirrors the real column layout confirmed
live (see the module docstring's probe notes), just trimmed to a handful of
rows. fetch()'s HTTP calls are mocked (no @pytest.mark.network test, same
reasoning as test_51_eprocurement.py: this is a daily bulk CC0 export, not
something to hit repeatedly in CI, though unlike eprocurement.py no auth is
needed here so a live test would at least be *possible* — just not something
worth doing on every run).
"""
import csv
import inspect
import io
import zipfile
from datetime import date

import pytest
import normalize
from connectors import bekanntmachungsservice as bms
from connectors import ted, boamp, eprocurement

CPV = ["45000000-7"]
KW = ["Zelt"]
SINCE = date(2026, 10, 1)


def _make_zip(tables):
    """tables: {filename: [ {col: val, ...}, ... ]} -> zip bytes, CSV per file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, rows in tables.items():
            out = io.StringIO()
            if rows:
                writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            zf.writestr(name, out.getvalue())
    return buf.getvalue()


def _minimal_tables(notice_id="N1", version="01", lots=None):
    lots = lots if lots is not None else ["LOT-0001"]
    notice = [{"noticeIdentifier": notice_id, "noticeVersion": version,
               "procedureIdentifier": "P1", "procedureLegalBasis": "de-vob",
               "formType": "competition", "noticeType": "cn-standard",
               "publicationDate": "2026-10-05T02:00:00Z"}]
    procedure = [{"noticeIdentifier": notice_id, "noticeVersion": version, "procedureType": "open"}]
    purpose = [{"noticeIdentifier": notice_id, "noticeVersion": version, "lotIdentifier": "",
                "internalIdentifier": "REF-123", "mainNature": "", "additionalNature": "",
                "title": "Test tender", "estimatedValue": "", "estimatedValueCurrency": "",
                "description": "A test tender description."}]
    for lot in lots:
        purpose.append({"noticeIdentifier": notice_id, "noticeVersion": version, "lotIdentifier": lot,
                         "internalIdentifier": "", "mainNature": "works", "additionalNature": "",
                         "title": f"{lot} title", "estimatedValue": "", "estimatedValueCurrency": "",
                         "description": f"{lot} description"})
    classification = [{"noticeIdentifier": notice_id, "noticeVersion": version, "lotIdentifier": "",
                        "classificationType": "cpv", "mainClassificationCode": "45000000-7",
                        "additionalClassificationCodes": "", "options": ""}]
    for i, lot in enumerate(lots):
        classification.append({"noticeIdentifier": notice_id, "noticeVersion": version, "lotIdentifier": lot,
                                "classificationType": "cpv",
                                "mainClassificationCode": "45111000-8" if i == 0 else "45200000-9",
                                "additionalClassificationCodes": "", "options": ""})
    place = [{"noticeIdentifier": notice_id, "noticeVersion": version, "lotIdentifier": lot,
              "placePerformanceCity": "Berlin", "placePerformancePostCode": "10115",
              "placePerformanceCountrySubdivision": "DE300", "placePerformanceCountryCode": "DEU"}
             for lot in lots]
    organisation = [
        {"noticeIdentifier": notice_id, "noticeVersion": version, "organisationName": "Stadt Berlin",
         "organisationIdentifier": "", "organisationCity": "Berlin", "organisationPostCode": "10115",
         "organisationCountrySubdivision": "DE300", "organisationCountryCode": "DEU",
         "organisationInternetAddress": "", "organisationNaturalPerson": "", "organisationRole": "buyer",
         "buyerProfileURL": "", "buyerLegalType": "", "buyerContractingEntity": "",
         "winnerSize": "", "winnerOwnerNationality": "", "winnerListed": ""},
    ]
    notice_result = [{"noticeIdentifier": notice_id, "noticeVersion": version,
                       "noticeValue": "", "noticeValueCurrency": "",
                       "noticeFrameworkValue": "", "noticeFrameworkValueCurrency": ""}]
    return {
        "notice.csv": notice, "procedure.csv": procedure, "purpose.csv": purpose,
        "classification.csv": classification, "placeOfPerformance.csv": place,
        "organisation.csv": organisation, "noticeResult.csv": notice_result,
    }


# ── build_params ─────────────────────────────────────────────────────────────

def test_build_params_is_one_days_request():
    p = bms.build_params(CPV, KW, date(2026, 10, 5))
    assert p == {"pubDay": "2026-10-05", "format": "csv.zip"}

def test_build_params_and_fetch_share_connectors_argument_order():
    # extends test_10_boamp.py / test_51_eprocurement.py's same-named check
    # to the fourth connector.
    ted_params = list(inspect.signature(ted.fetch).parameters)[:3]
    boamp_params = list(inspect.signature(boamp.fetch).parameters)[:3]
    eprocurement_params = list(inspect.signature(eprocurement.fetch).parameters)[:3]
    bms_params = list(inspect.signature(bms.fetch).parameters)[:3]
    assert ted_params == boamp_params == eprocurement_params == bms_params == \
        ["cpv_codes", "keywords", "since"]


# ── parse_response — the CSV join (in-memory ZIP, real column layout) ──────

def test_parse_response_empty_zip_yields_empty_list():
    assert bms.parse_response(_make_zip({"notice.csv": []})) == []

def test_parse_response_basic_fields():
    recs = bms.parse_response(_make_zip(_minimal_tables()))
    assert len(recs) == 1
    r = recs[0]
    assert r["noticeIdentifier"] == "N1"
    assert r["noticeVersion"] == "01"
    assert r["title"] == "Test tender"
    assert r["description"] == "A test tender description."
    assert r["internalIdentifier"] == "REF-123"
    assert r["buyerName"] == "Stadt Berlin"
    assert r["procedureType"] == "open"

def test_parse_response_collects_notice_and_lot_level_cpv():
    recs = bms.parse_response(_make_zip(_minimal_tables(lots=["LOT-0001", "LOT-0002"])))
    # notice-level code + both distinct lot-level codes, deduped, order preserved
    assert recs[0]["cpvCodes"] == ["45000000-7", "45111000-8", "45200000-9"]

def test_parse_response_mainnature_falls_back_to_first_lot():
    # notice-level purpose row's mainNature is blank (confirmed live reality)
    recs = bms.parse_response(_make_zip(_minimal_tables()))
    assert recs[0]["mainNature"] == "works"

def test_parse_response_winner_role_priority_winner_beats_tenderer():
    tables = _minimal_tables()
    tables["organisation.csv"].append(
        {"noticeIdentifier": "N1", "noticeVersion": "01", "organisationName": "Also-Ran GmbH",
         "organisationIdentifier": "", "organisationCity": "", "organisationPostCode": "",
         "organisationCountrySubdivision": "", "organisationCountryCode": "",
         "organisationInternetAddress": "", "organisationNaturalPerson": "", "organisationRole": "tenderer",
         "buyerProfileURL": "", "buyerLegalType": "", "buyerContractingEntity": "",
         "winnerSize": "", "winnerOwnerNationality": "", "winnerListed": ""})
    tables["organisation.csv"].append(
        {"noticeIdentifier": "N1", "noticeVersion": "01", "organisationName": "Actual Winner GmbH",
         "organisationIdentifier": "", "organisationCity": "", "organisationPostCode": "",
         "organisationCountrySubdivision": "", "organisationCountryCode": "",
         "organisationInternetAddress": "", "organisationNaturalPerson": "", "organisationRole": "winner",
         "buyerProfileURL": "", "buyerLegalType": "", "buyerContractingEntity": "",
         "winnerSize": "", "winnerOwnerNationality": "", "winnerListed": ""})
    recs = bms.parse_response(_make_zip(tables))
    # 'winner' role must win even though 'tenderer' appears first in the file
    assert recs[0]["winnerName"] == "Actual Winner GmbH"

def test_parse_response_no_winner_role_yields_none():
    recs = bms.parse_response(_make_zip(_minimal_tables()))
    assert recs[0]["winnerName"] is None

def test_parse_response_nuts_codes_deduped_across_lots():
    recs = bms.parse_response(_make_zip(_minimal_tables(lots=["LOT-0001", "LOT-0002"])))
    assert recs[0]["nutsCodes"] == ["DE300"]  # same region both lots -> deduped to one


# ── fetch() — mocked HTTP, multi-day iteration ──────────────────────────────

class _FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_since_in_the_future_returns_empty_without_a_request(monkeypatch):
    calls = []
    monkeypatch.setattr(bms.requests, "get", lambda *a, **kw: calls.append(1) or _FakeResponse(b""))
    recs = bms.fetch(CPV, KW, date(2099, 1, 1))
    assert recs == []
    assert calls == []


def test_fetch_clamps_since_to_historical_floor(monkeypatch):
    # a `since` well before 2022-12-01 must not be sent as-is (would 400 live).
    # Returns one notice on the very first call so max_records=1 stops the
    # loop immediately — otherwise this would walk ~1400 real empty days.
    requested_days = []

    def fake_get(url, params=None, **kw):
        requested_days.append(params["pubDay"])
        return _FakeResponse(_make_zip(_minimal_tables()))

    monkeypatch.setattr(bms.requests, "get", fake_get)
    bms.fetch(CPV, KW, date(2020, 1, 1), max_records=1)
    assert requested_days == [bms.HISTORICAL_FLOOR.isoformat()]


def test_fetch_iterates_one_request_per_day(monkeypatch):
    requested_days = []

    def fake_get(url, params=None, **kw):
        requested_days.append(params["pubDay"])
        return _FakeResponse(_make_zip({"notice.csv": []}))

    monkeypatch.setattr(bms.requests, "get", fake_get)
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=3)
    bms.fetch(CPV, KW, since)
    yesterday = today - bms.datetime.timedelta(days=1)
    expected = []
    d = since
    while d <= yesterday:
        expected.append(d.isoformat())
        d += bms.datetime.timedelta(days=1)
    assert requested_days == expected


def test_fetch_collapses_to_latest_version_across_days(monkeypatch):
    # same noticeIdentifier appears on day 1 as v1("01") and day 2 as v2("02")
    # — confirms both the cross-day collapse AND that comparison is numeric,
    # not lexicographic (string "02" > "01" would also pass; the real risk —
    # "1" vs "10" — is covered by test_version_comparison_is_numeric_not_lexicographic).
    day1_zip = _make_zip(_minimal_tables(notice_id="N1", version="01"))
    tables_v2 = _minimal_tables(notice_id="N1", version="02")
    tables_v2["purpose.csv"][0]["title"] = "Corrected title"
    day2_zip = _make_zip(tables_v2)
    responses = iter([day1_zip, day2_zip])

    def fake_get(url, params=None, **kw):
        return _FakeResponse(next(responses))

    monkeypatch.setattr(bms.requests, "get", fake_get)
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=2)
    recs = bms.fetch([], KW, since)
    assert len(recs) == 1
    assert recs[0]["title"] == "Corrected title"
    assert recs[0]["noticeVersion"] == "02"


def test_fetch_version_comparison_is_numeric_not_lexicographic(monkeypatch):
    # confirmed live: real data mixes "1"/"01" and "2"/"02" zero-padding for
    # the same semantic version — a string compare would rank "10" < "2".
    day1_zip = _make_zip(_minimal_tables(notice_id="N1", version="2"))
    tables_v10 = _minimal_tables(notice_id="N1", version="10")
    tables_v10["purpose.csv"][0]["title"] = "Version ten"
    day2_zip = _make_zip(tables_v10)
    responses = iter([day1_zip, day2_zip])
    monkeypatch.setattr(bms.requests, "get", lambda url, params=None, **kw: _FakeResponse(next(responses)))
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=2)
    recs = bms.fetch([], KW, since)
    assert recs[0]["title"] == "Version ten"  # int(10) > int(2), not "10" < "2"


def test_fetch_filters_by_cpv_including_lot_level_match(monkeypatch):
    tables = _minimal_tables(lots=["LOT-0001"])  # notice-level 45000000-7, lot-level 45111000-8
    monkeypatch.setattr(bms.requests, "get", lambda url, params=None, **kw: _FakeResponse(_make_zip(tables)))
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=1)
    # matches only on the LOT-level code, not the notice-level one
    assert len(bms.fetch(["45111000-8"], KW, since)) == 1
    assert len(bms.fetch(["99999999-9"], KW, since)) == 0


def test_fetch_no_cpv_filter_returns_everything(monkeypatch):
    tables = _minimal_tables()
    monkeypatch.setattr(bms.requests, "get", lambda url, params=None, **kw: _FakeResponse(_make_zip(tables)))
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=1)
    assert len(bms.fetch([], KW, since)) == 1


def test_fetch_respects_max_records(monkeypatch):
    # two distinct notices across two days, cap at 1
    tables_a = _minimal_tables(notice_id="A", version="01")
    tables_b = _minimal_tables(notice_id="B", version="01")
    responses = iter([_make_zip(tables_a), _make_zip(tables_b)])
    monkeypatch.setattr(bms.requests, "get", lambda url, params=None, **kw: _FakeResponse(next(responses)))
    today = bms.datetime.date.today()
    since = today - bms.datetime.timedelta(days=2)
    recs = bms.fetch([], KW, since, max_records=1)
    assert len(recs) == 1


# ── normalize_bekanntmachungsservice ────────────────────────────────────────

def test_normalize_country_is_de(raw_bekanntmachungsservice_supply):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["country"] == "DE"

def test_normalize_language_always_german(raw_bekanntmachungsservice_supply, raw_bekanntmachungsservice_below_threshold):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["language"] == "deu"
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_below_threshold)["language"] == "deu"

def test_normalize_source_string(raw_bekanntmachungsservice_supply):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["source"] == "Bekanntmachungsservice"

def test_normalize_deadline_always_empty(raw_bekanntmachungsservice_supply, raw_bekanntmachungsservice_below_threshold):
    # CR-010 §2: confirmed absent from both CSV and OCDS — deferred, not a bug.
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["deadline"] == ""
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_below_threshold)["deadline"] == ""

def test_normalize_award_fields_populated_unlike_belgium(raw_bekanntmachungsservice_supply):
    # CR-010 §2: unlike eprocurement.py, this source's CSV has plain
    # award columns — no XML parsing needed, so these ARE populated.
    r = normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)
    assert r["raw_award_winner"] == "SPIE IMO Service GmbH"
    assert r["raw_award_value"] == "23790"
    assert r["raw_award_currency"] == "EUR"
    assert r["raw_award_detail"] is None  # not scoped for this CR — see docstring

def test_normalize_no_winner_yields_none_award_fields(raw_bekanntmachungsservice_below_threshold):
    r = normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_below_threshold)
    assert r["raw_award_winner"] is None
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None

def test_normalize_category_from_main_nature(raw_bekanntmachungsservice_supply, raw_bekanntmachungsservice_below_threshold):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["category"] == "Supply"
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_below_threshold)["category"] == "Works"

def test_normalize_internal_identifier_maps_through(raw_bekanntmachungsservice_below_threshold):
    raw = dict(raw_bekanntmachungsservice_below_threshold, internalIdentifier="BEK-2026-0007")
    assert normalize.normalize_bekanntmachungsservice(raw)["internal_identifier"] == "BEK-2026-0007"

def test_normalize_internal_identifier_absent_is_none(raw_bekanntmachungsservice_below_threshold):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_below_threshold)["internal_identifier"] is None

def test_normalize_pub_date_trimmed_to_date(raw_bekanntmachungsservice_supply):
    assert normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)["pub_date"] == "2026-10-13"

def test_normalize_pub_number_is_notice_identifier(raw_bekanntmachungsservice_supply):
    # stable across corrigendum versions — see module docstring on why this
    # (not noticeIdentifier+version) is what lets a later run's corrigendum
    # upsert the same stored record.
    r = normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)
    assert r["pub_number"] == "6a06759e-fe07-4383-800c-b6236d96f2a0"

def test_normalize_url_uses_notice_identifier(raw_bekanntmachungsservice_supply):
    r = normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)
    assert r["url"] == "https://oeffentlichevergabe.de/ui/en/search/details?noticeId=6a06759e-fe07-4383-800c-b6236d96f2a0"

def test_normalize_procedure_passes_through_mixed_formats(raw_bekanntmachungsservice_supply):
    # confirmed live: some notices carry a clean eForms code ("open"), others
    # raw German descriptive text — both just pass through lowercased.
    raw = dict(raw_bekanntmachungsservice_supply, procedureType="Beabsichtigte Beschrankte Ausschreibung")
    assert normalize.normalize_bekanntmachungsservice(raw)["procedure"] == "beabsichtigte beschrankte ausschreibung"

def test_normalize_value_is_pre_award_estimate_not_award_total(raw_bekanntmachungsservice_supply):
    # value/value_currency = estimatedValue (BT-27, pre-award) — distinct
    # from raw_award_value (the award total) — same split normalize_ted draws.
    raw = dict(raw_bekanntmachungsservice_supply, estimatedValue="500000", estimatedValueCurrency="EUR")
    r = normalize.normalize_bekanntmachungsservice(raw)
    assert r["value"] == "500000"
    assert r["value_currency"] == "EUR"
    assert r["raw_award_value"] == "23790"  # unaffected, still the award total


# ── schema parity — the full 4-way check (CR-010 §3) ────────────────────────

def test_all_four_sources_share_identical_schema(
    raw_ted_supply, raw_boamp_supply, raw_eprocurement_supply, raw_bekanntmachungsservice_supply
):
    keysets = {
        "TED": set(normalize.normalize_ted(raw_ted_supply)),
        "BOAMP": set(normalize.normalize_boamp(raw_boamp_supply)),
        "e-Procurement": set(normalize.normalize_eprocurement(raw_eprocurement_supply)),
        "Bekanntmachungsservice": set(normalize.normalize_bekanntmachungsservice(raw_bekanntmachungsservice_supply)),
    }
    canonical = keysets["TED"]
    for name, keys in keysets.items():
        assert keys == canonical, f"{name} schema diverges: {keys ^ canonical}"
