"""CR-013 Stage 3 (§4) — manual tender entry: create, edit, delete, restore.

Covers the CR's §7 items for this stage: derived fields come from the same
code path as a scraped record; a below-floor manual tender is stored, flagged
and visible; editing every user field leaves `hash` unchanged; identity is
stamped server-side; scraped tenders cannot be edited; manual records survive
a full pipeline run.
"""
import copy
import threading
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import api
import manual_tenders
import normalize
import run
import store
from conftest import TEST_ACCOUNT_NAME, TEST_ACCOUNT_NAME_B, TEST_TENANT_ID, make_identity

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0, "SEK": 11.23, "GBP": 0.85}}
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(manual_tenders, "_fx_rates", lambda: FX_RATES)
    monkeypatch.setattr(run.translate, "translate_cached", lambda _c, t: ("EN: " + (t or ""), "ok"))
    c = store.init_db(db_path)
    store.ensure_tenant(c, TEST_TENANT_ID)
    return c


@pytest.fixture
def site(conn):
    return store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")


def _fields(site, **over):
    f = {"origin_site_id": site["id"], "url": "https://www.dtvp.de/notice/123",
         "tag_line": "Supply of military shelter tents", "description": "Lot 1: 200 tents",
         "buyer": "Bundeswehr BAAINBw", "country": "DEU", "place": "Koblenz", "category": "Supply",
         "procedure": "open", "pub_date": "2026-07-10", "deadline": "2027-09-30",
         "cpv_codes": ["39522530"], "value": "500000", "value_currency": "EUR", "language": "eng"}
    f.update(over)
    return f


def _create(conn, site, **over):
    return manual_tenders.create(conn, TEST_TENANT_ID, TEST_ACCOUNT_NAME, _fields(site, **over),
                                 now=NOW, fx_rates=FX_RATES)


# ── create ───────────────────────────────────────────────────────────────────

def test_create_assigns_identity_server_side(conn, site):
    t = _create(conn, site)
    assert t["pub_number"] == "MAN-2026-0001"
    assert (t["source"], t["entry_method"], t["status"]) == ("Manual", "manual", "new")
    assert t["hash"] == normalize.record_hash({"source": "Manual", "pub_number": "MAN-2026-0001"})
    assert (t["created_by"], t["created_at"]) == (TEST_ACCOUNT_NAME, "2026-07-15T00:00:00+00:00")
    assert t["first_seen"] == "2026-07-15" and t["origin_site_id"] == site["id"]
    assert _create(conn, site)["pub_number"] == "MAN-2026-0002"


def test_derived_fields_equal_the_scraped_path(conn, site):
    """Same input through run.process_record (the scraped path) and through
    manual create must derive identical matching/value/filter/class fields."""
    t = _create(conn, site, value="2000000", value_currency="SEK")
    scraped = {k: copy.deepcopy(t[k]) for k in manual_tenders.USER_FIELDS if k != "origin_site_id"}
    scraped.update({"source": "TED", "pub_number": "999-2026"})
    full_keywords, cpv_set, exclusions = run.matching_context(conn, TEST_TENANT_ID)
    run.process_record(conn, TEST_TENANT_ID, scraped, full_keywords, cpv_set, exclusions,
                       NOW, FX_RATES, NOW.date().isoformat())
    s = store.get_record(conn, TEST_TENANT_ID, "999-2026")
    for k in ("matched_terms", "match_source", "value_eur", "fx_rate_date", "exclude_reason",
              "notice_type", "awarded_to"):
        assert t[k] == s[k], k
    assert t["value_eur"]  # SEK really was converted


def test_below_floor_manual_tender_is_stored_flagged_and_visible(conn, site):
    t = _create(conn, site, value="10000", value_currency="EUR")
    assert t["exclude_reason"] == "below_value_floor"
    shown = api.list_tenders(identity=make_identity(), limit=1000, offset=0, sort="deadline",
                             source=None, category=None, match_source=None, country=None, q=None,
                             status=None, has_deadline=None, notice_type=None, include_excluded=False)
    assert t["pub_number"] in [r["pub_number"] for r in shown["results"]]


def test_past_deadline_is_accepted(conn, site):
    t = _create(conn, site, pub_date="2026-05-01", deadline="2026-06-01")
    assert store.get_record(conn, TEST_TENANT_ID, t["pub_number"]) is not None


