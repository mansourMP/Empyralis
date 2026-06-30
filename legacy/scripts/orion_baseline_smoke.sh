#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUNTIME_KEY="${1:-${RUNTIME_KEY:-replace-with-strong-key}}"
API_URL="${ORION_API_URL:-http://127.0.0.1:8001}"

echo "== Empyralis Baseline Smoke =="
echo "root: ${ROOT_DIR}"
echo "api:  ${API_URL}"
echo

echo "-- Syntax checks --"
python3 -m py_compile \
  server.py \
  scripts/orion_terminal/core.py \
  scripts/orion_terminal/flows.py \
  scripts/orion_terminal/flows_launcher.py \
  scripts/orion_terminal/flows_run.py \
  scripts/orion_terminal/flows_setup.py \
  scripts/orion_terminal/flows_shared.py \
  scripts/orion_terminal/prompt_engine.py \
  scripts/orion_terminal/wizard_engine.py \
  scripts/orion_terminal/app.py \
  scripts/orion_terminal_wizard.py
bash -n bin/orion
echo "ok: syntax"
echo

echo "-- Runtime health checks --"
if curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" >/dev/null; then
  echo "ok: runtime health reachable"
  curl -fsS -H "X-API-Key: ${RUNTIME_KEY}" "${API_URL}/health" | python3 -m json.tool | sed -n '1,40p'
else
  echo "warn: runtime health not reachable at ${API_URL} (stack may be down)"
fi
echo

echo "-- Terminal launcher help sanity --"
python3 scripts/orion_terminal_wizard.py --help | sed -n '1,40p'
echo

echo "done"
