#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY="${RUNTIME_KEY:-${ORION_API_KEY:-}}"
WORKSPACE_ID="${WORKSPACE_ID:-default}"
LABEL="${LABEL:-Telegram Bot}"
BOT_TOKEN="${BOT_TOKEN:-}"
ALLOW_FROM="${ALLOW_FROM:-}"

if [[ -z "${RUNTIME_KEY}" && -f "${RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(tr -d '\r\n' < "${RUNTIME_KEY_FILE}" 2>/dev/null || true)"
fi

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Missing runtime API key."
  echo "Set RUNTIME_KEY or ORION_API_KEY, or start stack first."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required. Install jq and rerun."
  exit 1
fi

if [[ -z "${BOT_TOKEN}" ]]; then
  read -r -s -p "Telegram bot token: " BOT_TOKEN
  echo
fi

if [[ ! "${BOT_TOKEN}" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]]; then
  echo "Invalid token format. Expected '<digits>:<secret>'."
  exit 1
fi

echo "Validating bot token..."
GETME="$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || true)"
OK="$(echo "${GETME}" | jq -r '.ok // false')"
if [[ "${OK}" != "true" ]]; then
  echo "Telegram rejected token."
  echo "${GETME}" | jq
  exit 1
fi
USERNAME="$(echo "${GETME}" | jq -r '.result.username // "unknown"')"
echo "Bot OK: @${USERNAME}"

echo
echo "Send /start to your bot now: https://t.me/${USERNAME}"
read -r -p "Press Enter after sending /start..."

UPDATES="$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?limit=100" || true)"
CHAT_ID="$(echo "${UPDATES}" | jq -r 'if .ok then ([.result[] | (.message.chat.id // .edited_message.chat.id // empty)] | last // empty) else empty end')"

if [[ -z "${CHAT_ID}" ]]; then
  echo "No chat_id found yet."
  echo "Send one plain message to the bot, then rerun this script."
  exit 1
fi

echo "Detected chat_id: ${CHAT_ID}"

if [[ -z "${ALLOW_FROM}" ]]; then
  read -r -p "Allowed sender IDs/usernames (comma-separated, optional): " ALLOW_FROM || true
fi

PAYLOAD="$(jq -n \
  --arg label "${LABEL}" \
  --arg ws "${WORKSPACE_ID}" \
  --arg bt "${BOT_TOKEN}" \
  --arg cid "${CHAT_ID}" \
  --arg allow_from "${ALLOW_FROM}" \
  '
  def allow_from_items:
    ($allow_from
      | split(",")
      | map(gsub("^\\s+|\\s+$"; ""))
      | map(select(length > 0)));
  {
    label:$label,
    connector:"telegram_bot",
    workspace_id:$ws,
    credentials:{bot_token:$bt,chat_id:$cid},
    metadata:(if (allow_from_items | length) > 0 then {allow_from: allow_from_items} else {} end)
  }')"

echo "Saving connector to vault..."
CREATE_OUT="$(curl -fsS -X POST \
  -H "X-API-Key: ${RUNTIME_KEY}" \
  -H "Content-Type: application/json" \
  "${API_URL}/connectors/vault" \
  -d "${PAYLOAD}" || true)"

if echo "${CREATE_OUT}" | jq -e '.id? // empty' >/dev/null 2>&1; then
  echo "Connector created:"
  echo "${CREATE_OUT}" | jq '{id,label,connector,workspace_id,status}'
else
  echo "Connector create response:"
  echo "${CREATE_OUT}" | jq
  exit 1
fi

echo
echo "Autopilot status:"
curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/channels/telegram/autopilot/status" \
  | jq '{ok,autopilot:{enabled,active,thread_alive,processed_updates,runs_started,connectors_seen,last_error},connectors}'
