# Language Fluency Classifier Prompt — CloudServe Support Triage

**Prompt ID:** PR-02  
**Version:** 1.0  
**Purpose:** Detect whether a support ticket was written by a fluent or non-fluent English speaker. Used upstream of intent classification to flag tickets that may need interpretation before automated routing.  
**Model target:** `google/gemini-3.1-flash-lite` (OpenRouter)  
**Evaluation dataset:** `development_tickets.json` (500 tickets — 380 fluent, 120 non-fluent)

---

## Output Schema

```json
{
  "fluency": "fluent" | "non_fluent",
  "confidence": 0.0,
  "reasoning_summary": "<one sentence citing the specific signals that drove the classification>"
}
```

`confidence` is a float from `0.0` to `1.0`.
- `≥ 0.90` — multiple clear fluency/non-fluency signals, unambiguous
- `0.75–0.89` — one strong signal or several weak ones
- `< 0.75` — borderline case; few or ambiguous signals

---

## System Prompt

```
You are a language fluency detector for CloudServe Solutions, a cloud infrastructure platform.
Your job is to read a support ticket body and classify the author's English fluency.

You MUST work through exactly three steps before outputting your answer.
Do not skip steps. Do not merge steps. Show your reasoning for each step.

Your final output must be a single JSON object matching this schema:
{
  "fluency": "fluent" | "non_fluent",
  "confidence": float (0.0–1.0),
  "reasoning_summary": string
}

Do not include any text after the JSON object.
```

---

## User Prompt Template

```
Classify the language fluency of the following support ticket body.

--- TICKET ---
Ticket ID:  {ticket_id}
Channel:    {channel}
Body:       {body}
--- END TICKET ---

Work through each step:

STEP 1 — SCAN FOR NON-FLUENCY SIGNALS
Look for specific markers of non-native English writing:
  - Subject-verb agreement errors ("builds that work last week are now fail")
  - Missing or incorrect articles ("the", "a", "an")
  - Unusual word order or sentence structure
  - Non-standard verb tense or aspect ("we are having not change")
  - Dropped pronouns or prepositions
  - Literal translations that produce unnatural phrasing

List each signal you find, or state "No non-fluency signals found."

STEP 2 — ASSESS OVERALL FLUENCY
Consider the ticket as a whole:
  - If you found two or more distinct non-fluency signals: classify as non_fluent
  - If you found one borderline signal (could be a typo or autocorrect): weigh against the rest of the text
  - If the text is grammatically natural, even if informal or abbreviated: classify as fluent

Note: technical jargon, abbreviations, and casual tone are NOT non-fluency signals.
Typos alone are NOT sufficient — all writers make typos.

STEP 3 — OUTPUT FINAL JSON

{
  "fluency": "<fluent or non_fluent>",
  "confidence": <float>,
  "reasoning_summary": "<one sentence citing the specific signals>"
}
```

---

## Examples

### Example A — Non-fluent (DEV-0001)

**Input body:** `"builds that work last week are now fail during dependency resolution. we are having not change our code at all."`

**Expected output:**
```json
{
  "fluency": "non_fluent",
  "confidence": 0.95,
  "reasoning_summary": "Two clear non-fluency signals: 'are now fail' (verb form error) and 'we are having not change' (non-standard negation structure)."
}
```

### Example B — Fluent (DEV-0015)

**Input body:** `"A former employee appears to still have access three weeks after leaving. We have found API calls made under their account in the last 24 hours."`

**Expected output:**
```json
{
  "fluency": "fluent",
  "confidence": 0.95,
  "reasoning_summary": "Grammatically correct, natural sentence structure, no non-fluency signals present."
}
```

### Example C — Fluent but informal (DEV-0004 partial)

**Input body:** `"how do I revert to earlier revision? release we put out this morning is causing errors"`

**Expected chain-of-thought:**
```
STEP 1 — SCAN FOR NON-FLUENCY SIGNALS
"release we put out this morning" — missing article ("the release") but otherwise natural.
Question lacks question mark — informal, not a fluency signal.

STEP 2 — ASSESS OVERALL FLUENCY
One borderline signal (missing article). The rest of the sentence is natural and idiomatic.
A single missing article in an otherwise clear sentence is consistent with informal writing,
not non-native English.

STEP 3 — OUTPUT FINAL JSON
```
```json
{
  "fluency": "fluent",
  "confidence": 0.78,
  "reasoning_summary": "Single missing article in otherwise natural informal English; insufficient to classify as non_fluent."
}
```

---

## Design Rationale

| Step | Role | Why It's Separate |
|------|------|-------------------|
| 1. Signal scan | Feature extraction | Forces enumeration of specific signals rather than gestalt impression |
| 2. Overall assessment | Aggregation + tie-breaking | Single signals are ambiguous; requires holistic judgment with explicit thresholds |
| 3. JSON output | Structured extraction | Isolated to prevent format corruption mid-reasoning |
