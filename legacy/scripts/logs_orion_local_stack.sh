#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/.orion-stack/logs"

LINES="${1:-80}"
if ! [[ "${LINES}" =~ ^[0-9]+$ ]]; then
  echo "Usage: bash scripts/logs_empyralis_local_stack.sh [lines]"
  echo "Example: bash scripts/logs_empyralis_local_stack.sh 120"
  exit 1
fi

mkdir -p "${LOG_DIR}"

RUNTIME_LOG="${LOG_DIR}/runtime.log"
BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"
WORKER_LOG="${LOG_DIR}/worker.log"

touch "${RUNTIME_LOG}" "${BACKEND_LOG}" "${FRONTEND_LOG}" "${WORKER_LOG}"

echo "Streaming Empyralis stack logs (last ${LINES} lines each). Press Ctrl+C to stop."
echo
tail -n "${LINES}" -F "${RUNTIME_LOG}" "${BACKEND_LOG}" "${FRONTEND_LOG}" "${WORKER_LOG}"