def test_non_english_is_translated_through_the_shared_path(conn, site):
    t = _create(conn, site, language="deu", tag_line="Lieferung von Zelten", description="Zelte")
    assert (t["tag_line_en"], t["description_en"], t["translation_status"]) == (
        "EN: Lieferung von Zelten", "EN: Zelte", "ok")


def test_english_is_left_untranslated_like_scraped(conn, site):
    t = _create(conn, site)
    assert (t["tag_line_en"], t["translation_status"]) == ("", "")


@pytest.mark.parametrize("over, field", [
    ({"tag_line": "   "}, "tag_line"),
    ({"cpv_codes": ["3952"]}, "cpv_codes"),
    ({"deadline": "31/12/2026"}, "deadline"),
    ({"deadline": "2026-02-30"}, "deadline"),
    ({"pub_date": "2026-08-01", "deadline": "2026-07-01"}, "deadline"),
    ({"value": "lots"}, "value"),
    ({"value": "1000", "value_currency": ""}, "value_currency"),
    ({"language": "german"}, "language"),
    ({"origin_site_id": 99999}, "origin_site_id"),
])
def test_bad_input_names_the_field(conn, site, over, field):
    with pytest.raises(manual_tenders.ManualTenderError) as e:
        _create(conn, site, **over)
    assert e.value.field == field


def test_input_is_normalised(conn, site):
    t = _create(conn, site, cpv_codes=["39522530-1", "39522530", "35113400"],
                value="1,250,000", value_currency="eur", tag_line="  Supply   of tents ")
    assert t["cpv_codes"] == ["39522530", "35113400"]
    assert (t["value"], t["value_currency"], t["tag_line"]) == ("1250000", "EUR", "Supply of tents")


def test_another_tenants_origin_site_is_refused(conn):
    store.ensure_tenant(conn, 2)
    theirs = store.add_origin_site(conn, 2, "Their portal")
    with pytest.raises(manual_tenders.ManualTenderError) as e:
        _create(conn, theirs)
    assert e.value.field == "origin_site_id"


def test_api_refuses_client_supplied_identity_or_derived_fields(site):
    for smuggled in ("pub_number", "source", "created_by", "status", "exclude_reason", "entry_method"):
        with pytest.raises(ValidationError):
            api.ManualTenderBody(**_fields(site), **{smuggled: "x"})


def test_api_create_stamps_the_caller(conn, site):
    body = api.ManualTenderBody(**_fields(site))
    t = api.create_manual_tender(body, identity=make_identity(account_name=TEST_ACCOUNT_NAME_B))
    assert t["created_by"] == TEST_ACCOUNT_NAME_B


def test_api_bad_input_is_a_422_naming_the_field(conn, site):
    with pytest.raises(api.HTTPException) as e:
        api.create_manual_tender(api.ManualTenderBody(**_fields(site, deadline="soon")),
                                 identity=make_identity())
    assert e.value.status_code == 422 and e.value.detail["field"] == "deadline"


def test_concurrent_creates_get_distinct_numbers(conn, site):
    made, errors = [], []

    def worker():
        try:
            for _ in range(3):
                made.append(_create(conn, site)["pub_number"])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(set(made)) == 15
    assert len(store.all_records(conn, TEST_TENANT_ID)) == 15


# ── edit ─────────────────────────────────────────────────────────────────────

def test_editing_every_user_field_leaves_hash_unchanged(conn, site):
    t = _create(conn, site)
    other = store.add_origin_site(conn, TEST_TENANT_ID, "UNGM")
    new = {"origin_site_id": other["id"], "url": "https://ungm.org/x", "tag_line": "Tents, corrected",
           "description": "Lot 1 and 2", "buyer": "UNHCR", "country": "CHE", "place": "Geneva",
           "category": "Services", "procedure": "restricted", "pub_date": "2026-07-11",
           "deadline": "2026-10-15", "cpv_codes": ["35113400"], "value": "750000",
           "value_currency": "GBP", "language": "fra"}
    edited, changed = manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME_B,
                                            new, now=NOW, fx_rates=FX_RATES)
    assert (edited["hash"], edited["pub_number"], edited["source"]) == (t["hash"], t["pub_number"], "Manual")
    assert (edited["created_by"], edited["created_at"]) == (t["created_by"], t["created_at"])
    for k, v in new.items():
        assert edited[k] == v, k
    assert set(manual_tenders.USER_FIELDS) <= set(changed)
    assert len(store.all_records(conn, TEST_TENANT_ID)) == 1  # edited in place, nothing orphaned


