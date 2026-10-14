"""Step 51 — CR-010 §1: Belgium e-Procurement (BDA) connector.
Interface:
  connectors.eprocurement.build_params(cpv_codes, keywords, since, limit=100, offset=0) -> dict
  connectors.eprocurement.parse_response(json_data) -> list[dict]
  connectors.eprocurement.fetch(cpv_codes, keywords, since) -> list[dict]  # live, paginated
  normalize.normalize_eprocurement(raw) -> normalised record (SAME schema as TED/BOAMP)

Unlike test_06_ted_connector.py / test_10_boamp.py, there's no @pytest.mark.network
live-fetch test here: the real endpoint requires an EPROCUREMENT_AUTH_CLIENT_SECRET
this repo deliberately doesn't ship (see connectors/eprocurement.py's docstring on
why it isn't hardcoded), and this is an internal API on a government service CR-010
§1 asks us to be "a good guest" toward — not something to hit on every test run even
if a secret were configured. fetch()'s HTTP calls (both the token grant and the
search POST) are mocked instead, same as test_18_translate.py does for DeepL.
"""
import inspect
from datetime import date

import pytest
import normalize
from connectors import eprocurement, ted, boamp

CPV = ["45000000-7"]
KW = ["tent", "Zelt"]
SINCE = date(2026, 10, 1)


# ── build_params / parse_response (pure logic, no network) ─────────────────

def test_build_params_includes_cpv_codes():
    p = eprocurement.build_params(CPV, KW, SINCE)
    assert p["cpvCodes"] == CPV

def test_build_params_omits_cpv_key_when_none_given():
    # confirmed live: an unfiltered search sends no `cpvCodes` key at all,
    # not an empty list.
    p = eprocurement.build_params([], KW, SINCE)
    assert "cpvCodes" not in p

def test_build_params_never_sends_terms():
    # see module docstring: `terms`' real tokenization was never confirmed
    # live, so keywords is accepted for interface parity but not sent.
    p = eprocurement.build_params(CPV, KW, SINCE)
    assert "terms" not in p

def test_build_params_sends_paired_date_range():
    p = eprocurement.build_params(CPV, KW, SINCE)
    assert p["publicationDateFrom"] == "2026-10-01"
    assert "publicationDateTo" in p and p["publicationDateTo"]  # today, paired

def test_build_params_translates_offset_to_1_indexed_page():
    p = eprocurement.build_params(CPV, KW, SINCE, limit=25, offset=50)
    assert p["page"] == 3
    assert p["pageSize"] == 25

def test_build_params_first_page_is_page_1():
    p = eprocurement.build_params(CPV, KW, SINCE, limit=25, offset=0)
    assert p["page"] == 1

def test_parse_response_extracts_publications():
    data = {"publications": [{"referenceNumber": "A"}, {"referenceNumber": "B"}], "totalCount": 2}
    assert len(eprocurement.parse_response(data)) == 2

def test_parse_response_empty_when_no_publications():
    assert eprocurement.parse_response({"totalCount": 0}) == []
    assert eprocurement.parse_response({}) == []


def test_build_params_and_fetch_share_ted_connectors_argument_order():
    # extends test_10_boamp.py's same-named check to the third connector —
    # locks in (cpv_codes, keywords, since, ...) so a new connector can't
    # silently reintroduce the swap CR-010 found there.
    ted_params = list(inspect.signature(ted.fetch).parameters)[:3]
    boamp_params = list(inspect.signature(boamp.fetch).parameters)[:3]
    eprocurement_params = list(inspect.signature(eprocurement.fetch).parameters)[:3]
    assert ted_params == boamp_params == eprocurement_params == ["cpv_codes", "keywords", "since"]


# ── fetch() — mocked HTTP (token grant + search POST), no real network ─────

