#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUNTIME_KEY_RAW="${RUNTIME_KEY:-replace-with-strong-key}"
RUNTIME_KEY_VALUE="$(printf "%s" "${RUNTIME_KEY_RAW}" | tr -d '[:space:]')"
if [[ -z "${RUNTIME_KEY_VALUE}" ]]; then
  RUNTIME_KEY_VALUE="replace-with-strong-key"
fi
if [[ "${RUNTIME_KEY_VALUE}" != "${RUNTIME_KEY_RAW}" ]]; then
  echo "Warning: runtime key contained whitespace and was normalized."
fi

runtime_is_up() {
  curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "http://127.0.0.1:8001/health" >/dev/null 2>&1
}

RUNTIME_WAS_UP=0
if runtime_is_up; then
  RUNTIME_WAS_UP=1
fi
ALLOW_ANY_CHAT="${ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT:-0}"
WORKSPACE_ID_VALUE="${ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID:-${WORKSPACE_ID:-ws-1}}"

BOT_TOKEN="${BOT_TOKEN:-}"
if [[ -z "${BOT_TOKEN}" ]]; then
  echo "BOT TOKEN:"
  read -rs BOT_TOKEN
  echo
fi
if [[ -z "${BOT_TOKEN}" ]]; then
  echo "Missing bot token."
  exit 1
fi
if [[ "${BOT_TOKEN}" == "YOUR_BOT_TOKEN" || "${BOT_TOKEN}" == "<YOUR_BOT_TOKEN>" ]]; then
  echo "You pasted the placeholder token. Replace it with your real BotFather token."
  echo "Expected format: 123456789:AA..."
  exit 1
fi
if [[ "${BOT_TOKEN}" != *:* ]]; then
  echo "Bot token format looks invalid. Expected something like: 123456:ABC..."
  exit 1
fi

telegram_get() {
  local method="$1"
  local query="${2:-}"
  curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/${method}${query}"
}

ME_JSON="$(telegram_get "getMe" "")"
if [[ "$(echo "${ME_JSON}" | jq -r '.ok // false')" != "true" ]]; then
  echo "Telegram token is invalid."
  echo "${ME_JSON}" | jq
  exit 1
fi
BOT_USERNAME="$(echo "${ME_JSON}" | jq -r '.result.username // empty')"

CHAT_ID="${CHAT_ID:-}"
if [[ "${ORION_TELEGRAM_PROMPT_CHAT_ID:-0}" == "1" && -z "${CHAT_ID}" ]]; then
  echo "CHAT ID (optional, press Enter to auto-detect):"
  read -r CHAT_ID
fi
CHAT_ID="$(echo "${CHAT_ID}" | tr -d '[:space:]')"
if [[ -n "${CHAT_ID}" && "${CHAT_ID}" == *:* ]]; then
  echo "CHAT ID looks like a bot token (contains ':')."
  echo "Use numeric chat_id (example: 1932934047) or press Enter for auto-detect."
  exit 1
fi

if [[ -z "${CHAT_ID}" ]]; then
  echo "Auto-detect mode: send a plain text message to @${BOT_USERNAME}, then wait..."
  BASE_OFFSET="$(telegram_get "getUpdates" "?timeout=0&limit=100" | jq -r '[.result[]?.update_id] | max // 0')"
  if [[ -z "${BASE_OFFSET}" || "${BASE_OFFSET}" == "null" ]]; then
    BASE_OFFSET=0
  fi
  for _ in $(seq 1 20); do
    RESP="$(telegram_get "getUpdates" "?offset=$((BASE_OFFSET+1))&timeout=2&limit=20")"
    if [[ "$(echo "${RESP}" | jq -r '.ok // false')" != "true" ]]; then
      sleep 1
      continue
    fi
    NEW_MAX="$(echo "${RESP}" | jq -r '[.result[]?.update_id] | max // 0')"
    if [[ -n "${NEW_MAX}" && "${NEW_MAX}" != "null" && "${NEW_MAX}" -gt "${BASE_OFFSET}" ]]; then
      BASE_OFFSET="${NEW_MAX}"
    fi
    DETECTED_CHAT_ID="$(echo "${RESP}" | jq -r '.result[]? | (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m | select($m != null) | select(($m.from.is_bot // false) == false) | ($m.chat.id|tostring)' | tail -n1)"
    if [[ -n "${DETECTED_CHAT_ID}" && "${DETECTED_CHAT_ID}" != "null" ]]; then
      CHAT_ID="${DETECTED_CHAT_ID}"
      break
    fi
  done
fi

if [[ -z "${CHAT_ID}" ]]; then
  echo "Could not auto-detect chat_id."
  echo "Make sure you sent a plain text message to @${BOT_USERNAME} from the target chat, then rerun."
  exit 1
