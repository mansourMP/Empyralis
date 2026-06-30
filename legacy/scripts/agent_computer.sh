#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
STACK_DIR="${ROOT_DIR}/.orion-stack"
STATE_DIR="${EMPYRALIS_AGENT_COMPUTER_STATE_DIR:-${STACK_DIR}/agent-computer}"
LOG_DIR="${STACK_DIR}/logs"
PID_DIR="${STACK_DIR}/pids"
ENV_FILE="${STATE_DIR}/agent-computer.env"
SUPERVISOR_PID_FILE="${PID_DIR}/agent-computer-supervisor.pid"
EDGE_PID_FILE="${PID_DIR}/agent-computer-edge.pid"
SUPERVISOR_BIN="${ROOT_DIR}/empyralis-supervisor/target/release/empyralis-supervisor"
EDGE_ENTRY="${ROOT_DIR}/empyralis-gateway/dist/index.js"
NODE_BIN_CANDIDATES=("/opt/homebrew/bin/node" "/usr/local/bin/node" "/usr/bin/node")
SUPERVISOR_URL="${EMPYRALIS_SUPERVISOR_URL:-http://127.0.0.1:7788}"
CONTROL_PLANE_URL="${EMPYRALIS_GATEWAY_API_URL:-http://127.0.0.1:8001/api}"
DISPLAY_NAME="${EMPYRALIS_GATEWAY_DISPLAY_NAME:-$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo "Agent Computer")}"
SERVICE_NAME="${EMPYRALIS_AGENT_COMPUTER_SERVICE_NAME:-ai.empyralis.agent-computer}"

usage() {
  cat <<'EOF'
Agent Computer runtime

Usage:
  scripts/agent_computer.sh install
  scripts/agent_computer.sh start
  scripts/agent_computer.sh stop
  scripts/agent_computer.sh status
  scripts/agent_computer.sh service-install --system [--dry-run]
  scripts/agent_computer.sh service-uninstall --system [--dry-run]
  scripts/agent_computer.sh service-status
  scripts/agent_computer.sh launchd-install
  scripts/agent_computer.sh launchd-uninstall
  scripts/agent_computer.sh launchd-run

Environment:
  EMPYRALIS_GATEWAY_PAIRING_TOKEN   Pairing token from Empyralis, used on first start.
  EMPYRALIS_GATEWAY_TOKEN           Existing paired-device token.
  EMPYRALIS_GATEWAY_API_URL         Control plane URL. Defaults to local dev runtime.
  EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED=1
                                      Start personal channel runtimes in the Gateway. Defaults to 0
                                      so local hardware execution is isolated from channel reconnect loops.
  EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID
                                      Optional workspace id used by status/start to warn when
                                      this computer is paired to a different workspace.
  EMPYRALIS_GATEWAY_ALLOW_WORKSPACE_MISMATCH=1
                                      Diagnostic override. Allows starting the cloud connection
                                      even when stored pairing state belongs to another workspace.
  EMPYRALIS_AGENT_COMPUTER_SERVICE_USER  User for Linux/macOS server service mode.
EOF
}

shell_quote() {
  printf "%q" "$1"
}

generate_secret() {
  python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
}

mkdirs() {
  mkdir -p "${STATE_DIR}" "${LOG_DIR}" "${PID_DIR}"
}

host_os() {
  if [[ -n "${EMPYRALIS_AGENT_COMPUTER_TEST_UNAME:-}" ]]; then
    echo "${EMPYRALIS_AGENT_COMPUTER_TEST_UNAME}"
    return 0
  fi
  uname -s
}

has_arg() {
  local target="$1"
  shift || true
  local arg
  for arg in "$@"; do
    [[ "${arg}" == "${target}" ]] && return 0
  done
  return 1
}

dry_run_requested() {
  [[ "${EMPYRALIS_AGENT_COMPUTER_DRY_RUN:-}" == "1" ]] || has_arg "--dry-run" "$@"
}

require_system_flag() {
  if ! has_arg "--system" "$@"; then
    echo "[Agent Computer] Refusing service command without --system."
    echo "This path is for Server/VPS service mode only; desktop auto-start uses launchd-install/user-session mode."
    exit 2
  fi
}

