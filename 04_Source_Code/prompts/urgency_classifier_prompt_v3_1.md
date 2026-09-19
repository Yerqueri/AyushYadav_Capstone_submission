# Urgency Classifier Prompt — CloudServe Support Triage

**Prompt ID:** PR-03  
**Version:** 3.1  
**Purpose:** Classify the urgency of a support ticket as `high`, `medium`, or `low`. Used to prioritise queue routing and SLA assignment. Runs after intent classification.  
**Model target:** `gpt-4o-mini` (OpenAI)  
**Evaluation dataset:** `development_tickets.json` (500 tickets — 146 high, 226 medium, 128 low)

---

## Urgency Level Reference

| Level | Meaning |
|-------|---------|
| `high` | Active, ongoing problem affecting production users or systems right now — needs immediate response |
| `medium` | Production is degraded, a process is broken, or a deadline is approaching — needs response within hours |
| `low` | How-to question, staging-only issue, or purely informational inquiry — standard queue |

---

## Output Schema

```json
{
  "urgency": "high" | "medium" | "low",
  "confidence": 0.0,
  "reasoning_summary": "<one sentence citing the dominant signal>"
}
```

`confidence` is a float from `0.0` to `1.0`.
- `≥ 0.85` — strong signal: explicit keywords, clear production context, or definitive intent prior
- `0.70–0.84` — clear level but requires inference from context
- `< 0.70` — ambiguous; level inferred without explicit keywords

---

## System Prompt

```
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

Do not include any text after the JSON object.
```

---

## User Prompt Template

```
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
      all users locked out, service completely unreachable, deployment blocked with no
      workaround, active data loss, active data exposure, or credentials exposed in
      a public location (e.g. public repo, public paste)
  (c) Explicit urgency language refers to a live, broken production system affecting
      multiple users or services — not a single user's access issue:
      "urgent", "ASAP", "down", "broken", "can't [access / deploy / connect]"

Answer NO if none of the above apply, or if the issue is in staging/development.
If NO → continue to Step 2.

STEP 2 — SCREEN FOR LOW URGENCY
Urgency is LOW only if BOTH of the following are true:
  (a) No active failure in production — issue is in staging/dev, or there is no failure at all
  (b) The request is entirely informational with no operational blocker:
      a how-to question, a feature request, or general product inquiry
      NOT LOW: any access or permission issue blocking any user — that is MEDIUM;
      any failing automated job or incorrect config causing a real problem — MEDIUM

If both (a) AND (b) are true → urgency is LOW.
If either is false → urgency is MEDIUM.

MEDIUM is the safe default for any operational problem not yet classified:
degraded but running, one of multiple things broken, automated jobs failing,
a compliance or audit request, a time-sensitive integration, or anything where the
customer is reporting a real problem but the service is still partially functional.

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
```

---

## Examples

### Example A — HIGH via body (active production regression requiring rollback)

**Input:**
```
Body:          how do I revert to earlier revision? release we put out this morning is
               causing errors and I better i roll back than try to fix forward.
Customer Tier: business
Intent:        rollback_request
```

**Expected chain-of-thought:**
```
STEP 1 — SCREEN FOR HIGH URGENCY
(a) Intent is rollback_request — rollback_request does not trigger (a) automatically.
(b) "release we put out this morning is causing errors" — a production release is actively
    breaking things; customers are impacted now. Service is degraded with no forward fix.
    YES. Urgency is HIGH. Skip to Step 3.

STEP 3 — CALIBRATE CONFIDENCE
0.85 — active production regression confirmed by body; "causing errors" is an explicit
failure signal, business tier, no staging qualifier.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "high",
  "confidence": 0.85,
  "reasoning_summary": "Active production regression this morning with customer unable to proceed; immediate rollback required."
}
```

---

### Example B — LOW (staging + how-to)

**Input:**
```
Body:          We are setting up SAML with our identity provider and users are being
               bounced back to an error page after authenticating. This is for our
               staging environment.
Customer Tier: standard
Intent:        sso_configuration
```

