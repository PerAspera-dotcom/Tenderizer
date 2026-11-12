"""CR-015 §3.1 — the one gateway to the Claude API.

Nothing else in src/ imports `anthropic` or constructs a client
(tests/test_66_llm_gateway.py fails the build if anything does). Every call
goes through `create(feature, **request)`, which checks, in this order:

  1. A key is present (ANTHROPIC_API_KEY). Necessary, never sufficient.
  2. The calling feature is switched on — one environment variable each:
       LLM_SCREENING, LLM_VAULT, LLM_COMPOSER   off unless set to on
       LLM_MANUAL_DRAFTS                         on unless set to off
     (manual-draft extraction is why the key exists — CR-015 §4 — so a
     forgotten variable must not break it).
  3. The call's class is permitted. A call made anywhere inside
     `scheduled_run()` (run.run_pipeline wraps itself in one) is
     *scheduled*, whatever the caller says about itself, and is refused
     unless that feature's own scheduled switch is on
     (LLM_SCHEDULED_SCREENING, ...). Everything else is *interactive*:
     a person acting on one thing.
  4. The budget has room, counted here once so a new caller cannot forget:
       LLM_MAX_CALLS_PER_RUN     scheduled calls per pipeline run (default 10)
       LLM_MAX_CALLS_PER_MONTH   all calls, calendar month UTC (default 60)
       LLM_MAX_USD_PER_MONTH     estimated spend, same month (default 7)
     The dollar ceiling is the one meant to bind: one call costs anything
     from ~$0.02 (screening) to ~$1.20 (a 200-page scanned pack), so no call
     count both allows normal use and stays under the $10 console cap. Spend
     is priced from recorded tokens (PRICES) and checked before each call;
     the ~$3 left under the console cap covers calls already in flight when
     the ceiling is reached.

A refused call returns None — the same "nothing came back" the callers
already handle for a missing key — and `last_refusal()` says why. API
errors are handled here too (§3.5): every anthropic.APIError returns None.
A systemic one (auth, permission, unknown model, exhausted credit, rate
limiting or server errors that outlived the SDK's own retries, no
connection) also stops all further calls for the rest of the scheduled run.
Errors that are not API errors (a bug in our own code) still raise.

The counters live in the database (schema.llm_usage_months / llm_usage),
not a file, so the guard fails closed: if they cannot be read or written —
table missing, database down — the call is refused, never let through as
"nothing spent yet". The month's call is reserved with one conditional
UPDATE ... WHERE calls < limit before the request goes out.
"""
import contextlib
import contextvars
import logging
import os
import pathlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

import db
from schema import llm_usage, llm_usage_months, metadata

ROOT = pathlib.Path(__file__).resolve().parent.parent

FEATURES = ("screening", "vault", "composer", "manual_drafts")
_DEFAULT_ON = {"manual_drafts"}
DEFAULT_MAX_CALLS_PER_RUN = 10
DEFAULT_MAX_CALLS_PER_MONTH = 60
DEFAULT_MAX_USD_PER_MONTH = 7.0

# $ per million tokens (input, output), list prices. A model missing here is
# priced at UNKNOWN_MODEL_PRICE — deliberately high, so a new model makes the
# estimate too pessimistic rather than too optimistic.
PRICES = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
UNKNOWN_MODEL_PRICE = (15.00, 75.00)

# Local development / tests without DATABASE_URL: the app's own SQLite file.
SQLITE_PATH = ROOT / "data" / "tenders.db"

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}

log = logging.getLogger(__name__)


# ── switches ────────────────────────────────────────────────────────────────

def _flag(name, default):
    value = (os.getenv(name) or "").strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    return default


def key_present():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def feature_enabled(feature):
    _check_feature(feature)
    return _flag(f"LLM_{feature.upper()}", feature in _DEFAULT_ON)


def scheduled_enabled(feature):
    _check_feature(feature)
    return _flag(f"LLM_SCHEDULED_{feature.upper()}", False)


def _int_env(name, default):
    try:
        return max(0, int(os.getenv(name, "")))
    except ValueError:
        return default


def max_calls_per_run():
    return _int_env("LLM_MAX_CALLS_PER_RUN", DEFAULT_MAX_CALLS_PER_RUN)


def max_calls_per_month():
    return _int_env("LLM_MAX_CALLS_PER_MONTH", DEFAULT_MAX_CALLS_PER_MONTH)


