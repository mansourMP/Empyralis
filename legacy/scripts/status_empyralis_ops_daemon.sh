#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
PID_FILE="${STATE_DIR}/pids/ops-daemon.pid"
KEY_FILE="${STATE_DIR}/ops_daemon_key"

HOST="${ORION_OPS_DAEMON_HOST:-127.0.0.1}"
PORT="${ORION_OPS_DAEMON_PORT:-8787}"
URL="http://${HOST}:${PORT}"

pid="-"
alive=0

if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    alive=1
  fi
fi

if [[ "${alive}" == "1" ]]; then
  echo "empyralis_ops_daemon: running pid=${pid} url=${URL}"
else
  echo "empyralis_ops_daemon: stopped url=${URL}"
fi

if [[ -f "${KEY_FILE}" ]]; then
  key="$(cat "${KEY_FILE}" 2>/dev/null || true)"
  if [[ -n "${key}" ]]; then
    fp="${#key}:${key:0:4}...${key: -4}"
    echo "key: ${fp}"
  else
    echo "key: empty"
  fi
else
  echo "key: missing"
fi

health="$(curl -fsS "${URL}/health" 2>/dev/null || true)"
if [[ -n "${health}" ]]; then
  if [[ "${alive}" != "1" ]]; then
    echo "empyralis_ops_daemon: running (health reachable, pid file stale)"
  fi
  echo "${health}" | jq -r '"health: ok=\(.ok) runtime_url=\(.runtime_url) ts=\(.ts)"' 2>/dev/null || true
  echo "${health}" | jq -r '
    .watchdog as $w |
    "watchdog: enabled=\($w.enabled) healthy=\($w.healthy) fails=\($w.consecutive_failures) recoveries_total=\($w.recovery_attempts_total) recoveries_1h=\($w.recovery_attempts_last_hour)"
  ' 2>/dev/null || true
  echo "${health}" | jq -r '
    .watchdog as $w |
    "watchdog_last: probe=\($w.last_probe_at // "-") recovery=\($w.last_recovery_at // "-") reason=\($w.last_unhealthy_reason // "-")"
  ' 2>/dev/null || true
else
  echo "health: unavailable"
fi