**Expected chain-of-thought:**
```
STEP 1 — SCREEN FOR HIGH URGENCY
(a) No. (b) No — staging environment, no production impact.
(c) No explicit urgency language. Answer: NO.

STEP 2 — SCREEN FOR LOW URGENCY
(a) No active production failure — explicitly staging only. TRUE.
(b) Setup/configuration task with no stated deadline; not an access blocker in production. TRUE.
Both (a) and (b) are true → LOW.

STEP 3 — CALIBRATE CONFIDENCE
0.90 — "This is for our staging environment" is an explicit, unambiguous LOW signal.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "low",
  "confidence": 0.90,
  "reasoning_summary": "SSO configuration error in staging environment only; no production users affected."
}
```

---

### Example C — HIGH via body (active database failure)

**Input:**
```
Body:          We are getting errors saying no connection is available. This started when
               we scaled up our service. The database itself shows very few active queries.
Customer Tier: standard
Intent:        database_issue
```

**Expected chain-of-thought:**
```
STEP 1 — SCREEN FOR HIGH URGENCY
(a) No.
(b) "errors saying no connection is available" — service is actively failing. No staging
mention; "started when we scaled up" confirms production context. Service cannot proceed.
YES. Urgency is HIGH. Skip to Step 3.

STEP 3 — CALIBRATE CONFIDENCE
0.80 — active production failure inferred from "no connection is available" (ongoing),
but no explicit urgency keywords ("urgent", "ASAP", etc.).

STEP 4 — OUTPUT
```
```json
{
  "urgency": "high",
  "confidence": 0.80,
  "reasoning_summary": "Active database connection failure in production; service is unreachable since scaling."
}
```

---

### Example D — MEDIUM (degraded, not blocked)

**Input:**
```
Body:          One of our production keys started returning 401 at around yesterday
               morning without any change on our side. Other keys against the same
               account are still working.
Customer Tier: business
Intent:        api_key_issue
```

**Expected chain-of-thought:**
```
STEP 1 — SCREEN FOR HIGH URGENCY
(a) No. (b) No — "Other keys... are still working." Service is not fully blocked.
(c) No explicit urgency language. Answer: NO.

STEP 2 — SCREEN FOR LOW URGENCY
(a) Production key is affected — not purely staging. FALSE.
(b) is irrelevant since (a) is false.
Not both true → MEDIUM.

STEP 3 — CALIBRATE CONFIDENCE
0.85 — clear MEDIUM: production is degraded (one key broken) but functional
(other keys working). No competing interpretation.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "medium",
  "confidence": 0.85,
  "reasoning_summary": "One production API key returning 401 but other keys are working — degraded but not fully blocked."
}
```

---

## Design Rationale

| Step | Role | Why It's Separate |
|------|------|-------------------|
| 1. HIGH screen | Hard gate | OR-logic over definitive signals; skip-to-step-3 prevents the model second-guessing a HIGH decision |
| 2. LOW screen | Hard gate (AND-logic) | Requiring BOTH conditions prevents low bias — a staging issue that has a deadline stays MEDIUM; access/permission issues are explicitly NOT LOW |
| 3. Calibrate | Decision lock-in + uncertainty | Forcing confidence reasoning prevents uniform overconfidence; MEDIUM is the explicit default |
| 4. JSON output | Structured extraction | Isolated to prevent format corruption mid-reasoning |

---

## Change History

| Version | Date | Change |
|---------|------|--------|
| 3.0 | 2026-09-14 | Previous version (baseline for this cycle) |
| 3.1 | 2026-09-16 | Removed `rollback_request` from STEP 1(a) auto-HIGH (rollback urgency now determined by body context); added credential-in-public-location to STEP 1(b); added multi-user qualifier to STEP 1(c) to prevent single-user access issues from triggering HIGH; narrowed STEP 2(b) LOW criteria — access/permission issues and failing jobs are explicitly NOT LOW (→ MEDIUM); updated STEP 3 confidence raise to reference only `security_incident` |

## Known Weaknesses (v3.1)

- Two-level urgency misses (HIGH→LOW and LOW→HIGH) can occur when body signals are weak or contradictory — STEP 3 confidence < 0.65 is the intended signal.
- Dataset label inconsistencies observed: VAL-0008/VAL-0074 (identical bodies, different labels) and VAL-0011/VAL-0031 (same body, different labels) — these cannot be resolved via prompt tuning.
- `rollback_request` urgency now depends entirely on body context; a terse rollback ticket with no body detail will default to MEDIUM.
