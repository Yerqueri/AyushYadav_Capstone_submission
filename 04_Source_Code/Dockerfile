FROM python:3.11-slim

WORKDIR /app

# gcc/g++ required by sentence-transformers; curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch & torchvision CPU-only first to prevent downloading CUDA binaries and version mismatch
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install Python deps before copying source so layer caching works
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download all Hugging Face, SpaCy, ChromaDB, and Guardrails models into image cache so zero network downloads occur at runtime
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['test']);"
RUN python -c "from transformers import pipeline; pipeline('text-classification', model='martin-ha/toxic-comment-model', device='cpu');"
RUN python -c "from guardrails_ai.detect_jailbreak import DetectJailbreak; DetectJailbreak(on_fail='noop', use_local=True);"
RUN python -c "import spacy; orig=spacy.load; spacy.load=lambda name, **kw: orig('en_core_web_sm', **kw) if name=='en_core_web_lg' else orig(name, **kw); from guardrails_ai.guardrails_pii import GuardrailsPII; GuardrailsPII(entities=['PHONE_NUMBER'], on_fail='noop');"

# Copy application source code and datasets
COPY . .

# Ensure storage/, data/, ingress/, and egress/ directories exist for volume mounts
RUN mkdir -p storage storage/results data evaluation/results ingress egress

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT ["./docker-entrypoint.sh"]
