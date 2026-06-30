#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
PID_FILE="${STATE_DIR}/pids/ops-daemon.pid"
PORT="${ORION_OPS_DAEMON_PORT:-8787}"

stopped=0

if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "Stopping Empyralis ops daemon (pid ${pid})"
    kill "${pid}" 2>/dev/null || true
    sleep 0.3
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
    stopped=1
  fi
  rm -f "${PID_FILE}"
fi

pkill -f "scripts/orion_ops_daemon.py" 2>/dev/null || true

stale_pids="$(lsof -ti tcp:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${stale_pids}" ]]; then
  echo "Clearing stale daemon port ${PORT}: $(echo "${stale_pids}" | tr '\n' ' ' | xargs)"
  echo "${stale_pids}" | xargs kill -9 2>/dev/null || true
  stopped=1
fi

if [[ "${stopped}" == "1" ]]; then
  echo "Empyralis ops daemon stopped."
else
  echo "Empyralis ops daemon not running."
fi
