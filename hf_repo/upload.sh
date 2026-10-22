#!/usr/bin/env bash
# Publish the model to the Hugging Face Hub.
#
# The weights are the base model's, unchanged. The fastest way to get a
# repository that loads with from_pretrained is to duplicate the base model
# on the Hub, then upload our card, our prompt contract and the GGUF we
# serve:
#
#   1. Open https://huggingface.co/Qwen/Qwen3-1.7B and click "Duplicate".
#      Name it <your-org>/geoai-outdoor-assistant. That copies the weights,
#      the config and the tokenizer into your namespace, so from_pretrained
#      works on it straight away.
#   2. Create a write token: Settings -> Access Tokens -> New token, role
#      Write.
#   3. Run this script with the token:
#
#      HF_TOKEN=hf_xxx ./hf_repo/upload.sh <your-org>/geoai-outdoor-assistant
#
# The script checks the repository before and after the upload, so it fails
# loudly rather than leaving a repository that cannot be loaded.
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
# client renames its subcommands between versions ("huggingface-cli api" does
# not exist in the current one), so the API is the stable path.
hf_python() {
    "$PY" - "$@" <<'PY'
import sys
from huggingface_hub import HfApi

repo = sys.argv[1]
api = HfApi(token=None)
files = [f.rfilename for f in api.list_repo_files(repo)]
for name in files:
    print(name)
PY
}

echo "checking $REPO"
FILES="$(hf_python "$REPO" || true)"
if [ -z "$FILES" ]; then
    echo "the repository does not exist or the token cannot see it." >&2
    echo "duplicate https://huggingface.co/Qwen/Qwen3-1.7B first, then run this again." >&2
    exit 1
fi

missing=""
for want in config.json tokenizer.json; do
    if printf '%s\n' "$FILES" | grep -qx "$want"; then
        echo "  ok      $want"
    else
        echo "  MISSING $want"
        missing="yes"
    fi
done
if printf '%s\n' "$FILES" | grep -q "safetensors"; then
    echo "  ok      the weights are there, from_pretrained will work"
else
    echo "  MISSING the weights, from_pretrained will NOT work"
    missing="yes"
fi
if [ -n "$missing" ]; then
    echo
    echo "the repository is not a copy of the base model yet." >&2
    echo "duplicate https://huggingface.co/Qwen/Qwen3-1.7B first, then run this again." >&2
    exit 1
fi

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
    echo "note: $GGUF is not here, so the GGUF was not uploaded."
fi

# -------------------------------------------------------------- post-flight
echo
echo "checking the result"
FILES="$(hf_python "$REPO" || true)"
missing=""
for want in README.md config.json tokenizer.json prompts.md intent.schema.json choice.schema.json serving_config.json; do
    if printf '%s\n' "$FILES" | grep -qx "$want"; then
        echo "  ok      $want"
    else
        echo "  MISSING $want"
        missing="yes"
    fi
done
if printf '%s\n' "$FILES" | grep -q "safetensors"; then
    echo "  ok      the weights are there, from_pretrained will work"
else
    echo "  MISSING the weights, from_pretrained will NOT work"
    missing="yes"
fi

if [ -n "$missing" ]; then
    echo
    echo "the repository is not complete yet." >&2
    exit 1
fi

echo
echo "done. make sure the licence says apache-2.0 in the repository settings."
echo "load it with:"
echo "  from transformers import AutoModelForCausalLM"
echo "  model = AutoModelForCausalLM.from_pretrained(\"$REPO\")"
