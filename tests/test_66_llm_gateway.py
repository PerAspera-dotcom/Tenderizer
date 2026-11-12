"""CR-015 — the hard divide around Claude API calls (src/llm.py).

Every test uses a fake client; none makes a real API call.
"""
import json
import pathlib
import re
from datetime import datetime, timezone

import anthropic
import httpx
import pytest

import api
import composer
import llm
import manual_drafts
import run
import screening
import store
import vault
from conftest import TEST_TENANT_ID, TEST_ACCOUNT_NAME

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0}}
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)
REPLY = '[{"question_id": %d, "answer": "Brussels", "source_snippet": "Brussels"}]'


# ── fakes ────────────────────────────────────────────────────────────────────

class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 120
    output_tokens = 30


class FakeClient:
    """Stands in for anthropic.Anthropic(). `script` is a list of replies or
    exceptions handed out in order (the last one repeats)."""
    instances = []

    def __init__(self, script=None):
        self.script = list(script or ["[]"])
        self.calls = []
        self.messages = self
        FakeClient.instances.append(self)

    def create(self, **request):
        self.calls.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return type("M", (), {"content": [_Block(item)], "usage": _Usage(), "stop_reason": "end_turn"})()


@pytest.fixture
def fake(monkeypatch):
    """Every client the gateway constructs is this one fake; returns it."""
    client = FakeClient()
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    return client


def _api_error(cls, status, message="boom"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=None)


def _key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")


