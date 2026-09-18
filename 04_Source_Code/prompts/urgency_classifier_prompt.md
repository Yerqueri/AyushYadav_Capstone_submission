# Urgency Classifier Prompt — CloudServe Support Triage

**Prompt ID:** PR-03  
**Version:** 3.5  
**Purpose:** Classify the urgency of a support ticket as `high`, `medium`, or `low`. Used to prioritise queue routing and SLA assignment. Runs after intent classification.  
**Model target:** `google/gemini-3.1-flash-lite` (OpenRouter)  
**Evaluation dataset:** `development_tickets.json` (500 tickets — 146 high, 226 medium, 128 low)

---

## Urgency Level Reference

| Level | Meaning |
|-------|---------|
| `high` | Active, ongoing problem affecting production users or systems right now — needs immediate response |
| `medium` | Production is degraded, a process is broken, a security concern is active, or a deadline is approaching — needs response within hours |
| `low` | How-to question, staging-only issue, configuration setup, or single-user non-critical inconvenience — standard queue |

---

## Output Schema

```json
{
  "urgency": "high" | "medium" | "low",
  "confidence": int (0-100),
  "reasoning_summary": "<one sentence citing the dominant signal>"
}
```

`confidence` is an integer from `0` to `100`.
- `≥ 85` — strong signal: explicit keywords, clear production context, or definitive intent prior
- `65–84` — clear level but requires inference from context
- `< 65` — ambiguous; level inferred without explicit keywords

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
  "confidence": int (0-100),
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
      all users locked out, service completely unreachable, build/deploy pipeline blocked
      with no workaround, a deployed release actively causing errors in production,
      a service component or integration that has stopped functioning and not self-recovered
      (e.g. webhook delivery halted, log forwarding stopped overnight),
      active data loss, active data exposure, or credentials exposed in a public location
      (e.g. public repo, public paste)
  (c) Explicit urgency language refers to a live, broken production system affecting
      multiple users or services — not a single user's access issue:
      "urgent", "ASAP", "down", "broken", "can't [access / deploy / connect]"

Answer NO if none of the above apply, or if the issue is in staging/development.
If NO → continue to Step 2.

STEP 2 — SCREEN FOR LOW URGENCY
Urgency is LOW only if BOTH of the following are true:
  (a) No active failure in production — issue is in staging/dev, or there is no failure at all
  (b) The request is informational or a configuration setup task with no production blocker:
      a how-to question, initial setup/configuration of a new feature not yet in production,
      a single-user non-critical access issue that does not block production work
      (e.g. one developer cannot see a non-critical project)

      NOT LOW — always MEDIUM:
        - billing_query, data_residency, compliance_request, or unclear_request intents
          (always-escalate categories that require human review)
        - compliance, audit, or data-retention requests (implied deadline, requires action)
        - feature requests (require product team triage, not purely informational)
        - access revocation for a departing employee (time-sensitive security hygiene)
        - access issues preventing production operations or affecting a team or service
        - a batch job, export, or database restore that has been queued or running
          significantly longer than expected (possible pipeline failure)
        - rate limits or quotas currently being hit in production (API calls returning errors)
        - automated jobs failing or incorrect config actively causing production problems
        - ongoing security concern: credentials, secrets, or sensitive data currently
          appearing in logs or outputs (even if framed as a how-to question)

If both (a) AND (b) are true → urgency is LOW.
If either is false → urgency is MEDIUM.

MEDIUM is the safe default for any operational problem not yet classified:
degraded but running, one of multiple things broken, automated jobs failing,
a compliance or audit request, a time-sensitive integration, ongoing security concerns
(e.g. sensitive data appearing in logs), a recent production incident requiring
a configuration change to prevent recurrence, or anything where the customer is
reporting a real problem but the service is still partially functional.

STEP 3 — CALIBRATE CONFIDENCE
Start at 80. Then adjust:
  Raise to >= 85 if the dominant signal is explicit (exact keyword match, clear
  production context, or a definitive HIGH intent — security_incident).
  Lower to 65-79 if urgency was inferred from context without explicit keywords.
  Lower to < 65 if the body is vague or signals are mixed.

STEP 4 — OUTPUT FINAL JSON

