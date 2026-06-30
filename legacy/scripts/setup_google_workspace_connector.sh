#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${EMPYRALIS_API_URL:-${ORION_API_URL:-http://127.0.0.1:8001}}"
RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY="${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-}}}"
WORKSPACE_ID="${WORKSPACE_ID:-default}"
LABEL="${LABEL:-Google Workspace}"
ACCESS_TOKEN="${ACCESS_TOKEN:-}"
CALENDAR_ID="${CALENDAR_ID:-primary}"
TIMEZONE="${TIMEZONE:-UTC}"
GWS_CONFIG_DIR="${GWS_CONFIG_DIR:-${ROOT_DIR}/.gws-config}"
CONNECT_MODE="${CONNECT_MODE:-}"

if [[ -z "${RUNTIME_KEY}" && -f "${RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(tr -d '\r\n' < "${RUNTIME_KEY_FILE}" 2>/dev/null || true)"
fi

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Missing runtime API key."
  echo "Set RUNTIME_KEY/EMPYRALIS_API_KEY/ORION_API_KEY, or start stack first."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required. Install jq and rerun."
  exit 1
fi

if [[ -z "${CONNECT_MODE}" ]]; then
  if [[ -n "${ACCESS_TOKEN}" ]]; then
    CONNECT_MODE="token"
  elif [[ -f "${GWS_CONFIG_DIR}/client_secret.json" ]]; then
    CONNECT_MODE="gws_local"
  else
    CONNECT_MODE="token"
  fi
fi

ensure_runtime_up() {
  if curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" >/dev/null 2>&1; then
    return 0
  fi

  echo "Empyralis runtime is offline. Starting local stack..."
  RUNTIME_KEY="${RUNTIME_KEY}" bash "${ROOT_DIR}/scripts/start_empyralis_local_stack.sh" >/dev/null 2>&1 || true

  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.4
  done
  return 1
}

if ! ensure_runtime_up; then
  echo "Runtime is still unavailable at ${API_URL}."
  echo "Run this, then retry:"
  echo "  cd ${ROOT_DIR} && RUNTIME_KEY='${RUNTIME_KEY}' bash scripts/start_empyralis_local_stack.sh"
  exit 1
fi

read -r -p "Calendar ID [${CALENDAR_ID}]: " _calendar || true
if [[ -n "${_calendar:-}" ]]; then
  CALENDAR_ID="${_calendar}"
fi
read -r -p "Timezone [${TIMEZONE}]: " _timezone || true
if [[ -n "${_timezone:-}" ]]; then
  TIMEZONE="${_timezone}"
fi

if [[ "${CONNECT_MODE}" == "gws_local" ]]; then
  if ! command -v gws >/dev/null 2>&1; then
    echo "gws is required for local Google Workspace auth mode."
    exit 1
  fi
  if [[ ! -f "${GWS_CONFIG_DIR}/client_secret.json" ]]; then
    echo "Local Google Workspace CLI config is missing: ${GWS_CONFIG_DIR}/client_secret.json"
    exit 1
  fi

  echo "Validating local Google Workspace CLI auth..."
  STATUS="$(env GOOGLE_WORKSPACE_CLI_CONFIG_DIR="${GWS_CONFIG_DIR}" gws auth status || true)"
  AUTH_METHOD="$(echo "${STATUS}" | jq -r '.auth_method // empty')"
  if [[ -z "${AUTH_METHOD}" || "${AUTH_METHOD}" == "none" ]]; then
    echo "Local gws auth is not ready:"
    echo "${STATUS}" | jq
    exit 1
  fi
  PROFILE="$(env GOOGLE_WORKSPACE_CLI_CONFIG_DIR="${GWS_CONFIG_DIR}" gws gmail users getProfile --params '{"userId":"me"}' || true)"
  EMAIL_ADDR="$(echo "${PROFILE}" | jq -r '.emailAddress // empty')"
  if [[ -z "${EMAIL_ADDR}" ]]; then
    echo "Local gws Gmail profile lookup failed:"
    echo "${PROFILE}" | jq
    exit 1
  fi
  echo "Local gws auth OK for: ${EMAIL_ADDR}"
  PAYLOAD="$(jq -n \
    --arg label "${LABEL}" \
    --arg ws "${WORKSPACE_ID}" \
    --arg calendar_id "${CALENDAR_ID}" \
    --arg timezone "${TIMEZONE}" \
    '{label:$label,connector:"google_workspace",workspace_id:$ws,credentials:{auth_mode:"gws_local",gws_config_dir:".gws-config",calendar_id:$calendar_id,timezone:$timezone}}')"
else
  if [[ -z "${ACCESS_TOKEN}" ]]; then
    read -r -s -p "Google OAuth access token: " ACCESS_TOKEN
    echo
  fi

  if [[ -z "${ACCESS_TOKEN}" ]]; then
    echo "Access token is required."
    exit 1
  fi

  echo "Validating Google token via Gmail profile..."
  PROFILE="$(curl -sS -H "Authorization: Bearer ${ACCESS_TOKEN}" https://gmail.googleapis.com/gmail/v1/users/me/profile || true)"
  EMAIL_ADDR="$(echo "${PROFILE}" | jq -r '.emailAddress // empty')"
  if [[ -z "${EMAIL_ADDR}" ]]; then
    echo "Google token validation failed:"
    echo "${PROFILE}" | jq
    exit 1
  fi
  echo "Google token OK for: ${EMAIL_ADDR}"

  echo "Validating Google token via Calendar list..."
  CAL_LIST="$(curl -sS -H "Authorization: Bearer ${ACCESS_TOKEN}" "https://www.googleapis.com/calendar/v3/users/me/calendarList" || true)"
  CAL_KIND="$(echo "${CAL_LIST}" | jq -r '.kind // empty')"
  if [[ "${CAL_KIND}" != "calendar#calendarList" ]]; then
    echo "Google calendar validation warning (continuing with Gmail-only mode):"
    echo "${CAL_LIST}" | jq
    echo "Calendar actions will stay disabled until scope is added:"
    echo "  https://www.googleapis.com/auth/calendar"
  else
    echo "Calendar scope OK."
  fi

  PAYLOAD="$(jq -n \
    --arg label "${LABEL}" \
    --arg ws "${WORKSPACE_ID}" \
    --arg token "${ACCESS_TOKEN}" \
    --arg calendar_id "${CALENDAR_ID}" \
    --arg timezone "${TIMEZONE}" \
    '{label:$label,connector:"google_workspace",workspace_id:$ws,credentials:{access_token:$token,calendar_id:$calendar_id,timezone:$timezone}}')"
fi

echo "Saving connector to vault..."
if ! ensure_runtime_up; then
  echo "Runtime became unavailable before connector save."
  exit 1
fi
CREATE_RAW="$(curl -sS -w '\nHTTP_STATUS:%{http_code}' -X POST \
  -H "X-API-Key: ${RUNTIME_KEY}" \
  -H "Content-Type: application/json" \
  "${API_URL}/connectors/vault" \
  -d "${PAYLOAD}" || true)"
CREATE_OUT="$(printf "%s" "${CREATE_RAW}" | sed '$d')"
CREATE_STATUS="$(printf "%s" "${CREATE_RAW}" | tail -n1 | sed 's/^HTTP_STATUS://')"
if [[ -z "${CREATE_STATUS}" ]]; then
  CREATE_STATUS="000"
fi

CID="$(echo "${CREATE_OUT}" | jq -r '.id // empty')"
if [[ "${CREATE_STATUS}" != "200" || -z "${CID}" ]]; then
  echo "Connector create response:"
  echo "HTTP ${CREATE_STATUS}"
  if printf "%s" "${CREATE_OUT}" | jq . >/dev/null 2>&1; then
    echo "${CREATE_OUT}" | jq
  else
    printf "%s\n" "${CREATE_OUT}"
  fi
  exit 1
fi

echo "Connector created:"
echo "${CREATE_OUT}" | jq '{id,label,connector,workspace_id,status}'

echo
echo "Testing connector through Empyralis runtime..."
TEST_OUT="$(curl -fsS -X POST -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/connectors/vault/${CID}/test" || true)"
echo "${TEST_OUT}" | jq

echo
echo "Done. Email channel is now available via Google Workspace connector."
