# CloudServe Support Triage — Backend

A coordinator-mediated multi-agent pipeline following Object-Oriented Programming (OOP) and SOLID design principles. It classifies incoming support tickets, retrieves relevant documentation, and either drafts an automated response or escalates to human agents via a 3-tier operational architecture.

---

## 3-Tier Operational Architecture

```text
POST /tickets
    │
    ▼
[Guardrail Checkpoint 1 — INGRESS]   detect_jailbreak + detect_prompt_injection
    │ pass
    ▼
[PR-00 Coordinator loop — max MAX_COORDINATOR_TURNS turns]
    ├─► PR-02 FluencyClassifier      (skipped if language_fluency provided)
    ├─► PR-01 IntentClassifier       ──► EXIT A (escalate) if must_not_auto_respond
    ├─► PR-03 UrgencyClassifier
    ├─► PR-04 RagAgent               ──► EXIT B (escalate) if not answerable
    └─► PR-05 ResponseDrafter
    │
    ▼
[Guardrail Checkpoint 2 — EGRESS]   bert_toxic + guardrails_pii + detect_system_prompt_leakage
                                    + extracted_summary_sentences_match
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3-Tier Operational Routing & Outlet                                         │
├──────────────────────────────────────┬──────────────────────────────────────┤
│ Tier 1: L1 Direct Autonomous AI      │ 61.0% FCR (305 / 500 tickets)        │
│ Tier 2: L2 Human Copilot Approver    │ 22.8% FCR (114 / 500 pre-drafted)    │
│ Tier 3: L3 Senior Specialist Queue   │ 16.2% Load (81 unanswerable cases)   │
└──────────────────────────────────────┴──────────────────────────────────────┘
    │
    ▼
[Decision Log — SQLite]  [Prometheus Metrics — :8001]
    │
    ▼
HTTP 200 TriageResponse
```

---

## Modular OOP Code Structure

