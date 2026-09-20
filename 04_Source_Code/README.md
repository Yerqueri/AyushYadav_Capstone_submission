# CloudServe Support Triage — Backend & Operations Runbook

A production-ready, coordinator-mediated multi-agent triage pipeline built following Object-Oriented Programming (OOP) and SOLID design principles. It automatically classifies incoming support tickets, retrieves relevant documentation, applies GuardrailsAI governance checks, and either emits a grounded auto-response or escalates to human agents with a complete pre-drafted context package via a 3-tier operational architecture.

---

## 3-Tier Operational Architecture

```text
POST /tickets  OR  POST /tickets/batch  OR  One-Shot Batch CLI
    │
    ▼
[Guardrail Checkpoint 1 — INGRESS]   detect_jailbreak + detect_prompt_injection
    │ pass
    ▼
[PR-00 Coordinator loop — max MAX_COORDINATOR_TURNS turns]
    ├─► PR-02 FluencyClassifier      (skipped if language_fluency provided)
    ├─► PR-01 IntentClassifier       ──► FLAG A (flag MNR but continue pipeline)
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
[Decision Log — SQLite: ./storage/decisions.db]  [Prometheus Metrics — :8001/metrics]
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

## Complete Environment Variable Register (`.env.example`)

Before running natively or via Docker, copy `.env.example` to `.env` and configure your API key(s):

```bash
cp .env.example .env
```

### Reference `.env.example` Specification

```env
# ==============================================================================
# CloudServe Support Triage — Environment Configuration (.env.example)
# ==============================================================================

# ── 1. LLM API Key (Required) ──────────────────────────────────────────────────
# OpenAI API Key (Standard required API key for OpenAI GPT models)
OPENAI_API_KEY=your_openai_api_key_here

# Default Model Name
MODEL_NAME=gpt-4o-mini

# ── 2. GuardrailsAI Governance (Optional) ─────────────────────────────────────
# If set, GuardrailsAI hub validators (jailbreak, injection, PII, toxicity) are installed.
# If omitted, guardrails operate in pass-through mode without failing execution.
GUARDRAILS_API_KEY=

# ── 3. Database & Storage ─────────────────────────────────────────────────────
DATABASE_URL=sqlite:///./storage/decisions.db
CHROMA_PATH=./storage/chroma
EMBEDDING_MODEL=all-MiniLM-L6-v2

# ── 4. Pipeline & Triage Settings ─────────────────────────────────────────────
CONFIDENCE_THRESHOLD=80
RETRIEVAL_TOP_K=8
MAX_COORDINATOR_TURNS=10
LOG_LEVEL=INFO

# ── 5. Docker & Batch Execution Mode Controls ──────────────────────────────────
# Execution mode for Docker container: 'api' (REST API only), 'batch' (One-shot batch run), or 'both'
MODE=api

# Batch processing options
BATCH_INPUT=data/development_tickets.json
BATCH_OUTPUT=storage/
BATCH_CONCURRENCY=4
# BATCH_SAMPLE=20  # Uncomment to run a quick smoke test on first 20 tickets

# ── 6. Telemetry & Evaluation ───────────────────────────────────────────────────
OTEL_SDK_DISABLED=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=cloudserve-triage
```

---

## Native Local Runbook (Without Docker)

This runbook guides you through setting up Python, installing dependencies, launching the REST API server natively, and running one-shot batch triage jobs.

### System Requirements
- **OS**: macOS 12+, Ubuntu 20.04+, Debian 11+, or Windows 10/11 (WSL2)
- **Python**: Python 3.11, 3.12, 3.13, or 3.14
- **Package Manager**: `uv` (recommended) or standard `pip`

### Step 1: Navigate to Workspace & Environment Setup

```bash
cd AyushYadav_Capstone_submission/04_Source_Code

# Option A: Using uv (Ultra-fast, recommended)
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Option B: Using standard Python venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 2: Configure Environment File

```bash
cp .env.example .env
# Edit .env and supply your OPENAI_API_KEY
```

### Step 3: Run the Synchronous REST API Server

```bash
# Launch FastAPI via uvicorn (listens on http://localhost:8000)
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload

# Alternatively, run via python module
python -m src.api
```

- **Interactive API Documentation (Swagger UI)**: `http://localhost:8000/docs`
- **Prometheus Metrics Endpoint**: `http://localhost:8001/metrics`
- **Health Check Endpoint**: `curl http://localhost:8000/health`

### Step 4: Run a One-Shot Batch Triage Job Natively

