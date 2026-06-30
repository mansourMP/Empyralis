#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"
bash scripts/install_orion_cli.sh >/dev/null
hash -r 2>/dev/null || true

unset ORION_CLI_CHAT_REQUIRE_RUN_PREFIX
unset ORION_CLI_CHAT_AUTOFOCUS_TELEGRAM
unset ORION_CLI_CHAT_CLEAR_SCREEN
unset ORION_CLI_CHAT_FULL_REDRAW
unset ORION_CLI_CHAT_SPINNER
export ORION_CLI_ONE_APP_MODE=1
export ORION_CLI_CHAT_LIVE_INPUT=0
export ORION_CLI_LINE_INPUT=1

exec orion tui --engine "${ORION_CLI_TUI_ENGINE:-codex}"
