#!/usr/bin/env bash
# Serve Nemotron 3 Nano Omni (vision) locally with llama.cpp on a Mac, for
# `haptic-hub run` person/object labels. See hub/README.md "Serving Nemotron locally".
#
#   brew install llama.cpp
#   hub/scripts/serve-nemotron-local.sh --download   # once, ~23 GB
#   hub/scripts/serve-nemotron-local.sh              # then leave it running
set -euo pipefail

DIR="${NEMOTRON_DIR:-$HOME/models/nemotron-3-nano-omni}"
QUANT="${NEMOTRON_QUANT:-MXFP4_MOE}"
PORT="${NEMOTRON_PORT:-8080}"
REPO=https://huggingface.co/unsloth/NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-GGUF/resolve/main
MODEL="$DIR/model-$QUANT.gguf"
MMPROJ="$DIR/mmproj-F16.gguf"

fetch() {  # resumes after a dropped connection; HF downloads often stall
  local attempt
  for attempt in $(seq 1 30); do
    curl -fL -C - --speed-limit 100000 --speed-time 60 -o "$1" "$2" && return 0
    echo "download interrupted (attempt $attempt), resuming..." >&2
  done
  return 1
}

if [[ "${1:-}" == "--download" ]]; then
  mkdir -p "$DIR"
  fetch "$MMPROJ" "$REPO/mmproj-F16.gguf"
  fetch "$MODEL" "$REPO/NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-$QUANT.gguf"
  exit 0
fi

for f in "$MODEL" "$MMPROJ"; do
  [[ -f "$f" ]] || { echo "missing $f; run: $0 --download" >&2; exit 1; }
done

# --jinja: the hub turns thinking off with chat_template_kwargs, which needs the
# model's own chat template. 127.0.0.1 only; nothing else should reach it.
exec llama-server -m "$MODEL" --mmproj "$MMPROJ" \
  --alias nemotron-3-nano-omni --jinja \
  --host 127.0.0.1 --port "$PORT" -ngl 99 -c 8192 --parallel 1
