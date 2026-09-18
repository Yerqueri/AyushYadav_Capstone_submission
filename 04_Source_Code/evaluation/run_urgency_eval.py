"""
Urgency Classifier — Local Evaluation Harness
=============================================

Loads development_tickets.json, calls the urgency classifier against each ticket
concurrently, scores four metrics, prints a summary table, and saves per-ticket
results to evaluation/results/{experiment_name}.csv.

The urgency classifier receives the ground-truth intent label as input (not a
predicted intent) so urgency accuracy is measured in isolation.

Usage
-----
    # Run full eval against all 500 dev tickets
    python run_urgency_eval.py

    # Quick smoke test on first 20 tickets
    python run_urgency_eval.py --sample 20 --experiment-name "urgency-smoke-test"

    # Named experiment with higher concurrency
    python run_urgency_eval.py --experiment-name "urgency-v1-gemini" --concurrency 8

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


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

DATASET_PATH = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "development_tickets.json"
RESULTS_DIR  = Path(__file__).parent / "results"

MODEL = os.getenv("MODEL_NAME", "gpt-4.1-mini")

VALID_URGENCY  = {"high", "medium", "low"}
URGENCY_LEVELS = {"high": 2, "medium": 1, "low": 0}

CSV_COLUMNS = [
    "ticket_id",
    "predicted_urgency",
    "expected_urgency",
    "urgency_correct",
    "urgency_safety_score",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/urgency_classifier_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an urgency classifier for CloudServe Solutions, a cloud infrastructure platform.
Your job is to read a support ticket and classify its urgency as high, medium, or low.

You MUST work through exactly four steps before outputting your answer.
Do not skip steps. Do not merge steps. Show your reasoning for each step.

Your final output must be a single JSON object matching this schema:
{
  "urgency": "high" | "medium" | "low",
  "confidence": float (0.0–1.0),
  "reasoning_summary": string
}

Do not include any text after the JSON object.\
"""

