# Intent Classifier Prompt — CloudServe Support Triage

**Prompt ID:** PR-01  
**Version:** 1.1  
**Purpose:** Classify incoming support tickets into one of 22 intent classes, flag must-not-auto-respond cases, and emit a calibrated confidence score. Urgency is handled by a separate agent.  
**Model target:** `google/gemini-3.1-flash-lite` (OpenRouter)  
**Evaluation dataset:** `development_tickets.json` (500 tickets, prefix `DEV-`)

---

## Intent Class Reference

| Intent | Description |
|--------|-------------|
| `account_access` | Customer cannot log in or is locked out of their account |
| `api_key_issue` | API key not working, revoked, or returning auth errors |
| `api_usage_question` | How-to questions about using the API (rate limits, pagination, etc.) |
| `authentication_failure` | Auth errors beyond API keys — OAuth, tokens, SSO session problems |
| `billing_query` | Invoice questions, charge disputes, plan changes, payment methods **[ALWAYS ESCALATE]** |
| `compliance_request` | Audit evidence, data retention records, regulatory documentation **[ALWAYS ESCALATE]** |
| `configuration_help` | Help configuring services, environment settings, or integrations |
| `data_export` | Requesting a bulk export of data or logs |
| `data_residency` | Questions about where data is stored and regional data laws **[ALWAYS ESCALATE]** |
| `database_issue` | Database connection failures, query errors, slow queries |
| `deployment_failure` | Build or deployment pipeline failures, CI/CD errors |
| `feature_request` | New capability or product enhancement request **[ALWAYS ESCALATE]** |
| `integration_help` | Connecting third-party tools or setting up webhooks/APIs |
| `onboarding` | New customer getting started, initial setup guidance |
| `performance_degradation` | Slow response times, high latency, timeouts |
| `quota_or_overage` | Approaching or exceeding plan limits, overage charges |
| `rate_limit` | Hitting API or service rate limits (429 responses) |
| `rollback_request` | Rolling back a deployment or reverting a configuration change |
| `security_incident` | Confirmed breach, unauthorized access, active credential compromise **[ALWAYS ESCALATE]** |
| `sso_configuration` | Setting up or troubleshooting SAML/SSO/IdP configuration |
| `unclear_request` | Insufficient context to determine intent **[ALWAYS ESCALATE]** |
| `webhook_issue` | Webhooks not firing, wrong payloads, delivery failures |

**Always-escalate intents** (must_not_auto_respond = true regardless of confidence):  
`security_incident`, `compliance_request`, `feature_request`, `unclear_request`, `data_residency`, `billing_query`

---

## Output Schema

```json
{
  "intent": "<one of the 22 intent classes>",
  "confidence": 0.0,
  "must_not_auto_respond": true | false,
  "reasoning_summary": "<one sentence explaining the classification>"
}
```

`confidence` is a float from `0.0` to `1.0`. Default: `0.80`.
- `≥ 0.85` — all raise conditions met, unambiguous keywords, no competing intent
- `0.80` — clear intent with minor ambiguity, competing intent ruled out quickly
- `0.65–0.79` — competing intents, short/vague body, non-fluent, or inferred intent
- `< 0.65` — two or more lower conditions simultaneously, or genuine ambiguity

---

## System Prompt

```
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

Do not include any text after the JSON object.
```

---

## User Prompt Template

```
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

{
  "intent": "<chosen intent>",
  "confidence": <float>,
  "must_not_auto_respond": <true|false>,
  "reasoning_summary": "<one sentence summary of why this classification was chosen>"
}
```

---

## Example — Filled Prompt (DEV-0001)

### Input

```
--- TICKET ---
Ticket ID:       DEV-0001
Channel:         chat
Subject:         (none)
Body:            builds that work last week are now fail during dependency 
                 resolution. we are having not change our code at all.
Customer Tier:   standard
Language:        non_fluent
--- END TICKET ---
```

### Expected Chain-of-Thought Output

```
STEP 1 — DECODE THE REQUEST
Non-fluent English. Restated: "Our builds, which worked last week, are now 
failing at dependency resolution, and we have not changed our code."
Urgency cue: "now fail" (present tense, ongoing break). No production-down 
language, but blocking deploys.

STEP 2 — EXTRACT TECHNICAL SIGNALS
Technical terms: "builds", "dependency resolution", "fail"
Action verb: failing (passive/ongoing)
No error codes provided. Environment context: CI/CD pipeline implied.

STEP 3 — SCREEN FOR HARD-ESCALATE INTENTS
No hard-escalate trigger found.
- Not a confirmed breach or compromise (no unauthorized access evidence).
- Not compliance, feature request, unclear (clear technical signal present),
  data residency, or billing.

STEP 4 — SELECT THE PRIMARY INTENT
Primary: deployment_failure
Competing consideration: configuration_help (dependency config might be the 
root cause), but the observable symptom is a failing build — deployment_failure 
is the correct surface-level triage intent.

STEP 5 — CALIBRATE CONFIDENCE
Score: 0.88
"Build failure during dependency resolution" maps directly to deployment_failure 
with no serious competing intent; non-fluent language is clear enough once 
normalised.

STEP 6 — OUTPUT FINAL JSON
{
  "intent": "deployment_failure",
  "confidence": 0.88,
  "must_not_auto_respond": false,
  "reasoning_summary": "Builds failing at dependency resolution with no customer-side changes is an active deployment pipeline failure requiring immediate auto-response."
}
```

**Ground truth:** intent=`deployment_failure`, must_not_auto_respond=`false` ✓

---

## Prompt Design Rationale

| Step | Role | Why It's Separate |
|------|------|-------------------|
| 1. Decode | Input normalization | Non-fluent tickets (21% of dataset) distort signal extraction if read raw |
| 2. Signals | Feature extraction | Forces the model to surface tokens rather than pattern-match on gestalt impression |
| 3. Hard-escalate screen | Safety gate | Four intents require 100% recall; screening them before open classification prevents them being overridden by a plausible technical intent |
| 4. Intent selection | Core classification | Explicit tie-breaking instruction reduces multi-label hedging; disambiguation guide prevents the four most common confusion pairs |
| 5. Confidence | Self-calibration | Forces explicit uncertainty reasoning rather than a silent high-confidence wrong answer |
| 6. JSON output | Structured extraction | Isolating output to the final step prevents mid-reasoning format corruption |

---

## Change History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-09-12 | Initial prompt — six-step chain-of-thought, 22 intent classes, 6 always-escalate |
| 1.1 | 2026-09-16 | Tightened `security_incident` (401/rotation excluded); tightened `unclear_request` (threshold: <2 signals); added STEP 4 disambiguation guide for 4 confusion pairs (api_key_issue/security_incident, quota_or_overage/billing_query, api_usage_question/data_export, authentication_failure/account_access) |

## Known Weaknesses (v1.1)

- `billing_query` and `data_residency` are intentionally always-escalate even for informational questions — accepted business decision due to financial/legal liability exposure.
- `account_access` vs `configuration_help` boundary is ambiguous for permission-management tickets (2 known label conflicts in validation set).
