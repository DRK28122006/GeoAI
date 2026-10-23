#!/usr/bin/env bash
# Control the local llama.cpp model server (dev only).
# The shipped app uses the in-process llama-cpp-python binding; this server is
# handy for quick manual testing and for the frontend/backend folks.
set -euo pipefail

MODEL="${MODEL:-/home/mesh/Projects/GEOai/deepesh-repo/models/Qwen3-1.7B-Q4_K_M.gguf}"
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
CTX="${CTX:-4096}"
THREADS="${THREADS:-4}"

case "${1:-status}" in
  start)
    if curl -sf "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
      echo "already running on ${HOST}:${PORT}"; exit 0
    fi
    # GGML_VK_VISIBLE_DEVICES=-1 hides the iGPU: CPU/AVX-512 is faster here (see docs/RUNBOOK.md)
    GGML_VK_VISIBLE_DEVICES=-1 llama-server \
      -m "$MODEL" -c "$CTX" -t "$THREADS" \
      --host "$HOST" --port "$PORT" \
      --jinja --reasoning-budget 0 --metrics
    ;;
  systemd)
    systemctl --user restart llama-server
    systemctl --user status llama-server --no-pager
    ;;
  stop)
    systemctl --user stop llama-server 2>/dev/null || pkill -f "llama-server .*${MODEL}" || true
    echo "stopped"
    ;;
  status)
    systemctl --user status llama-server --no-pager || true
    curl -s "http://${HOST}:${PORT}/health" && echo
    ;;
  chat)
    shift || true
    curl -s "http://${HOST}:${PORT}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -d "$(python3 - "$@" <<'PY'
import json, sys
print(json.dumps({
    "messages": [{"role": "user", "content": " ".join(sys.argv[1:]) or "hi"}],
    "temperature": 0.3, "max_tokens": 300,
}))
PY
)" | python3 -m json.tool
    ;;
  *)
    echo "usage: $0 {start|systemd|stop|status|chat [prompt]}" >&2
    exit 1
    ;;
esac
