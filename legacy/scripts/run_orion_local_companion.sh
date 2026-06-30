#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
RUNTIME_KEY="${1:-${RUNTIME_KEY:-${ORION_API_KEY:-}}}"
ORION_GOAL="${ORION_GOAL:-${2:-Create a short marketing plan}}"
TRUST_MODE="${TRUST_MODE:-auto}"
EXECUTION_TARGET="${EXECUTION_TARGET:-local_companion}"
OUTCOME_PACK="${OUTCOME_PACK:-}"
PACK_TOPICS="${PACK_TOPICS:-}"
PACK_CHANNELS="${PACK_CHANNELS:-}"
PACK_OFFERS="${PACK_OFFERS:-}"

export ORION_GOAL TRUST_MODE EXECUTION_TARGET OUTCOME_PACK PACK_TOPICS PACK_CHANNELS PACK_OFFERS

if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Usage:"
  echo "  RUNTIME_KEY='replace-with-strong-key' bash scripts/run_empyralis_local_companion.sh"
  echo "  or: bash scripts/run_empyralis_local_companion.sh replace-with-strong-key \"Create a short marketing plan\""
  exit 1
fi

cd "${ROOT_DIR}"

if ! curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" >/dev/null 2>&1; then
  echo "Runtime is not reachable on ${API_URL}."
  echo "Start stack first:"
  echo "  RUNTIME_KEY='${RUNTIME_KEY}' bash ${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
  exit 2
fi

PAYLOAD_FILE="$(mktemp /tmp/empyralis-run.XXXXXX.json)"
trap 'rm -f "${PAYLOAD_FILE}"' EXIT

python3 > "${PAYLOAD_FILE}" <<'PY'
import json
import os

goal = os.environ.get("ORION_GOAL", "Create a short marketing plan")
trust_mode = os.environ.get("TRUST_MODE", "auto")
execution_target = os.environ.get("EXECUTION_TARGET", "local_companion")
outcome_pack = os.environ.get("OUTCOME_PACK", "").strip()

metadata = {
    "trust_mode": trust_mode,
    "execution_target": execution_target,
}

if outcome_pack:
    metadata["outcome_pack"] = outcome_pack
    pack_inputs = {}
    topics = os.environ.get("PACK_TOPICS", "").strip()
    channels = os.environ.get("PACK_CHANNELS", "").strip()
    offers = os.environ.get("PACK_OFFERS", "").strip()
    if topics:
        pack_inputs["topics"] = topics
    if channels:
        pack_inputs["channels"] = channels
    if offers:
        pack_inputs["offers"] = offers
    if pack_inputs:
        metadata["pack_inputs"] = pack_inputs

payload = {
    "engine": "orion",
    "user_goal": goal,
    "metadata": metadata,
}
print(json.dumps(payload, separators=(",", ":")))
PY

RESP="$(
  curl -sS -w '\nHTTP_STATUS:%{http_code}\n' -X POST "${API_URL}/turn" \
    -H "X-API-Key: ${RUNTIME_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @"${PAYLOAD_FILE}"
)"

BODY="$(echo "${RESP}" | sed '/^HTTP_STATUS:/d')"
CODE="$(echo "${RESP}" | awk -F: '/^HTTP_STATUS:/{print $2}')"

if [[ "${CODE}" != "200" ]]; then
  echo "Run start failed (HTTP ${CODE})."
  echo "${BODY}"
  exit 3
fi

RUN_ID="$(echo "${BODY}" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data.get("run_id",""))')"
if [[ -z "${RUN_ID}" ]]; then
  echo "Run start returned no run_id."
  echo "${BODY}"
  exit 4
fi

echo "${BODY}" | python3 -c 'import json,sys; data=json.load(sys.stdin); data["engine"]="empyralis"; print(json.dumps(data,separators=(",",":")))'
echo "RUN_ID=${RUN_ID}"
echo "Streaming: ${API_URL}/runs/${RUN_ID}/stream"
curl --no-buffer -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/runs/${RUN_ID}/stream"
