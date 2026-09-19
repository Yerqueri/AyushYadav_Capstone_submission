# Coordinator Agent Prompt — CloudServe Support Triage

**Prompt ID:** PR-00  
**Version:** 1.1  
**Purpose:** Orchestrate the full support triage pipeline. The coordinator receives a raw ticket, decides which sub-agents to invoke and in what order, passes the correct inputs to each, and makes all routing decisions. No sub-agent communicates with any other — all information flows through the coordinator.  
**Model target:** `gpt-4o-mini` (OpenAI)  
**Evaluation dataset:** `development_tickets.json` (500 tickets — 311 auto_respond, 189 escalate)

---

## Architecture

```
                         ┌─────────────────────────────────────┐
                         │          COORDINATOR (PR-00)          │
                         │                                       │
  Raw ticket ──────────► │  decides what to call, when, and     │
                         │  with what inputs at every step       │
                         │                                       │
                         └──┬──────┬──────┬──────┬──────┬───────┘
                            │      │      │      │      │
                            ▼      ▼      ▼      ▼      ▼
                          PR-02  PR-01  PR-03  PR-04  PR-05
                         fluency intent urgency  RAG   draft
```

Sub-agents never receive each other's outputs directly. The coordinator collects each result and decides what to pass to the next agent.

---

## Available Sub-Agents

| Agent ID | Name | Purpose | Required inputs |
|----------|------|---------|-----------------|
| `PR-02` | `fluency_classifier` | Detect fluent vs non-fluent English | `ticket_id`, `channel`, `body` |
| `PR-01` | `intent_classifier` | Classify into 22 intent classes; set `must_not_auto_respond` | `ticket_id`, `channel`, `subject`, `body`, `customer_tier`, `language_fluency` |
| `PR-03` | `urgency_classifier` | Classify urgency as high/medium/low | `ticket_id`, `channel`, `subject`, `body`, `customer_tier`, `intent` |
| `PR-04` | `rag_agent` | Identify relevant docs and assess answerability | `ticket_id`, `intent`, `urgency`, `body` |
| `PR-05` | `response_drafter` | Draft the customer response from retrieved docs | `ticket_id`, `channel`, `body`, `customer_tier`, `language_fluency`, `intent`, `urgency`, `must_not_auto_respond`, `relevant_doc_ids`, `answerable` |

---

## Standard Pipeline

```
[1] fluency_classifier    → language_fluency
[2] intent_classifier     → intent, must_not_auto_respond, confidence
    ↓
    FLAG A: must_not_auto_respond = true?
      YES → NOTE the flag. Continue the pipeline — do NOT stop here.
            The draft and relevant docs will be passed to the human reviewer.
      NO  → continue normally
    ↓
[3] urgency_classifier    → urgency
[4] rag_agent             → relevant_doc_ids, answerable
    ↓
    EXIT B: answerable = false?
      YES → FINAL_DECISION: escalate (reason: no documentation covers this ticket)
            draft=null. Do NOT invoke response_drafter.
      NO  → continue
    ↓
[5] response_drafter      → draft, citations, answered_fully
    ↓
    if must_not_auto_respond = true (FLAG A was set):
      FINAL_DECISION: escalate, include draft for human reviewer
    else:
      FINAL_DECISION: auto_respond, include draft
```

Key principle: **FLAG A changes the final route but does not skip any agents.** Every MNR ticket gets urgency classification, RAG retrieval, and a draft — giving the human reviewer a complete work package.

---

## Output Schema — Single Coordinator Turn

The coordinator is called once per turn. Each call produces exactly one of two actions:

```json
{
  "next_action": "invoke_agent" | "final_decision",
  "agent_call": {
    "agent_id": "PR-XX",
    "agent_name": "...",
    "inputs": { ... }
  },
  "final_decision": {
    "route": "auto_respond" | "escalate",
    "escalation_reason": null | "...",
    "draft": null | "...",
    "confidence": 0.0
  },
  "reasoning": "<one sentence>"
}
```

- Exactly one of `agent_call` or `final_decision` is non-null.
- `agent_call` is non-null when `next_action = "invoke_agent"`.
- `final_decision` is non-null when `next_action = "final_decision"`.

---

## System Prompt

```
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

Your output must be a single JSON object. Do not include any text after the JSON object.
```

---

## User Prompt Template

```
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
  4. rag_agent           (requires: intent and urgency from steps 2–3)
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
    "confidence": <float 0.0–1.0>
  }},
  "reasoning": "<one sentence>"
}}
```

---

## Completed Invocations Block Format

```
[INVOCATION 1]
Agent:   PR-02 (fluency_classifier)
Inputs:  {"ticket_id": "DEV-0001", "channel": "chat", "body": "..."}
Output:  {"fluency": "non_fluent", "confidence": 0.95, "reasoning_summary": "..."}

[INVOCATION 2]
Agent:   PR-01 (intent_classifier)
Inputs:  {"ticket_id": "DEV-0001", ..., "language_fluency": "non_fluent"}
Output:  {"intent": "deployment_failure", "confidence": 0.88,
          "must_not_auto_respond": false, "reasoning_summary": "..."}
```

---

