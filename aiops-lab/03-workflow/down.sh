#!/usr/bin/env bash
# Phase 3 정리. 컨테이너 + 네트워크 제거. -v 옵션 시 볼륨까지 삭제.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ "${1:-}" = "-v" ] || [ "${1:-}" = "--volumes" ]; then
  echo "[03-workflow/down] removing volumes (n8n_data / qdrant_data / ollama_data / redis_data)"
  docker compose down -v
else
  docker compose down
  echo "[03-workflow/down] containers removed. Volumes preserved (use './down.sh -v' to wipe)."
fi
