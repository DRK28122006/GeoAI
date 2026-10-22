# syntax=docker/dockerfile:1
#
# GeoAI Outdoor Assistant - production image.
#
# Two stages so the compilers and the sources do not ship with the service.
# The model weights are baked in, so the container serves answers without
# needing the network to boot, and /health can report the model as ready.

# --------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# The llama-cpp-python wheel published for linux is a musl (Alpine) build and
# cannot be loaded on glibc, so it is compiled here. GGML_NATIVE is off on
# purpose: an image compiled for the build machine's CPU dies with an
# illegal instruction on any other one. These flags are the common baseline.
RUN pip install --upgrade pip setuptools wheel \
    && pip install cmake ninja \
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_F16C=ON \
                   -DGGML_FMA=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
                   -DLLAMA_BUILD_EXAMPLES=OFF" \
       pip install --no-binary llama-cpp-python "llama-cpp-python==0.3.19"

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
    MAX_IN_FLIGHT=4

# curl is needed by the health check, ca-certificates by the outbound calls
# to the live data sources.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app/ ./app/
COPY pyproject.toml README.md ./

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

# /health answers 503 when the model is not loaded, so this fails correctly
# when the service is up but the assistant is not.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7860/health || exit 1

# One worker: the model runs in-process and is guarded by a lock, so more
# workers would only multiply the memory and serialise the model anyway.
# uvicorn handles SIGTERM and waits for the in-flight requests.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "1", \
     "--timeout-keep-alive", "75", \
     "--log-level", "info"]