def max_usd_per_month():
    try:
        return max(0.0, float(os.getenv("LLM_MAX_USD_PER_MONTH", "")))
    except ValueError:
        return DEFAULT_MAX_USD_PER_MONTH


def _check_feature(feature):
    if feature not in FEATURES:
        raise ValueError(f"unknown LLM feature {feature!r}")


# ── scheduled runs ──────────────────────────────────────────────────────────

@dataclass
class RunState:
    max_calls: int
    calls: int = 0
    failed_calls: int = 0
    stopped: str = None          # why scheduled calls stopped for this run, if they did
    errors: list = field(default_factory=list)


_run = contextvars.ContextVar("llm_scheduled_run", default=None)


@contextlib.contextmanager
def scheduled_run():
    """Every Claude call made inside this block is a scheduled call."""
    state = RunState(max_calls=max_calls_per_run())
    token = _run.set(state)
    try:
        yield state
    finally:
        _run.reset(token)


def current_run():
    return _run.get()


# ── monthly usage (database) ────────────────────────────────────────────────

class UsageUnavailable(RuntimeError):
    """The usage counters could not be read or written."""


_sqlite_ready = set()


def _engine():
    url = db.configured_url() or f"sqlite:///{SQLITE_PATH}"
    engine = db.get_engine(url)
    if engine.dialect.name == "sqlite" and url not in _sqlite_ready:
        # Local/test SQLite files get the tables the way store.init_db gives
        # them everything else. Postgres gets them only from the migration;
        # until it has run, every call is refused.
        metadata.create_all(engine, tables=[llm_usage_months, llm_usage], checkfirst=True)
        _sqlite_ready.add(url)
    return engine


def _month(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def price(model):
    return PRICES.get(model, UNKNOWN_MODEL_PRICE)


def _describe(error):
    return f"{type(error).__name__}: {str(error)[:300]}"


def _ensure_row(c, table, **key):
    exists = c.execute(select(func.count()).select_from(table).where(
        *[table.c[k] == v for k, v in key.items()])).scalar()
    if not exists:
        try:
            with c.begin_nested():
                c.execute(insert(table).values(**key))
        except IntegrityError:
            pass  # a concurrent caller inserted it first


def _reserve_monthly_call(feature, model):
    """Counts the call against this month before it is made, in one
    transaction. False if the month is already at its call ceiling. Raises
    UsageUnavailable if the counters cannot be reached."""
    month = _month()
    try:
        with _engine().begin() as c:
            _ensure_row(c, llm_usage_months, month=month)
            row = c.execute(update(llm_usage_months)
                            .where((llm_usage_months.c.month == month)
                                   & (llm_usage_months.c.calls < max_calls_per_month()))
                            .values(calls=llm_usage_months.c.calls + 1)
                            .returning(llm_usage_months.c.calls)).fetchone()
            if row is None:
                return False
            _ensure_row(c, llm_usage, month=month, feature=feature, model=model)
            c.execute(update(llm_usage)
                      .where((llm_usage.c.month == month) & (llm_usage.c.feature == feature)
                             & (llm_usage.c.model == model))
                      .values(calls=llm_usage.c.calls + 1))
        return True
    except Exception as e:
        raise UsageUnavailable(_describe(e)) from e


def _record_tokens(feature, model, message):
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    try:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return
    try:
        with _engine().begin() as c:
            c.execute(update(llm_usage)
                      .where((llm_usage.c.month == _month()) & (llm_usage.c.feature == feature)
                             & (llm_usage.c.model == model))
                      .values(input_tokens=llm_usage.c.input_tokens + input_tokens,
                              output_tokens=llm_usage.c.output_tokens + output_tokens))
    except Exception:
        # The call already happened and was counted; losing its tokens only
        # makes the spend estimate low by this one call. Logged loudly.
        log.exception("Could not record Claude token usage for %s (%s in / %s out)",
                      feature, input_tokens, output_tokens)


def month_usage():
    """This month's calls, tokens and estimated spend. Raises
    UsageUnavailable if the counters cannot be read."""
    month = _month()
    try:
        with _engine().connect() as c:
            calls = c.execute(select(llm_usage_months.c.calls)
                              .where(llm_usage_months.c.month == month)).scalar() or 0
            rows = c.execute(select(llm_usage.c.feature, llm_usage.c.model, llm_usage.c.calls,
                                    llm_usage.c.input_tokens, llm_usage.c.output_tokens)
                             .where(llm_usage.c.month == month)).fetchall()
    except Exception as e:
        raise UsageUnavailable(_describe(e)) from e
    by_feature, usd = {}, 0.0
    for feature, model, n, tokens_in, tokens_out in rows:
        p_in, p_out = price(model)
        cost = tokens_in / 1e6 * p_in + tokens_out / 1e6 * p_out
        usd += cost
        f = by_feature.setdefault(feature, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
        f["calls"] += n
        f["input_tokens"] += tokens_in
        f["output_tokens"] += tokens_out
        f["usd"] = round(f["usd"] + cost, 4)
    return {"month": month, "calls": calls, "limit": max_calls_per_month(),
            "usd": round(usd, 4), "usd_limit": max_usd_per_month(), "by_feature": by_feature}


def _budget_reason(usage):
    if usage["calls"] >= usage["limit"]:
        return f"Monthly Claude budget reached ({usage['limit']} calls)"
    if usage["usd"] >= usage["usd_limit"]:
        return f"Monthly Claude spending limit reached (~${usage['usd_limit']:.2f} estimated)"
    return None


# ── the gate ────────────────────────────────────────────────────────────────

_local = threading.local()


def last_refusal():
    """Why the last create() on this thread returned None (None after a
    successful call)."""
    return getattr(_local, "reason", None)


def _refuse(reason):
    _local.reason = reason
    return None


def unavailable_reason(feature, has_client=False):
    """Checks 1-4 without spending anything: None if a call would be made
    right now, otherwise the reason it would be refused."""
    _check_feature(feature)
    if not has_client and not key_present():
        return "No Anthropic API key is configured"
    if not feature_enabled(feature):
        return f"Claude is switched off for {feature.replace('_', ' ')} (LLM_{feature.upper()})"
    run = current_run()
    if run is not None:
        if not scheduled_enabled(feature):
            return (f"Scheduled Claude calls are switched off for {feature.replace('_', ' ')} "
                    f"(LLM_SCHEDULED_{feature.upper()})")
        if run.stopped:
            return run.stopped
        if run.calls >= run.max_calls:
            return f"Per-run Claude budget reached ({run.max_calls} calls)"
    try:
        return _budget_reason(month_usage())
    except UsageUnavailable as e:
        return f"Claude usage counter unavailable, so calls are refused ({e})"


def available(feature):
    return unavailable_reason(feature) is None


def _systemic(error):
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                          anthropic.NotFoundError, anthropic.RateLimitError,
                          anthropic.APIConnectionError)):
        return True
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return True
        text = str(error).lower()
        if "credit balance" in text or "billing" in text:
            return True
    return False