def _rec(pub, status="new", exclude_reason="", text="Delivery of tents to Brussels."):
    return {"source": "TED", "pub_number": pub, "tag_line": "Tent supply " + pub, "description": text,
            "buyer": "Ministry", "country": "BE", "place": "", "category": "Supply", "procedure": "open",
            "pub_date": "2026-07-01", "deadline": "2026-12-31", "cpv_codes": ["39522530"],
            "matched_terms": ["tent"], "match_source": "both", "url": "https://example.org/" + pub,
            "status": status, "exclude_reason": exclude_reason, "language": "eng",
            "tag_line_en": "Tent supply " + pub, "description_en": text,
            "internal_identifier": None, "entry_method": "scraped"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(run.translate, "check_health", lambda: ("ok", None))
    conn = store.init_db(path)
    store.ensure_tenant(conn, TEST_TENANT_ID)
    return path, conn


def _seed(conn, *records):
    for r in records:
        store.upsert(conn, TEST_TENANT_ID, r)
        if r["status"] != "new":
            store.set_status(conn, TEST_TENANT_ID, r["pub_number"], r["status"])


def _run(path, tmp_path):
    out = tmp_path / "r.xlsx"
    health = run.run_pipeline([], path, str(out), tenant_id=TEST_TENANT_ID, fx_rates=FX_RATES, now=NOW)
    return health, out


def _last_run_written(tmp_path):
    return (tmp_path / f"last_run_{TEST_TENANT_ID}.json").exists()


# ── §5: the divide is mechanical ─────────────────────────────────────────────

def test_nothing_outside_the_gateway_imports_anthropic():
    pattern = re.compile(r"^\s*(import\s+anthropic\b|from\s+anthropic\b)|anthropic\.(Anthropic|AsyncAnthropic)\(",
                         re.MULTILINE)
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "llm.py" and path.parent == SRC:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == [], f"Claude must be reached only through src/llm.py: {offenders}"


def test_the_divide_test_would_catch_a_violation():
    pattern = re.compile(r"^\s*(import\s+anthropic\b|from\s+anthropic\b)|anthropic\.(Anthropic|AsyncAnthropic)\(",
                         re.MULTILINE)
    assert pattern.search("import os\nimport anthropic\n")
    assert pattern.search("    from anthropic import Anthropic\n")
    assert pattern.search("client = anthropic.Anthropic()\n")
    assert not pattern.search("# the anthropic SDK is wrapped by llm.py\n")


# ── checks 1-2: key and feature switches ─────────────────────────────────────

def test_a_key_alone_switches_nothing_on_except_manual_drafts(monkeypatch):
    _key(monkeypatch)
    assert not llm.feature_enabled("screening")
    assert not llm.feature_enabled("vault")
    assert not llm.feature_enabled("composer")
    assert llm.feature_enabled("manual_drafts")  # CR-015 §4: on unless switched off
    monkeypatch.setenv("LLM_MANUAL_DRAFTS", "off")
    assert not llm.feature_enabled("manual_drafts")


def test_no_key_refuses_even_when_a_feature_is_on(monkeypatch, fake):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_SCREENING", "on")
    assert llm.create("screening", model="m", max_tokens=1, messages=[]) is None
    assert "key" in llm.last_refusal()
    assert fake.calls == []


def test_unknown_feature_is_a_programming_error():
    with pytest.raises(ValueError):
        llm.create("everything", model="m", max_tokens=1, messages=[])


@pytest.mark.parametrize("call, empty", [
    (lambda: screening.answer_questions({"tag_line_en": "t", "description_en": "d"},
                                        [{"id": 1, "question_text": "q"}]), {}),
    (lambda: composer.extract_requirements([{"filename": "s.pdf", "role": "sow", "pages": ["x"]}]), []),
    (lambda: composer.extract_style_guide(["example"]), None),
    (lambda: composer.refine_section("req", "current", "feedback", []), None),
    (lambda: vault.extract_metadata("doc.pdf", "application/pdf"), None),
])
def test_a_refused_call_returns_what_a_missing_key_returns(monkeypatch, fake, call, empty):
    monkeypatch.setattr(vault, "_pdf_first_pages_as_images", lambda path: ["img"])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = call()
    _key(monkeypatch)  # key present, feature switched off -> refused
    refused = call()
    assert without_key == empty and refused == empty
    assert fake.calls == []


def test_matrix_cell_refused_is_written_as_unknown_not_raised(monkeypatch, fake):
    _key(monkeypatch)
    cell = composer._generate_matrix_cell("req", "guidance", "1 person tent", [])
    assert cell["yes_no"] == "?" and "switched off" in cell["remark"]
    assert fake.calls == []


# ── check 3: interactive versus scheduled ────────────────────────────────────

def test_a_call_inside_a_scheduled_run_is_scheduled_whatever_the_caller(monkeypatch, fake):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    with llm.scheduled_run():
        assert llm.create("screening", model="m", max_tokens=1, messages=[]) is None
        assert "LLM_SCHEDULED_SCREENING" in llm.last_refusal()
        # manual drafts is on, but never permitted from a scheduled path either
        assert llm.create("manual_drafts", model="m", max_tokens=1, messages=[]) is None
    assert fake.calls == []
    assert llm.create("screening", model="m", max_tokens=1, messages=[]) is not None  # interactive
    assert len(fake.calls) == 1


def test_key_present_and_every_switch_off_a_full_run_makes_zero_calls(monkeypatch, fake, db, tmp_path):
    _key(monkeypatch)
    path, conn = db
    _seed(conn, _rec("T-1"), _rec("T-2", status="shortlisted"), _rec("T-3", status="needs_review"))
    health, out = _run(path, tmp_path)
    assert fake.calls == []
    assert out.exists() and _last_run_written(tmp_path)
    assert not any(k.startswith("Claude") for k in health)
    assert store.get_source_health(conn, TEST_TENANT_ID, "Claude")["last_result"] is None


def test_screening_on_but_scheduled_off_a_run_still_makes_zero_calls(monkeypatch, fake, db, tmp_path):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    path, conn = db
    _seed(conn, _rec("T-1", status="shortlisted"))
    _run(path, tmp_path)
    assert fake.calls == []


def test_scheduled_screening_on_still_makes_no_corpus_wide_calls(monkeypatch, fake, db, tmp_path):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_SCHEDULED_SCREENING", "on")
    path, conn = db
    corpus = [_rec(f"N-{i}") for i in range(12)]  # surfaced, nobody has acted on them
    acted = [_rec("S-1", status="shortlisted"), _rec("R-1", status="needs_review"),
             _rec("D-1", status="dismissed")]
    hidden = [_rec("X-1", status="shortlisted", exclude_reason="construction_works")]
    _seed(conn, *corpus, *acted, *hidden)
    health, out = _run(path, tmp_path)
    assert len(fake.calls) == 2  # only S-1 and R-1
    assert health["Claude screening"].startswith("screened")
    assert store.get_source_health(conn, TEST_TENANT_ID, "Claude")["last_result"] is not None


# ── check 4: budget ──────────────────────────────────────────────────────────

def test_per_run_ceiling_is_enforced_by_the_gateway_and_recorded(monkeypatch, fake, db, tmp_path):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_SCHEDULED_SCREENING", "on")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_RUN", "2")
    path, conn = db
    _seed(conn, *[_rec(f"S-{i}", status="shortlisted") for i in range(5)])
    health, out = _run(path, tmp_path)
    assert len(fake.calls) == 2
    assert "Per-run Claude budget reached (2 calls)" in health["Claude screening"]
    row = store.get_source_health(conn, TEST_TENANT_ID, "Claude")
    assert "budget" in (row.get("error_detail") or row.get("last_result") or "")
    assert out.exists() and _last_run_written(tmp_path)


