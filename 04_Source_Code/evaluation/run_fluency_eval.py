"""
Language Fluency Classifier — Local Evaluation Harness
=======================================================

Loads development_tickets.json, calls the fluency classifier against each ticket
concurrently, scores three metrics, prints a summary table, and saves per-ticket
results to evaluation/results/{experiment_name}.csv.

Usage
-----
    # Run full eval against all 500 dev tickets
    python run_fluency_eval.py

    # Quick smoke test on first 20 tickets
    python run_fluency_eval.py --sample 20 --experiment-name "fluency-smoke-test"

    # Named experiment with higher concurrency
    python run_fluency_eval.py --experiment-name "fluency-v1-gemini" --concurrency 8

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

VALID_FLUENCY = {"fluent", "non_fluent"}

CSV_COLUMNS = [
    "ticket_id",
    "predicted_fluency",
    "expected_fluency",
    "fluency_correct",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/language_fluency_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a language fluency detector for CloudServe Solutions, a cloud infrastructure platform.
Your job is to read a support ticket body and classify the author's English fluency.

You MUST work through exactly three steps before outputting your answer.
Do not skip steps. Do not merge steps. Show your reasoning for each step.

Your final output must be a single JSON object matching this schema:
{
  "fluency": "fluent" | "non_fluent",
  "confidence": float (0.0–1.0),
  "reasoning_summary": string
}

Do not include any text after the JSON object.\
"""

USER_PROMPT_TEMPLATE = """\
Classify the language fluency of the following support ticket body.

--- TICKET ---
Ticket ID:  {ticket_id}
Channel:    {channel}
Body:       {body}
--- END TICKET ---

Work through each step:

STEP 1 — SCAN FOR NON-FLUENCY SIGNALS
Look for specific markers of non-native English writing:
  - Subject-verb agreement errors ("builds that work last week are now fail")
  - Missing or incorrect articles ("the", "a", "an")
  - Unusual word order or sentence structure
  - Non-standard verb tense or aspect ("we are having not change")
  - Dropped pronouns or prepositions
  - Literal translations that produce unnatural phrasing

List each signal you find, or state "No non-fluency signals found."

STEP 2 — ASSESS OVERALL FLUENCY
Consider the ticket as a whole:
  - If you found two or more distinct non-fluency signals: classify as non_fluent
  - If you found one borderline signal (could be a typo or autocorrect): weigh against the rest of the text
  - If the text is grammatically natural, even if informal or abbreviated: classify as fluent

Note: technical jargon, abbreviations, and casual tone are NOT non-fluency signals.
Typos alone are NOT sufficient — all writers make typos.

STEP 3 — OUTPUT FINAL JSON

{{
  "fluency": "<fluent or non_fluent>",
  "confidence": <float>,
  "reasoning_summary": "<one sentence citing the specific signals>"
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
    matches = list(re.finditer(r'\{[^{}]*"fluency"[^{}]*\}', text, re.DOTALL))
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
    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=ticket["ticket_id"],
        channel=ticket["channel"],
        body=ticket["body"],
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

            result["fluency"] = result.get("fluency", "fluent")
            if result["fluency"] not in VALID_FLUENCY:
                result["fluency"] = "fluent"

            result["confidence"] = float(result.get("confidence", 0.5))
            result["confidence"] = max(0.0, min(1.0, result["confidence"]))
            result["raw_response"] = raw
            return result

        except Exception as exc:
            if attempt == max_retries:
                return {
                    "fluency": "fluent",
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

def eval_fluency_accuracy(prediction: dict, ground_truth: str) -> dict:
    predicted = prediction.get("fluency", "")
    return {
        "score":   int(predicted == ground_truth),
        "comment": f"predicted={predicted}, expected={ground_truth}",
    }


def eval_confidence_calibration(prediction: dict, ground_truth: str) -> dict:
    confidence = float(prediction.get("confidence", 0.5))
    is_correct = prediction.get("fluency", "") == ground_truth
    return {
        "bucket":  "correct" if is_correct else "wrong",
        "score":   confidence,
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
            expected   = ticket["language_fluency"]

            fa = eval_fluency_accuracy(prediction, expected)
            cc = eval_confidence_calibration(prediction, expected)

            results.append({
                "ticket_id":         ticket["ticket_id"],
                "predicted_fluency": prediction["fluency"],
                "expected_fluency":  expected,
                "fluency_correct":   fa["score"],
                "confidence":        prediction["confidence"],
                "confidence_bucket": cc["bucket"],
                "error":             prediction.get("error", False),
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

    fluency_scores = [r["fluency_correct"] for r in results]
    conf_correct   = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong     = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count    = sum(1 for r in results if r["error"])

    non_fluent_total   = sum(1 for r in results if r["expected_fluency"] == "non_fluent")
    non_fluent_correct = sum(1 for r in results if r["expected_fluency"] == "non_fluent" and r["fluency_correct"])

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("fluency_accuracy",       mean(fluency_scores), len(fluency_scores)),
        ("non_fluent_recall",       non_fluent_correct / non_fluent_total if non_fluent_total else 0.0, non_fluent_total),
        ("confidence_on_correct",  mean(conf_correct),  len(conf_correct)),
        ("confidence_on_wrong",    mean(conf_wrong),    len(conf_wrong)),
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
    p = argparse.ArgumentParser(description="Run language fluency classifier evaluation locally.")
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
        f"fluency-classifier-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

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
