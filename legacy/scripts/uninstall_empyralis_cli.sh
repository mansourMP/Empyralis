#!/usr/bin/env bash
set -euo pipefail

TARGET="${HOME}/.local/bin/empyralis"
LEGACY_TARGET="${HOME}/.local/bin/orion"

if [[ -L "${TARGET}" || -f "${TARGET}" ]]; then
  rm -f "${TARGET}"
  echo "Removed ${TARGET}"
else
  echo "Nothing to remove: ${TARGET}"
fi

if [[ -L "${LEGACY_TARGET}" || -f "${LEGACY_TARGET}" ]]; then
  rm -f "${LEGACY_TARGET}"
  echo "Removed ${LEGACY_TARGET}"
else
  echo "Nothing to remove: ${LEGACY_TARGET}"
fi