{{
  "urgency": "<high, medium, or low>",
  "confidence": <int>,
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
(a) Intent is rollback_request — does not trigger (a) automatically.
(b) "release we put out this morning is causing errors" — a deployed release is actively
    causing production errors with no forward fix ready. YES. Urgency is HIGH. Skip to Step 3.

STEP 3 — CALIBRATE CONFIDENCE
85 — active production regression confirmed by body; "causing errors" is an explicit
failure signal with business tier and no staging qualifier.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "high",
  "confidence": 85,
  "reasoning_summary": "Deployed release is actively causing production errors; immediate rollback required."
}
```

---

### Example B — LOW (staging + setup)

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
(b) Initial SSO configuration setup; not yet in production; none of the NOT-LOW cases apply. TRUE.
Both true → LOW.

STEP 3 — CALIBRATE CONFIDENCE
90 — "This is for our staging environment" is an explicit, unambiguous LOW signal.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "low",
  "confidence": 90,
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
80 — active production failure inferred from "no connection is available" (ongoing),
but no explicit urgency keywords ("urgent", "ASAP", etc.).

STEP 4 — OUTPUT
```
```json
{
  "urgency": "high",
  "confidence": 80,
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
Not both true → MEDIUM.

STEP 3 — CALIBRATE CONFIDENCE
85 — clear MEDIUM: production is degraded (one key broken) but functional.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "medium",
  "confidence": 85,
  "reasoning_summary": "One production API key returning 401 but other keys are working — degraded but not fully blocked."
}
```

---

### Example E — MEDIUM (ongoing security concern, framed as how-to)

**Input:**
```
Body:          Where should secrets be stored so they do not appear in the logs?
               At the moment ours are being printed at startup and I am not comfortable
               with that.
Customer Tier: standard
Intent:        configuration_help
```

**Expected chain-of-thought:**
```
STEP 1 — SCREEN FOR HIGH URGENCY
(a) No. (b) No explicit active breach or public exposure; secrets are in internal logs.
(c) No urgency language. Answer: NO.

STEP 2 — SCREEN FOR LOW URGENCY
(a) No obvious production failure, but secrets are actively being logged at startup. TRUE.
(b) Framed as a how-to question — BUT this matches NOT-LOW: "ongoing security concern:
    credentials/sensitive data currently appearing in logs" → FALSE.
Not both true → MEDIUM.

STEP 3 — CALIBRATE CONFIDENCE
75 — ongoing security concern (secrets in logs) inferred; no explicit production impact stated.

STEP 4 — OUTPUT
```
```json
{
  "urgency": "medium",
  "confidence": 75,
  "reasoning_summary": "Secrets are actively being printed in logs at startup — ongoing security concern even if framed as how-to."
}
```

---

## Design Rationale

| Step | Role | Why It's Separate |
|------|------|-------------------|
| 1. HIGH screen | Hard gate | OR-logic over definitive signals; skip-to-step-3 prevents second-guessing a HIGH decision |
| 2. LOW screen | Hard gate (AND-logic) | NOT-LOW list explicitly blocks common false-LOW patterns (compliance, feature requests, security concerns, access revocations, stuck jobs, rate limits) |
| 3. Calibrate | Decision lock-in + uncertainty | Forcing confidence reasoning prevents uniform overconfidence; MEDIUM is the explicit default |
| 4. JSON output | Structured extraction | Isolated to prevent format corruption mid-reasoning |

---

## Change History

| Version | Date | Change |
|---------|------|--------|
| 3.0 | 2026-09-14 | Previous version (baseline for this cycle) |
| 3.1 | 2026-09-16 | Removed `rollback_request` from STEP 1(a) auto-HIGH; added credential exposure and multi-user qualifier; narrowed LOW criteria (first pass) |
| 3.2 | 2026-09-17 | STEP 1(b): added "deployed release actively causing errors"; STEP 2(b): replaced broad NOT-LOW list with explicit categories; added Example E (ongoing security concern) |
| 3.3 | 2026-09-17 | STEP 1(b): added "service component or integration stopped functioning and not self-recovered" (e.g. webhook delivery, log forwarding); STEP 2 NOT-LOW: added batch job/export stuck, always-escalate intents, departing employee access revocation; confidence scale changed from float to int |
| 3.4 | 2026-09-17 | Added "users currently unable to authenticate" to STEP 1(b) and "platform operation blocking work" to NOT-LOW — reverted due to over-triage on single-user login cases |
| 3.5 | 2026-09-17 | Reverted v3.4 additions; retained rate limits hit in production as NOT-LOW (replaces over-broad "platform operation blocking work") |

## Known Weaknesses (v3.5)

- Dataset contains label inconsistencies for `rollback_request` urgency (identical bodies labeled HIGH vs MEDIUM) — no prompt fix possible.
- Credentials exposed in a public repo are classified HIGH by this prompt (security hygiene) but some dataset labels have these as MEDIUM — intentional divergence favoring safety.
- A large export job stuck/queued for many hours may read as LOW from ticket text if framed as a procedural question — prompt cannot infer time-sensitivity not stated in the body.
- DEV-0026: API pagination data integrity issue labeled HIGH but ticket text reads as a how-to question — classifier correctly infers LOW/MEDIUM; label appears inconsistent.
- Over-triage (predicting higher urgency than labeled) is the accepted failure mode; under-triage is the dangerous failure mode to minimize.
