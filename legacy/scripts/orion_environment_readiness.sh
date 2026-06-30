#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${EMPYRALIS_API_URL:-${ORION_API_URL:-http://127.0.0.1:8001}}"
RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY="${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-}}}"
WORKSPACE_ID="${WORKSPACE_ID:-default}"

normalize_runtime_key() {
  printf "%s" "${1:-}" | tr -d '[:space:]'
}

if [[ -z "${RUNTIME_KEY}" && -f "${RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(tr -d '\r\n' < "${RUNTIME_KEY_FILE}" 2>/dev/null || true)"
fi
RUNTIME_KEY="$(normalize_runtime_key "${RUNTIME_KEY}")"

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Missing runtime API key."
  echo "Set RUNTIME_KEY/EMPYRALIS_API_KEY/ORION_API_KEY or start stack first."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required."
  exit 1
fi

score=0
max_score=10
checks=()

mark() {
  local ok="$1"
  local pts="$2"
  local label="$3"
  if [[ "${ok}" == "1" ]]; then
    score=$((score + pts))
    checks+=("[ok +${pts}] ${label}")
  else
    checks+=("[-- +0] ${label}")
  fi
}

HEALTH_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" 2>/dev/null || echo '{}')"
WORKER_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/runtime/runtimes/status" 2>/dev/null || echo '{}')"
CONNECTORS_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/connectors/vault?workspace_id=${WORKSPACE_ID}" 2>/dev/null || echo '{"items":[]}' )"
TG_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/channels/telegram/autopilot/status" 2>/dev/null || echo '{}')"
WA_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/channels/whatsapp/autopilot/status" 2>/dev/null || echo '{}')"

runtime_ok="$(echo "${HEALTH_JSON}" | jq -r 'if .ok == true then "1" else "0" end' 2>/dev/null || echo "0")"
worker_ok="$(echo "${WORKER_JSON}" | jq -r '
  if (.ok == true and ((.workers // []) | length) > 0) then "1"
  elif (((.summary.online // 0) | tonumber) > 0) then "1"
  elif (((.items // []) | map(select(.online == true)) | length) > 0) then "1"
  else "0" end
' 2>/dev/null || echo "0")"
auth_ok="$(echo "${HEALTH_JSON}" | jq -r 'if .openai_key_valid == true then "1" else "0" end' 2>/dev/null || echo "0")"
tg_connector_ok="$(echo "${CONNECTORS_JSON}" | jq -r 'if ((.items // []) | map(select(.connector=="telegram_bot")) | length) > 0 then "1" else "0" end' 2>/dev/null || echo "0")"
email_connector_ok="$(echo "${CONNECTORS_JSON}" | jq -r 'if ((.items // []) | map(select(.connector=="google_workspace")) | length) > 0 then "1" else "0" end' 2>/dev/null || echo "0")"
wa_connector_ok="$(echo "${CONNECTORS_JSON}" | jq -r 'if ((.items // []) | map(select(.connector=="whatsapp_twilio")) | length) > 0 then "1" else "0" end' 2>/dev/null || echo "0")"
tg_active_ok="$(echo "${TG_JSON}" | jq -r 'if .ok == true and .autopilot.active == true then "1" else "0" end' 2>/dev/null || echo "0")"
wa_active_ok="$(echo "${WA_JSON}" | jq -r 'if .ok == true and .autopilot.active == true then "1" else "0" end' 2>/dev/null || echo "0")"

mark "${runtime_ok}" 2 "Runtime is reachable"
mark "${worker_ok}" 1 "Local worker is connected"
mark "${auth_ok}" 2 "AI auth is valid"
mark "${tg_connector_ok}" 1 "Telegram connector exists"
mark "${email_connector_ok}" 2 "Email connector (Google Workspace) exists"
mark "${wa_connector_ok}" 1 "WhatsApp connector exists"
mark "${tg_active_ok}" 1 "Telegram autopilot active"
mark "${wa_active_ok}" 0 "WhatsApp autopilot active (optional)"

percent=$(( score * 100 / max_score ))

echo "Empyralis Environment Readiness: ${score}/${max_score} (${percent}%)"
printf '%s\n' "${checks[@]}"

if [[ "${email_connector_ok}" != "1" ]]; then
  echo
  echo "Missing email setup. Run:"
  echo "  bash scripts/setup_google_workspace_connector.sh"
fi

if [[ "${wa_connector_ok}" != "1" ]]; then
  echo
  echo "Missing WhatsApp setup. Run:"
  echo "  bash scripts/setup_whatsapp_connector.sh"
  echo "Non-interactive:"
  echo "  TWILIO_ACCOUNT_SID=AC... TWILIO_AUTH_TOKEN=... TWILIO_WHATSAPP_FROM='whatsapp:+14155238886' NON_INTERACTIVE=1 bash scripts/setup_whatsapp_connector.sh"
fi