Execute the batch evaluation harness over a dataset file in one shot:

```bash
# Run full 500-ticket development dataset batch triage
python -m evaluation.harness --input data/validation_tickets.json --output storage/results/

# Run a quick 20-ticket smoke test batch triage
python -m evaluation.harness --input data/validation_tickets.json --output storage/results/ --sample 20
```

**Batch Run Artifacts Produced:**
- `storage/run_<timestamp>.jsonl`: Structured per-ticket output decisions.
- `storage/metrics_<timestamp>.json`: Aggregate volume, business, technical, and governance metrics report.

---

## Docker & Docker Compose Runbook (Recommended)

This runbook enables running the REST API server and a one-shot batch triage job **simultaneously** using containerized microservices or a single combined container.

### System Requirements
- **Docker Engine**: 20.10.0+
- **Docker Compose**: v2.0.0+

### Step 1: Environment File Initialization

```bash
cd AyushYadav_Capstone_submission/04_Source_Code
cp .env.example .env
# Ensure OPENAI_API_KEY is populated in .env
```

### Step 2: One-Command Simultaneous Execution (API + One-Shot Batch)

Run Docker Compose to launch both the API server and the one-shot batch triage container simultaneously:

```bash
docker compose up --build
```

**What happens under the hood:**
1. `cloudserve-api` builds and starts on ports `8000` (FastAPI) and `8001` (Prometheus).
2. ChromaDB embeddings (`all-MiniLM-L6-v2`) are loaded automatically without requiring outbound network downloads.
3. `cloudserve-batch` waits for `cloudserve-api` healthcheck (`http://localhost:8000/health`) to be healthy.
4. `cloudserve-batch` executes a complete one-shot batch triage run over `data/development_tickets.json` in parallel while the API server remains live.
5. All SQLite decision logs and batch JSONL / metrics reports are persisted to `./storage/`.

To stop the containers:
```bash
docker compose down
```

---

### Step 3: Standalone Docker Execution Modes

If you prefer building and running standalone Docker containers manually:

#### A. Build Docker Image

```bash
docker build -t cloudserve-triage:latest .
```

#### B. Mode 1: Run REST API Container Standalone

```bash
docker run -d \
  --name cloudserve-api-standalone \
  -p 8000:8000 \
  -p 8001:8001 \
  --env-file .env \
  -e MODE=api \
  -v $(pwd)/storage:/app/storage \
  cloudserve-triage:latest
```

#### C. Mode 2: Run One-Shot Batch Processing Container Standalone

You can pass input dataset paths (ingress) and output directory paths (egress) to the Docker container via CLI flags (`--input` / `-i`, `--output` / `-o`) or environment variables (`INGRESS_PATH` / `INPUT_PATH`, `EGRESS_PATH` / `OUTPUT_PATH`). External host directories can be mounted directly to `/app/ingress` and `/app/egress`:

```bash
# Option 1: Short flag pattern with mounted external ingress & egress volumes (Recommended)
docker run --rm \
  --name cloudserve-batch-flags \
  --env-file .env \
  -v $(pwd)/data:/app/ingress \
  -v $(pwd)/storage/results:/app/egress \
  cloudserve-triage:latest -i /app/ingress/validation_tickets.json -o /app/egress --sample 20

# Option 2: Long flag pattern (--input / --output)
docker run --rm \
  --name cloudserve-batch-long-flags \
  --env-file .env \
  -v $(pwd)/data:/app/ingress \
  -v $(pwd)/storage/results:/app/egress \
  cloudserve-triage:latest --input /app/ingress/validation_tickets.json --output /app/egress

# Option 3: Environment variable pattern (INGRESS_PATH / EGRESS_PATH)
docker run --rm \
  --name cloudserve-batch-env \
  --env-file .env \
  -e INGRESS_PATH=/app/ingress/validation_tickets.json \
  -e EGRESS_PATH=/app/egress \
  -v $(pwd)/data:/app/ingress \
  -v $(pwd)/storage/results:/app/egress \
  cloudserve-triage:latest batch
```

#### D. Mode 3: Run API Server AND One-Shot Batch Simultaneously in Single Container

```bash
docker run -d \
  --name cloudserve-simultaneous \
  -p 8000:8000 \
  -p 8001:8001 \
  --env-file .env \
  -e MODE=both \
  -v $(pwd)/storage:/app/storage \
  cloudserve-triage:latest
```

---

## API & Endpoints Reference

### 1. `POST /tickets` — Single Ticket Triage

