"""
Response Draft Agent — Local Evaluation Harness
================================================

Evaluates the response drafter (PR-05) against the 200-ticket ground truth
response dataset. For each ticket, the harness provides the actual expected
documents as context (simulating a perfect RAG retrieval), then scores the
draft on citation coverage, must_mention compliance, must_not_claim violations,
answerability accuracy, and confidence calibration.

Metrics
-------
  citation_coverage       — fraction of expected doc IDs cited in the draft
  must_mention_coverage   — fraction of must_mention terms found in the draft
  must_not_claim_rate     — fraction of must_not_claim phrases found (lower = better)
  answered_fully_correct  — correct binary classification of answered_fully
  confidence_on_correct   — mean confidence when answered_fully is correct
  confidence_on_wrong     — mean confidence when answered_fully is wrong

Usage
-----
    python run_response_draft_eval.py --sample 20 --experiment-name "draft-smoke-test"
    python run_response_draft_eval.py
    python run_response_draft_eval.py --experiment-name "draft-v1-gemini" --concurrency 4

Results
-------
Console:  Printed summary table at end of run.
CSV:      evaluation/results/{experiment_name}.csv

Notes
-----
- This eval uses ground_truth_responses.json (200 tickets), NOT development_tickets.json.
- Documents are passed directly from documentation.json — no vector search required.
- Tickets marked must_not_auto_respond=true are included but expected to produce
  escalation placeholders, not substantive drafts.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except ImportError:
    pass

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: uv pip install 'openai>=1.0.0'")


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

TICKETS_PATH   = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "development_tickets.json"
GT_PATH        = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "ground_truth_responses.json"
DOCS_PATH      = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "documentation.json"
RESULTS_DIR    = Path(__file__).parent / "results"

MODEL = os.getenv("MODEL_NAME", "gpt-4.1-mini")

CSV_COLUMNS = [
    "ticket_id",
    "intent",
    "must_not_auto_respond",
    "citation_coverage",
    "must_mention_coverage",
    "must_not_claim_rate",
    "predicted_answered_fully",
    "expected_answered_fully",
    "answered_fully_correct",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/response_draft_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a support response drafter for CloudServe Solutions, a cloud infrastructure platform.
Your job is to write a clear, accurate response to a customer support ticket using ONLY
the provided documentation. You must not invent any information.

You MUST work through exactly five steps before outputting your answer.
Do not skip steps. Do not merge steps. Show your reasoning for each step.

Your final output must be a single JSON object matching this schema:
{
  "draft": string,
  "citations": [list of doc IDs],
  "answered_fully": true | false,
  "confidence": float (0.0–1.0),
  "reasoning_summary": string
}

Do not include any text after the JSON object.\
"""

