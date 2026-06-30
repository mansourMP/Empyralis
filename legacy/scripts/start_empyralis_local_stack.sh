#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ORION_LOCAL_RUNTIME_USE_POSTGRES="${ORION_LOCAL_RUNTIME_USE_POSTGRES:-1}"
exec bash "${ROOT_DIR}/scripts/start_orion_local_stack.sh" "$@"