def create(feature, *, client=None, **request):
    """client.messages.create(**request) if every check passes, else None.
    `client` is a test seam only — a fake standing in for the real client."""
    reason = unavailable_reason(feature, has_client=client is not None)
    if reason:
        return _refuse(reason)
    run = current_run()
    model = str(request.get("model") or "unknown")
    try:
        reserved = _reserve_monthly_call(feature, model)
    except UsageUnavailable as e:
        log.error("Claude call for %s refused: usage counter unavailable (%s)", feature, e)
        return _refuse(f"Claude usage counter unavailable, so calls are refused ({e})")
    if not reserved:
        return _refuse(f"Monthly Claude budget reached ({max_calls_per_month()} calls)")
    if run is not None:
        run.calls += 1
    try:
        message = (client or anthropic.Anthropic()).messages.create(**request)
    except anthropic.APIError as e:
        detail = _describe(e)
        log.warning("Claude call for %s failed: %s", feature, detail)
        if run is not None:
            run.failed_calls += 1
            run.errors.append(detail)
            if _systemic(e):
                run.stopped = f"Claude calls stopped for this run after {detail}"
        return _refuse(f"The Claude API call failed ({detail})")
    _record_tokens(feature, model, message)
    _local.reason = None
    return message


def text_of(message):
    """The first text block's text, or None for a refused/empty call."""
    if message is None:
        return None
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", "text") == "text" and getattr(block, "text", None) is not None:
            return block.text
    return None


def status():
    """For Portal Health: what is switched on, and this month's spend."""
    try:
        usage, usage_error = month_usage(), None
    except UsageUnavailable as e:
        usage, usage_error = None, str(e)
    return {
        "key_present": key_present(),
        "features": {f: feature_enabled(f) for f in FEATURES},
        "scheduled": {f: scheduled_enabled(f) for f in FEATURES},
        "max_calls_per_run": max_calls_per_run(),
        "usage": usage,
        "usage_error": usage_error,
    }
