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

# Pre-download all-MiniLM-L6-v2 embedding model so container startup needs zero outbound model downloads
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()"

# Copy application source code and datasets
COPY . .

# Ensure storage/, data/, ingress/, and egress/ directories exist for volume mounts
RUN mkdir -p storage storage/results data evaluation/results ingress egress

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT ["./docker-entrypoint.sh"]
