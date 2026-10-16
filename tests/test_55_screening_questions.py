"""CR-011 §3 — per-organization configurable screening questions.

screening.answer_questions(record, questions) -> unit coverage (mocked
Claude call, same pattern as test_29_composer.py's extract_requirements
tests — no live Anthropic call in the suite).

store.py: question CRUD (add/edit/disable/reorder), the seed-once-ever
backfill (_sync_screening_questions, called from ensure_tenant), answer
caching (set/get, missing-question detection, edit-invalidates-answers).

run.py: pipeline wiring — a surfaced tender with a missing answer gets one
computed; an excluded tender doesn't; nothing happens with zero enabled
questions; editing a question backfills across already-ingested tenders on
the NEXT run, with no separate script.

api.py: the three CRUD endpoints, and _attach_screening_answers' shape on
list_tenders/get_tender.
"""
from datetime import datetime, timezone

import anthropic

import api
import run
import screening
import store
from conftest import TEST_TENANT_ID, make_identity

OTHER_TENANT_ID = 999
# raw_ted_supply's fixed deadline (2026-08-31) must stay in the fixture's
# own future relative to whatever `now` run_pipeline uses, or the expired-
# deadline check skips ingesting it entirely before screening questions
# ever see it (see run.py/test_11_run.py's own NOW -- the exact bug CR-010's
# 295c675 fixed for 17 other tests the same way).
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


# ── screening.answer_questions (mocked Claude call) ─────────────────────────

def _questions(*texts):
    return [{"id": i + 1, "question_text": t} for i, t in enumerate(texts)]


def test_answer_questions_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    record = {"tag_line_en": "Tent supply", "description_en": "Supply of tents for 2 years."}
    assert screening.answer_questions(record, _questions("Delivery location?")) == {}


def test_answer_questions_no_questions_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    record = {"tag_line_en": "Tent supply", "description_en": "text"}
    assert screening.answer_questions(record, []) == {}


def test_answer_questions_no_text_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    assert screening.answer_questions({}, _questions("Delivery location?")) == {}


def _fake_client(response_text):
    class _FakeContentBlock:
        text = response_text

    class _FakeMessage:
        content = [_FakeContentBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeMessage()

    class _FakeClient:
        messages = _FakeMessages()

    return _FakeClient()


def test_answer_questions_parses_claude_json_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    response = ('[{"question_id": 1, "answer": "Brussels", "source_snippet": "delivery in Brussels"},'
                ' {"question_id": 2, "answer": "Not stated", "source_snippet": ""}]')
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _fake_client(response))

    record = {"tag_line_en": "Tent supply", "description_en": "Delivery in Brussels. 500 units."}
    result = screening.answer_questions(record, _questions("Delivery location?", "Contract duration?"))

    assert result[1] == ("Brussels", "delivery in Brussels", "answered")
    assert result[2] == ("Not stated", None, "not_stated")


def test_answer_questions_ignores_unknown_question_ids(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    response = '[{"question_id": 999, "answer": "x", "source_snippet": ""}]'
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _fake_client(response))
    record = {"tag_line_en": "t", "description_en": "d"}
    assert screening.answer_questions(record, _questions("Delivery location?")) == {}


def test_answer_questions_malformed_response_returns_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _fake_client("not json at all"))
    record = {"tag_line_en": "t", "description_en": "d"}
    assert screening.answer_questions(record, _questions("Delivery location?")) == {}


def test_parse_screening_response_handles_code_fence():
    text = '```json\n[{"question_id": 1, "answer": "A", "source_snippet": "s"}]\n```'
    parsed = screening._parse_screening_response(text, {1})
    assert parsed[1] == ("A", "s", "answered")


def test_answer_text_literally_not_stated_normalizes_status(monkeypatch):
    # the LLM might answer "Not stated" as the "answer" string itself rather
    # than leaving it empty -- both must land as status 'not_stated'.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    response = '[{"question_id": 1, "answer": "not stated", "source_snippet": "whatever"}]'
    monkeypatch.setattr(anthropic, "Anthropic", lambda: _fake_client(response))
    record = {"tag_line_en": "t", "description_en": "d"}
    result = screening.answer_questions(record, _questions("Delivery location?"))
    assert result[1] == ("Not stated", None, "not_stated")


