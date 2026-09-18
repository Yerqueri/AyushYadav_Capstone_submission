"""
Coordinator Agent — Local Evaluation Harness
============================================

Evaluates the coordinator (PR-00) against the 500-ticket development dataset.
Sub-agent responses are mocked from ground truth labels, so only the coordinator's
orchestration logic and routing decisions are under test.

Metrics
-------
  routing_accuracy      — final route matches labels.expected_route
  escalation_safety     — 0.0 under-triage / 0.5 over-triage / 1.0 correct
  pipeline_efficiency   — turns_taken matches expected_turns for the ticket's exit path
  confidence_on_correct — mean confidence when route is correct
  confidence_on_wrong   — mean confidence when route is wrong

Usage
-----
    python run_coordinator_eval.py --sample 20 --experiment-name "coord-smoke-test"
    python run_coordinator_eval.py
    python run_coordinator_eval.py --experiment-name "coord-v1-gemini" --concurrency 4

Results
-------
Console:  Printed summary table at end of run.
CSV:      evaluation/results/{experiment_name}.csv

Notes
-----
- Sub-agents are mocked: their outputs are derived directly from ground truth labels.
  This isolates the coordinator's routing decisions from sub-agent errors.
- When real sub-agents are ready, replace get_mock_output() with actual LLM calls.
- The coordinator is called in a loop until it produces final_decision.
  Max turns = 8 (safety limit; the normal pipeline completes in ≤ 6 turns).
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

MAX_TURNS = 8  # Safety limit; normal pipeline completes in ≤ 6 turns

# Expected turns per exit path:
#   FLAG A (must_not_auto_respond): full pipeline + final = 6 turns (same as normal, route=escalate)
#   EXIT B (not answerable):        PR-02 → PR-01 → PR-03 → PR-04 → final = 5 turns
#   Normal (auto_respond):          PR-02 → PR-01 → PR-03 → PR-04 → PR-05 → final = 6 turns

CSV_COLUMNS = [
    "ticket_id",
    "expected_route",
    "predicted_route",
    "routing_correct",
    "escalation_safety_score",
    "expected_turns",
    "turns_taken",
    "efficiency_correct",
    "exit_gate",
    "confidence",
    "confidence_bucket",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates (mirrors prompts/coordinator_prompt.md)
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the coordinator agent for CloudServe Solutions' support triage pipeline.
You orchestrate a set of specialist sub-agents to classify, assess, and respond
to incoming support tickets.

You operate in a loop. On each turn you receive:
  1. The original support ticket
  2. A list of sub-agent invocations completed so far (agent ID, inputs given, output received)

On each turn you output exactly one action:
  - invoke_agent: call the next sub-agent and specify its exact inputs
  - final_decision: end the pipeline with a routing decision

No sub-agent communicates with another. You are the only one who reads their outputs
and decides what to do next.

Available sub-agents — use these exact agent_id values in your output:
  PR-02  fluency_classifier   — Detect fluent vs non-fluent English
  PR-01  intent_classifier    — Classify intent; sets must_not_auto_respond
  PR-03  urgency_classifier   — Classify urgency as high/medium/low
  PR-04  rag_agent            — Find relevant docs; assess answerability
  PR-05  response_drafter     — Draft customer response from retrieved docs

Your output must be a single JSON object. Do not include any text after the JSON object.\
"""

