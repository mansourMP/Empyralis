#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-auto}"

if [[ ! -f "${ROOT_DIR}/src-tauri/Cargo.toml" ]]; then
  echo "[error] src-tauri/Cargo.toml not found."
  exit 1
fi

echo "[build] Empyralis desktop target: ${TARGET}"
cd "${ROOT_DIR}"
npm run tauri:build

echo
echo "Build output:"
echo "  ${ROOT_DIR}/src-tauri/target"
