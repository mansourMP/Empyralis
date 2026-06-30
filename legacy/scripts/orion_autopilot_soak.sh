#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
DURATION_SECONDS="${1:-90}"
POLL_SECONDS="${2:-3}"

if ! [[ "${DURATION_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "duration must be integer seconds"
  exit 2
fi
if ! [[ "${POLL_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "poll interval must be integer seconds"
  exit 2
fi
if (( DURATION_SECONDS < 10 )); then
  echo "duration must be >= 10 seconds"
  exit 2
fi
if (( POLL_SECONDS < 1 )); then
  echo "poll interval must be >= 1 second"
  exit 2
fi

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

fetch_tg_json() {
  curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}/channels/telegram/autopilot/status" 2>/dev/null || echo '{}'
}

echo "== Empyralis Autopilot Soak =="
echo "duration=${DURATION_SECONDS}s poll=${POLL_SECONDS}s api=${API_URL}"

start_ts="$(date +%s)"
end_ts=$((start_ts + DURATION_SECONDS))

baseline_errors=-1
last_processed=-1
last_runs=-1

while true; do
  now_ts="$(date +%s)"
  if (( now_ts >= end_ts )); then
    break
  fi

  TG_JSON="$(fetch_tg_json)"
  metrics="$(TG_JSON="${TG_JSON}" python3 - <<'PY'
import json
import os
raw = os.environ.get("TG_JSON", "{}")
try:
    d = json.loads(raw or "{}")
except Exception:
    d = {}
auto = d.get("autopilot") if isinstance(d.get("autopilot"), dict) else {}
fields = {
    "enabled": bool(auto.get("enabled")),
    "active": bool(auto.get("active")),
    "thread": bool(auto.get("thread_alive")),
    "connectors": int(auto.get("connectors_seen") or 0),
    "processed": int(auto.get("processed_updates") or 0),
    "runs": int(auto.get("runs_started") or 0),
    "error_count": int(auto.get("error_count") or 0),
    "consecutive": int(auto.get("consecutive_errors") or 0),
    "last_error": str(auto.get("last_error") or "").replace("\n", " "),
}
print("{enabled}|{active}|{thread}|{connectors}|{processed}|{runs}|{error_count}|{consecutive}|{last_error}".format(**fields))
PY
)"

  IFS='|' read -r enabled active thread_alive connectors processed runs error_count consecutive last_error <<<"${metrics}"

  if [[ "${enabled}" != "True" ]]; then
    echo "FAIL: telegram autopilot is not enabled"
    exit 3
  fi
  if [[ "${active}" != "True" || "${thread_alive}" != "True" ]]; then
    echo "FAIL: telegram autopilot thread is not healthy (active=${active} thread_alive=${thread_alive})"
    exit 3
  fi
  if (( connectors < 1 )); then
    echo "FAIL: no telegram connectors bound"
    exit 3
  fi

  if (( baseline_errors < 0 )); then
    baseline_errors=${error_count}
  fi
  if (( error_count > baseline_errors + 2 )); then
    echo "FAIL: autopilot errors increased during soak (baseline=${baseline_errors} current=${error_count})"
    exit 4
  fi
  if (( consecutive >= 3 )); then
    echo "FAIL: autopilot consecutive errors reached ${consecutive}: ${last_error:--}"
    exit 4
  fi

  if (( last_processed >= 0 && processed < last_processed )); then
    echo "FAIL: processed_updates decreased (${last_processed} -> ${processed})"
    exit 5
  fi
  if (( last_runs >= 0 && runs < last_runs )); then
    echo "FAIL: runs_started decreased (${last_runs} -> ${runs})"
    exit 5
  fi

  last_processed=${processed}
  last_runs=${runs}

  printf "%s | tg ok connectors=%s processed=%s runs=%s errors=%s consecutive=%s\n" \
    "$(date '+%Y-%m-%d %H:%M:%S')" "${connectors}" "${processed}" "${runs}" "${error_count}" "${consecutive}"

  sleep "${POLL_SECONDS}"
done

echo "soak_result: PASS (${DURATION_SECONDS}s)"
