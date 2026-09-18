#!/bin/sh
set -e

# Install GuardrailsAI hub validators at container start.
# If GUARDRAILS_API_KEY is absent, the validators fall back to pass-through
# mode (see src/guardrails.py) — the pipeline still runs, just without them.
if [ -n "$GUARDRAILS_API_KEY" ]; then
    echo "Installing GuardrailsAI validators..."
    guardrails hub install hub://guardrails/detect_jailbreak --quiet || true
    guardrails hub install hub://guardrails/detect_prompt_injection --quiet || true
    guardrails hub install hub://guardrails/detect_system_prompt_leakage --quiet || true
    guardrails hub install hub://guardrails/extracted_summary_sentences_match --quiet || true
    guardrails hub install hub://guardrails/bert_toxic --quiet || true
    guardrails hub install hub://guardrails/guardrails_pii --quiet || true
    echo "GuardrailsAI validators ready."
else
    echo "GUARDRAILS_API_KEY not set — validators will run in pass-through mode."
fi

exec uvicorn src.api:app --host 0.0.0.0 --port 8000
