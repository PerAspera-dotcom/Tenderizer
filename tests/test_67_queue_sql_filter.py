"""CR-017 §1.1 — the Review Queue list filters in SQL before enriching.

The move from "load every row, filter in Python" to "filter in SQL, enrich
the survivors" must not change which tenders the list returns, or what it
says about them. `_reference_list` below is a frozen copy of list_tenders as
it stood before CR-017 (all_records → enrich everything → filter in Python).
Every test here compares the live endpoint against it on one fixture that
covers every status, both entry methods (plus a blank one), expired / today /
live / blank / timestamped deadlines, past_tender and blank notice types,
excluded manual and scraped rows, soft-deleted rows, duplicate edges of every
kind (resolved, chained, crossing the visible/hidden boundary, touching a
dismissed_final or deleted row) and relevance overrides.

Set equality is the requirement; these tests also check order and that
every returned row is identical, field for field — which covers relevance
scores (their training pools include hidden, expired and past rows) and
duplicate groups (whose members can sit outside the filtered set).

The date is pinned (api._today), never date.today().
"""
import itertools
import random
import shutil

import pytest
from sqlalchemy import insert, update

import api
import store
import visibility
from conftest import TEST_TENANT_ID, make_identity
from schema import tender_duplicates, tenders

TODAY = "2026-07-15"

STATUSES = ["new", "needs_review", "reviewed", "shortlisted", "dismissed", "dismissed_final", ""]
ENTRY = ["scraped", "manual"]
DEADLINES = ["", "2026-07-01", "2026-07-14T23:59:00", TODAY, f"{TODAY}T09:00:00+02:00", "2026-08-01", "2027-01-31"]
NOTICE_TYPES = ["tender", "past_tender", "award"]
EXCLUDE = ["", "F3: value below floor"]

SOURCES = ["TED", "BOAMP", "TenderNed", "e-Procurement", "CanadaBuys"]
COUNTRIES = ["BE", "FR", "NL", "DE", "CA"]
CATEGORIES = ["Supply", "Services", "Works"]
MATCH_SOURCES = ["cpv", "keyword", "both", None, "None"]
CPVS = [["39522530"], ["39522000", "45000000"], ["79952000"], [], ["39522530", "79952000"]]
TITLES = ["Tentes de réception", "Équipement événementiel", "Zelte für Bundeswehr", "Partytent huur", "Tent rental"]
BUYERS = ["Ville de Liège", "BAAINBw", "Gemeente Utrecht", "SPF Intérieur", "Public Works Canada"]


@pytest.fixture(scope="module")
def template_db(tmp_path_factory):
    """The fixture database, built once per module; each test gets a copy.
    Module scope runs before conftest's per-test DATABASE_URL guard, so it
    applies the same guard itself."""
    path = tmp_path_factory.mktemp("cr017") / "template.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("DATABASE_URL", raising=False)
        c = store.init_db(str(path))
        store.ensure_tenant(c, TEST_TENANT_ID)
        pubs = _populate(c)
    return path, pubs


@pytest.fixture
def conn(tmp_path, monkeypatch, template_db):
    template, pubs = template_db
    db_path = tmp_path / "t.db"
    shutil.copyfile(template, db_path)
    monkeypatch.setattr(api, "DB_PATH", str(db_path))
    monkeypatch.setattr(api, "_today", lambda: TODAY)
    _PUBS[:] = pubs
    return store.init_db(str(db_path))


_PUBS = []


def _build(conn):
    return list(_PUBS)


