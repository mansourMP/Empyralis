#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY="${RUNTIME_KEY:-${ORION_API_KEY:-}}"

ACCOUNT_SID="${ACCOUNT_SID:-}"
TO_NUMBER="${TO_NUMBER:-}"      # Twilio WhatsApp sender (connector from_number), e.g. whatsapp:+14155238886
FROM_NUMBER="${FROM_NUMBER:-}"  # End-user sender, e.g. whatsapp:+15551234567
MESSAGE_TEXT="${MESSAGE_TEXT:-/empyralis status}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-${ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET:-}}"

if [[ -z "${RUNTIME_KEY}" && -f "${RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(tr -d '\r\n' < "${RUNTIME_KEY_FILE}" 2>/dev/null || true)"
fi

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Missing runtime API key."
  echo "Set RUNTIME_KEY/ORION_API_KEY or start stack first."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required."
  exit 1
fi

if [[ -z "${ACCOUNT_SID}" ]]; then
  read -r -p "Twilio Account SID: " ACCOUNT_SID
fi
if [[ -z "${TO_NUMBER}" ]]; then
  read -r -p "Twilio WhatsApp To number (connector from_number, e.g. whatsapp:+14155238886): " TO_NUMBER
fi
if [[ -z "${FROM_NUMBER}" ]]; then
  read -r -p "Inbound From number (your sender, e.g. whatsapp:+15551234567): " FROM_NUMBER
fi

if [[ -z "${ACCOUNT_SID}" || -z "${TO_NUMBER}" || -z "${FROM_NUMBER}" ]]; then
  echo "Account SID, To number, and From number are required."
  exit 1
fi

if [[ "${TO_NUMBER}" != whatsapp:* || "${FROM_NUMBER}" != whatsapp:* ]]; then
  echo "Both TO_NUMBER and FROM_NUMBER must start with 'whatsapp:'."
  exit 1
fi

STATUS_BEFORE="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/channels/whatsapp/autopilot/status" 2>/dev/null || echo '{}')"
processed_before="$(echo "${STATUS_BEFORE}" | jq -r '.autopilot.processed_messages // 0' 2>/dev/null || echo "0")"
runs_before="$(echo "${STATUS_BEFORE}" | jq -r '.autopilot.runs_started // 0' 2>/dev/null || echo "0")"
active_before="$(echo "${STATUS_BEFORE}" | jq -r '.autopilot.active // false' 2>/dev/null || echo "false")"

message_sid="WA-PROBE-$(date +%s)"
webhook_url="${API_URL%/}/channels/whatsapp/twilio/webhook"
if [[ -n "${WEBHOOK_SECRET}" ]]; then
  webhook_url="${webhook_url}?secret=${WEBHOOK_SECRET}"
fi

tmp_body="$(mktemp)"
http_code="$(
  curl -sS -o "${tmp_body}" -w "%{http_code}" \
    -X POST "${webhook_url}" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "AccountSid=${ACCOUNT_SID}" \
    --data-urlencode "MessageSid=${message_sid}" \
    --data-urlencode "From=${FROM_NUMBER}" \
    --data-urlencode "To=${TO_NUMBER}" \
    --data-urlencode "Body=${MESSAGE_TEXT}"
)"
body="$(cat "${tmp_body}")"
rm -f "${tmp_body}"

STATUS_AFTER="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/channels/whatsapp/autopilot/status" 2>/dev/null || echo '{}')"
processed_after="$(echo "${STATUS_AFTER}" | jq -r '.autopilot.processed_messages // 0' 2>/dev/null || echo "0")"
runs_after="$(echo "${STATUS_AFTER}" | jq -r '.autopilot.runs_started // 0' 2>/dev/null || echo "0")"
active_after="$(echo "${STATUS_AFTER}" | jq -r '.autopilot.active // false' 2>/dev/null || echo "false")"
last_error_after="$(echo "${STATUS_AFTER}" | jq -r '.autopilot.last_error // ""' 2>/dev/null || echo "")"

echo "WhatsApp webhook probe result"
echo "http_code: ${http_code}"
echo "active: ${active_before} -> ${active_after}"
echo "processed_messages: ${processed_before} -> ${processed_after}"
echo "runs_started: ${runs_before} -> ${runs_after}"
if [[ -n "${last_error_after}" ]]; then
  echo "last_error: ${last_error_after}"
fi
echo
echo "webhook_response:"
printf '%s\n' "${body}"

if [[ "${http_code}" != "200" ]]; then
  exit 2
fi

if [[ "${processed_after}" -le "${processed_before}" ]]; then
  echo "Probe reached endpoint but was not processed (check connector AccountSid/from/to mapping)."
  exit 3
fi

echo
echo "Probe OK."
