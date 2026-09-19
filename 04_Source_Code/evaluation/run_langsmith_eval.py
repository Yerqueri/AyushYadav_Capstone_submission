"""
Intent Classifier — Local Evaluation Harness
=============================================

Loads development_tickets.json, calls the intent classifier against each ticket
concurrently, scores four metrics, prints a summary table, and saves per-ticket
results to evaluation/results/{experiment_name}.csv.

Usage
-----
    # Run full eval against all 500 dev tickets
    python run_langsmith_eval.py

    # Quick smoke test on first 20 tickets
    python run_langsmith_eval.py --sample 20 --experiment-name "smoke-test"

    # Named experiment with higher concurrency
    python run_langsmith_eval.py --experiment-name "intent-v1-gemini" --concurrency 8

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
from typing import Any

# Load .env from project root (parent of this file's directory)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except ImportError:
    pass

# ── OpenAI-compatible client (OpenAI) ──────────────────────────────────────
try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: uv pip install 'openai>=1.0.0'")


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

DATASET_PATH = Path(__file__).parent.parent / "requirements" / "05_Datasets" / "development_tickets.json"
RESULTS_DIR  = Path(__file__).parent / "results"

MODEL                = os.getenv("MODEL_NAME", "gpt-4.1-mini")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))

ALWAYS_ESCALATE = {
    "security_incident", "compliance_request", "feature_request",
    "unclear_request", "data_residency", "billing_query",
}

VALID_INTENTS = {
    "account_access", "api_key_issue", "api_usage_question",
    "authentication_failure", "billing_query", "compliance_request",
    "configuration_help", "data_export", "data_residency",
    "database_issue", "deployment_failure", "feature_request",
    "integration_help", "onboarding", "performance_degradation",
    "quota_or_overage", "rate_limit", "rollback_request",
    "security_incident", "sso_configuration", "unclear_request",
    "webhook_issue",
}

CSV_COLUMNS = [
    "ticket_id",
    "predicted_intent",
    "expected_intent",
    "intent_correct",
    "predicted_must_not_auto_respond",
    "expected_must_not_auto_respond",
    "escalation_safety_score",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/intent_classifier_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a support ticket classifier for CloudServe Solutions, a cloud infrastructure platform.
Your job is to read a support ticket and return a structured JSON classification.

You MUST work through exactly six steps before outputting your answer.
Do not skip steps. Do not merge steps. Show your reasoning for each step.

Your final output must be a single JSON object matching this schema:
{
  "intent": string,
  "confidence": float (0.0–1.0),
  "must_not_auto_respond": boolean,
  "reasoning_summary": string
}

Do not include any text after the JSON object.\
"""

