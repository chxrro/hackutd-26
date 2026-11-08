#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'Run this on a Linux x86_64 NVIDIA GPU instance.' >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
if ! docker info >/dev/null 2>&1; then
  echo 'Docker is not accessible. Check the Brev VM Docker daemon and user permissions.' >&2
  exit 1
fi
docker compose version
docker compose config --quiet
mkdir -p cache/huggingface
docker compose pull --quiet
docker compose up -d
printf '%s\n' 'Model loading has started. Check progress with: docker compose logs -f --tail=50'
printf '%s\n' 'Readiness: curl --fail http://127.0.0.1:8000/health'
printf '%s\n' 'Stopping Docker alone does not stop GPU billing. Stop the Brev instance after use.'