- **`src/prompts/`**: Prompt templates isolated into domain modules ([`fluency_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/fluency_prompts.py), [`intent_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/intent_prompts.py), [`urgency_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/urgency_prompts.py), [`rag_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/rag_prompts.py), [`draft_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/draft_prompts.py), [`coordinator_prompts.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/prompts/coordinator_prompts.py)).
- **`src/agents/`**: OOP agent hierarchy subclassing [`BaseAgent`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/agents/base.py) (Strategy / Template Method pattern).
- **`src/guardrails/`**: Modular guardrails package ([`base.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/guardrails/base.py), [`registry.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/guardrails/registry.py), [`pipeline.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/guardrails/pipeline.py), `ingress/`, `egress/`).
- **`src/models/`**: Pydantic domain models package ([`ticket.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/ticket.py), [`agent_outputs.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/agent_outputs.py), [`coordinator.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/coordinator.py), [`guardrails.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/guardrails.py), [`tools.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/tools.py), [`response.py`](file:///Users/work/Documents/projects/fde/capstone/AyushYadav_Capstone_submission/04_Source_Code/src/models/response.py)).

---

## Running with Docker (Recommended)

**Requirements:** Docker and Docker Compose.

```bash
cp .env.example .env
# fill in OPENAI_API_KEY (required)
```

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`. Prometheus metrics are on port `8001`.

- GuardrailsAI validators are installed automatically at container start if configured.
- The SQLite decision log is persisted in `./storage/decisions.db` via a volume mount.
- The `all-MiniLM-L6-v2` embedding model is baked into the image at build time; no network is needed at startup.

To stop: `docker compose down`.

---

## Local Setup (without Docker)

**Requirements:** Python 3.11+, `uv` (recommended)

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys:

```env
OPENAI_API_KEY=your_key_here
MODEL_NAME=gpt-4.1-mini
```

## Running Locally

```bash
uvicorn src.api:app --reload
# or
python -m src.api
```

The API listens on `http://localhost:8000`. Prometheus metrics are exposed on port `8001`.

---

## Batch Evaluation Harness

Run the full pipeline unattended over a ticket file:

```bash
python -m evaluation.harness \
    --input data/validation_tickets.json \
    --output evaluation/results/
```

Produces two files in `<output>`:

| File | Contents |
|---|---|
| `run_<timestamp>.jsonl` | One JSON line per ticket (full pipeline output) |
| `metrics_<timestamp>.json` | Aggregate report: volume, business, technical, governance |

---

## API Reference

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

Returns `{"status": "ok"}`.

---

## Tests

```bash
pytest tests/ -v
```

All 32 unit tests pass 100%.

---

## Metric Targets & Achieved Current State

| Agent / Component | Metric | Target | Current State (Achieved) |
|---|---|---|---|
| **PR-02 Fluency** | `non_fluent_recall` | ≥ 0.85 | **0.920 (92.0%)** — +0.07 over target |
| **PR-01 Intent** | `escalation_safety` | ≥ 0.95 | **0.977 (97.7%)** — 85/87 MNR safe |
| **PR-03 Urgency** | `urgency_safety`, `high_urgency_recall` | ≥ 0.90 | **0.850 (85.0%)** — 21.6s p50 latency |
| **PR-04 RAG** | `answerable_accuracy` / `doc_precision` / `doc_recall` | ≥ 0.85 / 0.80 / 0.80 | **81.2%** hit rate / **65.9%** / **71.6%** |
| **PR-05 Draft** | `must_not_claim_rate` | **0.0** — hard blocker | **0.0 (0.0%)** — 100% placeholder elimination |
| **PR-00 Coordinator** | `routing_accuracy`, zero under-triage | ≥ 0.90 | **83.8% Effective FCR** (61.0% Auto + 22.8% Copilot) |
| **System FCR** | `total_effective_fcr` | ≥ 65.0% (Benchmark) | **83.8%** (+40.0 pp vs 43.8% human baseline) |
| **Specialist Load** | `l3_queue_escalations` | Reduction | **16.2% load** (−71.2% reduction: 281 → 81 tickets) |
| **Response Speed** | `mean_response_time` | < 120 min | **21.81 seconds** (1,160× speedup vs 421.7 min) |
| **Governance & Safety**| `pipeline_error_rate` & audit log | 0.0% / 100% | **0.0% errors** / **100% logged** (500/500 SQLite) |
| **Reclaim Group** | `over_escalated_reclaim` | High Capture | **68.5%** (63/92 tickets captured for auto-resolution) |

---

## Key Performance Impact Summary

- **First Contact Resolution (FCR)**: Boosted from **43.8% (Human Baseline)** to **83.8% Total Effective FCR** (**61.0% Autonomous L1 AI** + **22.8% Assisted L2 Copilot**).
- **Specialist Workload Reduction**: L3 senior specialist escalation queue burden reduced by **−71.2%** (from 281 down to 81 tickets).
- **Resolution Speedup**: Mean response time collapsed from **421.7 minutes (~7 hours)** down to **21.81 seconds** (**1,160× speedup**).
- **Draft Quality & Pre-Drafting**: Generic placeholder strings (`"This ticket has been flagged for human review..."`) **100% eliminated** (0/124), delivering ready-to-edit pre-drafts for **91.9%** of escalated cases and saving **~19 hours** of manual human drafting time.

---

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Required |
| `MODEL_NAME` | `gpt-4.1-mini` | LLM for all sub-agents |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | ChromaDB embeddings |
| `DATABASE_URL` | `sqlite:///./storage/decisions.db` | SQLite Decision log |
| `MAX_COORDINATOR_TURNS` | `10` | Hard cap on coordinator loop |
| `RETRIEVAL_TOP_K` | `8` | Seed documents from semantic search |
| `CONFIDENCE_THRESHOLD` | `80` | Logged threshold |
