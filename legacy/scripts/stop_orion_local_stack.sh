#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
PID_DIR="${STATE_DIR}/pids"
START_LOCK_FILE="${STATE_DIR}/start.lock"

collect_descendants() {
  local parent="$1"
  local children
  children="$(pgrep -P "${parent}" 2>/dev/null || true)"
  if [[ -z "${children}" ]]; then
    return 0
  fi
  local child
  for child in ${children}; do
    echo "${child}"
    collect_descendants "${child}"
  done
}

kill_tree() {
  local root_pid="$1"
  local pids=()
  local descendants
  descendants="$(collect_descendants "${root_pid}" || true)"
  if [[ -n "${descendants}" ]]; then
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] && pids+=("${pid}")
    done <<< "${descendants}"
  fi

  local idx
  for ((idx=${#pids[@]}-1; idx>=0; idx--)); do
    if kill -0 "${pids[$idx]}" 2>/dev/null; then
      kill "${pids[$idx]}" 2>/dev/null || true
    fi
  done
  if kill -0 "${root_pid}" 2>/dev/null; then
    kill "${root_pid}" 2>/dev/null || true
  fi

  sleep 0.3

  for ((idx=${#pids[@]}-1; idx>=0; idx--)); do
    if kill -0 "${pids[$idx]}" 2>/dev/null; then
      kill -9 "${pids[$idx]}" 2>/dev/null || true
    fi
  done
  if kill -0 "${root_pid}" 2>/dev/null; then
    kill -9 "${root_pid}" 2>/dev/null || true
  fi
}

stop_pid_file() {
  local name="$1"
  local file="${PID_DIR}/${name}.pid"
  if [[ -f "${file}" ]]; then
    local pid
    pid="$(cat "${file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "Stopping ${name} (pid ${pid})"
      kill_tree "${pid}"
    fi
    rm -f "${file}"
  fi
}

kill_port_if_busy() {
  local name="$1"
  local port="$2"
  local pids
  pids="$(lsof -ti tcp:${port} -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    local compact
    compact="$(echo "${pids}" | tr '\n' ' ' | xargs)"
    echo "Port cleanup for ${name} on ${port}: ${compact}"
    echo "${pids}" | xargs kill -9 2>/dev/null || true
  fi
}

stop_pid_file "worker"
stop_pid_file "frontend"
stop_pid_file "backend"
stop_pid_file "runtime"
stop_pid_file "ops-daemon"

# Cleanup orphan local worker processes (no listening port, so port cleanup won't catch them).
pkill -f "scripts/orion_local_worker.py" 2>/dev/null || true
pkill -f "scripts/run_local_worker.sh" 2>/dev/null || true
pkill -f "scripts/orion_ops_daemon.py" 2>/dev/null || true
# Cleanup orphan frontend dev processes from this repo that can leak file descriptors.
pkill -f "${ROOT_DIR}/frontend/node_modules/.bin/next dev" 2>/dev/null || true

# Final guard: make sure well-known ports are clear even if pid files were stale.
kill_port_if_busy "frontend" 3000
kill_port_if_busy "legacy-backend" 4000
kill_port_if_busy "runtime" 8001
kill_port_if_busy "ops-daemon" 8787

if [[ "${FORCE_PORT_CLEANUP:-0}" == "1" ]]; then
  for port in 8001 4000 3000; do
    pids="$(lsof -ti tcp:${port} 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
      echo "Force cleanup: killing port ${port} (${pids})"
      echo "${pids}" | xargs kill -9 2>/dev/null || true
    fi
  done
fi

rm -f "${START_LOCK_FILE}" 2>/dev/null || true

echo "Empyralis local stack stopped."
