#!/usr/bin/env bash
# Publish the model to the Hugging Face Hub.
#
# The repository is GGUF-only: it ships the Q4_K_M build the service loads,
# the model card, the prompt contract, the JSON schemas and the serving
# configuration. The unquantized weights stay in Qwen/Qwen3-1.7B, so nothing
# is duplicated here.
#
#   1. Create a write token: Settings -> Access Tokens -> New token, role
#      Write.
#   2. Run:
#
#      HF_TOKEN=hf_xxx ./hf_repo/upload.sh <your-org>/geoai-outdoor-assistant
#
# The script creates the repository if it does not exist, uploads the files,
# and checks the result. It fails loudly rather than leaving a repository
# that cannot be loaded.
set -euo pipefail

REPO="${1:-}"
if [ -z "$REPO" ]; then
    echo "usage: $0 <your-org>/geoai-outdoor-assistant" >&2
    exit 1
fi
if [ -z "${HF_TOKEN:-}" ]; then
    echo "HF_TOKEN is not set" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

# The hub client lives in the project environment, not necessarily in the
# system one.
if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="python3"
fi
if ! "$PY" -c "import huggingface_hub" 2>/dev/null; then
    echo "install the client: pip install -U 'huggingface_hub[cli]'" >&2
    exit 1
fi

# The checks and the upload both go through the Python API. The command line
# client renames its subcommands between versions, so the API is the stable
# path.
hf_python() {
    "$PY" - "$@" <<'PY'
import sys
from huggingface_hub import HfApi

repo = sys.argv[1]
api = HfApi(token=None)
for name in api.list_repo_files(repo):
    print(name)
PY
}

# ------------------------------------------------------------------- create
echo "creating $REPO if needed"
"$PY" - "$REPO" <<'PY'
import sys
from huggingface_hub import HfApi

repo = sys.argv[1]
api = HfApi()
api.create_repo(repo_id=repo, repo_type="model", private=False, exist_ok=True)
print("  ready")
PY

# ------------------------------------------------------------------- upload
echo
echo "uploading the model card"
"$PY" - "$REPO" "$HERE/README.md" <<'PY'
import sys
from huggingface_hub import HfApi

repo, path = sys.argv[1], sys.argv[2]
HfApi().upload_file(
    path_or_fileobj=path,
    path_in_repo="README.md",
    repo_id=repo,
    repo_type="model",
    commit_message="model card for the GeoAI outdoor assistant",
)
print("  README.md")
PY

echo "uploading the prompt contract and the schemas"
for file in serving_config.json prompts.md intent.schema.json choice.schema.json; do
    "$PY" - "$REPO" "$HERE/$file" "$file" <<'PY'
import sys
from huggingface_hub import HfApi

repo, path, name = sys.argv[1], sys.argv[2], sys.argv[3]
HfApi().upload_file(
    path_or_fileobj=path,
    path_in_repo=name,
    repo_id=repo,
    repo_type="model",
    commit_message=name,
)
print(f"  {name}")
PY
done

GGUF="$ROOT/models/Qwen3-1.7B-Q4_K_M.gguf"
if [ -f "$GGUF" ]; then
    echo "uploading the GGUF build we serve (1.1 GB, this takes a few minutes)"
    "$PY" - "$REPO" "$GGUF" <<'PY'
import sys
from huggingface_hub import HfApi

repo, path = sys.argv[1], sys.argv[2]
HfApi().upload_file(
    path_or_fileobj=path,
    path_in_repo="Qwen3-1.7B-Q4_K_M.gguf",
    repo_id=repo,
    repo_type="model",
    commit_message="Qwen3-1.7B Q4_K_M GGUF used by the llama.cpp server",
)
print("  Qwen3-1.7B-Q4_K_M.gguf")
PY
else
    echo "note: $GGUF is not here, so the GGUF was not uploaded." >&2
fi

# -------------------------------------------------------------- post-flight
echo
echo "checking the result"
FILES="$(hf_python "$REPO" || true)"
missing=""
for want in README.md prompts.md intent.schema.json choice.schema.json serving_config.json Qwen3-1.7B-Q4_K_M.gguf; do
    if printf '%s\n' "$FILES" | grep -qx "$want"; then
        echo "  ok      $want"
    else
        echo "  MISSING $want"
        missing="yes"
    fi
done

if [ -n "$missing" ]; then
    echo
    echo "the repository is not complete yet." >&2
    exit 1
fi

echo
echo "done. make sure the licence says apache-2.0 in the repository settings."
echo "load it with:"
echo "  from llama_cpp import Llama"
echo "  llm = Llama.from_pretrained(\"$REPO\", filename=\"Qwen3-1.7B-Q4_K_M.gguf\")"