validate_service_name() {
  if [[ ! "${SERVICE_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "[Agent Computer] Invalid service name. Use only letters, numbers, dot, underscore, and dash."
    exit 2
  fi
}

service_user() {
  local user="${EMPYRALIS_AGENT_COMPUTER_SERVICE_USER:-${SUDO_USER:-$(id -un 2>/dev/null || echo root)}}"
  echo "${user}"
}

validate_service_user() {
  local user="$1"
  if [[ ! "${user}" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]]; then
    echo "[Agent Computer] Invalid service user. Use a local account name without path or control characters."
    exit 2
  fi
}

require_root() {
  if [[ "$(id -u)" != "0" ]]; then
    echo "[Agent Computer] ${1:-This service command} must be run as root."
    exit 1
  fi
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  printf "%s" "${value}"
}

systemd_unit_path() {
  printf "/etc/systemd/system/%s.service" "${SERVICE_NAME}"
}

launchdaemon_plist_path() {
  printf "/Library/LaunchDaemons/%s.plist" "${SERVICE_NAME}"
}

load_env_if_present() {
  if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
  fi
}

write_env() {
  mkdirs
  load_env_if_present
  local secret="${EMPYRALIS_SUPERVISOR_SECRET:-}"
  if [[ -z "${secret}" ]]; then
    secret="$(generate_secret)"
  fi
  local state_dir="${EMPYRALIS_GATEWAY_STATE_DIR:-${STATE_DIR}/edge}"
  local personal_channels_enabled="${EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED:-0}"
  cat > "${ENV_FILE}" <<EOF
export EMPYRALIS_SUPERVISOR_SECRET=$(shell_quote "${secret}")
export EMPYRALIS_SUPERVISOR_URL=$(shell_quote "${SUPERVISOR_URL}")
export EMPYRALIS_SUPERVISOR_AUDIT_DB=$(shell_quote "${STATE_DIR}/supervisor-audit.sqlite3")
export EMPYRALIS_GATEWAY_API_URL=$(shell_quote "${CONTROL_PLANE_URL}")
export EMPYRALIS_GATEWAY_STATE_DIR=$(shell_quote "${state_dir}")
export EMPYRALIS_GATEWAY_DISPLAY_NAME=$(shell_quote "${DISPLAY_NAME}")
export EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT=$(shell_quote "${ROOT_DIR}")
export EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED=$(shell_quote "${personal_channels_enabled}")
EOF
  if [[ -n "${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}" ]]; then
    echo "export EMPYRALIS_GATEWAY_PAIRING_TOKEN=$(shell_quote "${EMPYRALIS_GATEWAY_PAIRING_TOKEN}")" >> "${ENV_FILE}"
  fi
  if [[ -n "${EMPYRALIS_GATEWAY_TOKEN:-}" ]]; then
    echo "export EMPYRALIS_GATEWAY_TOKEN=$(shell_quote "${EMPYRALIS_GATEWAY_TOKEN}")" >> "${ENV_FILE}"
  fi
  if [[ -n "${EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE:-}" ]]; then
    echo "export EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE=$(shell_quote "${EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE}")" >> "${ENV_FILE}"
  fi
  if [[ -n "${EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET:-}" ]]; then
    echo "export EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET=$(shell_quote "${EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET}")" >> "${ENV_FILE}"
  fi
  if [[ -n "${EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID:-}" ]]; then
    echo "export EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID=$(shell_quote "${EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID}")" >> "${ENV_FILE}"
  fi
  local telegram_env_name
  for telegram_env_name in EMPYRALIS_TELEGRAM_API_ID EMPYRALIS_TELEGRAM_API_HASH TELEGRAM_API_ID TELEGRAM_API_HASH; do
    local telegram_env_value="${!telegram_env_name-}"
    if [[ -z "${telegram_env_value}" ]]; then
      telegram_env_value="$(dotenv_value "${telegram_env_name}" || true)"
    fi
    if [[ -n "${telegram_env_value}" ]]; then
      echo "export ${telegram_env_name}=$(shell_quote "${telegram_env_value}")" >> "${ENV_FILE}"
    fi
  done
  chmod 600 "${ENV_FILE}" 2>/dev/null || true
}

dotenv_value() {
  local target="$1"
  local file line key value
  for file in "${ROOT_DIR}/.env.local" "${ROOT_DIR}/.env"; do
    [[ -f "${file}" ]] || continue
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -z "${line}" || "${line}" == \#* ]] && continue
      if [[ "${line}" == export[[:space:]]* ]]; then
        line="${line#export }"
        line="${line#"${line%%[![:space:]]*}"}"
      fi
      [[ "${line}" == *=* ]] || continue
      key="${line%%=*}"
      value="${line#*=}"
      key="${key%"${key##*[![:space:]]}"}"
      [[ "${key}" == "${target}" ]] || continue
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
      fi
      printf "%s" "${value}"
      return 0
    done < "${file}"
  done
  return 1
}

load_runtime_env() {
  write_env
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
}

is_pid_alive() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

pid_from_file() {
  local file="$1"
  [[ -f "${file}" ]] && cat "${file}" 2>/dev/null || true
}

runtime_process_pid() {
  local file="$1"
  local pattern="${2:-}"
  local command_pattern="${3:-.*}"
  local pid
  pid="$(pid_from_file "${file}")"
  if is_pid_alive "${pid}"; then
    echo "${pid}"
    return 0
  fi
  if [[ -n "${pattern}" ]]; then
    unmanaged_process_pids "${pattern}" "${file}" "${command_pattern}" | head -n 1
  fi
}

runtime_env_secret_hash() {
  python3 - <<'PY'
import hashlib
import os
import sys

secret = str(os.environ.get("EMPYRALIS_SUPERVISOR_SECRET") or "").strip()
if not secret:
    sys.exit(1)
print(hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12])
PY
}

runtime_process_secret_hash() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 1
  python3 - "${pid}" <<'PY'
import hashlib
import re
import subprocess
import sys

pid = sys.argv[1]
try:
    output = subprocess.check_output(
        ["ps", "eww", "-p", pid, "-o", "command="],
        text=True,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    sys.exit(1)
match = re.search(r"EMPYRALIS_SUPERVISOR_SECRET=([^\s]+)", output)
if not match:
    sys.exit(1)
print(hashlib.sha256(match.group(1).encode("utf-8")).hexdigest()[:12])
PY
}

runtime_process_secret_matches_env() {
  local pid="${1:-}" env_hash process_hash
  [[ -n "${pid}" ]] || return 1
  env_hash="$(runtime_env_secret_hash 2>/dev/null || true)"
  process_hash="$(runtime_process_secret_hash "${pid}" 2>/dev/null || true)"
  [[ -n "${env_hash}" && -n "${process_hash}" && "${env_hash}" == "${process_hash}" ]]
}

runtime_secret_status() {
  local env_hash supervisor_pid edge_pid supervisor_hash edge_hash
  env_hash="$(runtime_env_secret_hash 2>/dev/null || true)"
  supervisor_pid="$(runtime_process_pid "${SUPERVISOR_PID_FILE}" "${SUPERVISOR_BIN#${ROOT_DIR}/}" "${SUPERVISOR_BIN#${ROOT_DIR}/}$" || true)"
  edge_pid="$(runtime_process_pid "${EDGE_PID_FILE}" "${EDGE_ENTRY#${ROOT_DIR}/}" "${EDGE_ENTRY#${ROOT_DIR}/}$" || true)"
  supervisor_hash="$(runtime_process_secret_hash "${supervisor_pid}" 2>/dev/null || true)"
  edge_hash="$(runtime_process_secret_hash "${edge_pid}" 2>/dev/null || true)"
  if [[ -z "${env_hash}" || -z "${supervisor_hash}" || -z "${edge_hash}" ]]; then
    echo "secret_check: unavailable"
    return 0
  fi
  if [[ "${env_hash}" == "${supervisor_hash}" && "${env_hash}" == "${edge_hash}" ]]; then
    echo "secret_check: ok"
  else
    echo "secret_check: mismatch (restart Agent Computer; gateway and supervisor must share ${ENV_FILE})"
  fi
}

collect_descendants() {
  local parent="$1"
  local children
  children="$(pgrep -P "${parent}" 2>/dev/null || true)"
  [[ -z "${children}" ]] && return 0
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
    kill "${pids[$idx]}" 2>/dev/null || true
  done
  kill "${root_pid}" 2>/dev/null || true
  sleep 0.3
  for ((idx=${#pids[@]}-1; idx>=0; idx--)); do
    kill -9 "${pids[$idx]}" 2>/dev/null || true
  done
  kill -9 "${root_pid}" 2>/dev/null || true
}

resolve_node_bin() {
  local configured="${EMPYRALIS_AGENT_COMPUTER_NODE_BIN:-}"
  if [[ -n "${configured}" && -x "${configured}" ]]; then
    echo "${configured}"
    return 0
  fi
  local path_node
  path_node="$(command -v node 2>/dev/null || true)"
  if [[ -n "${path_node}" && -x "${path_node}" ]]; then
    echo "${path_node}"
    return 0
  fi
  local candidate
  for candidate in "${NODE_BIN_CANDIDATES[@]}"; do
    if [[ -x "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

ensure_runtime_dirs_safe_for_root_chown() {
  local stack_real
  stack_real="$(cd "${STACK_DIR}" && pwd -P)"
  local dir dir_real
  for dir in "${STATE_DIR}" "${LOG_DIR}" "${PID_DIR}"; do
    if [[ -L "${dir}" ]]; then
      echo "[Agent Computer] Refusing to chown symlinked runtime path: ${dir}"
      exit 2
    fi
    dir_real="$(cd "${dir}" && pwd -P)"
    case "${dir_real}" in
      "${stack_real}"|"${stack_real}"/*) ;;
      *)
        if [[ "${EMPYRALIS_AGENT_COMPUTER_ALLOW_EXTERNAL_STATE_CHOWN:-}" != "1" ]]; then
          echo "[Agent Computer] Refusing to chown runtime path outside ${STACK_DIR}: ${dir}"
          echo "[Agent Computer] Set EMPYRALIS_AGENT_COMPUTER_ALLOW_EXTERNAL_STATE_CHOWN=1 only for an intentional server-owned state directory."
          exit 2
        fi
        ;;
    esac
  done
}

install_runtime() {
  mkdirs
  write_env
  echo "[Agent Computer] Installing local runtime dependencies..."
  (cd "${ROOT_DIR}/empyralis-gateway" && npm install && npm run build)
  (cd "${ROOT_DIR}/empyralis-supervisor" && cargo build --release)
  echo "[Agent Computer] Installed."
  echo "Config: ${ENV_FILE}"
  echo "Start:  scripts/agent_computer.sh start"
  echo "Status: scripts/agent_computer.sh status"
}

ensure_installed() {
  if [[ ! -f "${EDGE_ENTRY}" || ! -x "${SUPERVISOR_BIN}" ]]; then
    echo "[Agent Computer] Runtime is not built yet. Run:"
    echo "  scripts/agent_computer.sh install"
    exit 1
  fi
}

wait_for_supervisor() {
  for _ in $(seq 1 30); do
    if curl -fsS "${SUPERVISOR_URL}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

start_supervisor() {
  local pid
  pid="$(pid_from_file "${SUPERVISOR_PID_FILE}")"
  if is_pid_alive "${pid}"; then
    if ! runtime_process_secret_matches_env "${pid}"; then
      echo "[Agent Computer] Local runner secret differs from ${ENV_FILE}; restarting local runner."
      kill_tree "${pid}" || true
      rm -f "${SUPERVISOR_PID_FILE}"
    else
      echo "[Agent Computer] Local runner already running (pid ${pid})."
      return 0
    fi
  fi
  pid="$(runtime_process_pid "${SUPERVISOR_PID_FILE}" "${SUPERVISOR_BIN#${ROOT_DIR}/}" "${SUPERVISOR_BIN#${ROOT_DIR}/}$" || true)"
  if is_pid_alive "${pid}"; then
    if ! runtime_process_secret_matches_env "${pid}"; then
      echo "[Agent Computer] Existing local runner secret differs from ${ENV_FILE}; restarting local runner."
      kill_tree "${pid}" || true
      rm -f "${SUPERVISOR_PID_FILE}"
    else
      echo "[Agent Computer] Local runner already running (pid ${pid})."
      echo "${pid}" > "${SUPERVISOR_PID_FILE}"
      chmod 600 "${SUPERVISOR_PID_FILE}" 2>/dev/null || true
      return 0
    fi
  fi
  pid="$(pid_from_file "${SUPERVISOR_PID_FILE}")"
  if is_pid_alive "${pid}"; then
    echo "[Agent Computer] Local runner already running (pid ${pid})."
    return 0
  fi
  rm -f "${SUPERVISOR_PID_FILE}"
  if wait_for_supervisor; then
    echo "[Agent Computer] Local runner health is reachable at ${SUPERVISOR_URL}, but process ownership could not be verified."
    echo "[Agent Computer] Stop the existing process or start with the matching ${ENV_FILE} before launching the cloud connection."
    return 1
  fi
  echo "[Agent Computer] Starting local runner..."
  nohup env \
    EMPYRALIS_SUPERVISOR_SECRET="${EMPYRALIS_SUPERVISOR_SECRET}" \
    EMPYRALIS_SUPERVISOR_AUDIT_DB="${EMPYRALIS_SUPERVISOR_AUDIT_DB}" \
    "${SUPERVISOR_BIN}" \
    > "${LOG_DIR}/agent-computer-supervisor.log" 2>&1 &
  echo "$!" > "${SUPERVISOR_PID_FILE}"
  chmod 600 "${SUPERVISOR_PID_FILE}" 2>/dev/null || true
  if ! wait_for_supervisor; then
    echo "[Agent Computer] Local runner failed to become healthy."
    echo "Log: ${LOG_DIR}/agent-computer-supervisor.log"
    exit 1
  fi
}

has_pairing_material() {
  if [[ -n "${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}" || -n "${EMPYRALIS_GATEWAY_TOKEN:-}" ]]; then
    return 0
  fi
  if [[ -f "${EMPYRALIS_GATEWAY_STATE_DIR}/tokens.json" ]]; then
    return 0
  fi
  return 1
}

stored_gateway_workspace_id() {
  local state_dir="${EMPYRALIS_GATEWAY_STATE_DIR:-${STATE_DIR}/edge}"
  python3 - "${state_dir}" <<'PY'
import json
import pathlib
import sys

state_dir = pathlib.Path(sys.argv[1])

def load_json(name: str) -> dict:
    try:
        payload = json.loads((state_dir / name).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

identity = load_json("identity.json")
registration = load_json("registration.json")
workspace_id = str(identity.get("workspaceId") or registration.get("workspace_id") or "").strip()
if workspace_id:
    print(workspace_id)
PY
}

ensure_expected_gateway_workspace() {
  local expected_workspace="${EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID:-}"
  if [[ -z "${expected_workspace}" || "${EMPYRALIS_GATEWAY_ALLOW_WORKSPACE_MISMATCH:-}" == "1" ]]; then
    return 0
  fi
  if [[ -n "${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}" ]]; then
    return 0
  fi
  local actual_workspace
  actual_workspace="$(stored_gateway_workspace_id || true)"
  if [[ -n "${actual_workspace}" && "${actual_workspace}" != "${expected_workspace}" ]]; then
    echo "[Agent Computer] Refusing to start cloud connection for the wrong workspace."
    echo "[Agent Computer] Expected workspace: ${expected_workspace}"
    echo "[Agent Computer] Stored pairing workspace: ${actual_workspace}"
    echo "[Agent Computer] Re-pair this computer from the active workspace, or set EMPYRALIS_GATEWAY_ALLOW_WORKSPACE_MISMATCH=1 only for diagnostics."
    return 1
  fi
  return 0
}

start_edge() {
  local pid
  pid="$(pid_from_file "${EDGE_PID_FILE}")"
  if is_pid_alive "${pid}"; then
    if ! runtime_process_secret_matches_env "${pid}"; then
      echo "[Agent Computer] Cloud connection secret differs from ${ENV_FILE}; restarting cloud connection."
      kill_tree "${pid}" || true
      rm -f "${EDGE_PID_FILE}"
    else
      echo "[Agent Computer] Cloud connection already running (pid ${pid})."
      return 0
    fi
  fi
  pid="$(runtime_process_pid "${EDGE_PID_FILE}" "${EDGE_ENTRY#${ROOT_DIR}/}" "${EDGE_ENTRY#${ROOT_DIR}/}$" || true)"
  if is_pid_alive "${pid}"; then
    if ! runtime_process_secret_matches_env "${pid}"; then
      echo "[Agent Computer] Existing cloud connection secret differs from ${ENV_FILE}; restarting cloud connection."
      kill_tree "${pid}" || true
      rm -f "${EDGE_PID_FILE}"
    else
      echo "[Agent Computer] Cloud connection already running (pid ${pid})."
      echo "${pid}" > "${EDGE_PID_FILE}"
      chmod 600 "${EDGE_PID_FILE}" 2>/dev/null || true
      return 0
    fi
  fi
  rm -f "${EDGE_PID_FILE}"
  if ! has_pairing_material; then
    echo "[Agent Computer] Local runner is ready, but this computer is not paired yet."
    echo "Pair it from Empyralis, then rerun:"
    echo "  EMPYRALIS_GATEWAY_PAIRING_TOKEN=... scripts/agent_computer.sh start"
    return 0
  fi
  if ! ensure_expected_gateway_workspace; then
    echo "[Agent Computer] Cloud connection was not started."
    return 2
  fi
  local node_bin
  if ! node_bin="$(resolve_node_bin)"; then
    echo "[Agent Computer] Node.js was not found. Install Node.js or set EMPYRALIS_AGENT_COMPUTER_NODE_BIN."
    exit 1
  fi
  echo "[Agent Computer] Starting cloud connection..."
  nohup env \
    EMPYRALIS_SUPERVISOR_SECRET="${EMPYRALIS_SUPERVISOR_SECRET}" \
    EMPYRALIS_SUPERVISOR_URL="${EMPYRALIS_SUPERVISOR_URL}" \
    EMPYRALIS_GATEWAY_API_URL="${EMPYRALIS_GATEWAY_API_URL}" \
    EMPYRALIS_GATEWAY_STATE_DIR="${EMPYRALIS_GATEWAY_STATE_DIR}" \
    EMPYRALIS_GATEWAY_DISPLAY_NAME="${EMPYRALIS_GATEWAY_DISPLAY_NAME}" \
    EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT="${EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT}" \
    EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED="${EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED}" \
    EMPYRALIS_GATEWAY_PAIRING_TOKEN="${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}" \
    EMPYRALIS_GATEWAY_TOKEN="${EMPYRALIS_GATEWAY_TOKEN:-}" \
    "${node_bin}" "${EDGE_ENTRY}" \
    > "${LOG_DIR}/agent-computer-edge.log" 2>&1 &
  echo "$!" > "${EDGE_PID_FILE}"
  chmod 600 "${EDGE_PID_FILE}" 2>/dev/null || true
}

start_runtime() {
  ensure_installed
  load_runtime_env
  start_supervisor
  if ! start_edge; then
    status_runtime || true
    exit 2
  fi
  status_runtime
}

stop_pid_file() {
  local label="$1"
  local file="$2"
  local pid
  pid="$(pid_from_file "${file}")"
  if is_pid_alive "${pid}"; then
    echo "[Agent Computer] Stopping ${label} (pid ${pid})..."
    kill_tree "${pid}"
  fi
  rm -f "${file}"
}

stop_runtime() {
  stop_pid_file "cloud connection" "${EDGE_PID_FILE}"
  stop_pid_file "local runner" "${SUPERVISOR_PID_FILE}"
  stop_unmanaged_processes "cloud connection" "${EDGE_ENTRY#${ROOT_DIR}/}" "${EDGE_PID_FILE}" "${EDGE_ENTRY#${ROOT_DIR}/}$"
  stop_unmanaged_processes "local runner" "${SUPERVISOR_BIN#${ROOT_DIR}/}" "${SUPERVISOR_PID_FILE}" "${SUPERVISOR_BIN#${ROOT_DIR}/}$"
  echo "[Agent Computer] Stopped."
}

status_line() {
  local label="$1"
  local file="$2"
  local pattern="${3:-}"
  local command_pattern="${4:-.*}"
  local pid
  pid="$(pid_from_file "${file}")"
  if is_pid_alive "${pid}"; then
    echo "${label}: running pid=${pid}"
  elif [[ -n "${pattern}" ]]; then
    local matches
    matches="$(unmanaged_process_pids "${pattern}" "${file}" "${command_pattern}" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    if [[ -n "${matches}" ]]; then
      echo "${label}: running unmanaged pid=${matches}"
    else
      echo "${label}: stopped"
    fi
  else
    echo "${label}: stopped"
  fi
}

unmanaged_process_status_line() {
  local label="$1"
  local pattern="$2"
  local managed_file="$3"
  local command_pattern="${4:-.*}"
  local managed_pid
  managed_pid="$(pid_from_file "${managed_file}")"
  if is_pid_alive "${managed_pid}"; then
    return 0
  fi
  local matches
  matches="$(
    ps -axo pid=,comm=,args= \
      | awk -v runtime_pattern="${pattern}" -v command_pattern="${command_pattern}" '
          index($0, runtime_pattern) > 0 && $0 ~ command_pattern { print $1 }
        ' \
      | tr '\n' ' ' \
      | sed 's/[[:space:]]*$//'
  )"
  if [[ -n "${matches}" ]]; then
    echo "${label}_unmanaged: running pid=${matches}"
  fi
}

unmanaged_process_pids() {
  local pattern="$1"
  local managed_file="$2"
  local command_pattern="${3:-.*}"
  local managed_pid
  managed_pid="$(pid_from_file "${managed_file}")"
  ps -axo pid=,comm=,args= \
    | awk -v runtime_pattern="${pattern}" -v command_pattern="${command_pattern}" -v managed_pid="${managed_pid}" '
        index($0, runtime_pattern) > 0 && $0 ~ command_pattern {
          pid=$1
          if (managed_pid == "" || pid != managed_pid) {
            print pid
          }
        }
      '
}

stop_unmanaged_processes() {
  local label="$1"
  local pattern="$2"
  local managed_file="$3"
  local command_pattern="${4:-.*}"
  local pid
  while IFS= read -r pid; do
    [[ -z "${pid}" ]] && continue
    if is_pid_alive "${pid}"; then
      echo "[Agent Computer] Stopping unmanaged ${label} (pid ${pid})..."
      kill_tree "${pid}"
    fi
  done < <(unmanaged_process_pids "${pattern}" "${managed_file}" "${command_pattern}")
}

gateway_scope_status() {
  local state_dir="${EMPYRALIS_GATEWAY_STATE_DIR:-${STATE_DIR}/edge}"
  local expected_workspace="${EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID:-}"
  python3 - "${state_dir}" "${expected_workspace}" <<'PY'
import json
import pathlib
import sys

state_dir = pathlib.Path(sys.argv[1])
expected_workspace = str(sys.argv[2] or "").strip()

def load_json(name: str) -> dict:
    path = state_dir / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {"_unreadable": str(path)}
    return payload if isinstance(payload, dict) else {}

identity = load_json("identity.json")
registration = load_json("registration.json")
gateway_id = str(identity.get("gatewayId") or registration.get("gateway_id") or "").strip()
device_id = str(identity.get("deviceId") or registration.get("device_id") or "").strip()
tenant_id = str(identity.get("tenantId") or registration.get("tenant_id") or "").strip()
workspace_id = str(identity.get("workspaceId") or registration.get("workspace_id") or "").strip()
display_name = str(registration.get("display_name") or "").strip()

if not gateway_id and not device_id and not workspace_id:
    print(f"gateway_scope: unpaired ({state_dir})")
else:
    parts = [
        f"workspace={workspace_id or 'unknown'}",
        f"tenant={tenant_id or 'unknown'}",
        f"gateway={gateway_id or 'unknown'}",
        f"device={device_id or 'unknown'}",
    ]
    if display_name:
        parts.append(f"name={display_name}")
    print("gateway_scope: " + " ".join(parts))
    if expected_workspace and workspace_id and expected_workspace != workspace_id:
        print(f"gateway_scope_warning: expected workspace {expected_workspace}, but this Agent Computer is paired to {workspace_id}")
PY
}

status_runtime() {
  load_env_if_present
  echo "== Agent Computer status =="
  status_line "local_runner" "${SUPERVISOR_PID_FILE}" "${SUPERVISOR_BIN#${ROOT_DIR}/}" "${SUPERVISOR_BIN#${ROOT_DIR}/}$"
  status_line "cloud_connection" "${EDGE_PID_FILE}" "${EDGE_ENTRY#${ROOT_DIR}/}" "${EDGE_ENTRY#${ROOT_DIR}/}$"
  gateway_scope_status
  if curl -fsS "${SUPERVISOR_URL}/health" >/dev/null 2>&1; then
    echo "health: local runner ok (${SUPERVISOR_URL})"
  else
    echo "health: local runner unavailable (${SUPERVISOR_URL})"
  fi
  runtime_secret_status
  echo "config: ${ENV_FILE}"
  echo "logs: ${LOG_DIR}/agent-computer-supervisor.log, ${LOG_DIR}/agent-computer-edge.log"
}

render_systemd_unit() {
  local user="$1"
  cat <<EOF
[Unit]
Description=Empyralis Agent Computer Server Runtime
Documentation=https://github.com/empyralis/empyralis
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${ROOT_DIR}
Environment=EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE=1
Environment=EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET=server_vps
ExecStart=/bin/bash ${ROOT_DIR}/scripts/agent_computer.sh service-run --system
ExecStop=/bin/bash ${ROOT_DIR}/scripts/agent_computer.sh stop
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
}

render_launchdaemon_plist() {
  local user="$1"
  local service_name_xml root_dir_xml log_path_xml user_xml
  service_name_xml="$(xml_escape "${SERVICE_NAME}")"
  root_dir_xml="$(xml_escape "${ROOT_DIR}")"
  log_path_xml="$(xml_escape "${LOG_DIR}/agent-computer-system.log")"
  user_xml="$(xml_escape "${user}")"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${service_name_xml}</string>
  <key>UserName</key>
  <string>${user_xml}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${root_dir_xml}/scripts/agent_computer.sh</string>
    <string>service-run</string>
    <string>--system</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${root_dir_xml}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE</key>
    <string>1</string>
    <key>EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET</key>
    <string>server_vps</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${log_path_xml}</string>
  <key>StandardErrorPath</key>
  <string>${log_path_xml}</string>
</dict>
</plist>
EOF
}

service_install_systemd() {
  local user
  validate_service_name
  user="$(service_user)"
  validate_service_user "${user}"
  if dry_run_requested "$@"; then
    render_systemd_unit "${user}"
    return 0
  fi
  require_root "service-install --system"
  ensure_installed
  mkdirs
  write_env
  ensure_runtime_dirs_safe_for_root_chown
  chown -R "${user}" "${STATE_DIR}" "${LOG_DIR}" "${PID_DIR}" 2>/dev/null || true
  local unit_path
  unit_path="$(systemd_unit_path)"
  render_systemd_unit "${user}" > "${unit_path}"
  chmod 644 "${unit_path}"
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"
  echo "[Agent Computer] Installed Linux system service: ${unit_path}"
  echo "[Agent Computer] Status: scripts/agent_computer.sh service-status"
}

service_uninstall_systemd() {
  validate_service_name
  if dry_run_requested "$@"; then
    echo "systemctl disable --now ${SERVICE_NAME}"
    echo "rm -f $(systemd_unit_path)"
    return 0
  fi
  require_root "service-uninstall --system"
  systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
  rm -f "$(systemd_unit_path)"
  systemctl daemon-reload
  echo "[Agent Computer] Removed Linux system service."
}

service_install_launchdaemon_system() {
  local user
  validate_service_name
  user="$(service_user)"
  validate_service_user "${user}"
  if ! has_arg "--server-mac" "$@" && [[ "${EMPYRALIS_AGENT_COMPUTER_ALLOW_MAC_SYSTEM_SERVICE:-}" != "1" ]]; then
    echo "[Agent Computer] Refusing macOS system LaunchDaemon install by default."
    echo "Desktop Macs must use launchd-install user-session mode until the user-session bridge is implemented."
    echo "For a server-style Mac, rerun with --server-mac after accepting that desktop capabilities will stay blocked."
    exit 2
  fi
  if dry_run_requested "$@"; then
    render_launchdaemon_plist "${user}"
    return 0
  fi
  require_root "macOS service-install --system --server-mac"
  ensure_installed
  mkdirs
  write_env
  ensure_runtime_dirs_safe_for_root_chown
  chown -R "${user}" "${STATE_DIR}" "${LOG_DIR}" "${PID_DIR}" 2>/dev/null || true
  local plist_path
  plist_path="$(launchdaemon_plist_path)"
  render_launchdaemon_plist "${user}" > "${plist_path}"
  chmod 644 "${plist_path}"
  launchctl bootout "system/${SERVICE_NAME}" >/dev/null 2>&1 || true
  launchctl bootstrap system "${plist_path}"
  launchctl enable "system/${SERVICE_NAME}"
  launchctl kickstart -k "system/${SERVICE_NAME}"
  echo "[Agent Computer] Installed macOS server LaunchDaemon: ${plist_path}"
}

service_uninstall_launchdaemon_system() {
  validate_service_name
  if dry_run_requested "$@"; then
    echo "launchctl bootout system/${SERVICE_NAME}"
    echo "rm -f $(launchdaemon_plist_path)"
    return 0
  fi
  require_root "macOS service-uninstall --system"
  launchctl bootout "system/${SERVICE_NAME}" >/dev/null 2>&1 || true
  launchctl disable "system/${SERVICE_NAME}" >/dev/null 2>&1 || true
  rm -f "$(launchdaemon_plist_path)"
  echo "[Agent Computer] Removed macOS server LaunchDaemon."
}

service_install() {
  require_system_flag "$@"
  case "$(host_os)" in
    Linux) service_install_systemd "$@" ;;
    Darwin) service_install_launchdaemon_system "$@" ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      echo "[Agent Computer] Windows Service install is provided through the PowerShell service script, not this bash path."
      echo "Use: scripts/install_agent_computer_windows_service.ps1 -Action install"
      exit 2
      ;;
    *)
      echo "[Agent Computer] Unsupported system service host: $(host_os)"
      exit 2
      ;;
  esac
}

service_uninstall() {
  require_system_flag "$@"
  case "$(host_os)" in
    Linux) service_uninstall_systemd "$@" ;;
    Darwin) service_uninstall_launchdaemon_system "$@" ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      echo "[Agent Computer] Windows Service uninstall is provided through the PowerShell service script."
      echo "Use: scripts/install_agent_computer_windows_service.ps1 -Action uninstall"
      exit 2
      ;;
    *)
      echo "[Agent Computer] Unsupported system service host: $(host_os)"
      exit 2
      ;;
  esac
}

service_status() {
  validate_service_name
  echo "== Agent Computer service status =="
  case "$(host_os)" in
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        systemctl status --no-pager "${SERVICE_NAME}" || true
      else
        echo "systemctl: unavailable"
      fi
      ;;
    Darwin)
      launchctl print "system/${SERVICE_NAME}" 2>/dev/null || echo "LaunchDaemon: not loaded"
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      echo "Use: scripts/install_agent_computer_windows_service.ps1 -Action status"
      ;;
    *)
      echo "Unsupported service host: $(host_os)"
      ;;
  esac
  status_runtime
}

service_supervisor_pid=""
service_edge_pid=""

cleanup_service_run() {
  local pid
  for pid in "${service_edge_pid:-}" "${service_supervisor_pid:-}"; do
    if is_pid_alive "${pid}"; then
      kill_tree "${pid}"
    fi
  done
}

start_supervisor_for_service() {
  local mode_label="${1:-service mode}"
  local pid
  pid="$(runtime_process_pid "${SUPERVISOR_PID_FILE}" "${SUPERVISOR_BIN#${ROOT_DIR}/}" "${SUPERVISOR_BIN#${ROOT_DIR}/}$" || true)"
  if is_pid_alive "${pid}"; then
    if ! runtime_process_secret_matches_env "${pid}"; then
      echo "[Agent Computer] Existing local runner secret differs from ${ENV_FILE}; restarting local runner in ${mode_label}."
      kill_tree "${pid}" || true
      rm -f "${SUPERVISOR_PID_FILE}"
    else
      echo "[Agent Computer] Local runner already running (pid ${pid})."
      echo "${pid}" > "${SUPERVISOR_PID_FILE}"
      chmod 600 "${SUPERVISOR_PID_FILE}" 2>/dev/null || true
      return 0
    fi
  fi
  if wait_for_supervisor; then
    echo "[Agent Computer] Local runner health is reachable at ${SUPERVISOR_URL}, but process ownership could not be verified."
    echo "[Agent Computer] Stop the existing process or start with the matching ${ENV_FILE} before launching the cloud connection."
    return 1
  fi
  echo "[Agent Computer] Starting local runner in ${mode_label}..."
  env \
    EMPYRALIS_SUPERVISOR_SECRET="${EMPYRALIS_SUPERVISOR_SECRET}" \
    EMPYRALIS_SUPERVISOR_AUDIT_DB="${EMPYRALIS_SUPERVISOR_AUDIT_DB}" \
    "${SUPERVISOR_BIN}" \
    >> "${LOG_DIR}/agent-computer-supervisor.log" 2>&1 &
  service_supervisor_pid="$!"
  echo "${service_supervisor_pid}" > "${SUPERVISOR_PID_FILE}"
  chmod 600 "${SUPERVISOR_PID_FILE}" 2>/dev/null || true
  if ! wait_for_supervisor; then
    echo "[Agent Computer] Local runner failed to become healthy."
    return 1
  fi
}

configure_edge_service_mode() {
  local system_mode="$1"
  local default_target="$2"
  if [[ -n "${system_mode}" ]]; then
    export EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE="${system_mode}"
  else
    unset EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE
    unset EMPYRALIS_AGENT_COMPUTER_SERVICE_MODE
  fi
  export EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET="${EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET:-${default_target}}"
}

run_edge_forever() {
  local mode_label="$1"
  local default_target="$2"
  local system_mode="${3:-}"
  while true; do
    load_env_if_present
    configure_edge_service_mode "${system_mode}" "${default_target}"
    if ! has_pairing_material; then
      echo "[Agent Computer] ${mode_label} is running, but this computer is not paired yet."
      echo "[Agent Computer] Add EMPYRALIS_GATEWAY_PAIRING_TOKEN or EMPYRALIS_GATEWAY_TOKEN, then the edge will start."
      sleep 30
      continue
    fi
    if ! ensure_expected_gateway_workspace; then
      echo "[Agent Computer] ${mode_label} is holding the local runner online, but cloud connection is blocked by workspace mismatch."
      sleep 30
      continue
    fi

    local existing_pid
    existing_pid="$(pid_from_file "${EDGE_PID_FILE}")"
    if is_pid_alive "${existing_pid}"; then
      echo "[Agent Computer] Cloud connection already running (pid ${existing_pid}); monitoring."
      while is_pid_alive "${existing_pid}"; do
        sleep 5
      done
      rm -f "${EDGE_PID_FILE}"
      echo "[Agent Computer] Cloud connection exited; restarting in 5 seconds."
      sleep 5
      continue
    fi

    local node_bin
    if ! node_bin="$(resolve_node_bin)"; then
      echo "[Agent Computer] Node.js was not found. Install Node.js or set EMPYRALIS_AGENT_COMPUTER_NODE_BIN."
      sleep 30
      continue
    fi
    echo "[Agent Computer] Starting cloud connection in ${mode_label}..."
    env \
      EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET="${EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET:-${default_target}}" \
      EMPYRALIS_SUPERVISOR_SECRET="${EMPYRALIS_SUPERVISOR_SECRET}" \
      EMPYRALIS_SUPERVISOR_URL="${EMPYRALIS_SUPERVISOR_URL}" \
      EMPYRALIS_GATEWAY_API_URL="${EMPYRALIS_GATEWAY_API_URL}" \
      EMPYRALIS_GATEWAY_STATE_DIR="${EMPYRALIS_GATEWAY_STATE_DIR}" \
      EMPYRALIS_GATEWAY_DISPLAY_NAME="${EMPYRALIS_GATEWAY_DISPLAY_NAME}" \
      EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT="${EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT}" \
      EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED="${EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED}" \
      EMPYRALIS_GATEWAY_PAIRING_TOKEN="${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}" \
      EMPYRALIS_GATEWAY_TOKEN="${EMPYRALIS_GATEWAY_TOKEN:-}" \
      "${node_bin}" "${EDGE_ENTRY}" \
      >> "${LOG_DIR}/agent-computer-edge.log" 2>&1 &
    service_edge_pid="$!"
    echo "${service_edge_pid}" > "${EDGE_PID_FILE}"
    chmod 600 "${EDGE_PID_FILE}" 2>/dev/null || true
    wait "${service_edge_pid}" || true
    service_edge_pid=""
    rm -f "${EDGE_PID_FILE}"
    echo "[Agent Computer] Cloud connection exited; restarting in 5 seconds."
    sleep 5
  done
}

service_run_system() {
  require_system_flag "$@"
  ensure_installed
  load_runtime_env
  configure_edge_service_mode "1" "server_vps"
  trap cleanup_service_run INT TERM EXIT
  start_supervisor_for_service "system service mode"
  run_edge_forever "system service mode" "server_vps" "1"
}

render_launchagent_plist() {
  local root_dir_xml log_path_xml
  root_dir_xml="$(xml_escape "${ROOT_DIR}")"
  log_path_xml="$(xml_escape "${LOG_DIR}/agent-computer-launchd.log")"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.empyralis.agent-computer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${root_dir_xml}/scripts/agent_computer.sh</string>
    <string>launchd-run</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${root_dir_xml}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${log_path_xml}</string>
  <key>StandardErrorPath</key>
  <string>${log_path_xml}</string>
</dict>
</plist>
EOF
}

launchd_install() {
  if [[ "$(host_os)" != "Darwin" ]]; then
    echo "launchd-install is only available on macOS."
    exit 1
  fi
  mkdirs
  local plist_dir="${HOME}/Library/LaunchAgents"
  local plist_path="${plist_dir}/ai.empyralis.agent-computer.plist"
  mkdir -p "${plist_dir}"
  if dry_run_requested "$@"; then
    render_launchagent_plist
    return 0
  fi
  render_launchagent_plist > "${plist_path}"
  launchctl bootout "gui/$(id -u)/ai.empyralis.agent-computer" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "${plist_path}"
  launchctl enable "gui/$(id -u)/ai.empyralis.agent-computer"
  launchctl kickstart -k "gui/$(id -u)/ai.empyralis.agent-computer"
  echo "[Agent Computer] Installed macOS auto-start service: ${plist_path}"
}

launchd_uninstall() {
  if [[ "$(host_os)" != "Darwin" ]]; then
    echo "launchd-uninstall is only available on macOS."
    exit 1
  fi
  local service="gui/$(id -u)/ai.empyralis.agent-computer"
  local plist_path="${HOME}/Library/LaunchAgents/ai.empyralis.agent-computer.plist"
  launchctl bootout "${service}" >/dev/null 2>&1 || true
  launchctl disable "${service}" >/dev/null 2>&1 || true
  rm -f "${plist_path}"
  echo "[Agent Computer] Removed macOS auto-start service."
}

launchd_run() {
  if [[ "$(host_os)" != "Darwin" ]]; then
    echo "launchd-run is only available on macOS."
    exit 1
  fi
  ensure_installed
  load_runtime_env
  configure_edge_service_mode "" "this_device"
  trap cleanup_service_run INT TERM EXIT
  start_supervisor_for_service "user-session launchd mode"
  run_edge_forever "user-session launchd mode" "this_device" ""
}

cmd="${1:-}"
case "${cmd}" in
  install) install_runtime ;;
  start) start_runtime ;;
  stop) stop_runtime ;;
  status) status_runtime ;;
  service-install) shift; service_install "$@" ;;
  service-uninstall) shift; service_uninstall "$@" ;;
  service-status) service_status ;;
  service-run) shift; service_run_system "$@" ;;
  launchd-install) shift; launchd_install "$@" ;;
  launchd-uninstall) launchd_uninstall ;;
  launchd-run) launchd_run ;;
  -h|--help|help|"") usage ;;
  *)
    echo "Unknown command: ${cmd}"
    usage
    exit 1
    ;;
esac
