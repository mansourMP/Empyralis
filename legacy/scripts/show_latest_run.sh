#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_STACK_SCRIPT}" ]]; then
  START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_orion_local_stack.sh"
fi
API_URL="${EMPYRALIS_API_URL:-${ORION_API_URL:-http://127.0.0.1:8001}}"
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY_RAW="${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-}}}"
LIMIT="${LIMIT:-1}"
SOURCE_MODE="${SHOW_LATEST_SOURCE:-auto}" # auto|telegram|history
START_LOG_PATH="/tmp/empyralis-show-latest-start.log"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --telegram)
      SOURCE_MODE="telegram"
      shift
      ;;
    --history)
      SOURCE_MODE="history"
      shift
      ;;
    --auto)
      SOURCE_MODE="auto"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage:
  RUNTIME_KEY='replace-with-strong-key' bash scripts/show_latest_run.sh [--auto|--telegram|--history]
  bash scripts/show_latest_run.sh replace-with-strong-key [--auto|--telegram|--history]
Env:
  EMPYRALIS_API_URL=http://127.0.0.1:8001
  EMPYRALIS_API_KEY=replace-with-strong-key
  SHOW_LATEST_SOURCE=auto|telegram|history
  LIMIT=1
EOF
      exit 0
      ;;
    --*)
      echo "Unknown option: $1"
      exit 2
      ;;
    *)
      if [[ -z "${RUNTIME_KEY_RAW}" ]]; then
        RUNTIME_KEY_RAW="$1"
      fi
      shift
      ;;
  esac
done

normalize_runtime_key() {
  printf "%s" "$1" | tr -d '[:space:]'
}

json_detail() {
  python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
value=data.get("detail")
if isinstance(value,str):
    print(value)
else:
    print("")
'
}

RUNTIME_KEY="$(normalize_runtime_key "${RUNTIME_KEY_RAW}")"
if [[ -z "${RUNTIME_KEY}" && -f "${STACK_RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(normalize_runtime_key "$(cat "${STACK_RUNTIME_KEY_FILE}" 2>/dev/null || true)")"
fi

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Usage:"
  echo "  RUNTIME_KEY='replace-with-strong-key' bash scripts/show_latest_run.sh [--auto|--telegram|--history]"
  echo "  or: bash scripts/show_latest_run.sh replace-with-strong-key [--auto|--telegram|--history]"
  exit 1
fi

ensure_runtime() {
  local http_code
  http_code="$(curl -sS -o /dev/null -w "%{http_code}" \
    --connect-timeout 2 \
    --max-time 4 \
    -H "X-API-Key: ${RUNTIME_KEY}" \
    "${API_URL}/health" 2>/dev/null || true)"

  if [[ "${http_code}" == "200" || "${http_code}" == "401" || "${http_code}" == "403" ]]; then
    return 0
  fi

  if [[ -x "${START_STACK_SCRIPT}" ]]; then
    echo "Empyralis runtime is offline. Starting local stack..."
    RUNTIME_KEY="${RUNTIME_KEY}" bash "${START_STACK_SCRIPT}" >"${START_LOG_PATH}" 2>&1 || {
      echo "Failed to start Empyralis stack. See ${START_LOG_PATH}"
      return 1
    }
    http_code="$(curl -sS -o /dev/null -w "%{http_code}" \
      --connect-timeout 2 \
      --max-time 6 \
      -H "X-API-Key: ${RUNTIME_KEY}" \
      "${API_URL}/health" 2>/dev/null || true)"
    if [[ "${http_code}" == "200" || "${http_code}" == "401" || "${http_code}" == "403" ]]; then
      return 0
    fi
    echo "Runtime still unreachable after startup attempt."
    echo "Check: ${START_LOG_PATH}"
    return 1
  fi

  echo "Runtime is offline and start script not found: ${START_STACK_SCRIPT}"
  return 1
}

ensure_runtime

fetch_json() {
  local path="$1"
  curl -sS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}${path}"
}

retry_with_stack_key_on_invalid() {
  local payload="$1"
  local detail
  detail="$(printf "%s" "${payload}" | json_detail)"
  if [[ "${detail}" == "Invalid API key" ]]; then
    local stack_key
    stack_key="$(normalize_runtime_key "$(cat "${STACK_RUNTIME_KEY_FILE}" 2>/dev/null || true)")"
    if [[ -n "${stack_key}" && "${stack_key}" != "${RUNTIME_KEY}" ]]; then
      echo "Provided runtime key is invalid. Retrying with stack runtime key..."
      RUNTIME_KEY="${stack_key}"
      return 0
    fi
  fi
  return 1
}

HISTORY_JSON="$(fetch_json "/history/runs?limit=${LIMIT}")"
HISTORY_DETAIL="$(printf "%s" "${HISTORY_JSON}" | json_detail)"
if [[ "${HISTORY_DETAIL}" == "Invalid API key" ]]; then
  STACK_KEY="$(normalize_runtime_key "$(cat "${STACK_RUNTIME_KEY_FILE}" 2>/dev/null || true)")"
  if [[ -n "${STACK_KEY}" && "${STACK_KEY}" != "${RUNTIME_KEY}" ]]; then
    echo "Provided runtime key is invalid. Retrying with stack runtime key..."
    RUNTIME_KEY="${STACK_KEY}"
    HISTORY_JSON="$(fetch_json "/history/runs?limit=${LIMIT}")"
    HISTORY_DETAIL="$(printf "%s" "${HISTORY_JSON}" | json_detail)"
  fi
fi

AUTOPILOT_JSON="$(fetch_json "/channels/telegram/autopilot/status")"
AUTOPILOT_DETAIL="$(printf "%s" "${AUTOPILOT_JSON}" | json_detail)"
if [[ "${AUTOPILOT_DETAIL}" == "Invalid API key" ]]; then
  if retry_with_stack_key_on_invalid "${AUTOPILOT_JSON}"; then
    AUTOPILOT_JSON="$(fetch_json "/channels/telegram/autopilot/status")"
    AUTOPILOT_DETAIL="$(printf "%s" "${AUTOPILOT_JSON}" | json_detail)"
    HISTORY_JSON="$(fetch_json "/history/runs?limit=${LIMIT}")"
    HISTORY_DETAIL="$(printf "%s" "${HISTORY_JSON}" | json_detail)"
  fi
fi

HISTORY_RUN_ID=""
if [[ -z "${HISTORY_DETAIL}" ]]; then
  HISTORY_RUN_ID="$(echo "${HISTORY_JSON}" | python3 -c 'import sys,json; d=json.load(sys.stdin); items=d.get("items") or []; print(items[0].get("run_id","") if items else "")')"
fi
TELEGRAM_RUN_ID=""
if [[ -z "${AUTOPILOT_DETAIL}" ]]; then
  TELEGRAM_RUN_ID="$(echo "${AUTOPILOT_JSON}" | python3 -c 'import sys,json; d=json.load(sys.stdin); connectors=d.get("connectors") or []; rid=""; 
for c in connectors:
    if isinstance(c, dict):
        rid=str(c.get("last_run_id") or "").strip()
        if rid:
            break
print(rid)')"
fi

RUN_ID=""
RUN_SOURCE=""
case "${SOURCE_MODE}" in
  telegram)
    RUN_ID="${TELEGRAM_RUN_ID}"
    RUN_SOURCE="telegram_autopilot"
    ;;
  history)
    RUN_ID="${HISTORY_RUN_ID}"
    RUN_SOURCE="history"
    ;;
  auto)
    if [[ -n "${TELEGRAM_RUN_ID}" ]]; then
      RUN_ID="${TELEGRAM_RUN_ID}"
      RUN_SOURCE="telegram_autopilot"
    else
      RUN_ID="${HISTORY_RUN_ID}"
      RUN_SOURCE="history"
    fi
    ;;
  *)
    echo "Invalid SHOW_LATEST_SOURCE: ${SOURCE_MODE} (use auto|telegram|history)"
    exit 2
    ;;