## Example — Full Pipeline Trace (DEV-0001, must_not_auto_respond = false)

### Turn 1 — No completed invocations

**Input:** Ticket DEV-0001. Completed invocations: (none)

**Expected output:**
```json
{
  "next_action": "invoke_agent",
  "agent_call": {
    "agent_id": "PR-02",
    "agent_name": "fluency_classifier",
    "inputs": {
      "ticket_id": "DEV-0001",
      "channel": "chat",
      "body": "builds that work last week are now fail during dependency resolution. we are having not change our code at all."
    }
  },
  "final_decision": null,
  "reasoning": "Pipeline not started; fluency_classifier is the first step and has no dependencies."
}
```

---

### Turn 3 — Intent complete, must_not_auto_respond = false

**Completed:** PR-01 → `{"intent": "deployment_failure", "must_not_auto_respond": false, "confidence": 0.88}`

**Expected output:**
```json
{
  "next_action": "invoke_agent",
  "agent_call": {
    "agent_id": "PR-03",
    "agent_name": "urgency_classifier",
    "inputs": {
      "ticket_id": "DEV-0001",
      "channel": "chat",
      "subject": "(none)",
      "body": "builds that work last week are now fail during dependency resolution. we are having not change our code at all.",
      "customer_tier": "standard",
      "intent": "deployment_failure"
    }
  },
  "final_decision": null,
  "reasoning": "must_not_auto_respond is false; passing intent to urgency_classifier as required input."
}
```

---

### Turn 3 (alternate) — Intent complete, must_not_auto_respond = true

**Completed:** PR-01 → `{"intent": "billing_query", "must_not_auto_respond": true, "confidence": 0.91}`

**Expected output:**
```json
{
  "next_action": "invoke_agent",
  "agent_call": {
    "agent_id": "PR-03",
    "agent_name": "urgency_classifier",
    "inputs": {
      "ticket_id": "VAL-0072",
      "channel": "docs_comment",
      "subject": "Invoice higher than expected",
      "body": "...",
      "customer_tier": "standard",
      "intent": "billing_query"
    }
  },
  "final_decision": null,
  "reasoning": "FLAG A noted (must_not_auto_respond=true) — continue pipeline; invoking urgency_classifier next."
}
```

---

### Turn 6 (FLAG A path) — Draft complete, must_not_auto_respond = true

**Completed:** PR-01 (billing_query, must_not_auto_respond=true), PR-03, PR-04, PR-05 (draft produced)

**Expected output:**
```json
{
  "next_action": "final_decision",
  "agent_call": null,
  "final_decision": {
    "route": "escalate",
    "escalation_reason": "billing_query requires human review; must_not_auto_respond = true.",
    "draft": "Thank you for contacting CloudServe. Your invoice includes charges for...",
    "confidence": 0.91
  },
  "reasoning": "All pipeline stages complete; FLAG A was set — escalating with draft for human reviewer."
}
```

---

### Turn 5 — RAG complete, not answerable (EXIT B)

**Completed:** PR-04 → `{"relevant_doc_ids": [], "answerable": false, "confidence": 0.80}`

**Expected output:**
```json
{
  "next_action": "final_decision",
  "agent_call": null,
  "final_decision": {
    "route": "escalate",
    "escalation_reason": "No documentation covers this ticket; an automated response would require inventing information.",
    "draft": null,
    "confidence": 0.80
  },
  "reasoning": "EXIT B triggered: rag_agent returned answerable = false — skipping response_drafter."
}
```

---

### Turn 6 — Draft complete, route to auto_respond

**Completed:** PR-05 → `{"draft": "Thank you for...", "citations": ["DOC-DEPLOY-001"], "answered_fully": true}`

**Expected output:**
```json
{
  "next_action": "final_decision",
  "agent_call": null,
  "final_decision": {
    "route": "auto_respond",
    "escalation_reason": null,
    "draft": "Thank you for...",
    "confidence": 0.88
  },
  "reasoning": "All pipeline stages complete; draft is ready and answered_fully = true."
}
```

---

## Design Rationale

| Design choice | Reason |
|---|---|
| Coordinator-mediated flow | Sub-agents are stateless specialists; the coordinator holds all context and prevents agents from making routing assumptions |
| Output one action per turn | Simplifies the orchestration loop — each coordinator call maps to exactly one external action; easy to trace and replay |
| FLAG A continues the pipeline | MNR tickets benefit from urgency classification, relevant doc retrieval, and a draft — the human reviewer gets a complete work package rather than a raw ticket with no context |
| EXIT B after RAG | If no docs cover the ticket, the drafter would have to invent information — better to escalate without a draft and let a human respond from their own knowledge |
| Inputs assembled explicitly by coordinator | Prevents sub-agents from receiving stale or misrouted context; every input value is chosen deliberately |

---

## Change History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-09-14 | Initial version — EXIT A (MNR) short-circuited the pipeline after PR-01 |
| 1.1 | 2026-09-17 | EXIT A converted to FLAG A — MNR tickets now run full pipeline (urgency, RAG, draft); draft included in escalation payload for human reviewer. Only EXIT B (not answerable) triggers an early stop. |
