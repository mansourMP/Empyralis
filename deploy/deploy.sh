#!/usr/bin/env bash
# Empyralis v2 — deploy latest code to production VPS.
# Run from repo root on the VPS: ./deploy/deploy.sh
set -euo pipefail

REPO_DIR="/opt/empyralis"
VENV="$REPO_DIR/.venv"
FRONTEND_DIR="$REPO_DIR/frontend/v2"
WWW_DIR="/var/www/empyralis"

# ── pull code ───────────────────────────────────────────────────────────────
if [ -d "$REPO_DIR/.git" ]; then
    cd "$REPO_DIR"
    git pull
else
    echo "ERROR: $REPO_DIR does not contain a git repo. Clone it first."
    exit 1
fi

# ── backend deps ────────────────────────────────────────────────────────────
python3 -m venv "$VENV" --clear
"$VENV/bin/pip" install -r "$REPO_DIR/requirements.txt"

# ── frontend build ──────────────────────────────────────────────────────────
if [ -f "$REPO_DIR/.env" ]; then
    set -a; source "$REPO_DIR/.env"; set +a
fi
API_URL="${EMPYRALIS_BASE_URL:-http://165.227.25.201}/api"
NEXT_PUBLIC_API_URL="$API_URL"

cd "$FRONTEND_DIR"
npm ci
NEXT_PUBLIC_API_URL="$API_URL" npm run build

# ── copy frontend ───────────────────────────────────────────────────────────
rm -rf "$WWW_DIR"/*
cp -r "$FRONTEND_DIR"/out/* "$WWW_DIR"/
chown -R empyralis:empyralis "$WWW_DIR"

# ── restart services ────────────────────────────────────────────────────────
systemctl restart empyralis-api empyralis-bot

echo ""
echo "✓ Deploy complete."
systemctl status empyralis-api --no-pager -l 2>&1 | head -8
echo ""
systemctl status empyralis-bot --no-pager -l 2>&1 | head -8
