#!/usr/bin/env bash
set -euo pipefail

SERVICE="gui/$(id -u)/ai.empyralis.ops-daemon"

echo "launchd service: ${SERVICE}"
if launchctl print "${SERVICE}" >/dev/null 2>&1; then
  launchctl print "${SERVICE}" | head -n 40
else
  echo "not loaded"
fi
