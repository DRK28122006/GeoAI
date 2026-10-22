#!/usr/bin/env bash
# Publish the model to the Hugging Face Hub.
#
# The weights are the base model's, unchanged. The fastest way to get a
# repository that loads with from_pretrained is to duplicate the base model
# on the Hub, then upload our card, our prompt contract and the GGUF we
# serve:
#
#   1. Open https://huggingface.co/Qwen/Qwen3-1.7B in a browser and click
#      "Duplicate". Name it <your-org>/geoai-outdoor-assistant. That copies
#      the weights, the config and the tokenizer into your namespace, so
#      from_pretrained works on it straight away.
#   2. Run this script with your token:
#
#      HF_TOKEN=hf_xxx ./hf_repo/upload.sh <your-org>/geoai-outdoor-assistant
#
# The script checks the repository before and after the upload, so it fails
# loudly rather than leaving a repository that does not load.
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

if command -v huggingface-cli >/dev/null 2>&1; then
    CLI="$(command -v huggingface-cli)"
elif command -v hf >/dev/null 2>&1; then
    CLI="$(command -v hf)"
else
    echo "install the client: pip install -U 'huggingface_hub[cli]'" >&2
    exit 1
fi

# ---------------------------------------------------------------- pre-flight
# from_pretrained needs config.json and the tokenizer. If the repository is
# not a copy of the base model yet, the upload would leave something that
# cannot be loaded, so we stop here instead.
echo "checking $REPO"
FILES="$("$CLI" api "$REPO" --json 2>/dev/null || true)"
if ! printf '%s' "$FILES" | grep -q '"config.json"'; then
    echo "the repository has no config.json, so from_pretrained would not work."
    echo "duplicate https://huggingface.co/Qwen/Qwen3-1.7B first, then run this again."
    exit 1
fi
if ! printf '%s' "$FILES" | grep -q 'tokenizer'; then
    echo "the repository has no tokenizer files."
    echo "duplicate https://huggingface.co/Qwen/Qwen3-1.7B first, then run this again."
    exit 1
fi
if ! printf '%s' "$FILES" | grep -q 'safetensors'; then
    echo "the repository has no safetensors weights."
    echo "duplicate https://huggingface.co/Qwen/Qwen3-1.7B first, then run this again."
    exit 1
fi
echo "  the repository looks like a copy of the base model, good"

# ------------------------------------------------------------------- upload
echo "uploading the model card"
"$CLI" upload "$REPO" "$HERE/README.md" --repo-type model \
    --commit-message "model card for the GeoAI outdoor assistant"

echo "uploading the prompt contract and the schemas"
for file in serving_config.json prompts.md intent.schema.json choice.schema.json; do
    "$CLI" upload "$REPO" "$HERE/$file" --repo-type model \
        --commit-message "$file"
done

GGUF="$ROOT/models/Qwen3-1.7B-Q4_K_M.gguf"
if [ -f "$GGUF" ]; then
    echo "uploading the GGUF build we serve"
    "$CLI" upload "$REPO" "$GGUF" --repo-type model \
        --commit-message "Qwen3-1.7B Q4_K_M GGUF used by the llama.cpp server"
else
    echo "note: $GGUF is not here, so the GGUF was not uploaded."
fi

# -------------------------------------------------------------- post-flight
echo
echo "checking the result"
FILES="$("$CLI" api "$REPO" --json 2>/dev/null || true)"
missing=0
for want in README.md config.json tokenizer.json intent.schema.json choice.schema.json; do
    if printf '%s' "$FILES" | grep -q "$want"; then
        echo "  ok      $want"
    else
        echo "  MISSING $want"
        missing=1
    fi
done
if printf '%s' "$FILES" | grep -q 'safetensors'; then
    echo "  ok      the weights are there, from_pretrained will work"
else
    echo "  MISSING the weights, from_pretrained will NOT work"
    missing=1
fi

if [ "$missing" -ne 0 ]; then
    echo
    echo "the repository is not complete yet." >&2
    exit 1
fi

echo
echo "done. set the licence to apache-2.0 in the repository settings if the"
echo "duplicate did not carry it over."
echo "load it with:"
echo "  from transformers import AutoModelForCausalLM"
echo "  model = AutoModelForCausalLM.from_pretrained(\"$REPO\")"
