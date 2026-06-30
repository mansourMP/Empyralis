#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.orion-stack"
LOG_DIR="${STATE_DIR}/logs"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_NAME="ai.empyralis.ops-daemon.plist"
PLIST_PATH="${PLIST_DIR}/${PLIST_NAME}"
LEGACY_SERVICE="gui/$(id -u)/ai.orion.ops-daemon"
SERVICE="gui/$(id -u)/ai.empyralis.ops-daemon"

HOST="${ORION_OPS_DAEMON_HOST:-127.0.0.1}"
PORT="${ORION_OPS_DAEMON_PORT:-8787}"
RUNTIME_URL="${ORION_API_URL:-http://127.0.0.1:8001}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${PLIST_DIR}" "${LOG_DIR}"

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.empyralis.ops-daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${ROOT_DIR}/scripts/orion_ops_daemon.py</string>
    <string>--host</string>
    <string>${HOST}</string>
    <string>--port</string>
    <string>${PORT}</string>
    <string>--runtime-url</string>
    <string>${RUNTIME_URL}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/ops-daemon-launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/ops-daemon-launchd.log</string>
</dict>
</plist>
EOF

launchctl bootout "${LEGACY_SERVICE}" >/dev/null 2>&1 || true
launchctl disable "${LEGACY_SERVICE}" >/dev/null 2>&1 || true
launchctl bootout "${SERVICE}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
launchctl enable "${SERVICE}"
launchctl kickstart -k "${SERVICE}"

echo "Installed launchd service: ${PLIST_PATH}"
echo "Status:"
launchctl print "${SERVICE}" | head -n 20 || true
