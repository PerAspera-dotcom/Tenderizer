"""CR-011 §3 — per-organization configurable screening questions, answered
automatically from notice text so a reviewer can decide go/no-go on a
Review Queue tender without opening the documents.

Deliberately NOT re-implementing value / CPV / the existing tag_line-as-
summary display as LLM questions — those are already reliable structured
fields (normalize.py's value/cpv_codes, tag_line/description_en), and
re-deriving them via an LLM call would be slower, cost tokens, and risk a
worse answer than the data already on hand. This module exists for the
genuinely unstructured information the client's own default question set
(CR-011 §3, store._DEFAULT_SCREENING_QUESTIONS) asks about: delivery
location, quantities/lots, certifications, eligibility, contract duration.

Notice text only for v1 (tag_line_en + description_en) — "computed at
ingest" (run.py, after translation) means this runs the moment a notice is
scraped, when no documents have been attached to it yet regardless of which
of this codebase's three separate document systems (Vault's capability
library, Composer's per-tender SOW/PartA uploads, Scout's own
translated_documents/document_tender_links attachments) might eventually
hold one. Document-text enrichment is a deliberate, flagged follow-up —
raised and scoped out when this feature was planned, not built here.

answer_questions(record, questions) -> {question_id: (answer_text,
source_snippet, status)} — ONE Claude call per tender answering every
currently-missing question together, same token-efficient "one call
returns a JSON array" shape composer.extract_requirements already uses.
Returns {} if the gateway refuses the call (no key, screening switched off,
not permitted in a scheduled run, budget spent — see llm.py), there's no
notice text to work from, or the response couldn't be parsed — same
graceful-skip convention as vault.extract_metadata/composer.
extract_requirements; a tender simply has no cached answers yet rather than
a broken pipeline run.

CR-015 §3.4: screening no longer walks the corpus. It runs on request for
one tender (POST /api/tenders/{pub_number}/screen), optionally in the
scheduled run for shortlisted / needs-review tenders only (off unless
LLM_SCHEDULED_SCREENING is on), and as a bounded, explicitly invoked
backfill (src/screening_backfill.py). All three go through screen_record.
"""
import json

import llm
import store

CLAUDE_MODEL = "claude-sonnet-4-6"  # matches vault.CLAUDE_MODEL / composer.CLAUDE_MODEL

_SCREENING_PROMPT = """You are screening a public tender notice for a company deciding whether to \
pursue it, answering specific questions so a reviewer doesn't have to read the full notice to \
triage it.

NOTICE TEXT:
{text}

Answer each numbered question below using ONLY information explicitly stated in the notice text \
above. If the text does not say, answer exactly "Not stated" — never guess, infer, or fill in \
typical/default values.

QUESTIONS:
{questions}

Return ONLY a JSON array (no markdown fences, no other text), one object per question, each \
shaped exactly:
{{
  "question_id": <the question's own id number, copied from the list above>,
  "answer": "<a concise answer in your own words, or exactly \\"Not stated\\">",
  "source_snippet": "<the exact quoted sentence or phrase from the notice text that supports \
this answer, or \\"\\" if not stated>"
}}
"""

# A notice's own title+description is always short (unlike a full SOW
# document) — generous ceiling, never expected to actually bind.
_MAX_CONTEXT_CHARS = 20_000


def _record_text(record):
    tag = record.get("tag_line_en") or record.get("tag_line") or ""
    desc = record.get("description_en") or record.get("description") or ""
    return f"{tag}\n\n{desc}".strip()


def answer_questions(record, questions):
    """`questions`: [{"id": int, "question_text": str}, ...] — the tenant's
    currently-missing questions for this one tender (see store.
    missing_screening_question_ids). Only ever asks about what's actually
    missing, so an edited/newly-added question doesn't re-spend tokens
    re-answering questions this tender already has a cached answer for.
    """
    if not questions:
        return {}
    text = _record_text(record)[:_MAX_CONTEXT_CHARS]
    if not text:
        return {}

    numbered = "\n".join(f"{q['id']}. {q['question_text']}" for q in questions)
    message = llm.create(
        "screening", model=CLAUDE_MODEL, max_tokens=2048,
        messages=[{"role": "user", "content": _SCREENING_PROMPT.format(text=text, questions=numbered)}],
    )
    reply = llm.text_of(message)
    if reply is None:
        return {}
    valid_ids = {q["id"] for q in questions}
    return _parse_screening_response(reply, valid_ids)


def has_text(record):
    return bool(_record_text(record))


def screen_record(conn, tenant_id, record, questions):
    """Answers this tender's missing questions (of `questions`, the tenant's
    enabled ones) and stores the answers. Returns the number of answers
    stored; 0 when nothing was missing or there was no text (no call made);
    None when a call was attempted and nothing usable came back (the gateway
    refused or failed — llm.last_refusal() says why — or the reply could not
    be parsed)."""
    by_id = {q["id"]: q for q in questions}
    missing = store.missing_screening_question_ids(conn, tenant_id, record["pub_number"], list(by_id))
    if not missing or not has_text(record):
        return 0
    answers = answer_questions(record, [by_id[q_id] for q_id in missing])
    if not answers:
        return None
    for q_id, (answer_text, snippet, status) in answers.items():
        store.set_screening_answer(conn, tenant_id, record["pub_number"], q_id, answer_text, snippet, status)
    return len(answers)


BACKFILL_DEFAULT_STATUSES = ("shortlisted", "needs_review")


def backfill(conn, tenant_id, limit, statuses=BACKFILL_DEFAULT_STATUSES, dry_run=False):
    """CR-015 §3.4: what replaces the run's old corpus-wide backfill — an
    explicitly invoked, bounded pass (`tenderizer screen-backfill`). Screens
    at most `limit` visible tenders in `statuses` that have notice text and
    at least one missing answer to an enabled question. Each call still goes
    through the gateway (feature switch, monthly ceiling). Returns a summary
    dict; `dry_run` counts what would be screened and makes no call."""
    import visibility
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive whole number")
    questions = store.get_tenant_screening_questions(conn, tenant_id, enabled_only=True)
    summary = {"candidates": 0, "attempted": 0, "screened": 0, "failed": 0, "stopped": None, "dry_run": dry_run}
    if not questions:
        summary["stopped"] = "No screening questions are enabled"
        return summary
    ids = [q["id"] for q in questions]
    todo = []
    for r in store.all_records(conn, tenant_id):
        if visibility.is_hidden(r) or r.get("status") not in statuses or not has_text(r):
            continue
        if store.missing_screening_question_ids(conn, tenant_id, r["pub_number"], ids):
            todo.append(r)
    summary["candidates"] = len(todo)
    if dry_run:
        summary["attempted"] = min(limit, len(todo))
        return summary
    for r in todo[:limit]:
        reason = llm.unavailable_reason("screening")
        if reason:
            summary["stopped"] = reason
            break
        summary["attempted"] += 1
        if screen_record(conn, tenant_id, r, questions):
            summary["screened"] += 1
        else:
            summary["failed"] += 1
    return summary


def _parse_screening_response(text, valid_question_ids):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, list):
        return {}

    out = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            q_id = int(item.get("question_id"))
        except (TypeError, ValueError):
            continue
        if q_id not in valid_question_ids:
            continue
        answer = (item.get("answer") or "").strip()
        snippet = (item.get("source_snippet") or "").strip() or None
        if not answer or answer.lower() == "not stated":
            out[q_id] = ("Not stated", None, "not_stated")
        else:
            out[q_id] = (answer, snippet, "answered")
    return out