def test_edit_re_derives_and_records_history(conn, site):
    t = _create(conn, site)
    assert t["exclude_reason"] == ""
    edited, changed = manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME_B,
                                            _fields(site, value="1000"), now=NOW, fx_rates=FX_RATES)
    assert edited["exclude_reason"] == "below_value_floor"
    assert set(changed) == {"value", "exclude_reason"}
    hist = store.get_tender_history(conn, TEST_TENANT_ID, t["pub_number"])
    by_field = {h["field"]: h for h in hist}
    assert (by_field["value"]["old_value"], by_field["value"]["new_value"]) == ("500000", "1000")
    assert by_field["manual_edit"]["new_value"] == TEST_ACCOUNT_NAME_B


def test_edit_with_no_change_records_nothing(conn, site):
    t = _create(conn, site)
    _, changed = manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME,
                                       _fields(site), now=NOW, fx_rates=FX_RATES)
    assert changed == [] and store.get_tender_history(conn, TEST_TENANT_ID, t["pub_number"]) == []


def test_text_edit_refreshes_translation(conn, site):
    t = _create(conn, site, language="deu", tag_line="Zelte", description="Alt")
    edited, _ = manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME,
                                      _fields(site, language="deu", tag_line="Zelte", description="Neu"),
                                      now=NOW, fx_rates=FX_RATES)
    assert edited["description_en"] == "EN: Neu"


def test_edit_keeps_review_state(conn, site):
    t = _create(conn, site)
    store.set_status(conn, TEST_TENANT_ID, t["pub_number"], "shortlisted")
    edited, _ = manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME,
                                      _fields(site, buyer="Other"), now=NOW, fx_rates=FX_RATES)
    assert edited["status"] == "shortlisted"


def test_scraped_tenders_cannot_be_edited_or_deleted(conn, site, raw_ted_supply):
    rec = normalize.normalize_ted(raw_ted_supply)
    store.upsert(conn, TEST_TENANT_ID, rec)
    with pytest.raises(manual_tenders.NotEditable):
        manual_tenders.update(conn, TEST_TENANT_ID, rec["pub_number"], TEST_ACCOUNT_NAME, _fields(site),
                              now=NOW, fx_rates=FX_RATES)
    with pytest.raises(manual_tenders.NotEditable):
        manual_tenders.delete(conn, TEST_TENANT_ID, rec["pub_number"], TEST_ACCOUNT_NAME)
    with pytest.raises(LookupError):  # the store refuses it too, independently of the caller
        store.update_manual_tender(conn, TEST_TENANT_ID, rec["pub_number"], dict(rec, buyer="x"),
                                   "me", ["buyer"])
    assert store.get_record(conn, TEST_TENANT_ID, rec["pub_number"])["buyer"] == rec["buyer"]
    with pytest.raises(api.HTTPException) as e:
        api.update_manual_tender(rec["pub_number"], api.ManualTenderBody(**_fields(site)),
                                 identity=make_identity())
    assert e.value.status_code == 404


def test_other_tenant_cannot_edit(conn, site):
    t = _create(conn, site)
    store.ensure_tenant(conn, 2)
    theirs = store.add_origin_site(conn, 2, "DTVP")
    with pytest.raises(manual_tenders.NotEditable):
        manual_tenders.update(conn, 2, t["pub_number"], "intruder", _fields(theirs), now=NOW, fx_rates=FX_RATES)


# ── delete / restore ─────────────────────────────────────────────────────────

