FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

# Build tools: the llama-cpp-python wheel published for linux is a musl build,
# so on glibc we compile from source with the CPU's own instructions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so the layer caches across code changes.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && pip install cmake ninja \
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_F16C=ON -DGGML_FMA=ON -DLLAMA_CURL=OFF" \
       pip install --no-binary llama-cpp-python "llama-cpp-python==0.3.19" \
    && pip install fastapi "uvicorn[standard]" httpx "pydantic>=2.0" \
       pydantic-settings diskcache PyYAML astral python-dotenv

COPY app/ ./app/
COPY scripts/ ./scripts/

# Model weights (1.1 GB). Baking them into the image means the container
# starts serving immediately and does not need the network to boot.
RUN mkdir -p models && \
    curl -L --fail -o models/Qwen3-1.7B-Q4_K_M.gguf \
    "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf" \
    && ls -lh models/

ENV SLM_MODEL_PATH=/app/models/Qwen3-1.7B-Q4_K_M.gguf \
    SLM_N_THREADS=4 \
    SLM_N_CTX=4096 \
    CACHE_DIR=/app/.cache

RUN mkdir -p /app/.cache

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:7860/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
