#!/usr/bin/env bash
# Empyralis v2 — install systemd units. Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

cp empyralis-api.service /etc/systemd/system/
cp empyralis-bot.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable empyralis-api empyralis-bot

echo "✓ Services installed and enabled."
echo "  Start them with: systemctl start empyralis-api empyralis-bot"
