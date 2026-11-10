"""CR-013 Stage 5 (§6) — the manual marker everywhere downstream, and how
cross-portal dedup treats a manual tender.

Marker: the Review Queue API filters on entry_method; the Excel report and
the daily digest carry "Manual" (plus "would be excluded: …" when a rule
would have hidden it).

Dedup (§9 Q5): signal 2 (description similarity) only compares two tenders
that share an exact CPV code AND both have a description_en — which only
non-English records get (DeepL). These tests pin the observed behaviour:
a German manual tender pairs with a German scraped one; an English manual
tender, or one without CPV codes, does not.
"""
from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook

import api
import dedup
import digest
import manual_tenders
import report
import run
import store
from conftest import TEST_TENANT_ID, make_identity

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0}}
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)
DESC_DE = "Lieferung von 200 Mannschaftszelten mit Bodenplane und Heizung fuer die Bundeswehr in Koblenz"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(manual_tenders, "_fx_rates", lambda: FX_RATES)
    # deterministic "translation": identical German text -> identical English
    monkeypatch.setattr(run.translate, "translate_cached", lambda _c, t: ("EN " + (t or ""), "ok"))
    c = store.init_db(db_path)
    store.ensure_tenant(c, TEST_TENANT_ID)
    return c


def _manual(conn, **over):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    f = {"origin_site_id": site["id"], "tag_line": "Zelte", "description": DESC_DE, "buyer": "BAAINBw",
         "category": "Supply", "deadline": "2027-09-30", "cpv_codes": ["39522530"], "value": "500000",
         "value_currency": "EUR", "language": "deu"}
    f.update(over)
    return manual_tenders.create(conn, TEST_TENANT_ID, "a", f, now=NOW, fx_rates=FX_RATES)


def _scraped(conn, pub, **over):
    r = {"source": "Bekanntmachungsservice", "pub_number": pub, "tag_line": "Zelte", "description": DESC_DE,
         "buyer": "BAAINBw", "country": "DE", "category": "Supply", "deadline": "2027-09-30",
         "cpv_codes": ["39522530"], "matched_terms": [], "match_source": "cpv", "language": "deu",
         "tag_line_en": "", "description_en": "EN " + DESC_DE, "translation_status": "ok",
         "internal_identifier": None, "url": "", "status": "new", "exclude_reason": ""}
    r.update(over)
    store.upsert(conn, TEST_TENANT_ID, r)
    return r


def _list(**kw):
    return [r["pub_number"] for r in api.list_tenders(
        identity=make_identity(), limit=1000, offset=0, sort="deadline", source=None, category=None,
        match_source=None, country=None, q=None, status=None, has_deadline=None, notice_type=None,
        include_excluded=False, **kw)["results"]]


# ── Review Queue facet ───────────────────────────────────────────────────────

def test_entry_method_facet(conn):
    m = _manual(conn)
    _scraped(conn, "BKMS-1")
    assert _list(entry_method="manual") == [m["pub_number"]]
    assert _list(entry_method="scraped") == ["BKMS-1"]
    assert set(_list(entry_method=None)) == {m["pub_number"], "BKMS-1"}


# ── report and digest ────────────────────────────────────────────────────────

def test_entry_label():
    assert report.entry_label({"entry_method": "scraped"}) == ""
    assert report.entry_label({}) == ""
    assert report.entry_label({"entry_method": "manual"}) == "Manual"
    assert report.entry_label({"entry_method": "manual", "exclude_reason": "below_value_floor"}) == \
        "Manual · would be excluded: below_value_floor"


def test_report_carries_the_manual_marker(conn, tmp_path):
    m = _manual(conn, value="10")  # below the floor: flagged, still reported
    _scraped(conn, "BKMS-1")
    out = tmp_path / "r.xlsx"
    report.build_report(store.all_records(conn, TEST_TENANT_ID), {}, str(out))
    ws = load_workbook(out)["Supply"]
    header = [c.value for c in ws[1]]
    entry_col, pub_col = header.index("Entry"), header.index("Pub. number")
    rows = {r[pub_col].value: r[entry_col].value for r in ws.iter_rows(min_row=2)}
    assert rows[m["pub_number"]] == "Manual · would be excluded: below_value_floor"
    assert rows["BKMS-1"] in ("", None)


def test_digest_carries_the_manual_marker(conn):
    m = _manual(conn)
    _scraped(conn, "BKMS-2", first_seen=m["first_seen"])
    body = digest.build_daily_digest(conn, TEST_TENANT_ID, today=m["first_seen"])
    lines = {pub: line for line in body.splitlines() for pub in (m["pub_number"], "BKMS-2") if pub in line}
    assert lines[m["pub_number"]].startswith("- [Manual] ")
    assert not lines["BKMS-2"].startswith("- [")


# ── dedup signal 2 with manual tenders (§9 Q5) ───────────────────────────────

def _pairs(conn):
    return {(a, b, t) for a, b, t, _ in dedup.find_cross_portal_duplicates(
        store.all_records(conn, TEST_TENANT_ID))}


def test_german_manual_pairs_with_german_scraped_by_similarity(conn):
    m = _manual(conn)
    _scraped(conn, "BKMS-1")
    assert (m["description_en"]) == "EN " + DESC_DE  # translated by the shared route
    assert ("BKMS-1", m["pub_number"], "similarity") in _pairs(conn)


def test_flagged_manual_still_pairs(conn):
    m = _manual(conn, value="10")  # below floor: excluded, but never hidden
    _scraped(conn, "BKMS-1")
    assert ("BKMS-1", m["pub_number"], "similarity") in _pairs(conn)


def test_english_manual_is_not_compared_observed_gap(conn):
    """Observed gap: English records never get a description_en, so signal 2
    skips them — scraped English pairs (TED-eng, Find a Tender, Contracts
    Finder, CanadaBuys) included. Pinned so a fix is a visible change."""
    desc = "Supply of 200 tents with groundsheets and heating"
    _manual(conn, language="eng", description=desc)
    _scraped(conn, "FTS-1", source="Find a Tender", language="eng", description=desc, description_en="")
    assert _pairs(conn) == set()


def test_manual_without_cpv_is_not_compared_observed_gap(conn):
    """Observed gap: signal 2 only compares tenders that share a CPV code; a
    manual entry with no CPV codes is never compared."""
    _manual(conn, cpv_codes=[])
    _scraped(conn, "BKMS-1")
    assert _pairs(conn) == set()
