#!/bin/bash
set -e

# Configure GuardrailsAI with API Token at container start if key is present.
# Note: All validator packages are pre-installed in the Docker image; no online hub downloading is needed.
if [ -n "$GUARDRAILS_API_KEY" ]; then
    echo "[Entrypoint] Configuring GuardrailsAI with API Token..."
    guardrails configure --token "$GUARDRAILS_API_KEY" --disable-metrics || guardrails configure --token "$GUARDRAILS_API_KEY" || true
    echo "[Entrypoint] GuardrailsAI configured."
else
    echo "[Entrypoint] GUARDRAILS_API_KEY not set — guardrails running in default local mode."
fi

# Default environment settings for mode and ingress/egress volume paths
EXEC_MODE="${MODE:-api}"
INPUT_PATH="${INPUT_PATH:-${INGRESS_PATH:-${BATCH_INPUT:-${INPUT:-data/development_tickets.json}}}}"
OUTPUT_PATH="${OUTPUT_PATH:-${EGRESS_PATH:-${BATCH_OUTPUT:-${OUTPUT:-storage/results}}}}"
CONCURRENCY_VAL="${BATCH_CONCURRENCY:-4}"
SAMPLE_VAL="${BATCH_SAMPLE:-}"

# Parse CLI arguments if provided
CLI_INPUT=""
CLI_OUTPUT=""
CLI_SAMPLE=""
CLI_CONCURRENCY=""

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        api|batch|both|all)
            EXEC_MODE="$1"
            shift
            ;;
        --input|-i|--ingress)
            CLI_INPUT="$2"
            EXEC_MODE="batch"
            shift 2
            ;;
        --output|-o|--egress)
            CLI_OUTPUT="$2"
            EXEC_MODE="batch"
            shift 2
            ;;
        --sample|-s)
            CLI_SAMPLE="$2"
            EXEC_MODE="batch"
            shift 2
            ;;
        --concurrency|-c)
            CLI_CONCURRENCY="$2"
            EXEC_MODE="batch"
            shift 2
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

# Apply CLI flag overrides if present
if [ -n "$CLI_INPUT" ]; then INPUT_PATH="$CLI_INPUT"; fi
if [ -n "$CLI_OUTPUT" ]; then OUTPUT_PATH="$CLI_OUTPUT"; fi
if [ -n "$CLI_SAMPLE" ]; then SAMPLE_VAL="$CLI_SAMPLE"; fi
if [ -n "$CLI_CONCURRENCY" ]; then CONCURRENCY_VAL="$CLI_CONCURRENCY"; fi

# If positional arguments represent a custom command (e.g. python -m ..., pytest, bash), execute it
if [ ${#POSITIONAL[@]} -gt 0 ] && [[ "${POSITIONAL[0]}" != -* ]]; then
    echo "[Entrypoint] Executing custom command: ${POSITIONAL[*]}"
    exec "${POSITIONAL[@]}"
fi

# Helper function to run batch triage harness
run_batch_job() {
    mkdir -p "$OUTPUT_PATH"
    echo "[Entrypoint] Starting batch triage run..."
    echo "[Entrypoint] Ingress (Input): ${INPUT_PATH} | Egress (Output): ${OUTPUT_PATH} | Concurrency: ${CONCURRENCY_VAL}"
    
    CMD="python -m evaluation.harness --input ${INPUT_PATH} --output ${OUTPUT_PATH} --concurrency ${CONCURRENCY_VAL}"
    if [ -n "$SAMPLE_VAL" ]; then
        CMD="$CMD --sample ${SAMPLE_VAL}"
        echo "[Entrypoint] Sampling first ${SAMPLE_VAL} tickets."
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
        echo "[Entrypoint] Mode: BOTH (API Server + Batch simultaneously)"
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