USER_PROMPT_TEMPLATE = """\
You are coordinating the triage pipeline for the following support ticket.

--- TICKET ---
Ticket ID:      {ticket_id}
Channel:        {channel}
Subject:        {subject}
Body:           {body}
Customer Tier:  {customer_tier}
Received At:    {received_at}
--- END TICKET ---

--- COMPLETED INVOCATIONS ---
{completed_invocations_block}
--- END COMPLETED INVOCATIONS ---

Work through each step:

STEP 1 — REVIEW COMPLETED WORK
List what has been completed so far and what outputs are now available.
If no invocations have been completed, state "Pipeline not started."

STEP 2 — CHECK ROUTING FLAGS
Check the following flags — they affect the final route but do NOT stop the pipeline early:

  FLAG A — must_not_auto_respond = true (from intent_classifier):
    The intent class requires human review. Note this flag but DO NOT stop here.
    Continue the pipeline normally through urgency, RAG, and response_drafter.
    The draft will be included in the escalation payload for the human reviewer.
    Make the final_decision (escalate, with draft) only after all agents have run.

  EXIT B — Escalate immediately if:
    - rag_agent output shows answerable = false
    Reason: no documentation covers this ticket; the response_drafter cannot produce
    a useful draft. Escalate after RAG — do not invoke response_drafter.

Only EXIT B triggers an early stop. FLAG A does not.

STEP 3 — DETERMINE THE NEXT ACTION
If EXIT B has not fired, invoke agents in this order:
  1. fluency_classifier  (no dependencies)
  2. intent_classifier   (requires: language_fluency from step 1)
  3. urgency_classifier  (requires: intent from step 2)
  4. rag_agent           (requires: intent and urgency from steps 2-3)
  5. response_drafter    (requires: all prior outputs — runs even if FLAG A is set)

Only invoke an agent if all its required inputs are available from completed invocations
or from the original ticket.

When assembling inputs for an agent:
  - Draw values from the original ticket fields where the agent requires them
  - Draw values from completed invocation outputs where the agent requires them
  - Do not invent or estimate missing values — if a required input is missing,
    invoke the agent that produces it first

STEP 4 — OUTPUT FINAL JSON

If invoking an agent:
{{
  "next_action": "invoke_agent",
  "agent_call": {{
    "agent_id": "<PR-XX>",
    "agent_name": "<name>",
    "inputs": {{ <exact key-value pairs the agent requires> }}
  }},
  "final_decision": null,
  "reasoning": "<one sentence>"
}}

If making a final decision:
{{
  "next_action": "final_decision",
  "agent_call": null,
  "final_decision": {{
    "route": "<auto_respond or escalate>",
    "escalation_reason": <null or one sentence explaining why>,
    "draft": <null or the draft string from response_drafter output>,
    "confidence": <float 0.0-1.0>
  }},
  "reasoning": "<one sentence>"
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Mock sub-agent outputs
# ──────────────────────────────────────────────────────────────────────────────

MOCK_AGENT_NAMES = {
    "PR-02": "fluency_classifier",
    "PR-01": "intent_classifier",
    "PR-03": "urgency_classifier",
    "PR-04": "rag_agent",
    "PR-05": "response_drafter",
}

# Reverse lookup: normalize agent_id by agent_name when the model uses the wrong ID
_AGENT_NAME_TO_ID = {v: k for k, v in MOCK_AGENT_NAMES.items()}


def get_mock_output(agent_id: str, ticket: dict) -> dict:
    """Return ground-truth-derived mock output for a sub-agent call."""
    labels = ticket["labels"]

    if agent_id == "PR-02":
        return {
            "fluency": ticket["language_fluency"],
            "confidence": 0.92,
            "reasoning_summary": "Language fluency assessed from writing patterns.",
        }
    if agent_id == "PR-01":
        return {
            "intent": labels["intent"],
            "must_not_auto_respond": labels["must_not_auto_respond"],
            "confidence": 0.88,
            "reasoning_summary": f"Intent classified as {labels['intent']}.",
        }
    if agent_id == "PR-03":
        return {
            "urgency": labels["urgency"],
            "confidence": 0.85,
            "reasoning_summary": f"Urgency classified as {labels['urgency']}.",
        }
    if agent_id == "PR-04":
        return {
            "relevant_doc_ids": labels.get("expected_doc_ids", []),
            "answerable": labels["answerable_from_docs"],
            "confidence": 0.85,
            "reasoning_summary": "Document relevance and answerability assessed.",
        }
    if agent_id == "PR-05":
        answered_fully = labels["answerable_from_docs"] and not labels["must_not_auto_respond"]
        return {
            "draft": (
                "Thank you for contacting CloudServe Solutions. "
                "Based on our documentation, here are the steps to resolve your issue. "
                "Please follow the guidance below and let us know if you need further assistance."
            ),
            "citations": labels.get("expected_doc_ids", []),
            "answered_fully": answered_fully,
            "confidence": 0.87,
            "reasoning_summary": "Response drafted from relevant documentation.",
        }
    return {"error": f"Unknown agent {agent_id}"}


def format_completed_invocations(invocations: list[dict]) -> str:
    if not invocations:
        return "(none)"
    parts = []
    for i, inv in enumerate(invocations, 1):
        parts.append(
            f"[INVOCATION {i}]\n"
            f"Agent:   {inv['agent_id']} ({inv['agent_name']})\n"
            f"Inputs:  {json.dumps(inv['inputs'])}\n"
            f"Output:  {json.dumps(inv['output'])}"
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
    """Extract the outermost JSON object from the model's response using balanced brace matching."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    last_close = cleaned.rfind("}")
    if last_close == -1:
        raise ValueError(f"No closing brace found in model output:\n{text[:400]}")

    depth = 0
    start = -1
    for i in range(last_close, -1, -1):
        if cleaned[i] == "}":
            depth += 1
        elif cleaned[i] == "{":
            depth -= 1
            if depth == 0:
                start = i
                break

    if start == -1:
        raise ValueError(f"Could not find balanced JSON object in model output:\n{text[:400]}")

    try:
        return json.loads(cleaned[start : last_close + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON parse failed: {exc}\nExtracted: {cleaned[start:last_close+1][:400]}"
        )


def call_coordinator_turn(
    ticket: dict,
    completed_invocations: list[dict],
    llm: OpenAI,
    max_retries: int = 2,
) -> dict:
    """Call the coordinator LLM once and return its parsed JSON output."""
    subject = ticket.get("subject") or "(none)"
    invocations_block = format_completed_invocations(completed_invocations)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=ticket["ticket_id"],
        channel=ticket["channel"],
        subject=subject,
        body=ticket["body"],
        customer_tier=ticket["customer_tier"],
        received_at=ticket["received_at"],
        completed_invocations_block=invocations_block,
    )

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            response = llm.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content
            return parse_json_from_response(raw)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Coordinator LLM call failed after {max_retries + 1} attempts: {last_exc}")


