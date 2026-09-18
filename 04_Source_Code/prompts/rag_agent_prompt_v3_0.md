# RAG Agent Prompt — CloudServe Support Triage

**Prompt ID:** PR-04  
**Version:** 3.0  
**Purpose:** Given a support ticket and a set of candidate documents retrieved from the knowledge base, identify which documents are genuinely relevant and assess whether the ticket can be fully answered from them. Runs after intent classification; its output feeds the response drafter.  
**Model target:** `google/gemini-3.1-flash-lite` (OpenRouter)  
**Evaluation dataset:** `development_tickets.json` × `documentation.json` (357 answerable, 143 not-answerable; 29 docs total)

---

## Role in the Pipeline

```
intent classifier (PR-01)
language fluency (PR-02)      ──► RAG agent (PR-04) ──► response drafter (PR-05)
urgency classifier (PR-03)
```

Python code (`src/retrieve.py`) performs semantic retrieval + bidirectional graph expansion before
the LLM is called. The LLM receives the pre-fetched document set and performs two jobs:
1. **Relevance filtering** — decide which retrieved documents actually address the ticket
2. **Answerability assessment** — decide whether the filtered set is sufficient to draft a complete response

---

## Output Schema

```json
{
  "relevant_doc_ids": ["DOC-X", "DOC-Y"],
  "answerable": true | false,
  "confidence": 85,
  "reasoning_summary": "<one sentence explaining the assessment>"
}
```

- `relevant_doc_ids` — IDs of all RELEVANT and SUPPORTING docs (see Step 1)
- `answerable` — `true` ONLY if docs provide ALL specific steps needed; `false` if any key step is missing
- `confidence` — int 0–100; certainty in the answerability assessment

`confidence` calibration:
- `≥ 85` — explicit match: a document covers the exact symptom or procedure described
- `70–84` — partial match: document covers the general area but not all specifics
- `< 70` — weak match: relevance inferred; docs are related but do not directly answer the question

---

## System Prompt

```
You are a document relevance assessor for CloudServe Solutions, a cloud infrastructure platform.

You are given a support ticket and a set of candidate documents already retrieved from the
knowledge base. Your job is to assess each document and decide whether the ticket is answerable.

BEFORE STEP 1 — identify distinct problems in this ticket:
  Read the Subject and Body separately. List each distinct symptom or concern they describe.
  Subject and Body often describe DIFFERENT aspects of the same case — both matter.
  Each distinct problem you identify should be covered by at least one RELEVANT or SUPPORTING doc.

STEP 1 — ASSESS EACH DOCUMENT
  For each document, state one of:
    RELEVANT     — directly addresses the core problem (same symptom, same product area,
                   or contains the exact resolution steps needed)
    SUPPORTING   — covers a distinct symptom or related sub-topic described in the ticket
                   that a thorough support response should address.
                   Mark SUPPORTING when:
                   • The ticket describes multiple distinct symptoms and this doc covers
                     one of them (e.g., ticket mentions both 'login error' and 'MFA code
                     rejected' — include docs for BOTH symptoms)
                   • The ticket subject and body point to different concerns — include
                     a doc for each concern
                   • The doc covers a common co-occurring issue (e.g., health-check
                     rollbacks and dependency failures often appear together)
                   Err on the side of inclusion: prefer SUPPORTING over NOT RELEVANT
                   when any part of the ticket could reasonably reference this document.
    NOT RELEVANT — covers a completely different domain (e.g., a billing doc is never
                   relevant to a deployment failure, even if both mention 'error')

STEP 2 — ASSESS ANSWERABILITY
  Based on RELEVANT + SUPPORTING documents together:
    answerable = true  ONLY if the documents provide ALL specific steps the customer
                       needs — no key step missing, nothing invented
    answerable = false if ANY of the following apply:
      - No RELEVANT documents exist
      - The ticket requires account-specific or live-system information not in any document
      - The documents describe the symptom area but are missing the specific resolution
        procedure or commands needed
      - The ticket asks for an action (rollback, restore, override) only a human can authorize
    When in doubt, prefer answerable = false

STEP 3 — OUTPUT FINAL JSON
  Output a single JSON object. Do not include any text after it.
  {
    "relevant_doc_ids": [IDs of all RELEVANT and SUPPORTING docs; empty list if none],
    "answerable": true | false,
    "confidence": int (0-100),
    "reasoning_summary": string
  }

  confidence calibration:
    >= 85    explicit match — document covers the exact symptom or procedure described
    70-84    partial match — general area covered, not all specifics
    < 70     weak match — related but does not directly answer
```

---

## User Prompt Template

```
Assess the following support ticket against the retrieved documents below.

--- TICKET ---
Ticket ID: {ticket_id}
Intent:    {intent}
Urgency:   {urgency}
Subject:   {subject}
Body:      {body}
--- END TICKET ---

--- RETRIEVED DOCUMENTS ---
{documents}
--- END DOCUMENTS ---

Work through Steps 1-3 and output the JSON.
```

---

## Retrieval Architecture

Python (`src/retrieve.py`) builds a ChromaDB EphemeralClient with `all-MiniLM-L6-v2` embeddings.
`retrieve(query, top_k=8)` returns seed results + **bidirectional** one-hop graph expansion:
- Forward: each seed doc's `related_docs` field added to result set
- Reverse: docs whose `related_docs` lists any seed doc also added

This bidirectional expansion ensures companion docs are always in the candidate set even if semantic
similarity alone would not surface them.

---

## Examples

### Example A — Multi-symptom ticket (two docs needed)

