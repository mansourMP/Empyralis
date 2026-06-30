#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"

normalize_runtime_key() {
  printf "%s" "$1" | tr -d '[:space:]'
}

RUNTIME_KEY_RAW="${1:-${RUNTIME_KEY:-${ORION_API_KEY:-}}}"
RUNTIME_KEY_VALUE="$(normalize_runtime_key "${RUNTIME_KEY_RAW}")"
if [[ -z "${RUNTIME_KEY_VALUE}" && -f "${STACK_KEY_FILE}" ]]; then
  RUNTIME_KEY_VALUE="$(normalize_runtime_key "$(cat "${STACK_KEY_FILE}" 2>/dev/null || true)")"
fi

cd "${ROOT_DIR}"
if [[ -n "${RUNTIME_KEY_VALUE}" ]]; then
  ORION_OPENCLAW_GATEWAY_POLICY="${ORION_OPENCLAW_GATEWAY_POLICY:-auto_stop}" \
  RUNTIME_KEY="${RUNTIME_KEY_VALUE}" \
  bash scripts/start_orion_local_stack.sh
else
  ORION_OPENCLAW_GATEWAY_POLICY="${ORION_OPENCLAW_GATEWAY_POLICY:-auto_stop}" \
  bash scripts/start_orion_local_stack.sh
fi
