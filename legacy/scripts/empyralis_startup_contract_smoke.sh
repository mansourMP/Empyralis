#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
STOP_SCRIPT="${ROOT_DIR}/scripts/stop_empyralis_local_stack.sh"
STATE_DIR="${ROOT_DIR}/.orion-stack"
LOG_FILE="${STATE_DIR}/phase65_startup_smoke.log"

HOST_IP="${ORION_HOST:-$(
  ipconfig getifaddr en0 2>/dev/null \
    || ipconfig getifaddr en1 2>/dev/null \
    || echo "127.0.0.1"
)}"

cleanup() {
  bash "${STOP_SCRIPT}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "${STATE_DIR}"

echo "Running Phase 65A startup smoke against ${HOST_IP}" | tee "${LOG_FILE}"
bash "${STOP_SCRIPT}" >/dev/null 2>&1 || true

ORION_HOST="${HOST_IP}" \
ORION_BIND_HOST="0.0.0.0" \
FRONTEND_HOST="0.0.0.0" \
START_BACKEND="0" \
START_FRONTEND="1" \
START_WORKER="0" \
START_OPS_DAEMON="0" \
bash "${START_SCRIPT}" >>"${LOG_FILE}" 2>&1

curl -fsS "http://127.0.0.1:8001/health" >/dev/null
curl -fsS "http://${HOST_IP}:8001/health" >/dev/null
curl -fsS "http://127.0.0.1:3000" >/dev/null
curl -fsS "http://${HOST_IP}:3000" >/dev/null

echo "Phase 65A startup smoke passed." | tee -a "${LOG_FILE}"