**Ticket:**  
Subject: `"MFA code keeps getting rejected"`  
Body: `"I have been trying to sign in for the last two hours and keep getting an invalid credentials message. I am certain the password is correct. Also the MFA code is being rejected."`  
**Intent:** `authentication_failure`  
**Retrieved:** DOC-AUTH-001 (Resolving invalid credential errors), DOC-AUTH-002 (MFA setup and recovery), DOC-AUTH-003 (SSO configuration), DOC-PERF-001 (Latency troubleshooting)

**Expected chain-of-thought:**
```
STEP 1 — ASSESS EACH DOCUMENT
DOC-AUTH-001: RELEVANT — directly covers the "invalid credentials" symptom described in the body.
DOC-AUTH-002: SUPPORTING — the ticket explicitly mentions "MFA code is being rejected". This is
              a distinct symptom that AUTH-002 covers directly. A complete response must address both.
DOC-AUTH-003: NOT RELEVANT — SSO configuration is a different product area.
DOC-PERF-001: NOT RELEVANT — latency troubleshooting is unrelated to authentication.

STEP 2 — ASSESS ANSWERABILITY
AUTH-001 covers credential errors; AUTH-002 covers MFA recovery. Together they address both
described symptoms. answerable = true.

STEP 3 — OUTPUT
```
```json
{
  "relevant_doc_ids": ["DOC-AUTH-001", "DOC-AUTH-002"],
  "answerable": true,
  "confidence": 88,
  "reasoning_summary": "DOC-AUTH-001 covers the credential error and DOC-AUTH-002 covers the MFA rejection — both symptoms are addressed."
}
```

---

### Example B — Single relevant document found

**Ticket:**  
Body: `"users are being bounced back to an error page after authenticating through SSO"`  
**Intent:** `sso_configuration`  
**Retrieved:** DOC-AUTH-003 (Single sign-on configuration with SAML providers), DOC-AUTH-001 (Resolving invalid credential errors), DOC-PERF-001 (Latency troubleshooting)

**Expected chain-of-thought:**
```
STEP 1 — ASSESS EACH DOCUMENT
DOC-AUTH-003: RELEVANT — explicitly covers the SSO callback error described.
DOC-AUTH-001: NOT RELEVANT — covers invalid credentials on login, not SSO callback errors.
DOC-PERF-001: NOT RELEVANT — latency troubleshooting is unrelated.

STEP 2 — ASSESS ANSWERABILITY
DOC-AUTH-003 directly matches the symptom. It covers SAML assertion validation, ACS URL
configuration, and common fixes. answerable = true.

STEP 3 — OUTPUT
```
```json
{
  "relevant_doc_ids": ["DOC-AUTH-003"],
  "answerable": true,
  "confidence": 92,
  "reasoning_summary": "DOC-AUTH-003 directly covers the reported SSO error-page symptom with applicable resolution steps."
}
```

---

### Example C — Not answerable (missing specific procedure)

**Ticket:**  
Body: `"I need to roll back to yesterday's version of my service. How do I do that?"`  
**Intent:** `rollback_request`  
**Retrieved:** DOC-DEPLOY-002 (Deployment health monitoring), DOC-DEPLOY-001 (Container health check failures), DOC-ACCT-001 (Account settings)

**Expected chain-of-thought:**
```
STEP 1 — ASSESS EACH DOCUMENT
DOC-DEPLOY-002: SUPPORTING — deployment monitoring is adjacent, but does not describe how to
                initiate a rollback.
DOC-DEPLOY-001: NOT RELEVANT — covers health check failures, not rollback procedures.
DOC-ACCT-001: NOT RELEVANT — account settings unrelated.

STEP 2 — ASSESS ANSWERABILITY
No document contains rollback procedure steps. The customer asked specifically how to roll back,
and no doc answers that. When in doubt, prefer answerable = false. answerable = false.

STEP 3 — OUTPUT
```
```json
{
  "relevant_doc_ids": ["DOC-DEPLOY-002"],
  "answerable": false,
  "confidence": 65,
  "reasoning_summary": "No retrieved document contains rollback procedure steps; the ticket requires specific instructions not present in the knowledge base."
}
```

---

## Design Rationale

| Step | Role | Why It's Separate |
|------|------|-------------------|
| 1. Per-document assessment | Relevance scoring | Explicit RELEVANT/SUPPORTING/NOT RELEVANT forces comprehensive coverage; multi-symptom guidance ensures secondary docs aren't dropped |
| 2. Answerability | Sufficiency gate | Relevant ≠ sufficient; strict "all steps present" criterion reduces over-confidence false positives |
| 3. JSON output | Structured extraction | Isolated to prevent format corruption mid-reasoning |

---

## Change History

| Version | Change |
|---------|--------|
| 1.0 | Initial. Four-step chain-of-thought: extract core question → assess each document (RELEVANT / PARTIAL / NOT RELEVANT) → assess answerability from RELEVANT only → output JSON. Retrieval simulated by eval harness. |
| 2.0 | Agent owns retrieval via `retrieve_documents` tool call. Step 1 reframed as query formulation + tool call. Two-turn harness conversation. Added `[RELATED DOCS]` field. |
| 3.0 | Retrieval moved to Python (`src/retrieve.py`); LLM receives pre-fetched docs. PARTIAL → SUPPORTING with explicit multi-symptom and subject/body divergence guidance. Answerability tightened: `true` only when ALL steps present; "when in doubt, prefer false". Confidence scale changed from float 0.0–1.0 to int 0–100. Subject field added to user prompt. Bidirectional graph expansion added to retrieval. |