def run_coordinator_pipeline(ticket: dict, llm: OpenAI) -> dict:
    """
    Run the full coordinator loop for one ticket.
    Sub-agent outputs are mocked from ground truth labels.
    Returns a result dict with route, confidence, turns, agents_called, exit_gate, error.
    """
    completed_invocations: list[dict] = []
    agents_called: list[str] = []

    for turn in range(1, MAX_TURNS + 1):
        try:
            result = call_coordinator_turn(ticket, completed_invocations, llm)
        except Exception as exc:
            return {
                "predicted_route": None,
                "confidence": 0.0,
                "turns_taken": turn,
                "agents_called": agents_called,
                "exit_gate": determine_exit_gate(agents_called),
                "error": True,
                "error_msg": str(exc),
            }

        next_action = result.get("next_action")

        if next_action == "final_decision":
            fd = result.get("final_decision") or {}
            route = fd.get("route")
            confidence = fd.get("confidence")
            try:
                confidence = max(0.0, min(1.0, float(confidence or 0.0)))
            except (TypeError, ValueError):
                confidence = 0.0
            return {
                "predicted_route": route,
                "confidence": confidence,
                "turns_taken": turn,
                "agents_called": agents_called,
                "exit_gate": determine_exit_gate(agents_called, route),
                "error": False,
            }

        if next_action == "invoke_agent":
            ac = result.get("agent_call") or {}
            agent_id   = ac.get("agent_id", "UNKNOWN")
            agent_name = ac.get("agent_name", MOCK_AGENT_NAMES.get(agent_id, "unknown"))
            inputs     = ac.get("inputs", {})

            # Normalize agent_id by agent_name when the model returns a non-PR-XX ID
            if agent_id not in MOCK_AGENT_NAMES and agent_name in _AGENT_NAME_TO_ID:
                agent_id = _AGENT_NAME_TO_ID[agent_name]

            mock_output = get_mock_output(agent_id, ticket)
            completed_invocations.append({
                "agent_id":   agent_id,
                "agent_name": agent_name,
                "inputs":     inputs,
                "output":     mock_output,
            })
            agents_called.append(agent_id)

        # If next_action is malformed, continue and let the coordinator self-correct
        # on the next turn (the updated invocations block remains unchanged).

    return {
        "predicted_route": None,
        "confidence": 0.0,
        "turns_taken": MAX_TURNS,
        "agents_called": agents_called,
        "exit_gate": determine_exit_gate(agents_called),
        "error": True,
        "error_msg": "Max turns exceeded without final_decision",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline analysis helpers
# ──────────────────────────────────────────────────────────────────────────────

def determine_exit_gate(agents_called: list[str], route: str | None = None) -> str:
    """
    Infer which exit gate fired.
      last = PR-04 → EXIT B (not answerable — drafter skipped)
      last = PR-05, route = auto_respond → normal
      last = PR-05, route = escalate    → flag_a (MNR — ran full pipeline, escalated with draft)
      last = PR-01 → EXIT A legacy (should not occur with new coordinator)
      other → unknown
    """
    if not agents_called:
        return "none"
    last = agents_called[-1]
    if last == "PR-04":
        return "exit_b"
    if last == "PR-05":
        return "normal" if route == "auto_respond" else "flag_a"
    if last == "PR-01":
        return "exit_a"
    return "unknown"


def determine_expected_turns(labels: dict) -> int:
    """
    Expected turn count for this ticket's correct path:
      EXIT B (not answerable, regardless of MNR):
                                       PR-02 + PR-01 + PR-03 + PR-04 + final = 5
      FLAG A (MNR=true, answerable):   full pipeline + final = 6  (route=escalate with draft)
      Normal (auto_respond):           full pipeline + final = 6  (route=auto_respond)
    EXIT B takes priority — if no docs, PR-05 is always skipped.
    """
    if not labels["answerable_from_docs"]:
        return 5
    return 6


# ──────────────────────────────────────────────────────────────────────────────
# Evaluators
# ──────────────────────────────────────────────────────────────────────────────

def eval_routing(prediction: dict, labels: dict) -> dict:
    predicted = prediction.get("predicted_route")
    expected  = labels["expected_route"]
    return {
        "correct": int(predicted == expected),
        "predicted": predicted,
        "expected": expected,
    }


def eval_escalation_safety(prediction: dict, labels: dict) -> float:
    """
    1.0 — correct routing
    0.5 — over-triage (predicted escalate, expected auto_respond): safe but suboptimal
    0.0 — under-triage (predicted auto_respond, expected escalate): dangerous
    """
    predicted = prediction.get("predicted_route")
    expected  = labels["expected_route"]
    if predicted == expected:
        return 1.0
    if predicted == "escalate" and expected == "auto_respond":
        return 0.5
    return 0.0  # predicted auto_respond, expected escalate


def eval_pipeline_efficiency(prediction: dict, labels: dict) -> dict:
    expected_turns = determine_expected_turns(labels)
    turns_taken    = prediction.get("turns_taken", MAX_TURNS)
    return {
        "expected_turns": expected_turns,
        "turns_taken":    turns_taken,
        "correct":        int(turns_taken == expected_turns),
    }


def eval_confidence_calibration(prediction: dict, labels: dict) -> dict:
    confidence = float(prediction.get("confidence") or 0.0)
    is_correct = prediction.get("predicted_route") == labels["expected_route"]
    return {"bucket": "correct" if is_correct else "wrong", "score": confidence}


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
# Evaluation runner
# ──────────────────────────────────────────────────────────────────────────────

def run_evaluation(tickets: list[dict], llm: OpenAI, concurrency: int) -> list[dict]:
    total   = len(tickets)
    done    = 0
    results = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_ticket = {
            executor.submit(run_coordinator_pipeline, ticket, llm): ticket
            for ticket in tickets
        }

        for future in as_completed(future_to_ticket):
            ticket     = future_to_ticket[future]
            prediction = future.result()
            labels     = ticket["labels"]

            routing    = eval_routing(prediction, labels)
            safety     = eval_escalation_safety(prediction, labels)
            efficiency = eval_pipeline_efficiency(prediction, labels)
            calibration = eval_confidence_calibration(prediction, labels)

            results.append({
                "ticket_id":              ticket["ticket_id"],
                "expected_route":         routing["expected"],
                "predicted_route":        routing["predicted"] or "error",
                "routing_correct":        routing["correct"],
                "escalation_safety_score": safety,
                "expected_turns":         efficiency["expected_turns"],
                "turns_taken":            efficiency["turns_taken"],
                "efficiency_correct":     efficiency["correct"],
                "exit_gate":              prediction.get("exit_gate", "unknown"),
                "confidence":             prediction.get("confidence", 0.0),
                "confidence_bucket":      calibration["bucket"],
                "error":                  prediction.get("error", False),
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

    routing_scores  = [r["routing_correct"]        for r in results]
    safety_scores   = [r["escalation_safety_score"] for r in results]
    efficiency_scores = [r["efficiency_correct"]   for r in results]
    conf_correct    = [r["confidence"] for r in results if r["confidence_bucket"] == "correct"]
    conf_wrong      = [r["confidence"] for r in results if r["confidence_bucket"] == "wrong"]
    error_count     = sum(1 for r in results if r["error"])

    # Under-triage count (the dangerous case: auto_respond when should escalate)
    under_triage = sum(
        1 for r in results
        if r["predicted_route"] == "auto_respond" and r["expected_route"] == "escalate"
    )

    def mean(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    rows = [
        ("routing_accuracy",     mean(routing_scores),   len(routing_scores)),
        ("escalation_safety",    mean(safety_scores),    len(safety_scores)),
        ("pipeline_efficiency",  mean(efficiency_scores), len(efficiency_scores)),
        ("confidence_on_correct", mean(conf_correct),    len(conf_correct)),
        ("confidence_on_wrong",   mean(conf_wrong),      len(conf_wrong)),
    ]

    for name, value, n in rows:
        print(f"  {name:<35} {value:.4f}  (n={n})")

    if under_triage:
        print(f"\n  UNDER-TRIAGE (dangerous): {under_triage}/{len(results)} tickets")
    if error_count:
        print(f"  Errors: {error_count}/{len(results)} tickets failed")

    print("=" * 60)
    print(f"\nResults saved to: {csv_path}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run coordinator agent evaluation locally.")
    p.add_argument("--sample",          type=int, default=None,
                   help="Evaluate only the first N tickets (default: all 500)")
    p.add_argument("--experiment-name", type=str, default=None)
    p.add_argument("--concurrency",     type=int, default=4,
                   help="Tickets to process in parallel (default: 4). "
                        "Each ticket requires up to 6 sequential LLM calls.")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    llm     = build_llm_client()
    tickets = load_dev_tickets(sample=args.sample)
    n       = len(tickets)

    experiment_name = args.experiment_name or \
        f"coordinator-n{n}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"Model:       {MODEL}")
    print(f"Tickets:     {n}")
    print(f"Max turns:   {MAX_TURNS} per ticket")
    print(f"Concurrency: {args.concurrency}")
    print(f"Experiment:  {experiment_name}")
    print()

    results  = run_evaluation(tickets, llm, args.concurrency)
    csv_path = save_results_csv(results, experiment_name)
    print_summary(results, csv_path)


if __name__ == "__main__":
    main()
