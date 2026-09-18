"""End-to-end batch harness — processes a full ticket file unattended (A9, A10).

Usage:
    python -m evaluation.harness --input data/validation_tickets.json \\
                                  --output evaluation/results/

Produces two files in <output>:
    run_<timestamp>.jsonl      one JSON line per ticket (full pipeline output)
    metrics_<timestamp>.json   aggregate metrics report

The harness never crashes: every ticket either produces a pipeline result or an
error record. All results are flushed to disk as they arrive, so a partial run is
recoverable.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Make src/ importable when run as  python -m evaluation.harness  from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.coordinator import run_pipeline
from src.ingest import normalize_ticket
from src.retrieve import get_collection

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# Optional inter-ticket delay (seconds) to stay within free-tier rate limits.
_BATCH_DELAY = float(os.getenv("BATCH_DELAY_SECONDS", "0"))


# ── Per-ticket processing ─────────────────────────────────────────────────────

def _process_ticket(raw: dict) -> dict:
    """Run one raw ticket dict through the full pipeline. Never raises."""
    t0 = time.perf_counter()
    try:
        ticket = normalize_ticket(raw)
        response = run_pipeline(ticket)
        latency = time.perf_counter() - t0
        return {
            "ticket_id": response.ticket_id,
            "channel": ticket.channel,
            "route": response.route,
            "intent": response.intent,
            "urgency": response.urgency,
            "confidence": response.confidence,
            "relevant_doc_ids": response.relevant_doc_ids,
            "rag_output": response.rag_output,
            "draft": response.draft,
            "escalation_reason": response.escalation_reason,
            "decision_id": response.decision_id,
            "guardrail_results": [r.model_dump() for r in response.guardrail_results],
            "latency_seconds": round(latency, 3),
            "error": None,
        }
    except Exception as exc:
        latency = time.perf_counter() - t0
        logger.error("Ticket %s failed: %s", raw.get("ticket_id", "?"), exc, exc_info=True)
        return {
            "ticket_id": raw.get("ticket_id", "unknown"),
            "channel": raw.get("channel", "unknown"),
            "route": "error",
            "intent": None,
            "urgency": None,
            "confidence": 0.0,
            "relevant_doc_ids": [],
            "escalation_reason": str(exc),
            "decision_id": None,
            "guardrail_results": [],
            "latency_seconds": round(latency, 3),
            "error": str(exc),
        }


# ── Metrics report ────────────────────────────────────────────────────────────

def _build_metrics(results: list[dict], wall_seconds: float) -> dict:
    n = len(results)
    if n == 0:
        return {"error": "no results"}

    routes = [r["route"] for r in results]
    latencies = sorted(r["latency_seconds"] for r in results)

    auto      = routes.count("auto_respond")
    escalated = routes.count("escalate")
    blocked   = routes.count("blocked")
    errors    = sum(1 for r in results if r["error"])

    p50 = latencies[max(0, int(n * 0.50) - 1)]
    p95 = latencies[max(0, int(n * 0.95) - 1)]
    mean_lat = round(sum(latencies) / n, 3)

    # Intent / urgency distribution
    intent_dist: dict[str, int] = defaultdict(int)
    urgency_dist: dict[str, int] = defaultdict(int)
    for r in results:
        if r["intent"]:
            intent_dist[r["intent"]] += 1
        if r["urgency"]:
            urgency_dist[r["urgency"]] += 1

    # Guardrail activations by validator
    guardrail_activations: dict[str, int] = defaultdict(int)
    pii_detections = 0
    for r in results:
        for gr in r.get("guardrail_results", []):
            if not gr.get("passed", True):
                guardrail_activations[gr["validator"]] += 1
                if gr["validator"] == "guardrails_pii":
                    pii_detections += 1

    decisions_logged = sum(1 for r in results if r["decision_id"])
    retrieval_hits   = sum(1 for r in results if r["relevant_doc_ids"])

    return {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_wall_seconds": round(wall_seconds, 1),
        "volume": {
            "tickets_processed": n,
            "auto_responded": auto,
            "escalated": escalated,
            "blocked_by_guardrails": blocked,
            "errors": errors,
            "auto_respond_rate": round(auto / n, 4),
            "escalation_rate": round(escalated / n, 4),
            "block_rate": round(blocked / n, 4),
            "error_rate": round(errors / n, 4),
        },
        "business": {
            "first_contact_resolution_rate": round(auto / n, 4),
            "escalation_rate": round(escalated / n, 4),
            "mean_response_time_seconds": mean_lat,
            "median_response_time_seconds": round(p50, 3),
        },
        "technical": {
            "latency_p50_seconds": round(p50, 3),
            "latency_p95_seconds": round(p95, 3),
            "retrieval_hit_rate": round(retrieval_hits / n, 4),
            "intent_distribution": dict(sorted(intent_dist.items())),
            "urgency_distribution": dict(urgency_dist),
        },
        "governance": {
            "decisions_logged": decisions_logged,
            "guardrail_activations_by_validator": dict(guardrail_activations),
            "pii_detections": pii_detections,
            "pipeline_errors": errors,
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CloudServe Support Triage — end-to-end batch harness",
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to input JSON file (array of ticket objects)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Directory where run JSONL and metrics JSON are written",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Process only the first N tickets (smoke test)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Number of tickets to process in parallel (default: 4)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading tickets from %s", input_path)
    with open(input_path) as f:
        tickets: list[dict] = json.load(f)
    if args.sample:
        tickets = tickets[:args.sample]
    total = len(tickets)
    logger.info("Loaded %d tickets", total)

    logger.info("Building ChromaDB collection...")
    get_collection()
    logger.info("ChromaDB ready — starting run (concurrency=%d).", args.concurrency)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path   = output_dir / f"run_{ts}.jsonl"
    metrics_path = output_dir / f"metrics_{ts}.json"

    results: list[dict] = []
    done_count = 0
    write_lock = threading.Lock()
    wall_start = time.perf_counter()

    with open(jsonl_path, "w") as out:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            future_to_raw = {executor.submit(_process_ticket, raw): raw for raw in tickets}
            for future in as_completed(future_to_raw):
                raw = future_to_raw[future]
                result = future.result()
                with write_lock:
                    done_count += 1
                    results.append(result)
                    out.write(json.dumps(result) + "\n")
                    out.flush()
                    print(f"  [{done_count}/{total}]", flush=True)
                if _BATCH_DELAY > 0:
                    time.sleep(_BATCH_DELAY)
    print()

    wall_seconds = time.perf_counter() - wall_start

    metrics = _build_metrics(results, wall_seconds)
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Human-readable summary
    v = metrics["volume"]
    b = metrics["business"]
    t = metrics["technical"]
    g = metrics["governance"]

    print(f"\n{'─'*54}")
    print(f"  CloudServe Triage — Batch Run Summary")
    print(f"{'─'*54}")
    print(f"  Tickets processed   : {v['tickets_processed']}")
    print(f"  Auto-responded      : {v['auto_responded']}  ({v['auto_respond_rate']:.1%})")
    print(f"  Escalated           : {v['escalated']}  ({v['escalation_rate']:.1%})")
    print(f"  Blocked (guardrail) : {v['blocked_by_guardrails']}")
    print(f"  Errors              : {v['errors']}")
    print(f"{'─'*54}")
    print(f"  Mean / median latency : {b['mean_response_time_seconds']}s / {b['median_response_time_seconds']}s")
    print(f"  p95 latency           : {t['latency_p95_seconds']}s")
    print(f"  Retrieval hit rate    : {t['retrieval_hit_rate']:.1%}")
    print(f"  Decisions logged      : {g['decisions_logged']}")
    if g["guardrail_activations_by_validator"]:
        print(f"  Guardrail activations : {g['guardrail_activations_by_validator']}")
    print(f"{'─'*54}")
    print(f"  Run file   : {jsonl_path}")
    print(f"  Metrics    : {metrics_path}")
    print(f"{'─'*54}\n")

    logger.info("Run complete: %d tickets in %.1fs.", total, wall_seconds)


if __name__ == "__main__":
    main()