# ── store.py: seeding, CRUD, answer caching ─────────────────────────────────

def _db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    return store.init_db(db_path)


def test_ensure_tenant_seeds_5_default_questions(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    questions = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)
    assert len(questions) == 5
    assert all(q["enabled"] for q in questions)
    assert [q["display_order"] for q in questions] == [0, 1, 2, 3, 4]


def test_disabling_every_question_does_not_get_reseeded(tmp_path, monkeypatch):
    # soft-state is the whole point (see schema.py's own comment) -- a
    # tenant that disables everything must stay disabled, not silently
    # reset back to enabled on the next ensure_tenant call.
    conn = _db(tmp_path, monkeypatch)
    for q in store.get_tenant_screening_questions(conn, TEST_TENANT_ID):
        store.update_screening_question(conn, TEST_TENANT_ID, q["id"], enabled=False)
    store.ensure_tenant(conn, TEST_TENANT_ID)  # second call, e.g. next pipeline run
    questions = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)
    assert len(questions) == 5  # not duplicated
    assert not any(q["enabled"] for q in questions)  # and not silently re-enabled


def test_add_screening_question_appends_at_the_end(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    new_q = store.add_screening_question(conn, TEST_TENANT_ID, "Does the buyer accept e-invoicing?")
    assert new_q["display_order"] == 5  # after the 5 seeded defaults (0-4)
    assert new_q["enabled"] is True
    questions = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)
    assert len(questions) == 6


def test_update_screening_question_text_deletes_its_cached_answers(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    q = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)[0]
    store.set_screening_answer(conn, TEST_TENANT_ID, "PUB-1", q["id"], "Brussels", "snippet", "answered")
    assert store.get_screening_answers_for_tender(conn, TEST_TENANT_ID, "PUB-1") != []

    store.update_screening_question(conn, TEST_TENANT_ID, q["id"], question_text="New wording?")

    assert store.get_screening_answers_for_tender(conn, TEST_TENANT_ID, "PUB-1") == []
    assert store.missing_screening_question_ids(conn, TEST_TENANT_ID, "PUB-1", [q["id"]]) == [q["id"]]


def test_update_screening_question_enable_only_keeps_answers(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    q = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)[0]
    store.set_screening_answer(conn, TEST_TENANT_ID, "PUB-1", q["id"], "Brussels", "snippet", "answered")
    store.update_screening_question(conn, TEST_TENANT_ID, q["id"], enabled=False)
    # answer still cached (just not shown while disabled, per get_screening_answers_for_tender's join)
    store.update_screening_question(conn, TEST_TENANT_ID, q["id"], enabled=True)
    answers = store.get_screening_answers_for_tender(conn, TEST_TENANT_ID, "PUB-1")
    assert answers[0]["answer_text"] == "Brussels"


def test_update_unknown_question_raises(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    try:
        store.update_screening_question(conn, TEST_TENANT_ID, 99999, enabled=False)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_missing_screening_question_ids_excludes_already_answered(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    qs = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)
    ids = [q["id"] for q in qs]
    store.set_screening_answer(conn, TEST_TENANT_ID, "PUB-1", ids[0], "x", None, "answered")
    missing = store.missing_screening_question_ids(conn, TEST_TENANT_ID, "PUB-1", ids)
    assert ids[0] not in missing
    assert set(missing) == set(ids[1:])


def test_two_tenants_have_independent_question_sets(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.ensure_tenant(conn, OTHER_TENANT_ID)
    store.add_screening_question(conn, OTHER_TENANT_ID, "Tenant-2-only question?")
    assert len(store.get_tenant_screening_questions(conn, TEST_TENANT_ID)) == 5
    assert len(store.get_tenant_screening_questions(conn, OTHER_TENANT_ID)) == 6


# ── run.py: pipeline wiring ──────────────────────────────────────────────────

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0}}


def _src(raw_ted_supply):
    import normalize
    return {"name": "TED", "fetch": lambda: [raw_ted_supply], "normalize": normalize.normalize_ted}


