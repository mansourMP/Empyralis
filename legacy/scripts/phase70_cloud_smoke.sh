#!/usr/bin/env bash
set -euo pipefail

PUBLIC_URL="${1:-${EMPYRALIS_PUBLIC_URL:-}}"
API_KEY="${EMPYRALIS_RUNTIME_API_KEY:-${ORION_API_KEY:-}}"

if [[ -z "${PUBLIC_URL}" ]]; then
  echo "Usage: EMPYRALIS_PUBLIC_URL=https://runtime.example.com $0"
  exit 1
fi

PUBLIC_URL="${PUBLIC_URL%/}"

echo "==> /health"
curl -fsS "${PUBLIC_URL}/health" | python3 -m json.tool >/dev/null

if [[ -n "${API_KEY}" ]]; then
  echo "==> /health/internal/db"
  curl -fsS -H "X-API-Key: ${API_KEY}" "${PUBLIC_URL}/health/internal/db" | python3 -m json.tool >/dev/null

  echo "==> /doctor"
  curl -fsS -H "X-API-Key: ${API_KEY}" "${PUBLIC_URL}/doctor" | python3 -m json.tool >/dev/null
else
  echo "Skipping privileged diagnostics; set EMPYRALIS_RUNTIME_API_KEY to verify /health/internal/db and /doctor."
fi

echo "Phase 70 cloud smoke passed for ${PUBLIC_URL}"
