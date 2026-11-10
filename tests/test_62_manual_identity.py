"""CR-013 Stage 2 (§3) — manual tender identity and the visibility rule.

The most important tests here are the regression ones: `visibility.is_hidden`
replaced `r.get("exclude_reason")` at seven call sites (api list + detail,
dedup same-source + cross-portal, run translation + screening + report). For
scraped tenders every one of those sites must behave exactly as before —
excluded means hidden — or construction/rental notices come back into the
Review Queue. Each site is checked two ways:

  * directly: an excluded scraped tender is absent / untouched;
  * against an oracle: with only scraped records in play, the new code's
    output equals the output under the old predicate, swapped back in.
"""
import threading
from datetime import datetime, timezone

import pytest

import api
import dedup
import run
import store
import visibility
from conftest import TEST_TENANT_ID, make_identity

OLD_RULE = staticmethod(lambda r: bool(r.get("exclude_reason")))  # pre-CR-013 behaviour
FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0}}
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _rec(pub, *, source="TED", entry_method="scraped", exclude_reason="", language="eng",
         buyer="Ministry of Defence", tag_line="Supply of shelter tents",
         description="Supply of military shelter tents and accessories", internal_identifier=None,
         deadline="2026-12-31", cpv=("39522530",), **extra):
    r = {"source": source, "pub_number": pub, "tag_line": tag_line, "description": description,
         "buyer": buyer, "country": "DE", "place": "", "category": "Supply", "procedure": "open",
         "pub_date": "2026-07-01", "deadline": deadline, "cpv_codes": list(cpv),
         "matched_terms": ["tent"], "match_source": "both", "url": "https://example.org/" + pub,
         "status": "new", "exclude_reason": exclude_reason, "language": language,
         "tag_line_en": tag_line if language == "eng" else "", "description_en": description,
         "internal_identifier": internal_identifier, "entry_method": entry_method}
    r.update(extra)
    return r


SCRAPED_EXCLUDED = _rec("S-EXCL", exclude_reason="construction_works", internal_identifier="REF-1")
SCRAPED_KEPT = _rec("S-KEPT", source="BOAMP", internal_identifier="REF-1")
MANUAL_EXCLUDED = _rec("MAN-2026-0001", source="Manual", entry_method="manual",
                       exclude_reason="below_value_floor", internal_identifier="REF-2")
MANUAL_KEPT = _rec("MAN-2026-0002", source="Manual", entry_method="manual")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    c = store.init_db(db_path)
    store.ensure_tenant(c, TEST_TENANT_ID)
    return c


def _store(conn, *records):
    for r in records:
        store.upsert(conn, TEST_TENANT_ID, r)


# ── the rule itself ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("record, hidden", [
    ({"exclude_reason": "construction_works"}, True),
    ({"exclude_reason": "rental", "entry_method": "scraped"}, True),
    ({"exclude_reason": ""}, False),
    ({}, False),
    ({"exclude_reason": "below_value_floor", "entry_method": "manual"}, False),
    ({"exclude_reason": "", "entry_method": "manual"}, False),
])
def test_is_hidden_truth_table(record, hidden):
    assert visibility.is_hidden(record) is hidden


def test_is_hidden_matches_old_rule_for_every_scraped_reason():
    for reason in ("", "container_modular_prefab", "rental", "below_value_floor",
                   "construction_works", "no_core_signal", "superseded", "deadline_too_close"):
        r = {"exclude_reason": reason, "entry_method": "scraped"}
        assert visibility.is_hidden(r) == OLD_RULE(r)


# ── sites 1-2: Review Queue list and detail (api.py) ─────────────────────────

def _list(**kw):
    return [r["pub_number"] for r in api.list_tenders(
        identity=make_identity(), limit=1000, offset=0, sort="deadline", source=None, category=None,
        match_source=None, country=None, q=None, status=None, has_deadline=None,
        notice_type=None, **kw)["results"]]


def test_api_list_hides_excluded_scraped_and_shows_excluded_manual(conn):
    _store(conn, SCRAPED_EXCLUDED, SCRAPED_KEPT, MANUAL_EXCLUDED, MANUAL_KEPT)
    shown = _list(include_excluded=False)
    assert "S-EXCL" not in shown
    assert {"S-KEPT", "MAN-2026-0001", "MAN-2026-0002"} <= set(shown)
    assert set(_list(include_excluded=True)) == {"S-EXCL", "S-KEPT", "MAN-2026-0001", "MAN-2026-0002"}


