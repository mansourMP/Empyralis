#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:8001}"
RUNTIME_KEY="${RUNTIME_KEY:-${1:-}}"

if [ -z "${RUNTIME_KEY}" ]; then
  echo "Usage:"
  echo "  RUNTIME_KEY='replace-with-strong-key' bash scripts/run_weekly_content_policy_test.sh"
  echo "  or: bash scripts/run_weekly_content_policy_test.sh replace-with-strong-key"
  exit 1
fi

cat >/tmp/orion-run.json <<'JSON'
{
  "engine": "orion",
  "user_goal": "Build weekly content",
  "metadata": {
    "trust_mode": "auto",
    "outcome_pack": "weekly-content-studio",
    "pack_inputs": {
      "topics": "New arrivals",
      "channels": "Instagram",
      "offers": "Book today"
    },
    "action_policy": {
      "blocked_actions": ["publish_content"]
    }
  }
}
JSON

python3 -m json.tool /tmp/orion-run.json >/dev/null

RUN_JSON="$(
  curl -sS -X POST "${API_URL}/turn" \
    -H "X-API-Key: ${RUNTIME_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/orion-run.json
)"

echo "${RUN_JSON}"

RUN_ID="$(echo "${RUN_JSON}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("run_id",""))')"

if [ -z "${RUN_ID}" ]; then
  echo "Start failed; expected run_id in response."
  exit 1
fi

echo "RUN_ID=${RUN_ID}"
echo "Streaming events from ${API_URL}/runs/${RUN_ID}/stream"

curl --no-buffer \
  -H "X-API-Key: ${RUNTIME_KEY}" \
  "${API_URL}/runs/${RUN_ID}/stream"