def _populate(conn):
    pubs = []
    combos = itertools.product(STATUSES, ENTRY, DEADLINES, NOTICE_TYPES, EXCLUDE)
    for i, (status, entry, deadline, notice_type, exclude) in enumerate(combos):
        pub = f"P-{i:04d}"
        store.upsert(conn, TEST_TENANT_ID, {
            "source": "Manual" if entry == "manual" else SOURCES[i % 5], "pub_number": pub,
            "tag_line": TITLES[i % 5], "description": "", "buyer": BUYERS[(i // 5) % 5],
            "country": COUNTRIES[(i // 3) % 5], "category": CATEGORIES[i % 3],
            "deadline": deadline, "pub_date": f"2026-0{1 + i % 6}-1{i % 10}",
            "cpv_codes": CPVS[(i // 7) % 5], "matched_terms": [], "match_source": MATCH_SOURCES[i % 5],
            "status": status, "exclude_reason": exclude, "notice_type": notice_type,
            "entry_method": entry, "url": f"https://example.test/{pub}",
            "reason_category": "wrong_sector" if status.startswith("dismissed") else None,
        })
        pubs.append(pub)
    rng = random.Random(17)
    with conn.begin() as c:
        # blank notice_type / entry_method: store.upsert never writes these,
        # but the old path read them as "tender" / "scraped" and so must SQL
        for pub in rng.sample(pubs, 40):
            c.execute(update(tenders).where(tenders.c.pub_number == pub).values(notice_type=""))
        for pub in rng.sample(pubs, 40):
            c.execute(update(tenders).where(tenders.c.pub_number == pub).values(entry_method=""))
        for pub in rng.sample(pubs, 30):
            c.execute(update(tenders).where(tenders.c.pub_number == pub).values(deleted_at="2026-07-01T00:00:00"))
        # duplicate edges: random pairs, a few chains, every match type
        edges = set()
        while len(edges) < 220:
            a, b = rng.sample(pubs, 2)
            if (b, a) not in edges:
                edges.add((a, b))
        for k in range(0, 60, 3):  # chains of three: a-b, b-c
            edges.add((pubs[k], pubs[k + 100]))
            edges.add((pubs[k + 100], pubs[k + 200]))
        for n, (a, b) in enumerate(sorted(edges)):
            match_type = ["same_source", "reference", "similarity"][n % 3]
            resolved = n % 7 == 0
            c.execute(insert(tender_duplicates).values(
                tenant_id=TEST_TENANT_ID, pub_number_a=a, pub_number_b=b, match_type=match_type,
                similarity=0.9 if match_type == "similarity" else None, detected_at="2026-07-01",
                resolved_by="r@example.com" if resolved else None,
                resolved_at="2026-07-02" if resolved else None))
    for pub in rng.sample(pubs, 15):
        store.upsert_relevance_override(conn, TEST_TENANT_ID, pub, 77, "checked", "r@example.com")
    return pubs


@pytest.fixture
def cheap_scores(monkeypatch):
    """The facet matrices call the list a few hundred times; the real scorer
    makes that minutes. This stand-in is as sensitive to the pools as the
    real one — any row missing from or added to either pool changes every
    score — so pool drift still fails these tests. The real scorer is used
    in test_scores_match_and_draw_on_rows_outside_the_filtered_set."""
    def fake(record, positive, negative, cpv_groups):
        key = (record["pub_number"], tuple(sorted(p["pub_number"] for p in positive)),
               tuple(sorted(n["pub_number"] for n in negative)))
        return {"score": hash(key) % 101, "reasoning": f"{len(positive)}+/{len(negative)}-"}
    monkeypatch.setattr(api.relevance, "score_relevance", fake)


# ── frozen pre-CR-017 path ───────────────────────────────────────────────────

def _reference_enriched(conn, identity):
    records = store.all_records(conn, TEST_TENANT_ID)
    api._attach_relevance(conn, TEST_TENANT_ID, records)
    api._add_dismissal_reason_category(records)
    api._attach_duplicates(conn, TEST_TENANT_ID, records)
    api._attach_screening_answers(conn, TEST_TENANT_ID, records)
    api._attach_presence(conn, TEST_TENANT_ID, records, identity)
    api._attach_notifications(conn, TEST_TENANT_ID, records)
    return records


def _reference_list(records, source=None, category=None, match_source=None, country=None, q=None,
                    status=None, has_deadline=None, notice_type=None, include_excluded=False,
                    entry_method=None):
    if not include_excluded:
        records = [r for r in records if not visibility.is_hidden(r)]
    if source:
        records = [r for r in records if (r.get("source") or "").upper() == source.upper()]
    if entry_method:
        records = [r for r in records if (r.get("entry_method") or "scraped") == entry_method]
    if category:
        records = [r for r in records if (r.get("category") or "").lower() == category.lower()]
    if match_source:
        _none_vals = (None, "None", "none", "")
        if match_source == "none":
            records = [r for r in records if r.get("match_source") in _none_vals]
        else:
            records = [r for r in records if r.get("match_source") == match_source]
    if country:
        records = [r for r in records if (r.get("country") or "").upper() == country.upper()]
    if q:
        ql = q.lower()
        records = [r for r in records
                   if ql in (r.get("tag_line") or "").lower()
                   or ql in (r.get("buyer") or "").lower()]
    if status:
        records = [r for r in records if (r.get("status") or "new") == status]
    if has_deadline is True:
        records = [r for r in records if r.get("deadline")]
    elif has_deadline is False:
        records = [r for r in records if not r.get("deadline")]
    if notice_type:
        records = [r for r in records if (r.get("notice_type") or "tender") == notice_type]
    else:
        records = [r for r in records if (r.get("notice_type") or "tender") != "past_tender"]
    if status != "dismissed_final":
        records = [r for r in records if not r.get("deadline") or r["deadline"][:10] >= TODAY]
    records.sort(key=lambda r: (r.get("deadline") or "9999-99-99"))
    return records


# ── the live path ────────────────────────────────────────────────────────────

_PARAMS = dict(source=None, category=None, match_source=None, country=None, q=None, status=None,
               has_deadline=None, notice_type=None, include_excluded=False, entry_method=None)


def _live(identity, **kw):
    return api.list_tenders(identity=identity, limit=1000, offset=0, **{**_PARAMS, **kw})


def _assert_same(reference, live):
    assert live["total"] == len(reference)
    ref_pubs = [r["pub_number"] for r in reference]
    live_pubs = [r["pub_number"] for r in live["results"]]
    assert set(live_pubs) == set(ref_pubs)          # the requirement
    assert live_pubs == ref_pubs                     # and the order
    for ref, got in zip(reference, live["results"]):
        assert got == ref, ref["pub_number"]         # every field, scores and groups included


# Every combination of the facets that moved into SQL.
SQL_FACETS = list(itertools.product(
    [None, "new", "needs_review", "reviewed", "shortlisted", "dismissed", "dismissed_final"],
    [None, "tender", "past_tender", "award"],
    [False, True],
    [None, "manual", "scraped"],
    [None, True, False],
))


def test_sql_path_matches_python_path_for_every_sql_facet(conn, cheap_scores):
    _build(conn)
    identity = make_identity()
    reference = _reference_enriched(conn, identity)
    for status, notice_type, include_excluded, entry_method, has_deadline in SQL_FACETS:
        kw = dict(status=status, notice_type=notice_type, include_excluded=include_excluded,
                  entry_method=entry_method, has_deadline=has_deadline)
        _assert_same(_reference_list([dict(r) for r in reference], **kw), _live(identity, **kw))


@pytest.mark.parametrize("kw", [
    {"source": "boamp"}, {"source": "TENDERNED"}, {"source": "Manual"},
    {"category": "services"}, {"country": "be"},
    {"match_source": "none"}, {"match_source": "keyword"},
    {"q": "équipement"}, {"q": "ÉQUIPEMENT"}, {"q": "liège"}, {"q": "tent"},
    {"q": "tent", "status": "new", "entry_method": "manual"},
    {"source": "TED", "include_excluded": True, "has_deadline": False},
    {"country": "NL", "notice_type": "past_tender", "status": "dismissed_final"},
])
def test_sql_path_matches_python_path_with_free_text_facets(conn, cheap_scores, kw):
    _build(conn)
    identity = make_identity()
    reference = _reference_enriched(conn, identity)
    _assert_same(_reference_list(reference, **kw), _live(identity, **kw))


def test_default_queue_is_not_trivially_empty_or_full(conn, cheap_scores):
    """Guards the fixture: the comparison above means something only if the
    default view keeps some rows and drops others for each reason."""
    pubs = _build(conn)
    shown = {r["pub_number"] for r in _live(make_identity())["results"]}
    assert 0 < len(shown) < len(pubs)
    rows = {r["pub_number"]: r for r in store.all_records(conn, TEST_TENANT_ID)}
    assert any(rows[p]["entry_method"] == "manual" and rows[p]["exclude_reason"] for p in shown)
    assert not any(rows[p]["exclude_reason"] and rows[p]["entry_method"] != "manual" for p in shown)
    assert not any((rows[p]["deadline"] or "9999")[:10] < TODAY for p in shown)


def test_scores_match_and_draw_on_rows_outside_the_filtered_set(conn):
    """Score equality, explicitly: the relevance pools must still include
    shortlisted / dismissed_final rows that the list itself would hide
    (excluded, expired, past_tender). Removing them changes the scores."""
    _build(conn)
    identity = make_identity()
    reference = {r["pub_number"]: r for r in _reference_enriched(conn, identity)}
    live = _live(identity)["results"]
    scored = [r for r in live if "relevance_score" in r]
    assert scored
    for r in scored:
        ref = reference[r["pub_number"]]
        assert (r["relevance_score"], r["relevance_reasoning"], r["relevance_corrected"]) == \
               (ref["relevance_score"], ref["relevance_reasoning"], ref["relevance_corrected"])
    pool = store.relevance_pool_records(conn, TEST_TENANT_ID)
    shown = {r["pub_number"] for r in live}
    assert any(p["pub_number"] not in shown for p in pool)


def test_duplicate_groups_include_members_outside_the_filtered_set(conn, cheap_scores):
    """A group whose members are split by the filter (one live, one hidden
    or expired) must report the same size and canonical as before."""
    _build(conn)
    identity = make_identity()
    reference = {r["pub_number"]: r for r in _reference_enriched(conn, identity)}
    live = _live(identity)["results"]
    shown = {r["pub_number"] for r in live}
    split = [r for r in live
             if any(m["pub_number"] not in shown
                    for g in (r["duplicate_group"], r["cross_portal_group"]) if g
                    for m in g["members"])]
    assert split, "fixture should produce groups that straddle the filter"
    for r in split:
        ref = reference[r["pub_number"]]
        assert r["duplicate_group"] == ref["duplicate_group"]
        assert r["cross_portal_group"] == ref["cross_portal_group"]
        assert r["duplicates"] == ref["duplicates"]


# ── count endpoint (nav badge) ───────────────────────────────────────────────

def test_count_matches_list_total_for_every_sql_facet(conn, cheap_scores):
    """Against the frozen Python path, which test_sql_path_matches_python_
    path_for_every_sql_facet ties to the live list's total."""
    _build(conn)
    identity = make_identity()
    reference = _reference_enriched(conn, identity)
    for status, notice_type, include_excluded, entry_method, has_deadline in SQL_FACETS:
        kw = dict(status=status, notice_type=notice_type, include_excluded=include_excluded,
                  entry_method=entry_method, has_deadline=has_deadline)
        assert api.count_tenders(identity=identity, **kw)["total"] == len(_reference_list(reference, **kw)), kw


def test_count_is_tenant_scoped(conn):
    _build(conn)
    store.ensure_tenant(conn, 2)
    assert api.count_tenders(identity=make_identity(tenant_id=2), status=None, notice_type=None,
                             include_excluded=False, entry_method=None, has_deadline=None)["total"] == 0


def test_list_is_tenant_scoped(conn):
    _build(conn)
    store.ensure_tenant(conn, 2)
    assert _live(make_identity(tenant_id=2))["total"] == 0