def test_monthly_ceiling_covers_interactive_calls_too(monkeypatch, fake):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_MONTH", "2")
    for _ in range(2):
        assert llm.create("screening", model="m", max_tokens=1, messages=[]) is not None
    assert llm.create("screening", model="m", max_tokens=1, messages=[]) is None
    assert "Monthly Claude budget reached (2 calls)" in llm.last_refusal()
    assert len(fake.calls) == 2


def test_monthly_usage_is_counted_in_the_database_with_tokens_and_spend(monkeypatch, fake):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_COMPOSER", "on")
    llm.create("composer", model="claude-sonnet-4-6", max_tokens=1, messages=[])
    usage = llm.month_usage()
    assert usage["calls"] == 1
    expected_usd = round(120 / 1e6 * 3.00 + 30 / 1e6 * 15.00, 4)
    assert usage["by_feature"]["composer"] == {"calls": 1, "input_tokens": 120, "output_tokens": 30,
                                               "usd": expected_usd}
    assert usage["usd"] == expected_usd
    with llm._engine().connect() as c:
        rows = c.execute(llm.llm_usage.select()).fetchall()
    assert [(r.month, r.feature, r.model, r.calls) for r in rows] == [
        (llm._month(), "composer", "claude-sonnet-4-6", 1)]


def test_the_default_budget_sits_inside_the_console_cap():
    # $10/month console cap: the estimated-spend ceiling binds first, with
    # room for a worst-case call (~$1.20, 200 scanned pages) already in flight.
    assert llm.max_usd_per_month() == 7.0
    assert llm.max_usd_per_month() + 1.20 * 2 < 10
    assert llm.max_calls_per_month() == 60 and llm.max_calls_per_run() == 10


def test_estimated_spend_ceiling_refuses_before_the_console_cap(monkeypatch, fake):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_MAX_USD_PER_MONTH", "0.001")
    # 120 in / 30 out on Sonnet 4.6 = $0.00081; the second call takes it past $0.001
    for _ in range(2):
        assert llm.create("screening", model="claude-sonnet-4-6", max_tokens=1, messages=[]) is not None
    assert llm.create("screening", model="claude-sonnet-4-6", max_tokens=1, messages=[]) is None
    assert "Monthly Claude spending limit reached" in llm.last_refusal()
    assert len(fake.calls) == 2
    assert not llm.available("manual_drafts")


def test_an_unknown_model_is_priced_pessimistically():
    assert llm.price("claude-something-new") == llm.UNKNOWN_MODEL_PRICE
    assert all(llm.UNKNOWN_MODEL_PRICE[i] >= p[i] for p in llm.PRICES.values() for i in (0, 1))


def test_the_guard_fails_closed_when_the_counter_cannot_be_reached(monkeypatch, fake):
    _key(monkeypatch)
    def broken():
        raise RuntimeError("database is down")
    monkeypatch.setattr(llm, "_engine", broken)
    assert llm.create("manual_drafts", model="m", max_tokens=1, messages=[]) is None
    assert "usage counter unavailable" in llm.last_refusal()
    assert fake.calls == []
    assert "usage counter unavailable" in llm.unavailable_reason("manual_drafts")
    assert llm.status()["usage"] is None and "database is down" in llm.status()["usage_error"]


def test_the_guard_fails_closed_when_the_tables_are_missing(monkeypatch, fake, tmp_path):
    # Postgres before the migration has run: no create_all fallback there.
    _key(monkeypatch)
    from sqlalchemy import create_engine
    bare = create_engine(f"sqlite:///{tmp_path / 'no_tables.db'}")
    monkeypatch.setattr(llm, "_engine", lambda: bare)
    assert llm.create("manual_drafts", model="m", max_tokens=1, messages=[]) is None
    assert "usage counter unavailable" in llm.last_refusal()
    assert fake.calls == []


