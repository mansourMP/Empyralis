#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
TARGET="${BIN_DIR}/empyralis"
LEGACY_TARGET="${BIN_DIR}/orion"
SOURCE="${ROOT_DIR}/bin/empyralis"
LEGACY_SOURCE="${ROOT_DIR}/bin/orion"

mkdir -p "${BIN_DIR}"
chmod +x "${SOURCE}" "${LEGACY_SOURCE}"

if [[ -e "${TARGET}" && ! -L "${TARGET}" ]]; then
  echo "Refusing to overwrite non-symlink: ${TARGET}"
  echo "Move it manually, then rerun."
  exit 1
fi

ln -snf "${SOURCE}" "${TARGET}"
ln -snf "${LEGACY_SOURCE}" "${LEGACY_TARGET}"

if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "PATH does not include ${BIN_DIR}."
  echo "Add this to ~/.zshrc:"
  echo "  export PATH=\"${BIN_DIR}:\$PATH\""
fi

echo "Installed Empyralis CLI:"
echo "  ${TARGET} -> ${SOURCE}"
echo "  ${LEGACY_TARGET} -> ${LEGACY_SOURCE} (legacy alias)"
echo
echo "Try:"
echo "  empyralis --help"
echo "  empyralis app"
echo "  empyralis go"
echo
echo "Recommended clean TUI launch:"
echo "  empyralis app"
echo "Recommended one-command startup checks:"
echo "  empyralis go"
echo
echo "Manual (single line only):"
echo "  unset ORION_CLI_CHAT_REQUIRE_RUN_PREFIX ORION_CLI_CHAT_AUTOFOCUS_TELEGRAM ORION_CLI_CHAT_CLEAR_SCREEN ORION_CLI_CHAT_FULL_REDRAW ORION_CLI_CHAT_SPINNER; ORION_CLI_ONE_APP_MODE=1 empyralis tui"