def test_api_list_scraped_only_matches_old_rule(conn, monkeypatch):
    _store(conn, SCRAPED_EXCLUDED, SCRAPED_KEPT,
           _rec("S-RENT", exclude_reason="rental"), _rec("S-OK2", buyer="Other"))
    new = _list(include_excluded=False)
    monkeypatch.setattr(visibility, "is_hidden", OLD_RULE)
    assert new == _list(include_excluded=False)
    assert set(new) == {"S-KEPT", "S-OK2"}


def test_api_detail_404s_excluded_scraped_but_returns_excluded_manual(conn):
    _store(conn, SCRAPED_EXCLUDED, MANUAL_EXCLUDED)
    with pytest.raises(api.HTTPException) as e:
        api.get_tender("S-EXCL", include_excluded=False, identity=make_identity())
    assert e.value.status_code == 404
    assert api.get_tender("S-EXCL", include_excluded=True, identity=make_identity())["pub_number"] == "S-EXCL"
    got = api.get_tender("MAN-2026-0001", include_excluded=False, identity=make_identity())
    assert got["exclude_reason"] == "below_value_floor"  # stored and flagged, still visible


# ── sites 3-4: dedup candidates (dedup.py) ───────────────────────────────────

def test_same_source_dedup_ignores_excluded_scraped_but_groups_excluded_manual():
    a = _rec("S-A", exclude_reason="rental")
    b = _rec("S-B", exclude_reason="rental")
    assert dedup.find_duplicate_groups([a, b]) == []
    m1 = _rec("MAN-2026-0003", source="Manual", entry_method="manual", exclude_reason="below_value_floor")
    m2 = _rec("MAN-2026-0004", source="Manual", entry_method="manual", exclude_reason="below_value_floor")
    groups = dedup.find_duplicate_groups([m1, m2])
    assert len(groups) == 1 and {r["pub_number"] for r in groups[0]} == {"MAN-2026-0003", "MAN-2026-0004"}


def test_cross_portal_dedup_ignores_excluded_scraped_but_pairs_excluded_manual():
    assert dedup.find_cross_portal_duplicates([SCRAPED_EXCLUDED, SCRAPED_KEPT]) == []
    manual = dict(MANUAL_EXCLUDED, internal_identifier="REF-1")
    pairs = dedup.find_cross_portal_duplicates([manual, SCRAPED_KEPT])
    assert [(a, b) for a, b, *_ in pairs] == [("MAN-2026-0001", "S-KEPT")]


def test_dedup_scraped_only_matches_old_rule(monkeypatch):
    records = [SCRAPED_EXCLUDED, SCRAPED_KEPT, _rec("S-C", source="TenderNed", internal_identifier="REF-1"),
               _rec("S-D", exclude_reason="rental", buyer="Ministry of Defence"),
               _rec("S-E", buyer="Ministry of Defence")]
    new_groups = dedup.find_duplicate_groups(records)
    new_pairs = dedup.find_cross_portal_duplicates(records)
    monkeypatch.setattr(visibility, "is_hidden", OLD_RULE)
    assert new_groups == dedup.find_duplicate_groups(records)
    assert new_pairs == dedup.find_cross_portal_duplicates(records)
    assert new_pairs  # the scenario does exercise a pair


# ── sites 5-7: the run's translation, screening and report passes (run.py) ───

