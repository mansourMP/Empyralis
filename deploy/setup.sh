#!/usr/bin/env bash
# Empyralis v2 — one-time VPS setup. Idempotent: safe to re-run.
set -euo pipefail

# ── packages ────────────────────────────────────────────────────────────────
apt-get update
apt-get install -y --no-upgrade \
    nginx \
    certbot python3-certbot-nginx \
    git \
    python3 python3-venv python3-pip \
    nodejs

# ── system user ─────────────────────────────────────────────────────────────
if ! id empyralis &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin empyralis
fi

# ── directories ─────────────────────────────────────────────────────────────
install -d -o empyralis -g empyralis -m 755 /opt/empyralis
install -d -o empyralis -g empyralis -m 755 /var/www/empyralis
install -d -o empyralis -g empyralis -m 755 /var/log/empyralis

# ── env file placeholder (operator fills in values) ─────────────────────────
if [ ! -f /opt/empyralis/.env ]; then
    cat > /opt/empyralis/.env <<'EOF'
# Empyralis v2 production environment — fill in your values
ANTHROPIC_API_KEY=sk-ant-...
SESSION_SECRET=<run: openssl rand -hex 32>
EMPYRALIS_VAULT_PASSPHRASE=<run: openssl rand -hex 32>
TELEGRAM_BOT_TOKEN=<from BotFather, optional>
GOOGLE_WORKSPACE_OAUTH_CLIENT_ID=<from Google Cloud Console>
GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET=<from Google Cloud Console>
EMPYRALIS_BASE_URL=https://yourdomain.com
EOF
    chown empyralis:empyralis /opt/empyralis/.env
    chmod 600 /opt/empyralis/.env
fi

echo "✓ VPS setup complete."
echo "  Next: edit /opt/empyralis/.env with your real values"
echo "  Then: run deploy/deploy.sh"
