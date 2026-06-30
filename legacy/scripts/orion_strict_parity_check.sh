#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
OPS_DAEMON_URL="${ORION_OPS_DAEMON_URL:-http://127.0.0.1:8787}"
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"

STRICT=0
DEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
    --deep)
      DEEP=1
      shift
      ;;
    -h|--help)
      cat <<'USAGE'
Usage:
  RUNTIME_KEY='replace-with-strong-key' bash scripts/orion_strict_parity_check.sh [--strict] [--deep]

Checks operational parity targets (OpenClaw-inspired) in one pass.
  --strict  Exit non-zero on any failed required check
  --deep    Also run heavyweight parity scanner and release gate
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 2
      ;;
  esac
done

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

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() {
  local label="$1"
  echo "[PASS] ${label}"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  local label="$1"
  echo "[FAIL] ${label}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
  local label="$1"
  echo "[WARN] ${label}"
  WARN_COUNT=$((WARN_COUNT + 1))
}

fetch_json() {
  local url="$1"
  if [[ "${url}" == "${OPS_DAEMON_URL}"* ]]; then
    curl -fsS "${url}" 2>/dev/null || echo '{}'
  else
    curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${url}" 2>/dev/null || echo '{}'
  fi
}

echo "Empyralis Strict Parity Checklist"
echo "root: ${ROOT_DIR}"
echo "api: ${API_URL}"
echo

HEALTH_JSON="$(fetch_json "${API_URL}/health")"
WORKERS_JSON="$(fetch_json "${API_URL}/runtime/runtimes/status")"
TG_JSON="$(fetch_json "${API_URL}/channels/telegram/autopilot/status")"
WA_JSON="$(fetch_json "${API_URL}/channels/whatsapp/autopilot/status")"
CONNECTORS_JSON="$(fetch_json "${API_URL}/connectors/vault?workspace_id=default")"
HISTORY_JSON="$(fetch_json "${API_URL}/history/runs?limit=1")"
DAEMON_JSON="$(fetch_json "${OPS_DAEMON_URL}/health")"