def test_soft_delete_hides_everywhere_and_restore_brings_it_back(conn, site):
    t = _create(conn, site)
    store.ensure_pipeline_entry(conn, TEST_TENANT_ID, t["pub_number"])
    store.set_pipeline_entry(conn, TEST_TENANT_ID, t["pub_number"], {"notes": "keep me"})
    store.set_status(conn, TEST_TENANT_ID, t["pub_number"], "shortlisted")
    assert [e["pub_number"] for e in store.get_pipeline_entries(conn, TEST_TENANT_ID)] == [t["pub_number"]]
    manual_tenders.delete(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME_B)
    assert store.all_records(conn, TEST_TENANT_ID) == []
    with pytest.raises(api.HTTPException):
        api.get_tender(t["pub_number"], include_excluded=True, identity=make_identity())
    gone = store.get_record(conn, TEST_TENANT_ID, t["pub_number"], include_deleted=True)
    assert gone["deleted_by"] == TEST_ACCOUNT_NAME_B and gone["deleted_at"]
    with pytest.raises(manual_tenders.NotEditable):  # can't edit a deleted one
        manual_tenders.update(conn, TEST_TENANT_ID, t["pub_number"], "x", _fields(site),
                              now=NOW, fx_rates=FX_RATES)
    from sqlalchemy import select
    from schema import pipeline
    with conn.connect() as cx:  # the pipeline row is kept through the delete …
        assert cx.execute(select(pipeline.c.notes).where(pipeline.c.pub_number == t["pub_number"])).scalar() == "keep me"
    assert store.get_pipeline_entries(conn, TEST_TENANT_ID) == []  # … but the tender leaves the pipeline view
    back = manual_tenders.restore(conn, TEST_TENANT_ID, t["pub_number"], TEST_ACCOUNT_NAME)
    assert back["deleted_at"] is None and back["hash"] == t["hash"]
    fields = [h["field"] for h in store.get_tender_history(conn, TEST_TENANT_ID, t["pub_number"])]
    assert fields[:2] == ["restored", "deleted"]


def test_deleting_twice_or_restoring_a_live_one_is_refused(conn, site):
    t = _create(conn, site)
    with pytest.raises(manual_tenders.NotEditable):
        manual_tenders.restore(conn, TEST_TENANT_ID, t["pub_number"], "x")
    manual_tenders.delete(conn, TEST_TENANT_ID, t["pub_number"], "x")
    with pytest.raises(manual_tenders.NotEditable):
        manual_tenders.delete(conn, TEST_TENANT_ID, t["pub_number"], "x")


# ── the regression that matters most ─────────────────────────────────────────

def test_manual_tenders_survive_a_full_pipeline_run(tmp_path, monkeypatch, raw_ted_supply):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(run.translate, "translate_cached", lambda _c, t: ("EN: " + (t or ""), "ok"))
    monkeypatch.setattr(run.translate, "check_health", lambda: ("ok", None))
    monkeypatch.setattr(run, "build_report", lambda *a: None)
    c = store.init_db(db)
    store.ensure_tenant(c, TEST_TENANT_ID)
    s = store.add_origin_site(c, TEST_TENANT_ID, "DTVP")
    kept = manual_tenders.create(c, TEST_TENANT_ID, "a", _fields(s), now=NOW, fx_rates=FX_RATES)
    flagged = manual_tenders.create(c, TEST_TENANT_ID, "a", _fields(s, value="10"), now=NOW, fx_rates=FX_RATES)
    past = manual_tenders.create(c, TEST_TENANT_ID, "a", _fields(s, pub_date="2026-01-01",
                                                                  deadline="2026-02-01"),
                                 now=NOW, fx_rates=FX_RATES)
    before = {t["pub_number"]: t for t in (kept, flagged, past)}
    src = {"name": "TED", "fetch": lambda: [raw_ted_supply], "normalize": normalize.normalize_ted}
    for _ in range(2):
        run.run_pipeline([src], db, str(tmp_path / "r.xlsx"), tenant_id=TEST_TENANT_ID,
                         fx_rates=FX_RATES, now=NOW)
    after = {r["pub_number"]: r for r in store.all_records(c, TEST_TENANT_ID)}
    for pub, b in before.items():
        assert pub in after, pub
        for k in manual_tenders.USER_FIELDS + ("hash", "exclude_reason", "entry_method", "status"):
            assert after[pub][k] == b[k], (pub, k)


# ── routing: the real request resolution, not just the handler functions ─────

@pytest.mark.parametrize("method, path, handler", [
    ("POST", "/api/tenders/manual", "create_manual_tender"),
    ("PUT", "/api/tenders/manual/MAN-2026-0001", "update_manual_tender"),
    ("DELETE", "/api/tenders/manual/MAN-2026-0001", "delete_manual_tender"),
    ("POST", "/api/tenders/manual/MAN-2026-0001/restore", "restore_manual_tender"),
    ("GET", "/api/origin-sites", "list_origin_sites"),
    ("POST", "/api/origin-sites", "create_origin_site"),
    ("GET", "/api/tenders/MAN-2026-0001", "get_tender"),  # still reaches the normal detail route
])
def test_routes_resolve_to_the_right_handler(method, path, handler):
    from starlette.routing import Match
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "query_string": b"",
             "headers": []}
    full = [r for r in api.app.routes if getattr(r, "matches", None) and r.matches(scope)[0] == Match.FULL]
    assert full and full[0].endpoint.__name__ == handler
