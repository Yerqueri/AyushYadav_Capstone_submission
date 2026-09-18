"""
RAG Agent — Local Evaluation Harness
=====================================

Evaluates the document relevance assessor (PR-04) against the 500-ticket
development dataset. Retrieval uses ChromaDB semantic search (all-MiniLM-L6-v2)
seeded by ticket body + intent, followed by one-hop graph expansion via the
related_docs field in documentation.json (see retrieval.py).

Metrics
-------
  answerable_accuracy   — correct binary classification of answerable / not-answerable
  doc_precision         — of docs predicted relevant, fraction that are truly expected
  doc_recall            — of expected docs, fraction predicted as relevant
  confidence_on_correct — mean confidence when answerable assessment is correct
  confidence_on_wrong   — mean confidence when answerable assessment is wrong

Usage
-----
    python run_rag_eval.py --sample 20 --experiment-name "rag-smoke-test"
    python run_rag_eval.py
    python run_rag_eval.py --experiment-name "rag-v2-gemini" --concurrency 8

Results
-------
Console:  Printed summary table at end of run.
CSV:      evaluation/results/{experiment_name}.csv
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

from retrieval import build_collection, retrieve


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

DATASET_PATH = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "development_tickets.json"
DOCS_PATH    = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "documentation.json"
RESULTS_DIR  = Path(__file__).parent / "results"

MODEL = os.getenv("MODEL_NAME", "gpt-4.1-mini")

CSV_COLUMNS = [
    "ticket_id",
    "expected_answerable",
    "predicted_answerable",
    "answerable_correct",
    "expected_doc_ids",
    "predicted_doc_ids",
    "doc_precision",
    "doc_recall",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/rag_agent_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a document relevance assessor for CloudServe Solutions, a cloud infrastructure platform.

Your job:
1. Retrieve candidate documents from the knowledge base using the retrieve_documents tool.
2. Identify which retrieved documents genuinely address the ticket.
3. Decide whether the ticket can be fully answered from those documents alone.

TOOL AVAILABLE
  retrieve_documents(query)
  Searches the CloudServe knowledge base using semantic similarity and knowledge-graph
  expansion. Returns a ranked list of candidate documents, each formatted as:
    [DOC-ID]       unique identifier
    [TITLE]        document title
    [RELATED DOCS] comma-separated IDs of graph neighbours — use as scope context only,
                   not as additional evidence unless those docs appear in the result set
    [CONTENT]      document body

WORKFLOW — follow these steps in order, do not skip any:

STEP 1 — FORMULATE QUERY AND RETRIEVE
  State the customer's core problem in one sentence.
  Derive a focused search query that captures the specific symptom, product area, and
  key technical details, then call retrieve_documents with that query.

BEFORE STEP 1 — identify distinct problems in this ticket:
  Read the Subject and Body separately. List each distinct symptom or concern they describe.
  Subject and Body often describe DIFFERENT aspects of the same case — both matter.
  Each distinct problem you identify should be covered by at least one RELEVANT or SUPPORTING doc.

STEP 2 — ASSESS EACH RETRIEVED DOCUMENT
  For each document returned by the tool, state:
    RELEVANT     — directly addresses the core problem (same symptom, same product area,
                   or contains the exact resolution steps needed)
    SUPPORTING   — covers a distinct symptom or related sub-topic described in the ticket
                   that a thorough support response should address.
                   Mark SUPPORTING when:
                   • The ticket describes multiple distinct symptoms and this doc covers
                     one of them (e.g., ticket mentions both "login error" and "MFA code
                     rejected" — include docs for BOTH symptoms)
                   • The ticket subject and body point to different concerns — include
                     a doc for each concern
                   • The doc covers a common co-occurring issue
                   Err on the side of inclusion: prefer SUPPORTING over NOT RELEVANT
                   when any part of the ticket could reasonably reference this document.
    NOT RELEVANT — covers a completely different domain (e.g., a billing doc is never
                   relevant to a deployment failure, even if both mention "error")

STEP 3 — ASSESS ANSWERABILITY
  Based on RELEVANT + SUPPORTING documents together:
    answerable = true  ONLY if the documents provide ALL specific steps the customer
                       needs — no key step missing, nothing invented
    answerable = false if ANY of the following apply:
      - No documents were marked RELEVANT
      - The ticket requires account-specific or live-system information not in any document
      - The ticket involves a judgment call that only a human can make
      - The documents describe the symptom area but are missing the specific resolution
        procedure or commands needed
      - The ticket asks for an action (rollback, restore, override) only a human can authorize
    When in doubt, prefer answerable = false

STEP 4 — OUTPUT FINAL JSON
  Output a single JSON object. Do not include any text after it.
  {{
    "relevant_doc_ids": [<list of RELEVANT and SUPPORTING doc IDs, empty list if none>],
    "answerable": <true|false>,
    "confidence": <float 0.0–1.0>,
    "reasoning_summary": "<one sentence>"
  }}

  confidence calibration:
    >= 0.85    explicit match — document covers the exact symptom or procedure described
    0.70–0.84  partial match — general area covered, not all specifics
    < 0.70     weak match — related but does not directly answer\
"""