def _run_with_spies(tmp_path, monkeypatch, scraped, manual):
    """One pipeline run. `scraped` arrive through a fake source (already
    normalised, so the real tag/filter path computes their exclude_reason);
    `manual` are stored beforehand as manual entries."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "t.db")
    c = store.init_db(db)
    store.ensure_tenant(c, TEST_TENANT_ID)
    _store(c, *manual)

    seen = {"translated": set(), "screened": set(), "reported": set()}
    texts = {}
    for r in scraped + manual:
        texts[r["tag_line"]] = r["pub_number"]

    def fake_translate(_conn, text):
        if text in texts:
            seen["translated"].add(texts[text])
        return "EN " + (text or ""), "ok"

    monkeypatch.setattr(run.translate, "translate_cached", fake_translate)
    monkeypatch.setattr(run.translate, "check_health", lambda: ("ok", None))
    monkeypatch.setattr(store, "get_tenant_screening_questions",
                        lambda *_a, **_k: [{"id": 1, "question": "q"}])
    monkeypatch.setattr(store, "missing_screening_question_ids", lambda *_a: [1])

    def fake_answer(rec, questions):
        seen["screened"].add(rec["pub_number"])
        return {}

    monkeypatch.setattr(run.screening, "answer_questions", fake_answer)
    monkeypatch.setattr(run, "build_report",
                        lambda surfaced, health, out: seen["reported"].update(r["pub_number"] for r in surfaced))
    src = {"name": "TED", "fetch": lambda: [dict(r) for r in scraped], "normalize": lambda r: r}
    run.run_pipeline([src], db, str(tmp_path / "r.xlsx"), tenant_id=TEST_TENANT_ID,
                     fx_rates=FX_RATES, now=NOW)
    return seen, c


def _scraped_for_run():
    # Real filters decide these: CPV division 45 -> construction_works.
    excluded = _rec("S-RUN-EXCL", language="deu", tag_line="Bauarbeiten Zelthalle", cpv=("45000000",),
                    exclude_reason="", matched_terms=[], match_source=None)
    kept = _rec("S-RUN-KEPT", language="deu", tag_line="Lieferung von Zelten",
                exclude_reason="", matched_terms=[], match_source=None)
    return [excluded, kept]


def test_run_skips_excluded_scraped_and_processes_excluded_manual(tmp_path, monkeypatch):
    manual = [_rec("MAN-2026-0009", source="Manual", entry_method="manual", language="deu",
                   tag_line="Manuell erfasst Zelte", exclude_reason="below_value_floor",
                   translation_status="")]
    seen, c = _run_with_spies(tmp_path, monkeypatch, _scraped_for_run(), manual)
    stored = {r["pub_number"]: r for r in store.all_records(c, TEST_TENANT_ID)}
    assert stored["S-RUN-EXCL"]["exclude_reason"] == "construction_works"  # filters really ran
    for site in ("translated", "screened", "reported"):
        assert "S-RUN-EXCL" not in seen[site], site
        assert "MAN-2026-0009" in seen[site], site
    # survives the run, flag untouched
    assert stored["MAN-2026-0009"]["exclude_reason"] == "below_value_floor"
    assert stored["MAN-2026-0009"]["entry_method"] == "manual"


def test_run_scraped_only_matches_old_rule(tmp_path, monkeypatch):
    new, _ = _run_with_spies(tmp_path / "new", monkeypatch, _scraped_for_run(), [])
    monkeypatch.setattr(visibility, "is_hidden", OLD_RULE)
    old, _ = _run_with_spies(tmp_path / "old", monkeypatch, _scraped_for_run(), [])
    assert new == old
    assert "S-RUN-EXCL" not in new["reported"] and "S-RUN-KEPT" in new["reported"]


# ── rescore never hides a manual record ──────────────────────────────────────

def test_rescore_pending_keeps_manual_visible(conn):
    m = _rec("MAN-2026-0010", source="Manual", entry_method="manual", cpv=("45000000",),
             matched_terms=[], match_source=None)
    _store(conn, m)
    store.rescore_pending(conn, TEST_TENANT_ID)
    (row,) = [r for r in store.all_records(conn, TEST_TENANT_ID) if r["pub_number"] == "MAN-2026-0010"]
    assert row["exclude_reason"]          # the rule computed an exclusion …
    assert "MAN-2026-0010" in _list(include_excluded=False)  # … and it is still shown


# ── entry_method / new columns round-trip ────────────────────────────────────

NORMALIZERS = ["ted", "boamp", "eprocurement", "bekanntmachungsservice", "find_a_tender",
               "contracts_finder", "canadabuys", "tenderned", "philgeps"]


@pytest.mark.parametrize("name", NORMALIZERS)
def test_every_normalizer_round_trips_as_scraped(name, request, conn):
    import normalize
    rec = getattr(normalize, f"normalize_{name}")(request.getfixturevalue(f"raw_{name}_supply"))
    assert "entry_method" not in rec  # normalize core untouched; store supplies the default
    store.upsert(conn, TEST_TENANT_ID, rec)
    (row,) = store.all_records(conn, TEST_TENANT_ID)
    assert row["entry_method"] == "scraped"
    for col in ("origin_site_id", "created_by", "created_at", "deleted_at", "deleted_by"):
        assert row[col] is None, col


def test_manual_fields_round_trip(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", created_by="tester@example.com")
    _store(conn, dict(MANUAL_KEPT, origin_site_id=site["id"], created_by="tester@example.com",
                      created_at="2026-11-10T09:00:00+00:00"))
    (row,) = store.all_records(conn, TEST_TENANT_ID)
    assert (row["entry_method"], row["origin_site_id"], row["created_by"], row["created_at"]) == (
        "manual", site["id"], "tester@example.com", "2026-11-10T09:00:00+00:00")


def test_soft_deleted_rows_are_left_out_unless_asked(conn):
    _store(conn, MANUAL_KEPT, dict(MANUAL_EXCLUDED, deleted_at="2026-11-10T10:00:00+00:00",
                                   deleted_by="tester@example.com"))
    assert [r["pub_number"] for r in store.all_records(conn, TEST_TENANT_ID)] == ["MAN-2026-0002"]
    assert len(store.all_records(conn, TEST_TENANT_ID, include_deleted=True)) == 2


# ── pub_number generation (§9 Q3) ────────────────────────────────────────────

def _draw(conn, tenant_id=TEST_TENANT_ID, year=2026):
    with conn.begin() as c:
        return store.next_manual_pub_number(c, tenant_id, year)


def test_pub_numbers_are_sequential_per_tenant_and_year(conn):
    store.ensure_tenant(conn, 2)
    assert [_draw(conn) for _ in range(3)] == ["MAN-2026-0001", "MAN-2026-0002", "MAN-2026-0003"]
    assert _draw(conn, tenant_id=2) == "MAN-2026-0001"   # independent per tenant
    assert _draw(conn, year=2027) == "MAN-2027-0001"     # restarts each year
    assert _draw(conn) == "MAN-2026-0004"


def test_rolled_back_draw_leaves_a_gap_never_a_duplicate(conn):
    assert _draw(conn) == "MAN-2026-0001"
    with pytest.raises(RuntimeError):
        with conn.begin() as c:
            store.next_manual_pub_number(c, TEST_TENANT_ID, 2026)
            raise RuntimeError("insert failed")
    assert _draw(conn) == "MAN-2026-0002"  # the rolled-back draw was never committed


def test_concurrent_draws_never_collide(conn):
    drawn, errors = [], []

    def worker():
        try:
            for _ in range(5):
                drawn.append(_draw(conn))
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(drawn) == 40 and len(set(drawn)) == 40
    assert sorted(drawn) == [f"MAN-2026-{n:04d}" for n in range(1, 41)]


# ── origin sites ─────────────────────────────────────────────────────────────

def test_origin_sites_ship_empty(conn):
    assert store.get_origin_sites(conn, TEST_TENANT_ID) == []


def test_add_origin_site_is_idempotent_on_normalised_name(conn):
    a = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP", domain="dtvp.de")
    b = store.add_origin_site(conn, TEST_TENANT_ID, "  dtvp ")
    assert a["id"] == b["id"] and b["name"] == "DTVP"
    assert len(store.get_origin_sites(conn, TEST_TENANT_ID)) == 1
    with pytest.raises(ValueError):
        store.add_origin_site(conn, TEST_TENANT_ID, "   ")


def test_origin_sites_are_tenant_isolated(conn):
    store.ensure_tenant(conn, 2)
    other = store.add_origin_site(conn, 2, "Their portal")
    assert store.get_origin_site(conn, TEST_TENANT_ID, other["id"]) is None
    assert store.get_origin_sites(conn, TEST_TENANT_ID) == []
    assert store.get_origin_site(conn, 2, other["id"])["name"] == "Their portal"


# ── the Manual source ────────────────────────────────────────────────────────

def test_manual_portal_is_seeded_but_never_fetched(conn):
    portals = {p["name"]: p for p in store.get_tenant_portals(conn, TEST_TENANT_ID)}
    assert portals["Manual"] == {"name": "Manual", "type": "manual", "enabled": True}
    names = [s["name"] for s in run._default_sources(conn, TEST_TENANT_ID, "2026-07-01")]
    assert "Manual" not in names and names  # every real connector still there


def test_portal_health_lists_manual_without_health_tracking(conn):
    entries = {e["name"]: e for e in api.get_health(tenant_id=TEST_TENANT_ID)}
    assert entries["Manual"]["status"] == "manual"
    assert store.get_source_health(conn, TEST_TENANT_ID, "Manual")["consecutive_failures"] == 0
