#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "Running full Empyralis checks (build + smoke + release gate)..."
bash scripts/orion_ci_gate.sh "$@"
