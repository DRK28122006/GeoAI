# syntax=docker/dockerfile:1
#
# GeoAI Outdoor Assistant - production image.
#
# Two stages, so the compilers and the sources do not ship with the service.
# The model weights are baked in, so the container serves answers without
# needing the network to boot.

# --------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_RETRIES=8 \
    PIP_TIMEOUT=120 \
    VIRTUAL_ENV=/opt/venv

RUN for i in 1 2 3 4 5; do apt-get update && break; echo "apt-get update failed, retrying"; sleep 5; done \
    && apt-get install -y --no-install-recommends --fix-missing \
        build-essential \
        cmake \
        ninja-build \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./

# The llama-cpp-python wheel published for linux is a musl (Alpine) build and
# cannot be loaded on glibc, so it is compiled here. GGML_NATIVE is off on
# purpose: an image compiled for the build machine's CPU dies with an
# illegal instruction on any other one. These flags are the common baseline.
RUN pip install --retries 5 --timeout 120 --upgrade pip setuptools wheel \
    && pip install --retries 5 --timeout 120 cmake ninja \
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_F16C=ON \
                   -DGGML_FMA=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
                   -DLLAMA_BUILD_EXAMPLES=OFF" \
       pip install --retries 5 --timeout 120 --no-binary llama-cpp-python "llama-cpp-python==0.3.19" \
    && pip install --retries 5 --timeout 120 fastapi "uvicorn[standard]" httpx "pydantic>=2.0" \
       pydantic-settings diskcache PyYAML astral pyrosm

# The application itself, installed without touching the dependencies we
# just pinned above. The config files travel with it as package data.
COPY app/ ./app/
RUN pip install --retries 5 --timeout 120 --no-deps .

# --------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    SLM_MODEL_PATH=/app/models/Qwen3-1.7B-Q4_K_M.gguf \
    SLM_N_THREADS=4 \
    SLM_N_CTX=4096 \
    SLM_EAGER_LOAD=true \
    CACHE_DIR=/app/.cache \
    LOG_LEVEL=INFO \
    MAX_IN_FLIGHT=2 \
    REQUEST_DEADLINE_S=90

# curl is needed by the health check, ca-certificates by the outbound calls
# to the live data sources.
RUN for i in 1 2 3 4 5; do apt-get update && break; echo "apt-get update failed, retrying"; sleep 5; done \
    && apt-get install -y --no-install-recommends --fix-missing \
        curl \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app/ ./app/

# The model weights (1.1 GB). Baking them in means the container starts
# serving immediately and needs no network to boot.
RUN mkdir -p /app/models /app/.cache \
    && curl -L --fail --retry 3 --retry-delay 5 \
       -o /app/models/Qwen3-1.7B-Q4_K_M.gguf \
       "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf" \
    && ls -lh /app/models/ \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 7860

# Liveness, not readiness: the container stays healthy even without the
# model, because the assistant can still answer with the rules and the
# templates. Readiness is at /ready.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7860/live || exit 1

# One worker: the model runs in-process and is guarded by a lock, so more
# workers would only multiply the memory and serialise the model anyway.
# uvicorn handles SIGTERM, waits for the in-flight requests and then runs
# the shutdown, where the model is released.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "1", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "45", \
     "--log-level", "info"]