esac

if [[ -z "${RUN_ID}" ]]; then
  if [[ "${SOURCE_MODE}" == "history" && -n "${HISTORY_DETAIL}" ]]; then
    echo "History query failed: ${HISTORY_DETAIL}"
    if [[ "${HISTORY_DETAIL}" == "Invalid API key" ]]; then
      echo "Tip: use the key from ${STACK_RUNTIME_KEY_FILE}"
    fi
  elif [[ "${SOURCE_MODE}" == "telegram" && -n "${AUTOPILOT_DETAIL}" ]]; then
    echo "Telegram autopilot status query failed: ${AUTOPILOT_DETAIL}"
  else
    [[ -n "${AUTOPILOT_DETAIL}" ]] && echo "Telegram status issue: ${AUTOPILOT_DETAIL}"
    [[ -n "${HISTORY_DETAIL}" ]] && echo "History issue: ${HISTORY_DETAIL}"
  fi
  echo "No runs found."
  exit 2
fi

RUN_JSON="$(fetch_json "/runs/${RUN_ID}")"
RUN_DETAIL="$(printf "%s" "${RUN_JSON}" | json_detail)"
if [[ -n "${RUN_DETAIL}" ]]; then
  echo "Run query failed: ${RUN_DETAIL}"
  exit 2
fi
python3 -c '
import json, sys

d = json.load(sys.stdin)
u = d.get("usage_masked") or {}
rd = d.get("result_data") or {}
gen = rd.get("generation") if isinstance(rd, dict) else {}
print("source:", "'"${RUN_SOURCE}"'")
print("run_id:", d.get("run_id"))
print("status:", d.get("status"))
print("provider:", u.get("provider"))
print("model:", u.get("model"))
print("tokens_total:", u.get("total_tokens_est"))
print("cost_band:", u.get("cost_band"))
if isinstance(gen, dict) and gen:
    print("generation_mode:", gen.get("mode"))
    print("generation_provider:", gen.get("provider"))
    print("generation_model:", gen.get("model"))
    print("attempted_providers:", gen.get("attempted_providers"))
    if gen.get("error"):
        print("generation_error:", gen.get("error"))
for key in ("error", "error_message", "last_error", "failure_reason", "message"):
    value = d.get(key)
    if value:
        print(f"{key}:", value)
print("summary:", d.get("result"))
' <<< "${RUN_JSON}"
