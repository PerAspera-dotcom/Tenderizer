"""CR-015 §3.6 — does a smaller model screen as well as claude-sonnet-4-6?

Takes N surfaced notices with text from a SQLite copy of the tenders table
(read-only; default data/tenders.db), asks the five default screening
questions with each model through the llm gateway, and writes a side-by-side
file plus a summary. Costs a few cents; needs ANTHROPIC_API_KEY.

    LLM_SCREENING=on python scripts/cr015_screening_model_compare.py --n 20

Nothing is written to the tenders data. The gateway counts these calls in a
scratch SQLite file, not the real usage tables (run it without DATABASE_URL).
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm        # noqa: E402
import screening  # noqa: E402
import store      # noqa: E402

ARMS = [
    ("claude-sonnet-4-6", {}),
    ("claude-haiku-5-5", {"output_config": {"effort": "low"}}),
]
PRICES = {"claude-sonnet-4-6": (3.00, 15.00), "claude-haiku-5-5": (0.10, 0.50)}  # $/1M in, out


def _notices(db_path, n):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT pub_number, tag_line, tag_line_en, description, description_en
        FROM tenders
        WHERE (exclude_reason IS NULL OR exclude_reason = '')
          AND length(COALESCE(NULLIF(description_en, ''), description, '')) > 200
        ORDER BY pub_date DESC LIMIT ?""", (n,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "tenders.db"))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=str(pathlib.Path(tempfile.gettempdir()) / "cr015_screening_compare.json"))
    args = ap.parse_args()

    if os.getenv("DATABASE_URL"):
        sys.exit("Unset DATABASE_URL: this comparison must not count against the real Claude budget")
    llm.SQLITE_PATH = pathlib.Path(tempfile.mkdtemp()) / "llm_usage.db"
    if not llm.available("screening"):
        sys.exit(f"Gateway refuses screening: {llm.unavailable_reason('screening')}")

    questions = [{"id": i + 1, "question_text": q} for i, q in enumerate(store._DEFAULT_SCREENING_QUESTIONS)]
    numbered = "\n".join(f"{q['id']}. {q['question_text']}" for q in questions)
    notices = _notices(args.db, args.n)
    results, tokens = [], {m: [0, 0] for m, _ in ARMS}
    for rec in notices:
        text = screening._record_text(rec)[:screening._MAX_CONTEXT_CHARS]
        row = {"pub_number": rec["pub_number"], "answers": {}}
        for model, extra in ARMS:
            msg = llm.create("screening", model=model, max_tokens=2048,
                             messages=[{"role": "user", "content": screening._SCREENING_PROMPT.format(
                                 text=text, questions=numbered)}], **extra)
            reply = llm.text_of(msg)
            if msg is not None and getattr(msg, "usage", None):
                tokens[model][0] += msg.usage.input_tokens
                tokens[model][1] += msg.usage.output_tokens
            parsed = screening._parse_screening_response(reply, {q["id"] for q in questions}) if reply else {}
            row["answers"][model] = {str(k): {"answer": a, "snippet": s, "status": st}
                                     for k, (a, s, st) in parsed.items()}
            if reply is None:
                row["answers"][model]["error"] = llm.last_refusal()
        results.append(row)

    base, other = ARMS[0][0], ARMS[1][0]
    same_status = total = snippet_in_text = snippets = 0
    for rec, row in zip(notices, results):
        text = screening._record_text(rec)
        for q in questions:
            a = row["answers"][base].get(str(q["id"]))
            b = row["answers"][other].get(str(q["id"]))
            if a and b:
                total += 1
                same_status += a["status"] == b["status"]
            if b and b["snippet"]:
                snippets += 1
                snippet_in_text += b["snippet"] in text
    summary = {
        "notices": len(notices),
        "answered_vs_not_stated_agreement": f"{same_status}/{total}",
        f"{other}_snippets_found_verbatim": f"{snippet_in_text}/{snippets}",
        "cost_usd": {m: round(i / 1e6 * PRICES[m][0] + o / 1e6 * PRICES[m][1], 4)
                     for m, (i, o) in tokens.items()},
        "tokens": tokens,
    }
    pathlib.Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2,
                                                  ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Side-by-side answers: {args.out}")


if __name__ == "__main__":
    main()