def test_portal_health_shows_a_broken_counter_as_failing(monkeypatch, db):
    _key(monkeypatch)
    def broken():
        raise RuntimeError("database is down")
    monkeypatch.setattr(llm, "_engine", broken)
    claude = {e["name"]: e for e in api.get_health(tenant_id=TEST_TENANT_ID)}["Claude"]
    assert claude["status"] == "failing" and "Usage counter unavailable" in claude["detail"]


def test_a_failed_call_still_counts_against_the_month(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    client = FakeClient([_api_error(anthropic.BadRequestError, 400)])
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    assert llm.create("screening", model="m", max_tokens=1, messages=[]) is None
    assert llm.month_usage()["calls"] == 1


# ── §3.5: an API failure never fails a run ───────────────────────────────────

def _scheduled_on(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_SCHEDULED_SCREENING", "on")


def test_an_api_error_on_one_tender_does_not_abort_the_run(monkeypatch, db, tmp_path):
    _scheduled_on(monkeypatch)
    q_id = None
    path, conn = db
    q_id = store.get_tenant_screening_questions(conn, TEST_TENANT_ID, enabled_only=True)[0]["id"]
    client = FakeClient([_api_error(anthropic.BadRequestError, 400, "prompt too long"), REPLY % q_id])
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    _seed(conn, _rec("S-1", status="shortlisted"), _rec("S-2", status="shortlisted"))
    health, out = _run(path, tmp_path)
    assert len(client.calls) == 2  # the second tender was still screened
    assert out.exists() and _last_run_written(tmp_path)
    assert health["Claude screening"].startswith("screened 1")
    assert "1 failed call" in health["Claude screening"]


def test_a_bug_in_one_tenders_screening_does_not_abort_the_run(monkeypatch, db, tmp_path):
    _scheduled_on(monkeypatch)
    path, conn = db
    seen = []

    def flaky(record, questions):
        seen.append(record["pub_number"])
        if len(seen) == 1:
            raise RuntimeError("unexpected")
        return {questions[0]["id"]: ("A", None, "answered")}

    monkeypatch.setattr(screening, "answer_questions", flaky)
    _seed(conn, _rec("S-1", status="shortlisted"), _rec("S-2", status="shortlisted"))
    health, out = _run(path, tmp_path)
    assert len(seen) == 2
    assert out.exists() and _last_run_written(tmp_path)


@pytest.mark.parametrize("error", [
    _api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"),
    _api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the API"),
    _api_error(anthropic.RateLimitError, 429, "rate limited"),
    _api_error(anthropic.InternalServerError, 529, "overloaded"),
    anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
])
def test_a_systemic_failure_stops_screening_for_the_run_and_the_run_completes(monkeypatch, db, tmp_path, error):
    _scheduled_on(monkeypatch)
    client = FakeClient([error])
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    path, conn = db
    _seed(conn, *[_rec(f"S-{i}", status="shortlisted") for i in range(4)])
    health, out = _run(path, tmp_path)
    assert len(client.calls) == 1  # stopped after the first systemic failure
    assert health["Claude screening"].startswith("error: Claude calls stopped for this run")
    row = store.get_source_health(conn, TEST_TENANT_ID, "Claude")
    assert row["last_result"].startswith("error")
    assert out.exists() and _last_run_written(tmp_path)


def test_portal_health_lists_claude(monkeypatch, db):
    _key(monkeypatch)
    entries = {e["name"]: e for e in api.get_health(tenant_id=TEST_TENANT_ID)}
    claude = entries["Claude"]
    assert claude["status"] == "live"  # manual drafts is on by default
    assert "manual drafts" in claude["detail"] and "of 60 calls" in claude["detail"]
    assert "~$0.00 of $7.00" in claude["detail"]


# ── §3.4: screening on request, and the bounded backfill ────────────────────

def test_screen_one_tender_on_request(monkeypatch, fake, db):
    from fastapi import HTTPException
    _key(monkeypatch)
    path, conn = db
    _seed(conn, _rec("T-1"))
    with pytest.raises(HTTPException) as e:
        api.screen_tender("T-1", tenant_id=TEST_TENANT_ID)
    assert e.value.status_code == 409 and "LLM_SCREENING" in e.value.detail
    assert fake.calls == []

    monkeypatch.setenv("LLM_SCREENING", "on")
    q_id = store.get_tenant_screening_questions(conn, TEST_TENANT_ID, enabled_only=True)[0]["id"]
    fake.script = [REPLY % q_id]
    result = api.screen_tender("T-1", tenant_id=TEST_TENANT_ID)
    assert result["answered"] == 1
    assert any(a["answer_text"] == "Brussels" for a in result["screening_answers"])
    assert len(fake.calls) == 1


def test_screen_on_request_reports_an_api_failure(monkeypatch, db):
    from fastapi import HTTPException
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    client = FakeClient([_api_error(anthropic.AuthenticationError, 401, "invalid x-api-key")])
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
    path, conn = db
    _seed(conn, _rec("T-1"))
    with pytest.raises(HTTPException) as e:
        api.screen_tender("T-1", tenant_id=TEST_TENANT_ID)
    assert e.value.status_code == 502 and "AuthenticationError" in e.value.detail


def test_backfill_is_bounded_by_its_limit(monkeypatch, fake, db):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    path, conn = db
    q_id = store.get_tenant_screening_questions(conn, TEST_TENANT_ID, enabled_only=True)[0]["id"]
    fake.script = [REPLY % q_id]
    _seed(conn, *[_rec(f"S-{i}", status="shortlisted") for i in range(5)], _rec("N-1"))
    summary = screening.backfill(conn, TEST_TENANT_ID, limit=3)
    assert summary["candidates"] == 5 and summary["attempted"] == 3 and summary["screened"] == 3
    assert len(fake.calls) == 3


def test_backfill_dry_run_makes_no_calls(monkeypatch, fake, db):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    path, conn = db
    _seed(conn, *[_rec(f"S-{i}", status="shortlisted") for i in range(4)])
    summary = screening.backfill(conn, TEST_TENANT_ID, limit=2, dry_run=True)
    assert summary == {"candidates": 4, "attempted": 2, "screened": 0, "failed": 0,
                       "stopped": None, "dry_run": True}
    assert fake.calls == []


def test_backfill_stops_when_the_gateway_says_no(monkeypatch, fake, db):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_SCREENING", "on")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_MONTH", "1")
    path, conn = db
    _seed(conn, *[_rec(f"S-{i}", status="shortlisted") for i in range(3)])
    summary = screening.backfill(conn, TEST_TENANT_ID, limit=3)
    assert summary["attempted"] == 1 and "Monthly" in summary["stopped"]
    assert len(fake.calls) == 1


@pytest.mark.parametrize("limit", [0, -1, None, "10"])
def test_backfill_requires_a_positive_limit(db, limit):
    path, conn = db
    with pytest.raises(ValueError):
        screening.backfill(conn, TEST_TENANT_ID, limit=limit)


def test_backfill_cli_requires_a_limit(monkeypatch):
    import sys
    import cli
    monkeypatch.setattr(sys, "argv", ["tenderizer", "screen-backfill", "--tenant", "1"])
    with pytest.raises(SystemExit):
        cli.main()


# ── manual drafts keep working, through the gateway ─────────────────────────

def test_manual_draft_extraction_goes_through_the_gateway(monkeypatch, fake):
    _key(monkeypatch)
    fake.script = [json.dumps({"fields": {}})]
    reading = manual_drafts.Reading(text="[a.pdf p.1]\nLieferung von Zelten")
    manual_drafts.extract_fields(reading)
    assert len(fake.calls) == 1 and llm.month_usage()["by_feature"]["manual_drafts"]["calls"] == 1


def test_manual_drafts_switched_off_fails_the_draft_with_the_reason(monkeypatch, fake):
    _key(monkeypatch)
    monkeypatch.setenv("LLM_MANUAL_DRAFTS", "off")
    with pytest.raises(manual_drafts.DraftError) as e:
        manual_drafts.extract_fields(manual_drafts.Reading(text="x"))
    assert "LLM_MANUAL_DRAFTS" in str(e.value)
    assert fake.calls == []


def test_concurrent_callers_never_take_the_month_past_its_ceiling(monkeypatch):
    # The reservation is one conditional UPDATE; threads racing for the last
    # calls either get one or are refused — never more than the ceiling.
    import threading
    monkeypatch.setenv("LLM_MAX_CALLS_PER_MONTH", "5")
    llm._engine()  # create the tables before the race
    results = []
    def take():
        try:
            results.append(llm._reserve_monthly_call("screening", "m"))
        except llm.UsageUnavailable:
            results.append(False)  # e.g. SQLite busy: refused, never let through
    threads = [threading.Thread(target=take) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    granted = sum(results)
    assert granted <= 5
    assert llm.month_usage()["calls"] == granted
