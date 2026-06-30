#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RUNTIME_KEY="${1:-${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-}}}}"

# Best-effort LAN IP detection for Mac Mini / laptop.
HOST_IP="$(
  ipconfig getifaddr en0 2>/dev/null \
    || ipconfig getifaddr en1 2>/dev/null \
    || echo "127.0.0.1"
)"

# For iPhone access: bind to all interfaces but advertise the LAN IP.
export ORION_HOST="${ORION_HOST:-${HOST_IP}}"
export ORION_BIND_HOST="${ORION_BIND_HOST:-0.0.0.0}"
export ORION_PORT="${ORION_PORT:-8001}"

# V1 scope: no external messaging connectors.
export EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED="${EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED:-0}"
export ORION_TELEGRAM_AUTOPILOT_ENABLED="${ORION_TELEGRAM_AUTOPILOT_ENABLED:-0}"
export EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED="${EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED:-0}"
export ORION_WHATSAPP_AUTOPILOT_ENABLED="${ORION_WHATSAPP_AUTOPILOT_ENABLED:-0}"

# Mobile V1 only needs the runtime + worker.
export START_BACKEND="${START_BACKEND:-0}"
export START_FRONTEND="${START_FRONTEND:-0}"
export START_OPS_DAEMON="${START_OPS_DAEMON:-0}"

echo "Starting Empyralis Mobile V1 core..."
echo "Runtime URL: http://${ORION_HOST}:${ORION_PORT}"
if [[ -n "${RUNTIME_KEY}" ]]; then
  echo "Runtime key: ${RUNTIME_KEY}"
else
  echo "Runtime key: auto-generate"
fi
echo

cd "${ROOT_DIR}"
if [[ -n "${RUNTIME_KEY}" ]]; then
  bash scripts/start_empyralis_local_stack.sh "${RUNTIME_KEY}"
else
  bash scripts/start_empyralis_local_stack.sh
fi
