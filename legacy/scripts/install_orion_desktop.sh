#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "${ROOT_DIR}/src-tauri/Cargo.toml" ]]; then
  echo "[error] src-tauri/Cargo.toml not found."
  exit 1
fi

echo "[setup] Installing Empyralis Tauri desktop dependencies..."
cd "${ROOT_DIR}"
npm install

echo
echo "Desktop dependencies installed."
echo "Run:"
echo "  bash scripts/run_orion_desktop.sh"
echo "  # Windows PowerShell: scripts/run_empyralis_desktop.ps1"
echo
echo "Build:"
echo "  bash scripts/build_orion_desktop.sh"
