# CloudServe Support Triage — Backend

A coordinator-mediated multi-agent pipeline that classifies incoming support tickets, retrieves relevant documentation, and either drafts an automated response or escalates to a human agent.

## Architecture

```
POST /tickets
    │
    ▼
[Guardrail Checkpoint 1 — INGRESS]   detect_jailbreak + detect_prompt_injection
    │ pass
    ▼
[PR-00 Coordinator loop — max MAX_COORDINATOR_TURNS turns]
    ├─► PR-02 fluency_classifier    (skipped if language_fluency provided)
    ├─► PR-01 intent_classifier     ──► EXIT A (escalate) if must_not_auto_respond
    ├─► PR-03 urgency_classifier
    ├─► PR-04 rag_agent             ──► EXIT B (escalate) if not answerable
    └─► PR-05 response_drafter
    │
    ▼
[Guardrail Checkpoint 2 — EGRESS]   bert_toxic + guardrails_pii + detect_system_prompt_leakage
                                    + extracted_summary_sentences_match
    │
    ▼
[Decision Log — SQLite]  [Prometheus Metrics — :8001]
    │
    ▼
HTTP 200 TriageResponse
```

## Running with Docker (recommended)

**Requirements:** Docker and Docker Compose.

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY (required) and GUARDRAILS_API_KEY (optional)
```

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`. Prometheus metrics are on port 8001.

- GuardrailsAI validators are installed automatically at container start if `GUARDRAILS_API_KEY` is set. If the key is absent the validators run in pass-through mode (the pipeline still works).
- The SQLite decision log is persisted in `./storage/decisions.db` via a volume mount.
- The `all-MiniLM-L6-v2` embedding model is baked into the image at build time; no network is needed at startup.

To stop: `docker compose down`. The `storage/` directory is kept on the host so the decision log survives restarts.

---

## Local Setup (without Docker)

**Requirements:** Python 3.11+, `uv` (recommended)

```bash
pip install -r requirements.txt
```

Install GuardrailsAI validators (requires `GUARDRAILS_API_KEY`):

```bash
guardrails hub install hub://guardrails/detect_jailbreak
guardrails hub install hub://guardrails/detect_prompt_injection
guardrails hub install hub://guardrails/detect_system_prompt_leakage
guardrails hub install hub://guardrails/extracted_summary_sentences_match
guardrails hub install hub://guardrails/bert_toxic
guardrails hub install hub://guardrails/guardrails_pii
```

Copy `.env.example` to `.env` and fill in your keys:

```
OPENROUTER_API_KEY=your_key_here
GUARDRAILS_API_KEY=your_guardrails_key_here
```

## Running Locally

```bash
uvicorn src.api:app --reload
# or
python -m src.api
```

The API listens on `http://localhost:8000`. Prometheus metrics are exposed on port 8001.

## Batch Evaluation Harness

Run the full pipeline unattended over a ticket file (no server required):

```bash
python -m evaluation.harness \
    --input data/validation_tickets.json \
    --output evaluation/results/
```

Produces two files in `<output>`:

| File | Contents |
|------|----------|
| `run_<timestamp>.jsonl` | One JSON line per ticket (full pipeline output) |
| `metrics_<timestamp>.json` | Aggregate report: volume, business, technical, governance |

Results are flushed per ticket — a partial run is recoverable. Set `BATCH_DELAY_SECONDS` to pace requests under free-tier rate limits.

To run inside the Docker container:

```bash
docker compose run --rm api \
    python -m evaluation.harness \
        --input data/validation_tickets.json \
        --output evaluation/results/
```

## API

### `POST /tickets`

Submit a support ticket for triage.

**Request body:**
```json
{
  "ticket_id": "string",
  "channel": "email | chat | web_form | api | forum | docs_comment",
  "subject": "string (optional)",
  "body": "string",
  "received_at": "ISO-8601 string",
  "customer_id": "string",
  "customer_name": "string",
  "customer_tier": "standard | business | enterprise",
  "customer_region": "string",
  "language_fluency": "fluent | non_fluent (optional — skips PR-02 if provided)"
}
```

**Response:**
```json
{
  "ticket_id": "string",
  "route": "auto_respond | escalate | blocked",
  "draft": "string or null",
  "escalation_reason": "string or null",
  "intent": "string or null",
  "urgency": "string or null",
  "relevant_doc_ids": ["string"],
  "confidence": 0.0,
  "guardrail_results": [{"validator": "string", "passed": true, "details": "string or null"}],
  "decision_id": "string (UUID)"
}
```

### `GET /health`

Returns `{"status": "ok"}`. Used by load balancer health checks.

## Tests

```
pytest tests/ -v
```

Tests that require real LLM calls are skipped when `OPENROUTER_API_KEY` is not set.

## Evaluations

All eval harnesses are in `evaluation/`. Run from that directory:

```
python run_langsmith_eval.py      --sample 20 --experiment-name "intent-smoke"
python run_fluency_eval.py        --sample 20 --experiment-name "fluency-smoke"
python run_urgency_eval.py        --sample 20 --experiment-name "urgency-smoke"
python run_rag_eval.py            --sample 20 --experiment-name "rag-smoke"
python run_response_draft_eval.py --sample 20 --experiment-name "draft-smoke"
python run_coordinator_eval.py    --sample 20 --experiment-name "coord-smoke"
```

Results are written to `evaluation/results/`.

## Metric Targets

| Agent | Metric | Target |
|-------|--------|--------|
| PR-02 fluency | `non_fluent_recall` | ≥ 0.85 |
| PR-01 intent | `escalation_safety` | ≥ 0.95 |
| PR-03 urgency | `urgency_safety`, `high_urgency_recall` | ≥ 0.90 |
| PR-04 RAG | `answerable_accuracy` / `doc_precision` / `doc_recall` | ≥ 0.85 / 0.80 / 0.80 |
| PR-05 draft | `must_not_claim_rate` | **0.0** — hard blocker |
| PR-00 coordinator | `routing_accuracy`, zero under-triage | ≥ 0.90 |

## Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENROUTER_API_KEY` | — | Required |
| `MODEL_NAME` | `google/gemini-3.1-flash-lite` | LLM for all agents |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | ChromaDB embeddings |
| `DATABASE_URL` | `sqlite:///./storage/decisions.db` | Decision log |
| `MAX_COORDINATOR_TURNS` | `10` | Hard cap on coordinator loop |
| `RETRIEVAL_TOP_K` | `3` | Seed documents from semantic search |
| `CONFIDENCE_THRESHOLD` | `0.80` | Logged threshold (not used for routing) |
| `GUARDRAILS_API_KEY` | — | Required for GuardrailsAI cloud validators |