def test_pipeline_computes_answers_for_a_surfaced_tender(tmp_path, monkeypatch, raw_ted_supply):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    response = '[{"question_id": %d, "answer": "Not stated", "source_snippet": ""}]'
    calls = []

    def fake_answer_questions(record, questions):
        calls.append((record["pub_number"], [q["id"] for q in questions]))
        return {q["id"]: ("Not stated", None, "not_stated") for q in questions}

    monkeypatch.setattr(run.screening, "answer_questions", fake_answer_questions)

    db = str(tmp_path / "t.db"); out = str(tmp_path / "r.xlsx")
    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)

    assert len(calls) == 1  # one call for the one ingested tender
    conn = store.init_db(db)
    answers = store.get_screening_answers_for_tender(conn, TEST_TENANT_ID, raw_ted_supply["publication-number"])
    assert len(answers) == 5  # all 5 default questions answered


def test_pipeline_skips_tenders_with_no_missing_questions(tmp_path, monkeypatch, raw_ted_supply):
    db = str(tmp_path / "t.db"); out = str(tmp_path / "r.xlsx")
    calls = []
    monkeypatch.setattr(run.screening, "answer_questions",
                         lambda record, questions: calls.append(1) or {})
    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    first_call_count = len(calls)
    assert first_call_count == 1

    # second run, same tender already fully answered (first call returned {}
    # above, so nothing actually got cached -- re-seed real answers directly
    # to simulate a prior successful run, then confirm a third run skips it)
    conn = store.init_db(db)
    for q in store.get_tenant_screening_questions(conn, TEST_TENANT_ID):
        store.set_screening_answer(conn, TEST_TENANT_ID, raw_ted_supply["publication-number"],
                                    q["id"], "x", None, "answered")
    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    assert len(calls) == first_call_count  # no new call -- nothing was missing


