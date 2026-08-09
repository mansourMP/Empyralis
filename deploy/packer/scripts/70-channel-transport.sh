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

# ── The transport's own Node ─────────────────────────────────────────────────
#
# openclaw needs Node 22+; the gateway ships prebuilt with native modules
# compiled against Node 20's ABI, so the box keeps both and only the
# transport's unit ever sees the newer one on its PATH. The version is asked of
# the gateway artifact, never typed here.
NODE_DIR="${INSTALL_ROOT}/openclaw-node"
WANT_NODE="$(node -e 'process.stdout.write(require(process.argv[1]).OPENCLAW_PINNED_NODE_VERSION)' \
  "${CURRENT_DIR}/gateway/dist/openclaw/provisioning/openclaw-version.js")"
case "$(uname -m)" in
  x86_64|amd64) NODE_ARCH="x64" ;;
  aarch64|arm64) NODE_ARCH="arm64" ;;
  *) echo "70-channel-transport: no Node build for $(uname -m)" >&2; exit 1 ;;
esac
NODE_TARBALL="node-v${WANT_NODE}-linux-${NODE_ARCH}.tar.xz"
TMP="$(mktemp -d)"
curl -fsSL -m 300 -o "${TMP}/${NODE_TARBALL}" \
  "${EMPYRALIS_NODE_DIST_BASE_URL:-https://nodejs.org/dist}/v${WANT_NODE}/${NODE_TARBALL}"
rm -rf "${NODE_DIR}"
mkdir -p "${NODE_DIR}"
tar -xJf "${TMP}/${NODE_TARBALL}" -C "${NODE_DIR}" --strip-components=1
rm -rf "${TMP}"
chown -R root:root "${NODE_DIR}"
echo "70-channel-transport: Node ${WANT_NODE} baked for the channel transport"

# As ${SERVICE_USER} and with the SAME npm prefix/cache/HOME the running
# gateway gets, so the binary lands somewhere the service can actually exec it
# — a global install as root would go to /usr/lib/node_modules, which the
# gateway's ProtectSystem=strict sandbox cannot reach.
sudo -u "${SERVICE_USER}" \
  env -i HOME="${INSTALL_ROOT}/cli/home" \
    PATH="${INSTALL_ROOT}/cli/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    NPM_CONFIG_PREFIX="${INSTALL_ROOT}/cli" \
    NPM_CONFIG_CACHE="${INSTALL_ROOT}/cli/npm-cache" \
    EMPYRALIS_OPENCLAW_NODE_BIN_DIR="${NODE_DIR}/bin" \
    node "${PLAN_ENTRY}" --runtime-only --require-user "${SERVICE_USER}"

echo "70-channel-transport: channel transport baked"
