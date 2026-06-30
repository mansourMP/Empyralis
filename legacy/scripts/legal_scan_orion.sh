#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "${ROOT_DIR}"

echo "Running Empyralis legal/branding scan..."

# User-facing runtime surfaces only.
TARGETS=(
  "bin"
  "scripts/orion_terminal"
  "frontend"
  "server.py"
)

# Brand strings we do not want in Empyralis runtime surfaces.
BLOCKLIST_REGEX='OpenClaw|openclaw|open claw|lobster'

set +e
HITS="$(rg -n --hidden --glob '!**/node_modules/**' --glob '!reference/**' --glob '!*.png' --glob '!*.jpg' --glob '!*.svg' "${BLOCKLIST_REGEX}" "${TARGETS[@]}" 2>/dev/null)"
RC=$?
set -e

if [[ ${RC} -eq 0 && -n "${HITS}" ]]; then
  echo ""
  echo "Blocked branding references found:"
  echo "${HITS}"
  echo ""
  echo "Result: FAIL"
  exit 1
fi

echo "No blocked branding references found."
echo "Result: PASS"
