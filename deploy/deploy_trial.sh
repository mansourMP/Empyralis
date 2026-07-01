#!/bin/bash
set -e
# Server files — rsync entire server/ tree (excludes __pycache__, .venv, etc.)
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
  /Users/mansur/empyralis/server/ root@165.227.25.201:/opt/empyralis/server/
# Frontend — build and deploy via next start (MAN-30: switched from frontend/v2 static to legacy/frontend SSR)
cd /Users/mansur/empyralis/legacy/frontend && npm run build 2>&1 | tail -5
ssh root@165.227.25.201 'systemctl restart empyralis-frontend; sleep 2; systemctl status empyralis-frontend --no-pager | head -4'
# Clear cache & restart backend
ssh root@165.227.25.201 'find /opt/empyralis/server -name __pycache__ -exec rm -rf {} + 2>/dev/null; systemctl restart empyralis-api empyralis-bot; sleep 2; echo "=== API ===" ; systemctl status empyralis-api --no-pager | head -4; echo "=== BOT ==="; tail -3 /var/log/empyralis/bot.log'
echo "=== DONE ==="
