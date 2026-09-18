FROM python:3.11-slim

WORKDIR /app

# gcc/g++ required by sentence-transformers; curl for the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps before copying source so this layer is cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download all-MiniLM-L6-v2 so startup doesn't need outbound network
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()"

# Copy application source
COPY . .

# storage/ is created at runtime; pre-create so the mount point exists
RUN mkdir -p storage

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT ["./docker-entrypoint.sh"]
