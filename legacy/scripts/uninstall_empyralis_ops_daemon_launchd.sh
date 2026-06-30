#!/usr/bin/env bash
set -euo pipefail

PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_NAME="ai.empyralis.ops-daemon.plist"
PLIST_PATH="${PLIST_DIR}/${PLIST_NAME}"
LEGACY_PLIST_PATH="${PLIST_DIR}/ai.orion.ops-daemon.plist"
SERVICE="gui/$(id -u)/ai.empyralis.ops-daemon"
LEGACY_SERVICE="gui/$(id -u)/ai.orion.ops-daemon"

launchctl bootout "${SERVICE}" >/dev/null 2>&1 || true
launchctl disable "${SERVICE}" >/dev/null 2>&1 || true
launchctl bootout "${LEGACY_SERVICE}" >/dev/null 2>&1 || true
launchctl disable "${LEGACY_SERVICE}" >/dev/null 2>&1 || true

rm -f "${PLIST_PATH}" "${LEGACY_PLIST_PATH}"

echo "Uninstalled launchd service ai.empyralis.ops-daemon"
