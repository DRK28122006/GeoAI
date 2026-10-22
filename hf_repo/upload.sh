#!/usr/bin/env bash
# Publish the model to the Hugging Face Hub.
#
# The weights are the base model's, unchanged. The fastest way to get a
# repository that loads with from_pretrained is to duplicate the base model
# on the Hub, then upload our card and the GGUF we serve:
#
#   1. On huggingface.co, open https://huggingface.co/Qwen/Qwen3-1.7B
#      and click "Duplicate". Name it <your-org>/geoai-outdoor-assistant.
#      That creates a copy of the weights, the config and the tokenizer in
#      your namespace, so from_pretrained works on it straight away.
#   2. Run this script with your token to upload the model card and the
#      GGUF build we serve in production.
#
# Usage:
#   HF_TOKEN=hf_xxx ./hf_repo/upload.sh <your-org>/geoai-outdoor-assistant
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

command -v huggingface-cli >/dev/null 2>&1 || command -v hf >/dev/null 2>&1 || {
    echo "install the huggingface hub client: pip install -U huggingface_hub" >&2
    exit 1
}

CLI="$(command -v huggingface-cli || command -v hf)"

echo "uploading the model card to $REPO"
"$CLI" upload "$REPO" "$HERE/README.md" --repo-type model \
    --commit-message "model card for the GeoAI outdoor assistant"

echo "uploading the GGUF build we serve"
"$CLI" upload "$REPO" "$ROOT/models/Qwen3-1.7B-Q4_K_M.gguf" --repo-type model \
    --commit-message "Qwen3-1.7B Q4_K_M GGUF used by the llama.cpp server"

echo
echo "done. check the model tab shows the card and the GGUF file."
echo "the repository should also have LICENSE set to apache-2.0 and the"
echo "base model's config, tokenizer and safetensors, which come from the"
echo "duplicate step."