USER_PROMPT_TEMPLATE = """\
Draft a response to the following support ticket using only the provided documents.

--- TICKET ---
Ticket ID:      {ticket_id}
Channel:        {channel}
Body:           {body}
Customer Tier:  {customer_tier}
Language:       {language_fluency}
--- END TICKET ---

--- UPSTREAM CLASSIFICATION ---
Intent:                 {intent}
Urgency:                {urgency}
Must Not Auto-Respond:  {must_not_auto_respond}
--- END UPSTREAM CLASSIFICATION ---

--- RELEVANT DOCUMENTS ---
{documents_block}
--- END RELEVANT DOCUMENTS ---

Work through each step:

STEP 1 — REVIEW ROUTING GATE & ESCALATION CONTEXT
Note if must_not_auto_respond is true.
Even if must_not_auto_respond is true, DO NOT stop or output an escalation placeholder.
Instead, proceed through Steps 2 to 5 to generate a complete, document-grounded draft response.
This draft will be provided to the human support agent as a ready-to-edit suggested response to save resolution time.

STEP 2 — MAP DOCUMENTS TO THE CUSTOMER'S QUESTION
Identify which parts of the customer's question each document addresses.
Note any part of the question that is NOT covered by any document — you will need
to acknowledge this gap explicitly in the draft.

STEP 3 — DRAFT THE RESPONSE
Write the customer-facing response following these rules:
  - Open with a brief acknowledgement of the problem (one sentence)
  - Present the resolution steps or information drawn directly from the documents
  - For each factual claim, note internally which document it comes from (you will
    cite these in the output, not in the response text itself)
  - If a part of the question cannot be answered from the documents, include a sentence
    such as: "I am not able to confirm [X] from our documentation — a member of our
    team will follow up on this point."
  - Close with a next step or offer of further assistance

Language adaptation:
  - fluent: standard professional tone, technical terms acceptable
  - non_fluent: short sentences, plain vocabulary, avoid idioms and abbreviations

Urgency adaptation:
  - high: action-first, skip pleasantries, lead with the most important step
  - medium: professional and clear
  - low: conversational, can include brief context before the resolution

STEP 4 — VERIFY NO HALLUCINATION
Review your draft sentence by sentence.
For each factual claim, confirm it appears explicitly in one of the provided documents.
If you find any sentence that you cannot attribute to a specific document:
  - Either remove it, or
  - Replace it with an explicit acknowledgement of uncertainty

List the doc IDs that provided content for the draft.

STEP 5 — OUTPUT FINAL JSON

{{
  "draft": "<the complete response text>",
  "citations": [<list of doc IDs that contributed content>],
  "answered_fully": <true if every aspect addressed, false if any gap acknowledged>,
  "confidence": <float>,
  "reasoning_summary": "<one sentence on coverage and any gaps>"
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_data(sample: int | None = None) -> tuple[list[dict], dict, dict]:
    with open(GT_PATH) as f:
        gt_responses = json.load(f)

    with open(TICKETS_PATH) as f:
        all_tickets = json.load(f)
    ticket_by_id = {t["ticket_id"]: t for t in all_tickets}

    with open(DOCS_PATH) as f:
        docs = json.load(f)
    doc_store = {d["doc_id"]: d for d in docs}

    # Attach ticket data to each ground truth entry
    combined = []
    for gt in gt_responses:
        tid = gt["ticket_id"]
        if tid in ticket_by_id:
            combined.append({"gt": gt, "ticket": ticket_by_id[tid]})

    if sample:
        combined = combined[:sample]

    return combined, doc_store


def format_documents_block(doc_objects: list[dict]) -> str:
    parts = []
    for d in doc_objects:
        content = d.get("content", "")[:1500]
        parts.append(
            f"[DOC-ID] {d['doc_id']}\n"
            f"[TITLE] {d['title']}\n"
            f"[CONTENT]\n{content}\n"
            f"[END DOC]"
        )
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# LLM call
# ──────────────────────────────────────────────────────────────────────────────

def build_llm_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("No API key found. Set OPENAI_API_KEY in .env.")
    return OpenAI(api_key=api_key)


def parse_json_from_response(text: str) -> dict:
    matches = list(re.finditer(r'\{[^{}]*"draft"[^{}]*\}', text, re.DOTALL))
    if matches:
        try:
            return json.loads(matches[-1].group())
        except json.JSONDecodeError:
            pass
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    last_brace = cleaned.rfind("}")
    first_brace = cleaned.rfind("{", 0, last_brace)
    if first_brace != -1 and last_brace != -1:
        try:
            return json.loads(cleaned[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from model output:\n{text[:500]}")


def draft_response(item: dict, doc_store: dict, llm: OpenAI, max_retries: int = 2) -> dict:
    ticket    = item["ticket"]
    gt        = item["gt"]
    labels    = ticket["labels"]

    doc_ids = gt.get("expected_doc_ids", [])
    docs    = [doc_store[did] for did in doc_ids if did in doc_store]
    docs_block = format_documents_block(docs) if docs else "(No relevant documents retrieved.)"

    subject = ticket.get("subject") or "(none)"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=ticket["ticket_id"],
        channel=ticket["channel"],
        body=ticket["body"],
        customer_tier=ticket["customer_tier"],
        language_fluency=ticket["language_fluency"],
        intent=labels["intent"],
        urgency=labels["urgency"],
        must_not_auto_respond=labels["must_not_auto_respond"],
        documents_block=docs_block,
    )

    for attempt in range(max_retries + 1):
        try:
            response = llm.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content
            result = parse_json_from_response(raw)

            result["draft"]          = result.get("draft", "")
            result["citations"]      = result.get("citations", [])
            result["answered_fully"] = bool(result.get("answered_fully", False))
            result["confidence"]     = float(result.get("confidence", 0.5))
            result["confidence"]     = max(0.0, min(1.0, result["confidence"]))
            result["raw_response"]   = raw
            return result

        except Exception as exc:
            if attempt == max_retries:
                return {
                    "draft": "",
                    "citations": [],
                    "answered_fully": False,
                    "confidence": 0.0,
                    "reasoning_summary": f"Draft failed: {exc}",
                    "raw_response": str(exc),
                    "error": True,
                }
            time.sleep(1.5 * (attempt + 1))


# ──────────────────────────────────────────────────────────────────────────────
# Evaluator functions
# ──────────────────────────────────────────────────────────────────────────────

def eval_citation_coverage(prediction: dict, gt: dict) -> float:
    expected = set(gt.get("expected_doc_ids", []))
    cited    = set(prediction.get("citations", []))
    if not expected:
        return 1.0
    return len(cited & expected) / len(expected)


def eval_must_mention_coverage(prediction: dict, gt: dict) -> float:
    must_mention = gt.get("must_mention", [])
    if not must_mention:
        return 1.0
    draft = prediction.get("draft", "").lower()
    hits  = sum(1 for term in must_mention if term.lower() in draft)
    return hits / len(must_mention)


def eval_must_not_claim_rate(prediction: dict, gt: dict) -> float:
    must_not = gt.get("must_not_claim", [])
    if not must_not:
        return 0.0
    draft = prediction.get("draft", "").lower()
    hits  = sum(1 for phrase in must_not if phrase.lower() in draft)
    return hits / len(must_not)


def eval_answered_fully(prediction: dict, ticket: dict, gt: dict) -> dict:
    predicted = prediction.get("answered_fully", False)
    # Ground truth: answerable from docs AND not must_not_auto_respond
    expected = (
        ticket["labels"]["answerable_from_docs"]
        and not ticket["labels"]["must_not_auto_respond"]
    )
    return {"predicted": predicted, "expected": expected, "correct": int(predicted == expected)}


def eval_confidence_calibration(prediction: dict, ticket: dict, gt: dict) -> dict:
    confidence = float(prediction.get("confidence", 0.5))
    expected_full = (
        ticket["labels"]["answerable_from_docs"]
        and not ticket["labels"]["must_not_auto_respond"]
    )
    is_correct = prediction.get("answered_fully", False) == expected_full
    return {"bucket": "correct" if is_correct else "wrong", "score": confidence}


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation runner
# ──────────────────────────────────────────────────────────────────────────────

def run_evaluation(combined: list[dict], doc_store: dict, llm: OpenAI, concurrency: int) -> list[dict]:
    total   = len(combined)
    done    = 0
    results = []

    future_to_item = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for item in combined:
            future = executor.submit(draft_response, item, doc_store, llm)
            future_to_item[future] = item

        for future in as_completed(future_to_item):
            item       = future_to_item[future]
            prediction = future.result()
            ticket     = item["ticket"]
            gt         = item["gt"]

            cc_result = eval_citation_coverage(prediction, gt)
            mm_result = eval_must_mention_coverage(prediction, gt)
            mnc_result = eval_must_not_claim_rate(prediction, gt)
            af_result = eval_answered_fully(prediction, ticket, gt)
            conf_result = eval_confidence_calibration(prediction, ticket, gt)

            results.append({
                "ticket_id":               ticket["ticket_id"],
                "intent":                  ticket["labels"]["intent"],
                "must_not_auto_respond":   ticket["labels"]["must_not_auto_respond"],
                "citation_coverage":       round(cc_result, 4),
                "must_mention_coverage":   round(mm_result, 4),
                "must_not_claim_rate":     round(mnc_result, 4),
                "predicted_answered_fully": af_result["predicted"],
                "expected_answered_fully":  af_result["expected"],
                "answered_fully_correct":   af_result["correct"],
                "confidence":              prediction["confidence"],
                "confidence_bucket":       conf_result["bucket"],
                "error":                   prediction.get("error", False),
            })

            done += 1
            print(f"\r  [{done}/{total}]", end="", flush=True)

    print()
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────────────────────

def save_results_csv(results: list[dict], experiment_name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{experiment_name}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    return path


def print_summary(results: list[dict], csv_path: Path) -> None:
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    citation_scores   = [r["citation_coverage"]      for r in results]
    mm_scores         = [r["must_mention_coverage"]   for r in results]
    mnc_scores        = [r["must_not_claim_rate"]     for r in results]
    af_scores         = [r["answered_fully_correct"]  for r in results]
    conf_correct      = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong        = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count       = sum(1 for r in results if r["error"])

    violation_count = sum(1 for r in results if r["must_not_claim_rate"] > 0)

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("citation_coverage",       mean(citation_scores),  len(citation_scores)),
        ("must_mention_coverage",   mean(mm_scores),        len(mm_scores)),
        ("must_not_claim_rate",     mean(mnc_scores),       len(mnc_scores)),
        ("answered_fully_accuracy", mean(af_scores),        len(af_scores)),
        ("confidence_on_correct",   mean(conf_correct),     len(conf_correct)),
        ("confidence_on_wrong",     mean(conf_wrong),       len(conf_wrong)),
    ]

    for name, value, n in rows:
        print(f"  {name:<35} {value:.4f}  (n={n})")

    if violation_count:
        print(f"\n  must_not_claim violations in {violation_count}/{len(results)} drafts")
    if error_count:
        print(f"  Errors: {error_count}/{len(results)} tickets failed")

    print("=" * 60)
    print(f"\nResults saved to: {csv_path}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run response draft agent evaluation locally.")
    p.add_argument("--sample",          type=int, default=None,
                   help="Evaluate only the first N ground truth entries (default: all 200)")
    p.add_argument("--experiment-name", type=str, default=None)
    p.add_argument("--concurrency",     type=int, default=4)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    llm               = build_llm_client()
    combined, doc_store = load_data(sample=args.sample)
    n                 = len(combined)

    experiment_name = args.experiment_name or \
        f"response-draft-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"Model:       {MODEL}")
    print(f"Entries:     {n}")
    print(f"Docs:        {len(doc_store)}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Experiment:  {experiment_name}")
    print()

    results  = run_evaluation(combined, doc_store, llm, args.concurrency)
    csv_path = save_results_csv(results, experiment_name)
    print_summary(results, csv_path)


if __name__ == "__main__":
    main()