USER_PROMPT_TEMPLATE = """\
Assess the following support ticket. Start by calling retrieve_documents with a query
derived from the ticket's core problem.

--- TICKET ---
Ticket ID: {ticket_id}
Intent:    {intent}
Urgency:   {urgency}
Subject:   {subject}
Body:      {body}
--- END TICKET ---\
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_documents",
            "description": (
                "Search the CloudServe knowledge base using semantic similarity and "
                "knowledge-graph expansion. Returns ranked candidate documents for the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query based on the ticket's core problem. "
                            "Focus on the specific symptom, product area, and key technical details."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }
]


# ──────────────────────────────────────────────────────────────────────────────
# Document loading
# ──────────────────────────────────────────────────────────────────────────────

def load_docs() -> dict[str, dict]:
    with open(DOCS_PATH) as f:
        docs = json.load(f)
    return {d["doc_id"]: d for d in docs}


def format_documents_block(doc_objects: list[dict]) -> str:
    parts = []
    for d in doc_objects:
        content = d.get("content", "")[:1500]  # cap per doc to stay within context
        related = ", ".join(d.get("related_docs", [])) or "none"
        parts.append(
            f"[DOC-ID] {d['doc_id']}\n"
            f"[TITLE] {d['title']}\n"
            f"[RELATED DOCS] {related}\n"
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
    matches = list(re.finditer(r'\{[^{}]*"answerable"[^{}]*\}', text, re.DOTALL))
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


def run_rag_agent(
    ticket: dict,
    collection: "chromadb.Collection",
    doc_list: list[dict],
    llm: OpenAI,
    max_retries: int = 2,
) -> dict:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=ticket["ticket_id"],
        intent=ticket["labels"]["intent"],
        urgency=ticket["labels"]["urgency"],
        subject=ticket.get("subject") or "unknown",
        body=ticket["body"],
    )
    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        try:
            # Turn 1 — model formulates a query and calls retrieve_documents
            resp1 = llm.chat.completions.create(
                model=MODEL,
                messages=base_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=512,
            )
            msg1 = resp1.choices[0].message

            if msg1.tool_calls:
                tool_call = msg1.tool_calls[0]
                args      = json.loads(tool_call.function.arguments)
                query     = args.get("query", ticket["body"])

                candidates  = retrieve(query, doc_list, collection)
                tool_result = format_documents_block(candidates)

                messages = base_messages + [
                    {
                        "role":    "assistant",
                        "content": msg1.content,
                        "tool_calls": [
                            {
                                "id":   tool_call.id,
                                "type": "function",
                                "function": {
                                    "name":      tool_call.function.name,
                                    "arguments": tool_call.function.arguments,
                                },
                            }
                        ],
                    },
                    {
                        "role":         "tool",
                        "tool_call_id": tool_call.id,
                        "content":      tool_result,
                    },
                ]

                # Turn 2 — model assesses retrieved documents and outputs JSON
                resp2 = llm.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=1024,
                )
                raw = resp2.choices[0].message.content or ""
            else:
                # Model skipped the tool call — use its direct output
                raw = msg1.content or ""

            result = parse_json_from_response(raw)
            result["relevant_doc_ids"] = result.get("relevant_doc_ids", [])
            if not isinstance(result["relevant_doc_ids"], list):
                result["relevant_doc_ids"] = []
            result["answerable"]   = bool(result.get("answerable", False))
            result["confidence"]   = float(result.get("confidence", 0.5))
            result["confidence"]   = max(0.0, min(1.0, result["confidence"]))
            result["raw_response"] = raw
            return result

        except Exception as exc:
            if attempt == max_retries:
                return {
                    "relevant_doc_ids": [],
                    "answerable":       False,
                    "confidence":       0.0,
                    "reasoning_summary": f"RAG agent failed: {exc}",
                    "raw_response":     str(exc),
                    "error":            True,
                }
            time.sleep(1.5 * (attempt + 1))


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_dev_tickets(sample: int | None = None) -> list[dict]:
    with open(DATASET_PATH) as f:
        tickets = json.load(f)
    if sample:
        tickets = tickets[:sample]
    return tickets


# ──────────────────────────────────────────────────────────────────────────────
# Evaluator functions
# ──────────────────────────────────────────────────────────────────────────────

def eval_answerable_accuracy(prediction: dict, ground_truth: dict) -> dict:
    predicted = prediction.get("answerable", False)
    expected  = ground_truth.get("answerable_from_docs", False)
    return {"score": int(predicted == expected)}


def eval_doc_precision_recall(prediction: dict, ground_truth: dict) -> dict:
    predicted_set = set(prediction.get("relevant_doc_ids", []))
    expected_set  = set(ground_truth.get("expected_doc_ids", []))

    if not predicted_set and not expected_set:
        return {"precision": 1.0, "recall": 1.0}

    precision = len(predicted_set & expected_set) / len(predicted_set) if predicted_set else 0.0
    recall    = len(predicted_set & expected_set) / len(expected_set)  if expected_set  else 1.0
    return {"precision": precision, "recall": recall}


def eval_confidence_calibration(prediction: dict, ground_truth: dict) -> dict:
    confidence = float(prediction.get("confidence", 0.5))
    is_correct = prediction.get("answerable", False) == ground_truth.get("answerable_from_docs", False)
    return {"bucket": "correct" if is_correct else "wrong", "score": confidence}


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation runner
# ──────────────────────────────────────────────────────────────────────────────

def run_evaluation(tickets: list[dict], doc_store: dict, llm: OpenAI, concurrency: int) -> list[dict]:
    total   = len(tickets)
    done    = 0
    results = []

    doc_list   = list(doc_store.values())
    collection = build_collection(doc_list)

    future_to_ticket: dict = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for ticket in tickets:
            future = executor.submit(run_rag_agent, ticket, collection, doc_list, llm)
            future_to_ticket[future] = ticket

        for future in as_completed(future_to_ticket):
            ticket =  future_to_ticket[future]
            prediction = future.result()
            labels     = ticket["labels"]

            aa = eval_answerable_accuracy(prediction, labels)
            pr = eval_doc_precision_recall(prediction, labels)
            cc = eval_confidence_calibration(prediction, labels)

            results.append({
                "ticket_id":            ticket["ticket_id"],
                "expected_answerable":  labels["answerable_from_docs"],
                "predicted_answerable": prediction["answerable"],
                "answerable_correct":   aa["score"],
                "expected_doc_ids":     "|".join(sorted(labels.get("expected_doc_ids", []))),
                "predicted_doc_ids":    "|".join(sorted(prediction.get("relevant_doc_ids", []))),
                "doc_precision":        round(pr["precision"], 4),
                "doc_recall":           round(pr["recall"], 4),
                "confidence":           prediction["confidence"],
                "confidence_bucket":    cc["bucket"],
                "error":                prediction.get("error", False),
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

    answerable_scores = [r["answerable_correct"] for r in results]
    precisions        = [r["doc_precision"] for r in results if r["expected_answerable"]]
    recalls           = [r["doc_recall"]    for r in results if r["expected_answerable"]]
    conf_correct      = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong        = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count       = sum(1 for r in results if r["error"])

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("answerable_accuracy",    mean(answerable_scores), len(answerable_scores)),
        ("doc_precision",          mean(precisions),        len(precisions)),
        ("doc_recall",             mean(recalls),           len(recalls)),
        ("confidence_on_correct",  mean(conf_correct),      len(conf_correct)),
        ("confidence_on_wrong",    mean(conf_wrong),        len(conf_wrong)),
    ]

    for name, value, n in rows:
        print(f"  {name:<35} {value:.4f}  (n={n})")

    if error_count:
        print(f"\n  Errors: {error_count}/{len(results)} tickets failed")

    print("=" * 60)
    print(f"\nResults saved to: {csv_path}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run RAG agent evaluation locally.")
    p.add_argument("--sample",          type=int, default=None)
    p.add_argument("--experiment-name", type=str, default=None)
    p.add_argument("--concurrency",     type=int, default=4)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    llm       = build_llm_client()
    doc_store = load_docs()
    tickets   = load_dev_tickets(sample=args.sample)
    n         = len(tickets)

    experiment_name = args.experiment_name or \
        f"rag-agent-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"Model:       {MODEL}")
    print(f"Tickets:     {n}")
    print(f"Docs:        {len(doc_store)}")
    print(f"Retrieval:   ChromaDB (all-MiniLM-L6-v2) + 1-hop graph expansion")
    print(f"Concurrency: {args.concurrency}")
    print(f"Experiment:  {experiment_name}")
    print()

    results  = run_evaluation(tickets, doc_store, llm, args.concurrency)
    csv_path = save_results_csv(results, experiment_name)
    print_summary(results, csv_path)


if __name__ == "__main__":
    main()