fi
echo "Using chat_id: ${CHAT_ID}"

# Prevent dual Telegram pollers (runtime autopilot + this script) from racing on getUpdates.
# If runtime is already up, stop it before inbound handshake.
if [[ "${RUNTIME_WAS_UP}" == "1" ]]; then
  echo "Pausing Empyralis runtime before handshake (avoids update polling conflicts)..."
  bash scripts/stop_empyralis_local_stack.sh >/dev/null 2>&1 || true
fi

echo "Inbound handshake: send any plain text to @${BOT_USERNAME} now (chat_id=${CHAT_ID})."
echo "Waiting up to ~60s for Telegram inbound update..."
BASE_OFFSET="$(telegram_get "getUpdates" "?timeout=0&limit=100" | jq -r '[.result[]?.update_id] | max // 0')"
if [[ -z "${BASE_OFFSET}" || "${BASE_OFFSET}" == "null" ]]; then
  BASE_OFFSET=0
fi
INBOUND_OK=0
SEEN_CHAT_ID=""
SEEN_TEXT=""
SEEN_SENDER_ID=""
SEEN_SENDER_USERNAME=""
for _ in $(seq 1 12); do
  RESP="$(telegram_get "getUpdates" "?offset=$((BASE_OFFSET+1))&timeout=5&limit=30")"
  if [[ "$(echo "${RESP}" | jq -r '.ok // false')" != "true" ]]; then
    sleep 1
    continue
  fi
  NEW_MAX="$(echo "${RESP}" | jq -r '[.result[]?.update_id] | max // 0')"
  if [[ -n "${NEW_MAX}" && "${NEW_MAX}" != "null" && "${NEW_MAX}" -gt "${BASE_OFFSET}" ]]; then
    BASE_OFFSET="${NEW_MAX}"
  fi
  SEEN_CHAT_ID="$(echo "${RESP}" | jq -r '
    .result[]? |
    (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m |
    select($m != null) |
    select(($m.from.is_bot // false) == false) |
    ($m.chat.id|tostring)
  ' | tail -n1)"
  SEEN_TEXT="$(echo "${RESP}" | jq -r '
    .result[]? |
    (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m |
    select($m != null) |
    select(($m.from.is_bot // false) == false) |
    ($m.text // $m.caption // "")
  ' | tail -n1)"
  SEEN_SENDER_ID="$(echo "${RESP}" | jq -r '
    .result[]? |
    (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m |
    select($m != null) |
    select(($m.from.is_bot // false) == false) |
    ($m.from.id|tostring)
  ' | tail -n1)"
  SEEN_SENDER_USERNAME="$(echo "${RESP}" | jq -r '
    .result[]? |
    (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m |
    select($m != null) |
    select(($m.from.is_bot // false) == false) |
    ($m.from.username // "")
  ' | tail -n1)"
  MATCH="$(echo "${RESP}" | jq -r --arg chat "${CHAT_ID}" '
    .result[]? |
    (.message // .edited_message // .channel_post // .edited_channel_post // .business_message // .edited_business_message // empty) as $m |
    select($m != null) |
    select(($m.from.is_bot // false) == false) |
    {chat_id: (($m.chat.id // "")|tostring), text: ($m.text // $m.caption // "")} |
    select(.chat_id == $chat) |
    .text
  ' | tail -n1)"
  if [[ -n "${MATCH}" && "${MATCH}" != "null" ]]; then
    INBOUND_OK=1
    echo "Inbound handshake OK."
    break
  fi
done

if [[ "${INBOUND_OK}" != "1" ]]; then
  echo "Inbound handshake failed: Telegram did not deliver a user message for chat_id=${CHAT_ID}."
  if [[ -n "${SEEN_CHAT_ID}" && "${SEEN_CHAT_ID}" != "null" ]]; then
    echo "Inbound was seen from a different chat_id: ${SEEN_CHAT_ID}"
    if [[ -n "${SEEN_TEXT}" && "${SEEN_TEXT}" != "null" ]]; then
      echo "Last inbound text: ${SEEN_TEXT}"
    fi
    echo "Use that chat_id next run, or press Enter at CHAT ID prompt to auto-detect."
  fi
  echo "Make sure you are messaging @${BOT_USERNAME} from the same Telegram account/chat, then rerun."
  exit 1
fi

ALLOW_FROM=""
if [[ -n "${SEEN_SENDER_ID}" && "${SEEN_SENDER_ID}" != "null" ]]; then
  ALLOW_FROM="${SEEN_SENDER_ID}"
fi
if [[ -n "${SEEN_SENDER_USERNAME}" && "${SEEN_SENDER_USERNAME}" != "null" ]]; then
  if [[ -n "${ALLOW_FROM}" ]]; then
    ALLOW_FROM="${ALLOW_FROM},@${SEEN_SENDER_USERNAME}"
  else
    ALLOW_FROM="@${SEEN_SENDER_USERNAME}"
  fi
fi

if ! runtime_is_up; then
  RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/start_empyralis_local_stack.sh >/dev/null
fi

BODY="$(jq -nc \
  --arg token "${BOT_TOKEN}" \
  --arg chat_id "${CHAT_ID}" \
  --arg workspace_id "${WORKSPACE_ID_VALUE}" \
  --arg allow_from "${ALLOW_FROM}" \
  '
  def allow_from_items:
    ($allow_from
      | split(",")
      | map(gsub("^\\s+|\\s+$"; ""))
      | map(select(length > 0)));
  {
    label: "Telegram Bot LIVE",
    connector: "telegram_bot",
    workspace_id: $workspace_id,
    credentials: {bot_token: $token, chat_id: $chat_id},
    metadata: (if (allow_from_items | length) > 0 then {allow_from: allow_from_items} else {} end)
  }'
)"

RESP="$(curl -sS -X POST \
  -H "X-API-Key: ${RUNTIME_KEY_VALUE}" \
  -H "Content-Type: application/json" \
  -d "${BODY}" \
  "http://127.0.0.1:8001/connectors/vault")"

echo "${RESP}" | jq

NEW_ID="$(echo "${RESP}" | jq -r '.id // empty')"
TESTED_BOT_USERNAME="$(echo "${RESP}" | jq -r '.test.bot.username // empty')"

if [[ -n "${NEW_ID}" ]]; then
  STATE_HOME="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
  VAULT_FILE="${CREDENTIAL_VAULT_FILE:-}"
  if [[ -z "${VAULT_FILE}" ]]; then
    VAULT_FILE="${STATE_HOME}/vault/credentials.json"
  fi
  python3 - "${NEW_ID}" "${VAULT_FILE}" <<'PY'
import json, sys, pathlib, time
new_id = sys.argv[1]
path = pathlib.Path(sys.argv[2])
if not path.exists():
    raise SystemExit(0)
data = json.loads(path.read_text() or "{}")
items = data.get("credentials")
if not isinstance(items, list):
    raise SystemExit(0)
kept = []
removed = 0
for item in items:
    if not isinstance(item, dict):
        continue
    if str(item.get("provider") or "").strip().lower() == "telegram_bot":
        if str(item.get("id") or "") != new_id:
            removed += 1
            continue
    kept.append(item)
if removed:
    backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    backup.write_text(json.dumps(data, indent=2))
data["credentials"] = kept
path.write_text(json.dumps(data, indent=2))
print(f"Pruned {removed} old telegram connector(s).")
PY
fi

# Avoid dual-poller conflicts: if OpenClaw gateway is running with the same bot token,
# it can consume updates before Empyralis sees them.
if command -v openclaw >/dev/null 2>&1; then
  openclaw gateway stop >/dev/null 2>&1 || true
fi

bash scripts/stop_empyralis_local_stack.sh >/dev/null 2>&1 || true
STATE_HOME="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
AUTOPILOT_STATE_FILE="${ORION_TELEGRAM_AUTOPILOT_STATE_FILE:-}"
if [[ -z "${AUTOPILOT_STATE_FILE}" ]]; then
  AUTOPILOT_STATE_FILE="${STATE_HOME}/channels/telegram/autopilot_state.json"
fi
rm -f "${AUTOPILOT_STATE_FILE}"

ORION_TELEGRAM_AUTOPILOT_ENABLED=1 \
ORION_TELEGRAM_AUTOPILOT_ENGINE=codex \
ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX=0 \
ORION_TELEGRAM_AUTOPILOT_SEND_ACK=0 \
ORION_AUTOPILOT_INCLUDE_RUN_META=0 \
ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT="${ALLOW_ANY_CHAT}" \
ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET=local_companion \
RUNTIME_KEY="${RUNTIME_KEY_VALUE}" \
bash scripts/start_empyralis_local_stack.sh

if [[ -n "${TESTED_BOT_USERNAME}" ]]; then
  echo "Bot username: @${TESTED_BOT_USERNAME}"
elif [[ -n "${BOT_USERNAME}" ]]; then
  echo "Bot username: @${BOT_USERNAME}"
fi
curl -sS -X POST \
  -H "X-API-Key: ${RUNTIME_KEY_VALUE}" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"[Empyralis probe] Reply here with: LIVE-OK\",\"workspace_id\":\"default\",\"chat_id\":\"${CHAT_ID}\"}" \
  "http://127.0.0.1:8001/channels/telegram/send" | jq

exec ./bin/empyralis autopilot watch-line 2
