#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

SOAK_SECONDS="${ORION_CI_SOAK_SECONDS:-12}"

echo "=== Empyralis CI Gate ==="

echo "[1/4] Lean Line Budget..."
bash scripts/lean_line_budget.sh --strict

echo "[2/4] Frontend Production Build..."
if [ -d "frontend" ]; then
  cd frontend
  npm run build
  cd "${ROOT_DIR}"
fi

echo "[3/4] Baseline Smoke..."
bash scripts/orion_baseline_smoke.sh

echo "[4/4] Runtime Release Gate..."
bash scripts/orion_release_gate.sh "${SOAK_SECONDS}"

echo "=== ALL GATES PASSED ==="
