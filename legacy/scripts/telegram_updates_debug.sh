#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

LIMIT="${1:-10}"
TIMEOUT="${2:-0}"

STATE_HOME="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
VAULT_FILE="${CREDENTIAL_VAULT_FILE:-}"
VAULT_KEY_FILE="${CREDENTIAL_VAULT_KEY_FILE:-}"
if [[ -z "${VAULT_FILE}" ]]; then
  VAULT_FILE="${STATE_HOME}/vault/credentials.json"
fi
if [[ -z "${VAULT_KEY_FILE}" ]]; then
  VAULT_KEY_FILE="${STATE_HOME}/vault/key"
fi

if [[ ! -f "${VAULT_FILE}" ]]; then
  echo "Missing vault file: ${VAULT_FILE}"
  exit 1
fi
if [[ ! -f "${VAULT_KEY_FILE}" ]]; then
  echo "Missing vault key file: ${VAULT_KEY_FILE}"
  exit 1
fi

ENC="$(jq -r '[.credentials[] | select((.provider|ascii_downcase)=="telegram_bot")][0].encrypted_secret // empty' "${VAULT_FILE}")"
if [[ -z "${ENC}" || "${ENC}" == "null" ]]; then
  echo "No telegram_bot connector found in vault."
  exit 1
fi

PASS="$(cat "${VAULT_KEY_FILE}")"
PLAIN="$(printf '%s' "${ENC}" | openssl enc -d -aes-256-cbc -pbkdf2 -salt -a -pass pass:"${PASS}")"
TOKEN="$(printf '%s' "${PLAIN}" | jq -r '.bot_token // empty')"
CHAT_ID_CFG="$(printf '%s' "${PLAIN}" | jq -r '.chat_id // empty')"

if [[ -z "${TOKEN}" ]]; then
  echo "Could not read bot_token from vault payload."
  exit 1
fi

ME_RESP="$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe")"
WEBHOOK_RESP="$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getWebhookInfo")"
RESP="$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getUpdates?timeout=${TIMEOUT}&limit=${LIMIT}")"
echo "${RESP}" | jq --argjson me "${ME_RESP}" --argjson webhook "${WEBHOOK_RESP}" --arg chat_id_cfg "${CHAT_ID_CFG}" '{
  bot: {
    id: ($me.result.id // null),
    username: ($me.result.username // null),
    first_name: ($me.result.first_name // null)
  },
  configured_chat_id: (if ($chat_id_cfg | length) > 0 then $chat_id_cfg else null end),
  webhook: {
    url: ($webhook.result.url // null),
    has_custom_certificate: ($webhook.result.has_custom_certificate // null),
    pending_update_count: ($webhook.result.pending_update_count // null),
    last_error_date: ($webhook.result.last_error_date // null),
    last_error_message: ($webhook.result.last_error_message // null)
  },
  ok,
  error_code,
  description,
  count: ((.result // []) | length),
  latest: (
    ((.result // [])[ -1 ]? // {}) |
    {
      update_id,
      text: (.message.text // .edited_message.text // .channel_post.text // .business_message.text // ""),
      chat_id: (.message.chat.id // .edited_message.chat.id // .channel_post.chat.id // .business_message.chat.id // null),
      from_id: (.message.from.id // .edited_message.from.id // .channel_post.from.id // .business_message.from.id // null),
      from_is_bot: (.message.from.is_bot // .edited_message.from.is_bot // .channel_post.from.is_bot // .business_message.from.is_bot // null)
    }
  )
}'
