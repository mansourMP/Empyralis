#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
RUNTIME_KEY="${1:-${RUNTIME_KEY:-${ORION_API_KEY:-}}}"
WORKER_ID="${WORKER_ID:-smoke-worker-$(date +%s)}"

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Usage:"
  echo "  RUNTIME_KEY='replace-with-strong-key' bash scripts/run_local_worker_smoke_test.sh"
  echo "  or: bash scripts/run_local_worker_smoke_test.sh replace-with-strong-key"
  exit 1
fi

cd "${ROOT_DIR}"

echo "Starting local-companion run..."
RUN_JSON="$(curl -sS -X POST "${API_URL}/turn" \
  -H "X-API-Key: ${RUNTIME_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"engine":"orion","user_goal":"Smoke test local worker","metadata":{"execution_target":"local_companion","connection_mode":"local_companion","trust_mode":"auto","outcome_pack":"weekly-content-studio","pack_inputs":{"topics":"Smoke Topic","channels":"Instagram","offers":"Book now"}}}')"

echo "${RUN_JSON}"
RUN_ID="$(echo "${RUN_JSON}" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data.get("run_id",""))')"
ROUTE_SELECTED="$(echo "${RUN_JSON}" | python3 -c 'import sys,json; data=json.load(sys.stdin); route=data.get("route") or {}; print(route.get("selected",""))')"
if [[ -z "${RUN_ID}" ]]; then
  echo "Failed to start run. Response above."
  exit 1
fi
echo "RUN_ID=${RUN_ID}"
if [[ "${ROUTE_SELECTED}" != "local_companion" ]]; then
  echo "Smoke test failed: expected route.selected=local_companion, got '${ROUTE_SELECTED:-<none>}'"
  exit 1
fi

echo "Running local worker in one-shot mode..."
python3 scripts/orion_local_worker.py \
  --runtime-url "${API_URL}" \
  --api-key "${RUNTIME_KEY}" \
  --worker-id "${WORKER_ID}" \
  --once \
  --max-wait-seconds 60 \
  --poll-seconds 1 \
  --step-delay-seconds 0.4

echo "Fetching final run state..."
RUN_STATUS_JSON="$(curl -sS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/runs/${RUN_ID}")"
echo "${RUN_STATUS_JSON}" | python3 -m json.tool

RUN_STATUS="$(echo "${RUN_STATUS_JSON}" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data.get("status",""))')"
if [[ "${RUN_STATUS}" != "completed" ]]; then
  echo "Smoke test failed: expected completed, got '${RUN_STATUS}'"
  exit 2
fi

echo "Smoke test passed."
