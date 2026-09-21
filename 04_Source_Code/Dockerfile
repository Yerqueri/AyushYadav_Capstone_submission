FROM python:3.11-slim

WORKDIR /app

# gcc/g++ required by sentence-transformers; binutils for strip; curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    binutils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch & torchvision CPU-only first to prevent downloading CUDA binaries and version mismatch
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install Python deps before copying source so layer caching works
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y kubernetes botocore 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -name "*.so" -exec strip --strip-unneeded {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /usr/local/lib/python3.11/site-packages/torch/test /usr/local/lib/python3.11/site-packages/torch/include /usr/local/lib/python3.11/site-packages/torch/share

# Pre-download required ML model weights in a single RUN step with automatic cache cleanup
RUN python -c "\
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['test']); \
from transformers import pipeline; pipeline('text-classification', model='martin-ha/toxic-comment-model', device='cpu'); \
from guardrails_ai.detect_jailbreak import DetectJailbreak; DetectJailbreak(on_fail='noop', use_local=True); \
import spacy, spacy.util; orig=spacy.load; spacy.load=lambda name, **kw: orig('en_core_web_sm', **kw) if name=='en_core_web_lg' else orig(name, **kw); orig_pkg=spacy.util.is_package; spacy.util.is_package=lambda name: True if name=='en_core_web_lg' else orig_pkg(name); from guardrails_ai.guardrails_pii import GuardrailsPII; GuardrailsPII(entities=['PHONE_NUMBER'], on_fail='noop'); \
" && rm -rf /root/.cache/pip /tmp/* && find /usr/local/lib/python3.11/site-packages/ -name "*.pyc" -delete && find /usr/local/lib/python3.11/site-packages/ -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Copy application source code and datasets
COPY src ./src
COPY data ./data
COPY docs ./docs
COPY evaluation ./evaluation
COPY prompts ./prompts
COPY tests ./tests
COPY docker-entrypoint.sh README.md docker-compose.yml ./

# Ensure storage/, data/, ingress/, and egress/ directories exist for volume mounts
RUN mkdir -p storage storage/results data evaluation/results ingress egress

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT ["./docker-entrypoint.sh"]
