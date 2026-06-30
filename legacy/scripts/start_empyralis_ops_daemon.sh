#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
LOG_DIR="${STATE_DIR}/logs"
PID_DIR="${STATE_DIR}/pids"
PID_FILE="${PID_DIR}/ops-daemon.pid"
KEY_FILE="${STATE_DIR}/ops_daemon_key"

HOST="${ORION_OPS_DAEMON_HOST:-127.0.0.1}"
PORT="${ORION_OPS_DAEMON_PORT:-8787}"
RUNTIME_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

is_pid_alive() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

if [[ -f "${PID_FILE}" ]]; then
  existing_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if is_pid_alive "${existing_pid}"; then
    echo "Empyralis ops daemon already running (pid ${existing_pid}) on http://${HOST}:${PORT}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

stale_pids="$(lsof -ti tcp:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${stale_pids}" ]]; then
  echo "Port ${PORT} is busy. Stopping stale process(es): $(echo "${stale_pids}" | tr '\n' ' ' | xargs)"
  echo "${stale_pids}" | xargs kill -9 2>/dev/null || true
  sleep 0.2
fi

nohup "${PYTHON_BIN}" "${ROOT_DIR}/scripts/orion_ops_daemon.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --runtime-url "${RUNTIME_URL}" \
  > "${LOG_DIR}/ops-daemon.log" 2>&1 &

daemon_pid="$!"
echo "${daemon_pid}" > "${PID_FILE}"
chmod 600 "${PID_FILE}" 2>/dev/null || true

for _ in $(seq 1 20); do
  if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "Failed to start Empyralis ops daemon. Check log: ${LOG_DIR}/ops-daemon.log"
  exit 1
fi

if [[ -f "${KEY_FILE}" ]]; then
  key="$(cat "${KEY_FILE}" 2>/dev/null || true)"
  if [[ -n "${key}" ]]; then
    fp="${#key}:${key:0:4}...${key: -4}"
    echo "Empyralis ops daemon key fingerprint: ${fp}"
  fi
fi

echo "Empyralis ops daemon is up: http://${HOST}:${PORT}"