USER_PROMPT_TEMPLATE = """\
Classify the urgency of the following support ticket.

--- TICKET ---
Ticket ID:       {ticket_id}
Channel:         {channel}
Subject:         {subject}
Body:            {body}
Customer Tier:   {customer_tier}
Intent:          {intent}
--- END TICKET ---

Work through each step:

STEP 1 — SCREEN FOR HIGH URGENCY
Answer YES if ANY of the following is true. If YES, urgency is HIGH — skip to Step 3.
  (a) Intent is security_incident
  (b) A production system is currently broken and customers or services cannot proceed:
      all users locked out, service completely unreachable, build/deploy pipeline blocked
      with no workaround, a deployed release actively causing errors in production,
      a service component or integration that has stopped functioning and not self-recovered
      (e.g. webhook delivery halted, log forwarding stopped overnight),
      active data loss, active data exposure, or credentials exposed in a public location
      (e.g. public repo, public paste)
  (c) Explicit urgency language refers to a live, broken production system affecting
      multiple users or services — not a single user's access issue:
      "urgent", "ASAP", "down", "broken", "can't [access / deploy / connect]"

Answer NO if none of the above apply, or if the issue is in staging/development.
If NO → continue to Step 2.

STEP 2 — SCREEN FOR LOW URGENCY
Urgency is LOW only if BOTH of the following are true:
  (a) No active failure in production — issue is in staging/dev, or there is no failure at all
  (b) The request is informational or a configuration setup task with no production blocker:
      a how-to question, initial setup/configuration of a new feature not yet in production,
      a single-user non-critical access issue that does not block production work
      (e.g. one developer cannot see a non-critical project)

      NOT LOW — always MEDIUM:
        - billing_query, data_residency, compliance_request, or unclear_request intents
          (always-escalate categories that require human review)
        - compliance, audit, or data-retention requests (implied deadline, requires action)
        - feature requests (require product team triage, not purely informational)
        - access revocation for a departing employee (time-sensitive security hygiene)
        - access issues preventing production operations or affecting a team or service
        - a batch job, export, or database restore that has been queued or running
          significantly longer than expected (possible pipeline failure)
        - rate limits or quotas currently being hit in production (API calls returning errors)
        - automated jobs failing or incorrect config actively causing production problems
        - ongoing security concern: credentials, secrets, or sensitive data currently
          appearing in logs or outputs (even if framed as a how-to question)

If both (a) AND (b) are true → urgency is LOW.
If either is false → urgency is MEDIUM.

MEDIUM is the safe default for any operational problem not yet classified:
degraded but running, one of multiple things broken, automated jobs failing,
a compliance or audit request, a time-sensitive integration, ongoing security concerns
(e.g. sensitive data appearing in logs), a recent production incident requiring
a configuration change to prevent recurrence, or anything where the customer is
reporting a real problem but the service is still partially functional.

STEP 3 — CALIBRATE CONFIDENCE
Start at 0.80. Then adjust:
  Raise to ≥ 0.85 if the dominant signal is explicit (exact keyword match, clear
  production context, or a definitive HIGH intent — security_incident).
  Lower to 0.65–0.79 if urgency was inferred from context without explicit keywords.
  Lower to < 0.65 if the body is vague or signals are mixed.

STEP 4 — OUTPUT FINAL JSON

{{
  "urgency": "<high, medium, or low>",
  "confidence": <float>,
  "reasoning_summary": "<one sentence citing the dominant signal>"
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# LLM call
# ──────────────────────────────────────────────────────────────────────────────

def build_llm_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("No API key found. Set OPENAI_API_KEY in .env.")
    return OpenAI(api_key=api_key)


def parse_json_from_response(text: str) -> dict:
    matches = list(re.finditer(r'\{[^{}]*"urgency"[^{}]*\}', text, re.DOTALL))
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


def classify_ticket(ticket: dict, llm: OpenAI, max_retries: int = 2) -> dict:
    subject = ticket.get("subject") or "(none)"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=ticket["ticket_id"],
        channel=ticket["channel"],
        subject=subject,
        body=ticket["body"],
        customer_tier=ticket["customer_tier"],
        intent=ticket["labels"]["intent"],
    )

    for attempt in range(max_retries + 1):
        try:
            response = llm.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            )
            raw = response.choices[0].message.content
            result = parse_json_from_response(raw)

            result["urgency"] = result.get("urgency", "medium")
            if result["urgency"] not in VALID_URGENCY:
                result["urgency"] = "medium"

            result["confidence"] = float(result.get("confidence", 0.5))
            result["confidence"] = max(0.0, min(1.0, result["confidence"]))
            result["raw_response"] = raw
            return result

        except Exception as exc:
            if attempt == max_retries:
                return {
                    "urgency": "medium",
                    "confidence": 0.0,
                    "reasoning_summary": f"Classification failed: {exc}",
                    "raw_response": str(exc),
                    "error": True,
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

def eval_urgency_accuracy(prediction: dict, ground_truth: dict) -> dict:
    predicted = prediction.get("urgency", "")
    expected  = ground_truth.get("urgency", "")
    return {
        "score":   int(predicted == expected),
        "comment": f"predicted={predicted}, expected={expected}",
    }


def eval_urgency_safety(prediction: dict, ground_truth: dict) -> dict:
    """
    1.0 — correct level
    0.5 — over-triage (predicted higher than actual — safe)
    0.0 — under-triage (predicted lower than actual — DANGEROUS)
    """
    predicted_level = URGENCY_LEVELS.get(prediction.get("urgency", "medium"), 1)
    expected_level  = URGENCY_LEVELS.get(ground_truth.get("urgency", "medium"), 1)

    if predicted_level == expected_level:
        return {"score": 1.0, "comment": "Correct"}
    elif predicted_level < expected_level:
        return {"score": 0.0,
                "comment": f"UNDER-TRIAGE — predicted {prediction.get('urgency')} for expected {ground_truth.get('urgency')}"}
    else:
        return {"score": 0.5,
                "comment": f"Over-triage — predicted {prediction.get('urgency')} for expected {ground_truth.get('urgency')} (safe)"}


def eval_confidence_calibration(prediction: dict, ground_truth: dict) -> dict:
    confidence = float(prediction.get("confidence", 0.5))
    is_correct = prediction.get("urgency", "") == ground_truth.get("urgency", "")
    return {
        "bucket": "correct" if is_correct else "wrong",
        "score":  confidence,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation runner
# ──────────────────────────────────────────────────────────────────────────────

def run_evaluation(tickets: list[dict], llm: OpenAI, concurrency: int) -> list[dict]:
    total   = len(tickets)
    done    = 0
    results = []

    future_to_ticket = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for ticket in tickets:
            future = executor.submit(classify_ticket, ticket, llm)
            future_to_ticket[future] = ticket

        for future in as_completed(future_to_ticket):
            ticket     = future_to_ticket[future]
            prediction = future.result()
            labels     = ticket["labels"]

            ua = eval_urgency_accuracy(prediction, labels)
            us = eval_urgency_safety(prediction, labels)
            cc = eval_confidence_calibration(prediction, labels)

            results.append({
                "ticket_id":           ticket["ticket_id"],
                "predicted_urgency":   prediction["urgency"],
                "expected_urgency":    labels["urgency"],
                "urgency_correct":     ua["score"],
                "urgency_safety_score": us["score"],
                "confidence":          prediction["confidence"],
                "confidence_bucket":   cc["bucket"],
                "error":               prediction.get("error", False),
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

    urgency_scores  = [r["urgency_correct"] for r in results]
    safety_scores   = [r["urgency_safety_score"] for r in results]
    conf_correct    = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong      = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count     = sum(1 for r in results if r["error"])

    high_total      = sum(1 for r in results if r["expected_urgency"] == "high")
    high_recall     = sum(1 for r in results if r["expected_urgency"] == "high" and r["predicted_urgency"] == "high")

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("urgency_accuracy",      mean(urgency_scores), len(urgency_scores)),
        ("urgency_safety",        mean(safety_scores),  len(safety_scores)),
        ("high_urgency_recall",   high_recall / high_total if high_total else 0.0, high_total),
        ("confidence_on_correct", mean(conf_correct),   len(conf_correct)),
        ("confidence_on_wrong",   mean(conf_wrong),     len(conf_wrong)),
    ]

    for name, value, n in rows:
        print(f"  {name:<35} {value:.4f}  (n={n})")

    if error_count:
        print(f"\n  Errors: {error_count}/{len(results)} tickets failed classification")

    print("=" * 60)
    print(f"\nResults saved to: {csv_path}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run urgency classifier evaluation locally.")
    p.add_argument("--sample",          type=int, default=None,
                   help="Evaluate only the first N tickets (default: all 500)")
    p.add_argument("--experiment-name", type=str, default=None,
                   help="Name for this run — used as the CSV filename (default: auto-generated)")
    p.add_argument("--concurrency",     type=int, default=4,
                   help="Number of parallel LLM calls (default: 4)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    llm     = build_llm_client()
    tickets = load_dev_tickets(sample=args.sample)
    n       = len(tickets)

    experiment_name = args.experiment_name or \
        f"urgency-classifier-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"Model:       {MODEL}")
    print(f"Tickets:     {n}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Experiment:  {experiment_name}")
    print()

    results  = run_evaluation(tickets, llm, args.concurrency)
    csv_path = save_results_csv(results, experiment_name)
    print_summary(results, csv_path)


if __name__ == "__main__":
    main()
