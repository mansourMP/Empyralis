#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_URL="${ORION_DESKTOP_FRONTEND_URL:-http://127.0.0.1:3000}"
RUNTIME_URL="${ORION_DESKTOP_RUNTIME_URL:-http://127.0.0.1:8001/health}"
START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_STACK_SCRIPT}" ]]; then
  START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_orion_local_stack.sh"
fi

if [[ ! -f "${ROOT_DIR}/src-tauri/Cargo.toml" ]]; then
  echo "[error] src-tauri/Cargo.toml not found."
  exit 1
fi

ensure_stack_ready() {
  local frontend_ok runtime_ok
  frontend_ok=0
  runtime_ok=0

  if curl -fsS "${FRONTEND_URL}" >/dev/null 2>&1; then
    frontend_ok=1
  fi
  if curl -fsS "${RUNTIME_URL}" >/dev/null 2>&1; then
    runtime_ok=1
  fi

  if [[ "${frontend_ok}" == "1" && "${runtime_ok}" == "1" ]]; then
    return 0
  fi

  echo "[setup] Local stack is not fully ready. Starting Empyralis services..."
  bash "${START_STACK_SCRIPT}"

  if ! curl -fsS "${FRONTEND_URL}" >/dev/null 2>&1; then
    echo "[error] Frontend did not become ready at ${FRONTEND_URL}"
    echo "        Check: ${ROOT_DIR}/.orion-stack/logs/frontend.log"
    exit 1
  fi
  if ! curl -fsS "${RUNTIME_URL}" >/dev/null 2>&1; then
    echo "[error] Runtime did not become ready at ${RUNTIME_URL}"
    echo "        Check: ${ROOT_DIR}/.orion-stack/logs/runtime.log"
    exit 1
  fi
}

ensure_stack_ready

echo "[run] Launching Empyralis Tauri desktop..."
cd "${ROOT_DIR}"
npm run tauri:dev
