#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAY_DIR="${ROOT_DIR}/apps/empyralis-tray"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Empyralis Agent DMG builds currently require macOS." >&2
  exit 1
fi

cd "${ROOT_DIR}"
npm install --prefix "${TRAY_DIR}"
npm run build --prefix "${TRAY_DIR}"

if rustup target list --installed | grep -q '^x86_64-apple-darwin$' \
  && rustup target list --installed | grep -q '^aarch64-apple-darwin$'; then
  npm run tauri:build:universal --prefix "${TRAY_DIR}"
else
  echo "Universal Rust targets are missing; building for the current Mac architecture." >&2
  echo "Install both targets for universal builds:" >&2
  echo "  rustup target add aarch64-apple-darwin x86_64-apple-darwin" >&2
  npm run tauri:build --prefix "${TRAY_DIR}"
fi

DIST_DIR="${TRAY_DIR}/dist"
mkdir -p "${DIST_DIR}"
find "${TRAY_DIR}/src-tauri/target" -path '*bundle/dmg/*.dmg' -type f -print -exec cp {} "${DIST_DIR}/Empyralis-Agent.dmg" \;

if [[ ! -f "${DIST_DIR}/Empyralis-Agent.dmg" ]]; then
  echo "No DMG was produced by Tauri." >&2
  exit 1
fi

if [[ -n "${EMPYRALIS_CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --sign "${EMPYRALIS_CODESIGN_IDENTITY}" "${DIST_DIR}/Empyralis-Agent.dmg" || true
else
  echo "EMPYRALIS_CODESIGN_IDENTITY is not set; produced an unsigned local DMG." >&2
fi

echo "DMG ready: ${DIST_DIR}/Empyralis-Agent.dmg"
