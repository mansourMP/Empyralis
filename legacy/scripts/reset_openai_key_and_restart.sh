#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
KEY_FILE="${STATE_DIR}/openai_api_key"
RUNTIME_KEY_VALUE="${RUNTIME_KEY:-replace-with-strong-key}"

mkdir -p "${STATE_DIR}"

read -r -s -p "Paste OpenAI API key (input hidden): " OPENAI_API_KEY_INPUT
echo
OPENAI_API_KEY_VALUE="$(printf "%s" "${OPENAI_API_KEY_INPUT}" | tr -d '[:space:]')"

if [[ -z "${OPENAI_API_KEY_VALUE}" ]]; then
  echo "Error: empty API key."
  exit 1
fi

echo "Validating key with OpenAI /v1/models ..."
TMP_JSON="$(mktemp)"
HTTP_CODE="$(
  curl -sS -o "${TMP_JSON}" -w "%{http_code}" \
    -H "Authorization: Bearer ${OPENAI_API_KEY_VALUE}" \
    -H "Content-Type: application/json" \
    https://api.openai.com/v1/models
)"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "Key validation failed (HTTP ${HTTP_CODE})."
  if command -v jq >/dev/null 2>&1; then
    jq -r '.error.message // "Unknown error."' "${TMP_JSON}" || true
  else
    cat "${TMP_JSON}" || true
  fi
  rm -f "${TMP_JSON}"
  exit 1
fi
rm -f "${TMP_JSON}"

printf "%s" "${OPENAI_API_KEY_VALUE}" > "${KEY_FILE}"
chmod 600 "${KEY_FILE}" 2>/dev/null || true
echo "Saved key to ${KEY_FILE}"

cd "${ROOT_DIR}"
if [[ -x "scripts/stop_empyralis_local_stack.sh" ]]; then
  bash scripts/stop_empyralis_local_stack.sh || true
else
  bash scripts/stop_orion_local_stack.sh || true
fi
START_STACK_SCRIPT="scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_STACK_SCRIPT}" ]]; then
  START_STACK_SCRIPT="scripts/start_orion_local_stack.sh"
fi
OPENAI_API_KEY="${OPENAI_API_KEY_VALUE}" \
ORION_AUTH_MODE=api_key \
ORION_DISABLE_OPENAI_API_KEY=0 \
RUNTIME_KEY="${RUNTIME_KEY_VALUE}" \
bash "${START_STACK_SCRIPT}"

echo
echo "Done. Quick checks:"
echo "  curl -s -H \"X-API-Key: ${RUNTIME_KEY_VALUE}\" http://127.0.0.1:8001/health"
echo "  ./bin/empyralis autopilot watch-line 2"
