#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_STACK_SCRIPT}" ]]; then
  START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_orion_local_stack.sh"
fi
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
SOAK_SECONDS="${ORION_RELEASE_SOAK_SECONDS:-45}"

normalize_runtime_key() {
  printf "%s" "${1:-}" | tr -d '[:space:]'
}

RUNTIME_KEY_VALUE="$(normalize_runtime_key "${RUNTIME_KEY:-${ORION_API_KEY:-}}")"
if [[ -z "${RUNTIME_KEY_VALUE}" && -f "${STACK_RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY_VALUE="$(normalize_runtime_key "$(cat "${STACK_RUNTIME_KEY_FILE}" 2>/dev/null || true)")"
fi
if [[ -z "${RUNTIME_KEY_VALUE}" ]]; then
  RUNTIME_KEY_VALUE="replace-with-strong-key"
fi

if [[ $# -gt 0 ]]; then
  SOAK_SECONDS="$1"
fi
if ! [[ "${SOAK_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "Invalid soak seconds: ${SOAK_SECONDS}"
  exit 2
fi

ensure_runtime() {
  local http_code
  http_code="$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 4 -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}/health" 2>/dev/null || true)"
  if [[ "${http_code}" == "200" || "${http_code}" == "401" || "${http_code}" == "403" ]]; then
    return 0
  fi
  echo "Runtime offline, starting local stack..."
  RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash "${START_STACK_SCRIPT}"
}

echo "=== Empyralis Release Gate ==="

echo "[1/4] Brand token audit"
bash scripts/orion_brand_token_audit.sh

echo "[2/4] Telegram command routing tests"
python3 -m unittest scripts.orion_terminal.tests.test_telegram_autopilot_profile_commands

echo "[3/4] Release status (strict)"
ensure_runtime
RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/empyralis_release_status.sh --strict

echo "[4/4] Telegram autopilot soak (${SOAK_SECONDS}s)"
RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/orion_autopilot_soak.sh "${SOAK_SECONDS}" 3

echo "=== RELEASE GATE PASSED ==="
