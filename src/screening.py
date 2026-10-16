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
Returns {} if ANTHROPIC_API_KEY isn't configured, there's no notice text to
work from, or the response couldn't be parsed — same graceful-skip
convention as vault.extract_metadata/composer.extract_requirements; a
tender simply has no cached answers yet rather than a broken pipeline run.
"""
import json
import os

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
    if not os.getenv("ANTHROPIC_API_KEY") or not questions:
        return {}
    text = _record_text(record)[:_MAX_CONTEXT_CHARS]
    if not text:
        return {}

    numbered = "\n".join(f"{q['id']}. {q['question_text']}" for q in questions)
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=2048,
        messages=[{"role": "user", "content": _SCREENING_PROMPT.format(text=text, questions=numbered)}],
    )
    valid_ids = {q["id"] for q in questions}
    return _parse_screening_response(message.content[0].text, valid_ids)


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
