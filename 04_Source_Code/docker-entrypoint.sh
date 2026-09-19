#!/bin/bash
set -e

# Install GuardrailsAI hub validators at container start if key is present.
# If GUARDRAILS_API_KEY is absent, validators run in pass-through mode.
if [ -n "$GUARDRAILS_API_KEY" ]; then
    echo "[Entrypoint] Configuring GuardrailsAI with API Token..."
    guardrails configure --token "$GUARDRAILS_API_KEY" --disable-metrics || guardrails configure --token "$GUARDRAILS_API_KEY" || true
    echo "[Entrypoint] Installing GuardrailsAI validators..."
    guardrails hub install hub://guardrails/detect_jailbreak --quiet || true
    guardrails hub install hub://guardrails/detect_prompt_injection --quiet || true
    guardrails hub install hub://guardrails/detect_system_prompt_leakage --quiet || true
    guardrails hub install hub://guardrails/extracted_summary_sentences_match --quiet || true
    guardrails hub install hub://guardrails/bert_toxic --quiet || true
    guardrails hub install hub://guardrails/guardrails_pii --quiet || true
    echo "[Entrypoint] GuardrailsAI validators ready."
else
    echo "[Entrypoint] GUARDRAILS_API_KEY not set — validators running in pass-through mode."
fi

# Determine Execution Mode (default: MODE=api)
EXEC_MODE="${MODE:-api}"

# Support custom command override (if $1 starts with something other than api/batch/both/all)
if [ "$#" -gt 0 ] && [ "$1" != "api" ] && [ "$1" != "batch" ] && [ "$1" != "both" ] && [ "$1" != "all" ]; then
    echo "[Entrypoint] Executing custom command: $@"
    exec "$@"
fi

# Override MODE if $1 was passed explicitly as api/batch/both
if [ "$1" = "api" ] || [ "$1" = "batch" ] || [ "$1" = "both" ] || [ "$1" = "all" ]; then
    EXEC_MODE="$1"
fi

BATCH_INPUT_PATH="${BATCH_INPUT:-data/development_tickets.json}"
BATCH_OUTPUT_PATH="${BATCH_OUTPUT:-storage/}"
BATCH_CONCURRENCY_VAL="${BATCH_CONCURRENCY:-4}"

# Helper function to run batch triage harness
run_batch_job() {
    echo "[Entrypoint] Starting one-shot batch triage run..."
    echo "[Entrypoint] Input: ${BATCH_INPUT_PATH} | Output: ${BATCH_OUTPUT_PATH} | Concurrency: ${BATCH_CONCURRENCY_VAL}"
    
    CMD="python -m evaluation.harness --input ${BATCH_INPUT_PATH} --output ${BATCH_OUTPUT_PATH} --concurrency ${BATCH_CONCURRENCY_VAL}"
    if [ -n "$BATCH_SAMPLE" ]; then
        CMD="$CMD --sample ${BATCH_SAMPLE}"
        echo "[Entrypoint] Sampling first ${BATCH_SAMPLE} tickets."
    fi
    
    eval $CMD
    echo "[Entrypoint] Batch triage run completed successfully."
}

case "$EXEC_MODE" in
    batch)
        echo "[Entrypoint] Mode: BATCH ONLY"
        run_batch_job
        exit 0
        ;;
    both|all)
        echo "[Entrypoint] Mode: BOTH (API Server + One-Shot Batch simultaneously)"
        echo "[Entrypoint] Launching FastAPI server on port 8000 in background..."
        uvicorn src.api:app --host 0.0.0.0 --port 8000 &
        API_PID=$!
        
        # Wait for API to come online
        echo "[Entrypoint] Waiting for API server healthcheck..."
        for i in $(seq 1 30); do
            if curl -s http://localhost:8000/health > /dev/null 2>&1; then
                echo "[Entrypoint] API server is ready!"
                break
            fi
            sleep 1
        done
        
        # Execute batch run
        run_batch_job
        
        echo "[Entrypoint] Keeping container active with API server (PID $API_PID)..."
        wait $API_PID
        ;;
    api|*)
        echo "[Entrypoint] Mode: API SERVER ONLY"
        echo "[Entrypoint] Starting FastAPI server on 0.0.0.0:8000..."
        exec uvicorn src.api:app --host 0.0.0.0 --port 8000
        ;;
esac