USER_PROMPT_TEMPLATE = """\
Classify the following support ticket by working through the six steps below.

--- TICKET ---
Ticket ID:       {ticket_id}
Channel:         {channel}
Subject:         {subject}
Body:            {body}
Customer Tier:   {customer_tier}
Language:        {language_fluency}
--- END TICKET ---

--- INTENT CLASSES ---
account_access, api_key_issue, api_usage_question, authentication_failure,
billing_query, compliance_request, configuration_help, data_export,
data_residency, database_issue, deployment_failure, feature_request,
integration_help, onboarding, performance_degradation, quota_or_overage,
rate_limit, rollback_request, security_incident, sso_configuration,
unclear_request, webhook_issue
--- END INTENT CLASSES ---

Work through each step:

STEP 1 — DECODE THE REQUEST
Read the ticket carefully. If the language is non-fluent or abbreviated,
restate the customer's actual problem in clear English in one sentence.
Note any implied urgency cues (words like "urgent", "down", "breaking",
"can't", production timelines, or revenue impact).

STEP 2 — EXTRACT TECHNICAL SIGNALS
List the specific technical terms, product areas, error codes, and
action verbs present in the ticket. These are your primary classification
features. Be precise — "401" is more specific than "error".

STEP 3 — SCREEN FOR HARD-ESCALATE INTENTS
Check whether the ticket matches any of these six always-escalate intents:
  - security_incident:   confirmed breach or active compromise — an unauthorized party
                         accessed systems, data, or credentials, or there is direct evidence
                         of ongoing unauthorized use. NOT security_incident: a key returning
                         401 errors or needing rotation is api_key_issue; secrets appearing
                         in logs is configuration_help.
  - compliance_request:  audit records, regulatory documentation, data retention evidence
  - feature_request:     asking for a new capability, enhancement, or product change
  - unclear_request:     body provides fewer than two technical signals — no error codes,
                         no product area, no action verb — making the actual problem
                         impossible to determine. When uncertain between a vague technical
                         intent and unclear_request, choose unclear_request.
  - data_residency:      questions about where data is stored, data sovereignty, regional
                         data laws — always requires human review due to legal exposure
  - billing_query:       invoice disputes, charge questions, plan changes, payment issues
                         — always requires human review due to financial exposure

If yes: state which one applies and mark must_not_auto_respond = true.
If no: state "No hard-escalate trigger found."

STEP 4 — SELECT THE PRIMARY INTENT
From the 22 intent classes, identify the single best match.
If two intents are competing, name both and explain in one sentence why
one takes precedence over the other.
State your chosen intent and a brief justification.

Disambiguation guide for commonly confused pairs:
  - api_key_issue vs security_incident: Choose api_key_issue when the ticket is about
    a key not working, needing rotation, or having wrong permissions — even if the key
    was accidentally exposed. Only choose security_incident if there is direct evidence
    an unauthorized party already used the credential to access systems or data.
  - quota_or_overage vs billing_query: Choose quota_or_overage when the ticket is about
    hitting or exceeding usage limits, or overage charges due to plan limits. Choose
    billing_query for general invoice questions, payment issues, or charge explanations
    unrelated to plan quotas.
  - api_usage_question vs data_export: Choose api_usage_question when the customer asks
    HOW to retrieve or paginate data via the API. Choose data_export only when the
    customer asks CloudServe to perform a bulk export on their behalf.
  - authentication_failure vs account_access: Choose authentication_failure for any
    failed sign-in that is not an explicit account lock (OAuth, session, MFA, console
    login failures). Choose account_access only for explicit account locks or suspended
    accounts.

STEP 5 — CALIBRATE CONFIDENCE
Start at 0.80. Then adjust:

Raise to ≥ 0.85 only when ALL of the following are true:
  - The ticket body contains multiple specific technical keywords (error codes,
    product names, action verbs) that point to exactly one intent class
  - You did NOT consider any competing intent in Step 4
  - The body is detailed enough that another reader would reach the same intent

Keep at 0.80 when:
  - One clear intent with minor ambiguity (one competing intent briefly considered
    but clearly ruled out)

Lower to 0.65–0.79 when ANY of these apply:
  - Two plausible intents competed and the choice required a judgment call
  - Ticket body is short (1–2 sentences) or missing technical specifics
  - Non-fluent language required interpretation to determine meaning
  - Intent was inferred from context rather than explicit keywords

Lower to < 0.65 when TWO OR MORE lower conditions apply simultaneously,
or when you genuinely cannot determine the dominant intent with confidence.

Justify your score in one sentence citing the specific signal(s) that drove it.

STEP 6 — OUTPUT FINAL JSON
Output the classification as a JSON object only. No prose after the object.
Set must_not_auto_respond = true ONLY if the intent is one of the six
hard-escalate classes (security_incident, compliance_request, feature_request,
unclear_request, data_residency, billing_query). Do not use confidence score
to determine this flag.

{{
  "intent": "<chosen intent>",
  "confidence": <float>,
  "must_not_auto_respond": <true|false>,
  "reasoning_summary": "<one sentence summary>"
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
    matches = list(re.finditer(r'\{[^{}]*"intent"[^{}]*\}', text, re.DOTALL))
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
        language_fluency=ticket["language_fluency"],
        confidence_threshold=CONFIDENCE_THRESHOLD,
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
                max_tokens=1024,
            )
            raw = response.choices[0].message.content
            result = parse_json_from_response(raw)

            result["intent"] = result.get("intent", "unclear_request")
            if result["intent"] not in VALID_INTENTS:
                result["intent"] = "unclear_request"

            result["confidence"] = float(result.get("confidence", 0.5))
            result["confidence"] = max(0.0, min(1.0, result["confidence"]))

            if result["intent"] in ALWAYS_ESCALATE:
                result["must_not_auto_respond"] = True
            else:
                result["must_not_auto_respond"] = bool(result.get("must_not_auto_respond", False))

            result["raw_response"] = raw
            return result

        except Exception as exc:
            if attempt == max_retries:
                return {
                    "intent": "unclear_request",
                    "confidence": 0.0,
                    "must_not_auto_respond": True,
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
# Evaluator functions — plain dicts in, score dict out
# ──────────────────────────────────────────────────────────────────────────────

def eval_intent_accuracy(prediction: dict, ground_truth: dict) -> dict:
    predicted = prediction.get("intent", "")
    expected  = ground_truth.get("intent", "")
    return {
        "key":     "intent_accuracy",
        "score":   int(predicted == expected),
        "comment": f"predicted={predicted}, expected={expected}",
    }


def eval_escalation_safety(prediction: dict, ground_truth: dict) -> dict:
    """
    1.0 — correct decision
    0.0 — false negative (failed to escalate) — DANGEROUS
    0.5 — false positive (over-cautious, safe)
    """
    predicted = bool(prediction.get("must_not_auto_respond", False))
    expected  = bool(ground_truth.get("must_not_auto_respond", False))

    if predicted == expected:
        return {"key": "escalation_safety", "score": 1.0, "comment": "Correct"}
    elif not predicted and expected:
        return {"key": "escalation_safety", "score": 0.0,
                "comment": "FALSE NEGATIVE — failed to flag a must-not-auto-respond ticket"}
    else:
        return {"key": "escalation_safety", "score": 0.5,
                "comment": "False positive — over-cautious escalation (safe)"}


def eval_confidence_calibration(prediction: dict, ground_truth: dict) -> dict:
    confidence = float(prediction.get("confidence", 0.5))
    is_correct = prediction.get("intent", "") == ground_truth.get("intent", "")
    bucket = "correct" if is_correct else "wrong"
    return {
        "key":     f"confidence_on_{bucket}",
        "score":   confidence,
        "comment": f"intent_correct={is_correct}, confidence={confidence:.2f}",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Local evaluation runner
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

            ia  = eval_intent_accuracy(prediction, labels)
            es  = eval_escalation_safety(prediction, labels)
            cc  = eval_confidence_calibration(prediction, labels)

            results.append({
                "ticket_id":                       ticket["ticket_id"],
                "predicted_intent":                prediction["intent"],
                "expected_intent":                 labels["intent"],
                "intent_correct":                  ia["score"],
                "predicted_must_not_auto_respond": prediction["must_not_auto_respond"],
                "expected_must_not_auto_respond":  labels["must_not_auto_respond"],
                "escalation_safety_score":         es["score"],
                "confidence":                      prediction["confidence"],
                "confidence_bucket":               cc["key"].replace("confidence_on_", ""),
                "error":                           prediction.get("error", False),
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

    intent_scores     = [r["intent_correct"] for r in results]
    escalation_scores = [r["escalation_safety_score"] for r in results]
    conf_correct      = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong        = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count       = sum(1 for r in results if r["error"])

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("intent_accuracy",      mean(intent_scores),     len(intent_scores)),
        ("escalation_safety",    mean(escalation_scores), len(escalation_scores)),
        ("confidence_on_correct", mean(conf_correct),     len(conf_correct)),
        ("confidence_on_wrong",   mean(conf_wrong),       len(conf_wrong)),
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
    p = argparse.ArgumentParser(description="Run intent classifier evaluation locally.")
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
        f"intent-classifier-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

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
