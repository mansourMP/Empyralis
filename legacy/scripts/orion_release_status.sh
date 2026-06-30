#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
STRICT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
    -h|--help)
      cat <<'USAGE'
Usage:
  RUNTIME_KEY='replace-with-strong-key' bash scripts/empyralis_release_status.sh [--strict]

Checks runtime, worker, Telegram/WhatsApp autopilot, and latest run summary.
--strict exits non-zero when release readiness requirements are not met.
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

fetch_json() {
  local path="$1"
  curl -fsS -H "X-API-Key: ${RUNTIME_KEY_VALUE}" "${API_URL}${path}" 2>/dev/null || echo '{}'
}

HEALTH_JSON="$(fetch_json "/health")"
WORKERS_JSON="$(fetch_json "/runtime/runtimes/status")"
TG_JSON="$(fetch_json "/channels/telegram/autopilot/status")"
WA_JSON="$(fetch_json "/channels/whatsapp/autopilot/status")"
HISTORY_JSON="$(fetch_json "/history/runs?limit=1")"

HEALTH_JSON="${HEALTH_JSON}" \
WORKERS_JSON="${WORKERS_JSON}" \
TG_JSON="${TG_JSON}" \
WA_JSON="${WA_JSON}" \
HISTORY_JSON="${HISTORY_JSON}" \
STRICT_MODE="${STRICT}" \
python3 - <<'PY'
import json
import os
import sys


def _load(name: str) -> dict:
    raw = os.environ.get(name, "{}")
    try:
        obj = json.loads(raw or "{}")
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _safe_bool(v):
    return bool(v)

health = _load("HEALTH_JSON")
workers = _load("WORKERS_JSON")
tg = _load("TG_JSON")
wa = _load("WA_JSON")
history = _load("HISTORY_JSON")
strict = str(os.environ.get("STRICT_MODE") or "0") == "1"

worker_summary = workers.get("summary") if isinstance(workers.get("summary"), dict) else {}
tg_auto = tg.get("autopilot") if isinstance(tg.get("autopilot"), dict) else {}
wa_auto = wa.get("autopilot") if isinstance(wa.get("autopilot"), dict) else {}

history_items = history.get("items") if isinstance(history.get("items"), list) else []
latest = history_items[0] if history_items and isinstance(history_items[0], dict) else {}

runtime_ok = _safe_bool(health.get("ok"))
auth_mode = str(health.get("auth_mode") or "unknown")
openai_valid = _safe_bool(health.get("openai_key_valid"))
openai_source = str(health.get("openai_credential_source") or "-")
workers_online = _safe_int(worker_summary.get("online"), 0)
workers_idle = _safe_int(worker_summary.get("idle"), 0)
workers_busy = _safe_int(worker_summary.get("busy"), 0)

tg_enabled = _safe_bool(tg_auto.get("enabled"))
tg_active = _safe_bool(tg_auto.get("active"))
tg_thread = _safe_bool(tg_auto.get("thread_alive"))
tg_connectors = _safe_int(tg_auto.get("connectors_seen"), 0)
tg_processed = _safe_int(tg_auto.get("processed_updates"), 0)
tg_runs = _safe_int(tg_auto.get("runs_started"), 0)
tg_error = str(tg_auto.get("last_error") or "").strip()

wa_enabled = _safe_bool(wa_auto.get("enabled"))
wa_active = _safe_bool(wa_auto.get("active"))
wa_thread = _safe_bool(wa_auto.get("thread_alive"))
wa_connectors = _safe_int(wa_auto.get("connectors_seen"), 0)
wa_processed = _safe_int(wa_auto.get("processed_messages"), 0)
wa_runs = _safe_int(wa_auto.get("runs_started"), 0)

latest_run_id = str(latest.get("run_id") or "-")
latest_status = str(latest.get("status") or "-")
latest_provider = str((latest.get("usage_masked") or {}).get("provider") or "-")
latest_summary = str(latest.get("result") or "").strip()
if len(latest_summary) > 180:
    latest_summary = latest_summary[:177] + "..."
if not latest_summary:
    latest_summary = "-"

print("Empyralis Release Status")
print(f"runtime_ok: {runtime_ok}")
print(f"auth_mode: {auth_mode} openai_valid: {openai_valid} source: {openai_source}")
print(f"workers: online={workers_online} idle={workers_idle} busy={workers_busy}")
print(
    "telegram: enabled={} active={} thread_alive={} connectors={} processed={} runs={} error={}".format(
        tg_enabled,
        tg_active,
        tg_thread,
        tg_connectors,
        tg_processed,
        tg_runs,
        tg_error or "-",
    )
)
print(
    "whatsapp: enabled={} active={} thread_alive={} connectors={} processed={} runs={}".format(
        wa_enabled,
        wa_active,
        wa_thread,
        wa_connectors,
        wa_processed,
        wa_runs,
    )
)
print(f"latest_run: id={latest_run_id} status={latest_status} provider={latest_provider}")
print(f"latest_summary: {latest_summary}")

release_ready = runtime_ok and workers_online >= 1 and openai_valid and tg_enabled and tg_active and tg_thread and tg_connectors >= 1
print(f"release_ready: {release_ready}")

if strict and not release_ready:
    sys.exit(3)
PY
