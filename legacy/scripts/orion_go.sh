#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_SCRIPT}" ]]; then
  START_SCRIPT="${ROOT_DIR}/scripts/start_orion_local_stack.sh"
fi
STATUS_SCRIPT="${ROOT_DIR}/scripts/status_empyralis_local_stack.sh"
if [[ ! -x "${STATUS_SCRIPT}" ]]; then
  STATUS_SCRIPT="${ROOT_DIR}/scripts/status_orion_local_stack.sh"
fi
API_URL="${EMPYRALIS_API_URL:-${ORION_API_URL:-http://127.0.0.1:8001}}"
OPS_DAEMON_URL="${ORION_OPS_DAEMON_URL:-http://127.0.0.1:8787}"
WATCH_LINE=0
WATCH_INTERVAL="${WATCH_INTERVAL:-2}"

normalize_runtime_key() {
  printf "%s" "${1:-}" | tr -d '[:space:]'
}

runtime_key_from_file=""
if [[ -f "${ROOT_DIR}/.orion-stack/runtime_key" ]]; then
  runtime_key_from_file="$(normalize_runtime_key "$(cat "${ROOT_DIR}/.orion-stack/runtime_key" 2>/dev/null || true)")"
fi
RUNTIME_KEY_VALUE="$(normalize_runtime_key "${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-${runtime_key_from_file:-}}}}")"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch)
      WATCH_LINE=1
      shift
      ;;
    --watch-interval)
      WATCH_INTERVAL="${2:-2}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: empyralis go [--watch] [--watch-interval N]

Starts Empyralis local stack, runs no-debug checks, and prints a clear status summary.
Options:
  --watch             Start autopilot watch-line after checks
  --watch-interval N  Poll interval seconds for watch-line (default: 2)
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use: empyralis go --help"
      exit 2
      ;;
  esac
done

if [[ ! -x "${START_SCRIPT}" ]]; then
  echo "Missing script: ${START_SCRIPT}"
  exit 1
fi

cd "${ROOT_DIR}"
if [[ -n "${RUNTIME_KEY_VALUE}" ]]; then
  RUNTIME_KEY="${RUNTIME_KEY_VALUE}" bash "${START_SCRIPT}"
else
  bash "${START_SCRIPT}"
  if [[ -f "${ROOT_DIR}/.orion-stack/runtime_key" ]]; then
    RUNTIME_KEY_VALUE="$(normalize_runtime_key "$(cat "${ROOT_DIR}/.orion-stack/runtime_key" 2>/dev/null || true)")"
  fi
fi

if [[ -z "${RUNTIME_KEY_VALUE}" ]]; then
  echo "Failed to resolve runtime key after stack start."
  exit 1
fi

echo
echo "== Empyralis Go Checks =="

HEALTH_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}/health")"
AUTO_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}/channels/telegram/autopilot/status")"
WORKER_JSON="$(curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}/runtime/runtimes/status")"
OPS_HEALTH_JSON="$(curl -fsS "${OPS_DAEMON_URL}/health" 2>/dev/null || echo '{}')"

echo "${HEALTH_JSON}" | jq -r '
  "health: ok=\(.ok) auth_mode=\(.auth_mode) openai_valid=\(.openai_key_valid) memory_ready=\(.memory_manager_ready // false)"
'
echo "${AUTO_JSON}" | jq -r '
  .autopilot as $a |
  "telegram_autopilot: active=\($a.active) thread_alive=\($a.thread_alive) processed=\($a.processed_updates) runs=\($a.runs_started) last_error=\($a.last_error // "-")"
'
echo "${WORKER_JSON}" | jq -r '
  .summary as $s |
  "local_worker: online=\($s.online) busy=\($s.busy) idle=\($s.idle) pending_runs=\($s.pending_runs)"
'
echo "${OPS_HEALTH_JSON}" | jq -r '
  .watchdog as $w |
  "ops_daemon: ok=\(.ok // false) watchdog_enabled=\($w.enabled // false) healthy=\($w.healthy // "-") fails=\($w.consecutive_failures // 0) recoveries=\($w.recovery_attempts_total // 0)"
'

echo
echo "quick_commands:"
echo "  ./bin/empyralis autopilot status"
echo "  ./bin/empyralis autopilot watch-line 2"
echo "  RUNTIME_KEY='${RUNTIME_KEY_VALUE}' bash scripts/show_latest_run.sh"
echo "  bash scripts/telegram_rebind_and_watch.sh"
echo "  bash scripts/status_empyralis_ops_daemon.sh"

if [[ "${WATCH_LINE}" == "1" ]]; then
  echo
  echo "Starting autopilot watch-line (${WATCH_INTERVAL}s). Press Ctrl+C to exit."
  ./bin/empyralis autopilot watch-line "${WATCH_INTERVAL}"
fi