def test_pipeline_skips_excluded_tenders(tmp_path, monkeypatch):
    # an F1-F8-excluded tender never surfaces, so it shouldn't burn a
    # screening-questions call either.
    import copy
    raw = {
        "publication-number": "EXCLUDED-1", "notice-title": {"eng": "Office chairs"},
        "description-proc": {"eng": "Office chairs for a government building."},
        "buyer-name": {"eng": ["Ministry"]}, "buyer-country": ["SWE"],
        "contract-nature": "supplies", "procedure-type": "open",
        "publication-date": "20260601", "deadline-receipt-request": ["2026-08-31T23:59:00+02:00"],
        "place-of-performance": ["SE110", "SWE"], "classification-cpv": ["39130000"],  # not a configured CPV
    }
    calls = []
    monkeypatch.setattr(run.screening, "answer_questions", lambda record, questions: calls.append(1) or {})
    db = str(tmp_path / "t.db"); out = str(tmp_path / "r.xlsx")
    import normalize
    src = {"name": "TED", "fetch": lambda: [raw], "normalize": normalize.normalize_ted}
    run.run_pipeline([src], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    assert calls == []


def test_pipeline_does_nothing_with_zero_enabled_questions(tmp_path, monkeypatch, raw_ted_supply):
    conn = store.init_db(str(tmp_path / "t.db"))
    for q in store.get_tenant_screening_questions(conn, TEST_TENANT_ID):
        store.update_screening_question(conn, TEST_TENANT_ID, q["id"], enabled=False)
    calls = []
    monkeypatch.setattr(run.screening, "answer_questions", lambda record, questions: calls.append(1) or {})
    db = str(tmp_path / "t.db"); out = str(tmp_path / "r.xlsx")
    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    assert calls == []


def test_editing_a_question_backfills_on_the_next_run(tmp_path, monkeypatch, raw_ted_supply):
    # CR-011 §3: "answers refresh" when the question set changes -- no
    # separate backfill script, just the normal next pipeline run.
    db = str(tmp_path / "t.db"); out = str(tmp_path / "r.xlsx")
    monkeypatch.setattr(run.screening, "answer_questions",
                         lambda record, questions: {q["id"]: ("A", None, "answered") for q in questions})
    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)

    conn = store.init_db(db)
    q = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)[0]
    store.update_screening_question(conn, TEST_TENANT_ID, q["id"], question_text="Rewritten question?")
    assert store.missing_screening_question_ids(
        conn, TEST_TENANT_ID, raw_ted_supply["publication-number"], [q["id"]]) == [q["id"]]

    run.run_pipeline([_src(raw_ted_supply)], db, out, tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    answers = store.get_screening_answers_for_tender(conn, TEST_TENANT_ID, raw_ted_supply["publication-number"])
    assert any(a["question_id"] == q["id"] and a["answer_text"] == "A" for a in answers)


# ── api.py: CRUD endpoints + attachment shape ───────────────────────────────

def test_get_screening_questions_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    result = api.get_screening_questions(tenant_id=TEST_TENANT_ID)
    assert len(result["questions"]) == 5


def test_post_screening_question_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    result = api.post_screening_question(
        api.NewScreeningQuestionBody(question_text="New one?"), tenant_id=TEST_TENANT_ID)
    assert result["question_text"] == "New one?"
    assert len(api.get_screening_questions(tenant_id=TEST_TENANT_ID)["questions"]) == 6


def test_post_screening_question_rejects_empty_text(tmp_path, monkeypatch):
    from fastapi import HTTPException
    conn = _db(tmp_path, monkeypatch)
    try:
        api.post_screening_question(api.NewScreeningQuestionBody(question_text="   "), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 422


def test_put_screening_question_endpoint(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    q_id = api.get_screening_questions(tenant_id=TEST_TENANT_ID)["questions"][0]["id"]
    api.put_screening_question(q_id, api.ScreeningQuestionBody(enabled=False), tenant_id=TEST_TENANT_ID)
    questions = api.get_screening_questions(tenant_id=TEST_TENANT_ID)["questions"]
    assert next(q for q in questions if q["id"] == q_id)["enabled"] is False


def test_put_unknown_screening_question_404s(tmp_path, monkeypatch):
    from fastapi import HTTPException
    conn = _db(tmp_path, monkeypatch)
    try:
        api.put_screening_question(99999, api.ScreeningQuestionBody(enabled=False), tenant_id=TEST_TENANT_ID)
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 404


def _tender(pub_number):
    return {"source": "TED", "pub_number": pub_number, "tag_line": "Tent supply",
            "description": "", "buyer": "Ministry X", "country": "SWE", "place": "",
            "category": "Supply", "procedure": "open", "pub_date": "2026-06-01",
            "deadline": "2030-01-01", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "cpv",
            "url": "http://ted.example/1", "first_seen": None, "exclude_reason": ""}


def test_list_tenders_attaches_screening_answers(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))
    q = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)[0]
    store.set_screening_answer(conn, TEST_TENANT_ID, "PUB-1", q["id"], "Brussels", "snippet", "answered")

    result = api.list_tenders(limit=100, offset=0, identity=make_identity(tenant_id=TEST_TENANT_ID))
    tender = next(t for t in result["results"] if t["pub_number"] == "PUB-1")
    assert tender["screening_answers"][0]["answer_text"] == "Brussels"


def test_list_tenders_empty_screening_answers_when_none_cached(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))
    result = api.list_tenders(limit=100, offset=0, identity=make_identity(tenant_id=TEST_TENANT_ID))
    tender = next(t for t in result["results"] if t["pub_number"] == "PUB-1")
    assert tender["screening_answers"] == []


def test_disabled_question_answer_hidden_from_attachment(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    store.upsert(conn, TEST_TENANT_ID, _tender("PUB-1"))
    q = store.get_tenant_screening_questions(conn, TEST_TENANT_ID)[0]
    store.set_screening_answer(conn, TEST_TENANT_ID, "PUB-1", q["id"], "Brussels", None, "answered")
    store.update_screening_question(conn, TEST_TENANT_ID, q["id"], enabled=False)

    result = api.list_tenders(limit=100, offset=0, identity=make_identity(tenant_id=TEST_TENANT_ID))
    tender = next(t for t in result["results"] if t["pub_number"] == "PUB-1")
    assert tender["screening_answers"] == []