runtime_ok="$(echo "${HEALTH_JSON}" | jq -r 'if .ok == true then "1" else "0" end' 2>/dev/null || echo "0")"
auth_ok="$(echo "${HEALTH_JSON}" | jq -r 'if .openai_key_valid == true then "1" else "0" end' 2>/dev/null || echo "0")"
auth_mode="$(echo "${HEALTH_JSON}" | jq -r '.auth_mode // "-"' 2>/dev/null || echo "-")"
worker_online="$(echo "${WORKERS_JSON}" | jq -r '
  if (((.summary.online // 0) | tonumber) > 0) then "1"
  elif (((.workers // []) | map(select(.online == true)) | length) > 0) then "1"
  elif (((.items // []) | map(select(.online == true)) | length) > 0) then "1"
  else "0" end
' 2>/dev/null || echo "0")"
tg_active="$(echo "${TG_JSON}" | jq -r 'if .ok == true and .autopilot.active == true then "1" else "0" end' 2>/dev/null || echo "0")"
tg_thread="$(echo "${TG_JSON}" | jq -r 'if .ok == true and .autopilot.thread_alive == true then "1" else "0" end' 2>/dev/null || echo "0")"
tg_connectors="$(echo "${TG_JSON}" | jq -r 'if ((.connectors // []) | length) > 0 then "1" else "0" end' 2>/dev/null || echo "0")"
tg_last_error="$(echo "${TG_JSON}" | jq -r '.autopilot.last_error // ""' 2>/dev/null || echo "")"
wa_enabled="$(echo "${WA_JSON}" | jq -r 'if .ok == true and .autopilot.enabled == true then "1" else "0" end' 2>/dev/null || echo "0")"
wa_active="$(echo "${WA_JSON}" | jq -r 'if .ok == true and .autopilot.active == true then "1" else "0" end' 2>/dev/null || echo "0")"
wa_thread="$(echo "${WA_JSON}" | jq -r 'if .ok == true and .autopilot.thread_alive == true then "1" else "0" end' 2>/dev/null || echo "0")"
wa_last_error="$(echo "${WA_JSON}" | jq -r '.autopilot.last_error // ""' 2>/dev/null || echo "")"
wa_connector_present="$(echo "${CONNECTORS_JSON}" | jq -r 'if ((.items // []) | map(select(.connector == "whatsapp_twilio")) | length) > 0 then "1" else "0" end' 2>/dev/null || echo "0")"

if [[ "${runtime_ok}" == "1" ]]; then
  pass "Runtime reachable"
else
  fail "Runtime reachable"
fi

if [[ "${auth_ok}" == "1" ]]; then
  pass "AI auth valid (mode=${auth_mode})"
else
  fail "AI auth valid (mode=${auth_mode})"
fi

if [[ "${worker_online}" == "1" ]]; then
  pass "Local worker online"
else
  fail "Local worker online"
fi

if [[ "${tg_active}" == "1" && "${tg_thread}" == "1" && "${tg_connectors}" == "1" ]]; then
  pass "Telegram autopilot active + thread alive + connector present"
else
  fail "Telegram autopilot active + thread alive + connector present"
fi

if [[ -z "${tg_last_error}" ]]; then
  pass "Telegram autopilot has no current error"
else
  warn "Telegram autopilot last_error=${tg_last_error}"
fi

if [[ "${wa_connector_present}" == "1" ]]; then
  if [[ "${wa_enabled}" == "1" && "${wa_active}" == "1" && "${wa_thread}" == "1" ]]; then
    pass "WhatsApp autopilot active for configured connector"
  else
    fail "WhatsApp autopilot active for configured connector"
  fi
  if [[ -z "${wa_last_error}" ]]; then
    pass "WhatsApp autopilot has no current error"
  else
    warn "WhatsApp autopilot last_error=${wa_last_error}"
  fi
else
  warn "No WhatsApp connector configured (optional)"
fi

RELEASE_OUTPUT=""
if RELEASE_OUTPUT="$(RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/orion_release_status.sh --strict 2>&1)"; then
  pass "Release status strict gate"
else
  fail "Release status strict gate"
  echo "${RELEASE_OUTPUT}" | sed 's/^/  > /'
fi

READINESS_OUTPUT=""
if READINESS_OUTPUT="$(RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/orion_environment_readiness.sh 2>&1)"; then
  readiness_score="$(echo "${READINESS_OUTPUT}" | awk '/^Empyralis Environment Readiness: / {split($4,a,"/"); print a[1]; exit}')"
  if [[ -n "${readiness_score}" && "${readiness_score}" -ge 9 ]]; then
    pass "Environment readiness >= 9/10 (${readiness_score}/10)"
  else
    fail "Environment readiness >= 9/10"
    echo "${READINESS_OUTPUT}" | sed 's/^/  > /'
  fi
else
  fail "Environment readiness script"
  echo "${READINESS_OUTPUT}" | sed 's/^/  > /'
fi

latest_run_id="$(echo "${HISTORY_JSON}" | jq -r '.items[0].run_id // ""' 2>/dev/null || echo "")"
latest_provider="$(echo "${HISTORY_JSON}" | jq -r '.items[0].usage_masked.provider // .items[0].engine // ""' 2>/dev/null || echo "")"
if [[ -n "${latest_run_id}" ]]; then
  pass "Latest run exists (${latest_run_id:0:8})"
  if [[ "${latest_provider}" == "local_companion" || "${latest_provider}" == "local-worker-v0" ]]; then
    warn "Latest run provider is local fallback (${latest_provider})"
  else
    pass "Latest run provider is non-fallback (${latest_provider:-unknown})"
  fi
else
  warn "No runs found in history"
fi

daemon_ok="$(echo "${DAEMON_JSON}" | jq -r 'if .ok == true then "1" else "0" end' 2>/dev/null || echo "0")"
daemon_watchdog_healthy="$(echo "${DAEMON_JSON}" | jq -r 'if .watchdog.healthy == false then "0" else "1" end' 2>/dev/null || echo "1")"
if [[ "${daemon_ok}" == "1" ]]; then
  if [[ "${daemon_watchdog_healthy}" == "1" ]]; then
    pass "Ops daemon healthy + watchdog healthy"
  else
    fail "Ops daemon watchdog unhealthy"
  fi
else
  warn "Ops daemon not reachable at ${OPS_DAEMON_URL} (optional for now)"
fi

if [[ "${DEEP}" == "1" ]]; then
  echo
  echo "Running deep parity checks..."
  if bash scripts/openclaw_parity_scan.sh >/tmp/orion_parity_scan.out 2>/tmp/orion_parity_scan.err; then
    pass "openclaw_parity_scan"
  else
    fail "openclaw_parity_scan"
    sed 's/^/  > /' /tmp/orion_parity_scan.out 2>/dev/null || true
    sed 's/^/  > /' /tmp/orion_parity_scan.err 2>/dev/null || true
  fi
  if RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash scripts/orion_release_gate.sh 15 >/tmp/orion_release_gate.out 2>/tmp/orion_release_gate.err; then
    pass "orion_release_gate"
  else
    fail "orion_release_gate"
    sed 's/^/  > /' /tmp/orion_release_gate.out 2>/dev/null || true
    sed 's/^/  > /' /tmp/orion_release_gate.err 2>/dev/null || true
  fi
fi

echo
echo "summary: pass=${PASS_COUNT} fail=${FAIL_COUNT} warn=${WARN_COUNT}"

if [[ "${STRICT}" == "1" && "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