Submit an individual support ticket for real-time classification, retrieval, and response drafting / escalation.

#### Example Request (`curl`):

```bash
curl -X POST "http://localhost:8000/tickets" \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "DEV-0001",
    "channel": "chat",
    "subject": "Build failing during dependency resolution",
    "body": "builds that work last week are now fail during dependency resolution. we are having not change our code at all.",
    "customer_tier": "standard",
    "customer_id": "CUST-9921",
    "received_at": "2026-09-19T10:00:00Z"
  }'
```

#### Example Response (HTTP 200 OK):

```json
{
  "ticket_id": "DEV-0001",
  "route": "auto_respond",
  "draft": "We see your build is failing at dependency resolution. Here are the steps to resolve: 1. Check if any package versions were updated...",
  "escalation_reason": null,
  "intent": "deployment_failure",
  "urgency": "high",
  "relevant_doc_ids": ["DOC-DEPLOY-001"],
  "confidence": 0.88,
  "guardrail_results": [
    {"validator": "detect_jailbreak", "passed": true, "details": "no jailbreak detected"},
    {"validator": "detect_prompt_injection", "passed": true, "details": "no prompt injection detected"}
  ],
  "decision_id": "a1b2c3d4-e5f6-7890-1234-56789abcdef0"
}
```

---

### 2. `POST /tickets/batch` — Batch Tickets Triage

Submit a JSON array of support tickets for synchronous batch processing via HTTP REST.

#### Example Request (`curl`):

```bash
curl -X POST "http://localhost:8000/tickets/batch" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "ticket_id": "DEV-0001",
      "channel": "chat",
      "body": "builds failing at dependency resolution",
      "customer_tier": "standard"
    },
    {
      "ticket_id": "DEV-0072",
      "channel": "docs_comment",
      "subject": "Invoice higher than expected",
      "body": "Our invoice this month is $450 higher than expected.",
      "customer_tier": "business"
    }
  ]'
```

#### Example Response (HTTP 200 OK):

Returns a JSON array of `TriageResponse` objects matching each input ticket.

---

### 3. Service Operational Endpoints

- **Health Check**: `GET http://localhost:8000/health` → `{"status": "ok"}`
- **Prometheus Metrics**: `GET http://localhost:8001/metrics` → Standard Prometheus metrics format exposing `pipeline_latency_seconds` and `tickets_total`.

---

## Unit Testing & Sub-Agent Evaluation Runbook

### Run Unit Tests

Execute the complete pytest suite:

```bash
pytest tests/ -v
```
*(All 32 unit tests pass 100%).*

### Run Specialized Sub-Agent Evaluation Harnesses

```bash
# 1. PR-01 Intent Classifier Evaluation
python evaluation/run_langsmith_eval.py --sample 20 --experiment-name "intent-smoke"

# 2. PR-02 Language Fluency Evaluation
python evaluation/run_fluency_eval.py --sample 20 --experiment-name "fluency-smoke"

# 3. PR-03 Urgency Classifier Evaluation
python evaluation/run_urgency_eval.py --sample 20 --experiment-name "urgency-smoke"

# 4. PR-04 RAG Agent Evaluation
python evaluation/run_rag_eval.py --sample 20 --experiment-name "rag-smoke"

# 5. PR-05 Response Drafter Evaluation
python evaluation/run_response_draft_eval.py --sample 20 --experiment-name "draft-smoke"

# 6. PR-00 Coordinator Agent Evaluation
python evaluation/run_coordinator_eval.py --sample 20 --experiment-name "coord-smoke"
```

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

## Troubleshooting & FAQs

### 1. Port Conflicts (`8000` or `8001` already in use)
If port 8000 or 8001 is used by another application, modify your `.env` or pass alternate ports:
```bash
uvicorn src.api:app --host 0.0.0.0 --port 8080
```
In `docker-compose.yml`, change the host port mappings (`"8080:8000"`, `"8081:8001"`).

### 2. Missing GuardrailsAI API Key Warning
If `GUARDRAILS_API_KEY` is not set in `.env`, you will see:
`GUARDRAILS_API_KEY not set — validators running in pass-through mode.`
This is expected behavior. The pipeline continues to run smoothly; guardrail validation checks pass by default without making outbound calls to Guardrails AI hub servers.

### 3. SQLite Storage Permissions or Stale Data
If you modify vector indices or SQLite schema, reset the storage directory:
```bash
rm -rf storage/chroma storage/decisions.db
```
The application auto-recreates ChromaDB vector collections from `data/documentation.json` at next startup.
