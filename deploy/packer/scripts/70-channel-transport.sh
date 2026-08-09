#!/usr/bin/env bash
# Bake the channel transport SOFTWARE into the image — and nothing else.
#
# Same decision, and the same reasoning, as 40-gateway-artifact.sh: a
# boot-time download is the single most likely silent killer of a provision,
# and `npm install --global` of the pinned transport is a minute of network on
# a thirty-second-old droplet. Doing it here means a bad pin fails a CI job a
# human is already watching.
#
# ═════════════════════════════════════════════════════════════════════════════
# WHAT IS DELIBERATELY *NOT* BAKED, AND WHY IT WOULD BE A REAL BUG
# ═════════════════════════════════════════════════════════════════════════════
#
#   BAKED (identical on every box)      PER BOX (first boot, empyralis-configure)
#   ────────────────────────────       ──────────────────────────────────────────
#   the pinned transport package        the two loopback secrets
#                                       OpenClaw's generated config
#                                       the systemd unit (it carries a secret)
#                                       the applied-policy record
#
# Every droplet the fleet provisions boots from ONE image. A secret resolved
# while baking is therefore a secret every customer shares, presented as a
# per-box one — so `--runtime-only` exists precisely to make that separation a
# flag rather than something whoever writes this file has to remember.
# 80-verify.sh refutes the presence of a baked secret, so the property is
# enforced rather than hoped for.
#
# Ported from install_channel_transport() in
# scripts/install-agent-computer.sh, minus the per-box half.
set -Eeuo pipefail

SERVICE_USER="empyralis"
INSTALL_ROOT="/opt/empyralis/agent-computer"
CURRENT_DIR="${INSTALL_ROOT}/current"
PLAN_ENTRY="${CURRENT_DIR}/gateway/dist/openclaw/provisioning/openclaw-install-plan-cli.js"

if [[ ! -f "${PLAN_ENTRY}" ]]; then
  echo "70-channel-transport: this gateway build has no channel transport installer (${PLAN_ENTRY})" >&2
  echo "70-channel-transport: continuing; the box will install it on first boot instead" >&2
  exit 0
fi

# As ${SERVICE_USER} and with the SAME npm prefix/cache/HOME the running
# gateway gets, so the binary lands somewhere the service can actually exec it
# — a global install as root would go to /usr/lib/node_modules, which the
# gateway's ProtectSystem=strict sandbox cannot reach.
sudo -u "${SERVICE_USER}" \
  env -i HOME="${INSTALL_ROOT}/cli/home" \
    PATH="${INSTALL_ROOT}/cli/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    NPM_CONFIG_PREFIX="${INSTALL_ROOT}/cli" \
    NPM_CONFIG_CACHE="${INSTALL_ROOT}/cli/npm-cache" \
    node "${PLAN_ENTRY}" --runtime-only --require-user "${SERVICE_USER}"

echo "70-channel-transport: channel transport baked"