class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_requires_client_secret(monkeypatch):
    monkeypatch.setattr(eprocurement, "AUTH_CLIENT_SECRET", None)
    with pytest.raises(RuntimeError):
        eprocurement.fetch(CPV, KW, SINCE)


def test_fetch_sends_bearer_token_from_the_token_grant(monkeypatch):
    monkeypatch.setattr(eprocurement, "AUTH_CLIENT_SECRET", "test-secret")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "openid-connect/token" in url:
            return _FakeResponse({"access_token": "fake-jwt"})
        return _FakeResponse({"publications": [], "totalCount": 0})

    monkeypatch.setattr(eprocurement.requests, "post", fake_post)
    eprocurement.fetch(CPV, KW, SINCE)

    search_call = next(c for c in calls if c[0] == eprocurement.SEARCH_ENDPOINT)
    assert search_call[1]["headers"]["Authorization"] == "Bearer fake-jwt"
    assert search_call[1]["headers"]["User-Agent"] == eprocurement.USER_AGENT


def test_fetch_paginates_until_total_count_reached(monkeypatch):
    monkeypatch.setattr(eprocurement, "AUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(eprocurement, "REQUEST_DELAY_SECONDS", 0)
    pages = [
        {"publications": [{"referenceNumber": str(i)} for i in range(25)], "totalCount": 30},
        {"publications": [{"referenceNumber": str(i)} for i in range(25, 30)], "totalCount": 30},
    ]

    def fake_post(url, **kwargs):
        if "openid-connect/token" in url:
            return _FakeResponse({"access_token": "fake-jwt"})
        return _FakeResponse(pages.pop(0))

    monkeypatch.setattr(eprocurement.requests, "post", fake_post)
    recs = eprocurement.fetch(CPV, KW, SINCE, limit=25)
    assert len(recs) == 30
    assert not pages  # both pages consumed


def test_fetch_stops_at_max_records_safety_cap(monkeypatch):
    monkeypatch.setattr(eprocurement, "AUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(eprocurement, "REQUEST_DELAY_SECONDS", 0)

    def fake_post(url, **kwargs):
        if "openid-connect/token" in url:
            return _FakeResponse({"access_token": "fake-jwt"})
        return _FakeResponse({
            "publications": [{"referenceNumber": str(i)} for i in range(25)],
            "totalCount": 10_000,
        })

    monkeypatch.setattr(eprocurement.requests, "post", fake_post)
    recs = eprocurement.fetch(CPV, KW, SINCE, limit=25, max_records=40)
    assert len(recs) == 40


# ── normalize_eprocurement ───────────────────────────────────────────────────

def test_normalize_eprocurement_country_is_be(raw_eprocurement_supply):
    assert normalize.normalize_eprocurement(raw_eprocurement_supply)["country"] == "BE"

def test_normalize_eprocurement_source_string_matches_ui(raw_eprocurement_supply):
    # must match the string already used in the handoff docs/UI — see CR-010 §1
    assert normalize.normalize_eprocurement(raw_eprocurement_supply)["source"] == "e-Procurement"

def test_normalize_eprocurement_works_nature_is_works_category(raw_eprocurement_supply):
    assert normalize.normalize_eprocurement(raw_eprocurement_supply)["category"] == "Works"

def test_normalize_eprocurement_maps_dossier_title_to_tagline(raw_eprocurement_supply):
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["tag_line"] == "Bakgoot voorgevel Handelsschool"

def test_normalize_eprocurement_monolingual_language_is_dutch(raw_eprocurement_supply):
    # single publicationLanguages: ["NL"] notice -> 3-letter 'nld', not the
    # platform's own 2-letter 'NL' (run.py's `language == "eng"` gate and
    # every other source use the 3-letter convention).
    assert normalize.normalize_eprocurement(raw_eprocurement_supply)["language"] == "nld"

def test_normalize_eprocurement_internal_identifier_is_dossier_reference(raw_eprocurement_supply):
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["internal_identifier"] == "PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot"

def test_normalize_eprocurement_pub_number_is_publication_level_reference(raw_eprocurement_supply):
    # distinct from internal_identifier/dossier reference — see docstring in
    # normalize.py: pub_number changes per republish/corrigendum, dossier's
    # reference doesn't.
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["pub_number"] == "PPP0TA-235/6035/2026-002-De Handelsschool-Dakgoot_001"

def test_normalize_eprocurement_dedupes_main_and_additional_cpv(raw_eprocurement_supply):
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["cpv_codes"] == ["45261910-6", "45261210-9"]

def test_normalize_eprocurement_place_is_nuts_codes(raw_eprocurement_supply):
    assert normalize.normalize_eprocurement(raw_eprocurement_supply)["place"] == "BE231"

def test_normalize_eprocurement_deadline_is_vault_submission_deadline(raw_eprocurement_supply):
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["deadline"] == "2026-11-13T11:00:00"

def test_normalize_eprocurement_url_uses_publication_workspace_id(raw_eprocurement_supply):
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["url"] == "https://www.publicprocurement.be/publication-workspaces/1b5e777f-e009-401b-8a04-96737a274165"

def test_normalize_eprocurement_url_empty_without_workspace_id(raw_eprocurement_supply):
    raw = dict(raw_eprocurement_supply)
    del raw["publicationWorkspaceId"]
    assert normalize.normalize_eprocurement(raw)["url"] == ""

def test_normalize_eprocurement_no_value_fields_populated(raw_eprocurement_supply):
    # absent from the search response entirely — see module docstring.
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["value"] == ""
    assert r["value_currency"] == ""

def test_normalize_eprocurement_award_fields_deferred_to_none(raw_eprocurement_supply):
    # CR-010 §1: deliberately deferred, not an oversight.
    r = normalize.normalize_eprocurement(raw_eprocurement_supply)
    assert r["raw_award_winner"] is None
    assert r["raw_award_value"] is None
    assert r["raw_award_currency"] is None
    assert r["raw_award_detail"] is None


# ── multilingual notice (publicationLanguages: ["FR", "EN"]) ────────────────

def test_normalize_eprocurement_multilingual_picks_english_tagline(raw_eprocurement_multilingual):
    # _pick_lang's existing LANG_PREF prefers 'eng' first — unchanged here,
    # just reused via the 2->3-letter mapping (_eprocurement_lang_dict).
    r = normalize.normalize_eprocurement(raw_eprocurement_multilingual)
    assert r["tag_line"] == "PSE22001-10075 - 1"
    assert r["language"] == "eng"

def test_normalize_eprocurement_multilingual_buyer_name(raw_eprocurement_multilingual):
    assert normalize.normalize_eprocurement(raw_eprocurement_multilingual)["buyer"] == "Enabel"

def test_normalize_eprocurement_no_nuts_codes_yields_empty_place(raw_eprocurement_multilingual):
    assert normalize.normalize_eprocurement(raw_eprocurement_multilingual)["place"] == ""


# ── schema parity (CR-010 §3: extend to every normalizer) ──────────────────
# The full 4-way check (all of CR-010's sources) lives in
# test_52_bekanntmachungsservice.py now that connector exists too — this one
# stays as the narrower 3-way TED/BOAMP/e-Procurement check.

def test_eprocurement_ted_boamp_share_identical_schema(
    raw_eprocurement_supply, raw_ted_supply, raw_boamp_supply
):
    # the whole pipeline depends on every source producing the same keys —
    # test_10_boamp.py checks TED<->BOAMP; this is the 3-way version.
    ted_keys = set(normalize.normalize_ted(raw_ted_supply))
    boamp_keys = set(normalize.normalize_boamp(raw_boamp_supply))
    eprocurement_keys = set(normalize.normalize_eprocurement(raw_eprocurement_supply))
    assert ted_keys == boamp_keys == eprocurement_keys
