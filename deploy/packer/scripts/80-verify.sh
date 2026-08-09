#!/usr/bin/env bash
# Build-time gate. Everything the image claims to be, asserted before the
# snapshot is taken.
#
# This is the part of the architecture change that actually buys the safety: on
# the boot-install path there was nowhere to run these checks except on a live
# customer droplet, where a failure is invisible. Here, a violated assertion
# fails a CI job.
#
# Failures are ACCUMULATED, not fatal-on-first, so one build tells you
# everything that is wrong instead of one thing at a time.
set -Eeuo pipefail

FAILURES=0

check() {
  # check <description> <command...>
  local description="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "${description}"
  else
    printf '  FAIL  %s\n' "${description}" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

refute() {
  # refute <description> <command...>  — passes when the command FAILS
  local description="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  FAIL  %s\n' "${description}" >&2
    FAILURES=$((FAILURES + 1))
  else
    printf '  ok    %s\n' "${description}"
  fi
}

echo "[image-build] verifying the image"

# ── Base OS ─────────────────────────────────────────────────────────────────
# install-agent-computer.sh's detect_ubuntu() gates on exactly this. The image
# has no runtime OS detection (the base is pinned in the template), so assert
# here instead — it catches a mis-set base_image variable at build time.
check "base OS is Ubuntu" bash -c '. /etc/os-release && [ "${ID:-}" = "ubuntu" ]'
check "Ubuntu version is 22.04 or 24.04" bash -c '. /etc/os-release && case "${VERSION_ID:-}" in 22.04|24.04) exit 0 ;; *) exit 1 ;; esac'

# ── Runtime ─────────────────────────────────────────────────────────────────
check "node is on PATH" command -v node
check "node is v20" bash -c 'node --version | grep -Eq "^v20\."'
check "npm is on PATH" command -v npm

# ── System dependencies (same list as scripts/install-agent-computer.sh) ────
for pkg in ca-certificates curl git build-essential openssl sudo tar gzip xz-utils python3 python3-minimal; do
  check "package ${pkg} installed" dpkg-query -W -f='${Status}' "${pkg}"
done
check "python3 is on PATH" command -v python3

# ── Service user and tree ───────────────────────────────────────────────────
check "service user empyralis exists" id empyralis
check "install root exists" test -d /opt/empyralis/agent-computer
check "bin dir exists" test -d /opt/empyralis/agent-computer/bin
check "state dir exists" test -d /var/lib/empyralis/agent-computer/gateway
check "config dir exists" test -d /etc/empyralis
check "log dir exists" test -d /var/log/empyralis
check "cli prefix exists" test -d /opt/empyralis/agent-computer/cli/bin
check "cli home exists" test -d /opt/empyralis/agent-computer/cli/home
check "npm cache dir exists" test -d /opt/empyralis/agent-computer/cli/npm-cache
check "state dir owned by empyralis" bash -c '[ "$(stat -c %U /var/lib/empyralis/agent-computer)" = "empyralis" ]'
check "cli tree owned by empyralis" bash -c '[ "$(stat -c %U /opt/empyralis/agent-computer/cli)" = "empyralis" ]'

# ── Gateway artifact ────────────────────────────────────────────────────────
check "current/ symlink resolves" test -d /opt/empyralis/agent-computer/current
check "gateway entrypoint present" test -f /opt/empyralis/agent-computer/current/gateway/dist/index.js
check "gateway package.json present" test -f /opt/empyralis/agent-computer/current/gateway/package.json
check "gateway node_modules present" test -d /opt/empyralis/agent-computer/current/gateway/node_modules
# The baked Node can actually parse the baked bundle. Catches a truncated
# extract or an artifact built for a different Node than the one installed here.
check "baked node parses the gateway bundle" node --check /opt/empyralis/agent-computer/current/gateway/dist/index.js
check "image manifest is valid JSON" python3 -m json.tool /etc/empyralis/image-manifest.json

# ── Launcher and unit ───────────────────────────────────────────────────────
check "run-gateway is executable" test -x /opt/empyralis/agent-computer/bin/run-gateway
check "run-gateway is valid bash" bash -n /opt/empyralis/agent-computer/bin/run-gateway
check "empyralis-configure is executable" test -x /usr/local/sbin/empyralis-configure
check "empyralis-configure is valid bash" bash -n /usr/local/sbin/empyralis-configure
check "gateway unit installed" test -f /etc/systemd/system/empyralis-gateway.service
check "gateway unit enabled" systemctl is-enabled empyralis-gateway.service
refute "gateway unit is NOT running" systemctl is-active --quiet empyralis-gateway.service
check "unit guards on the env file" grep -q '^ConditionPathExists=/etc/empyralis/agent-computer.env$' /etc/systemd/system/empyralis-gateway.service
check "unit creates its runtime dir" grep -q '^RuntimeDirectory=empyralis$' /etc/systemd/system/empyralis-gateway.service
# MemoryDenyWriteExecute kills Node outright (V8 W+X). The unit's own header
# comment explains this in prose, which contains the same substring — so this
# must check for the systemd DIRECTIVE (unindented, key=value, no leading '#'),
# not a bare substring match, or the explanatory comment trips this check on
# every build regardless of whether the real setting is present.
refute "unit does NOT set MemoryDenyWriteExecute" grep -Eq '^MemoryDenyWriteExecute=' /etc/systemd/system/empyralis-gateway.service

# ── Nothing customer-specific may be baked in ───────────────────────────────
# This is the security assertion. The image is shared by every customer; a
# pairing token, gateway token, or registration file baked in here would be
# handed to every box that ever boots from it.
refute "NO env file baked in" test -e /etc/empyralis/agent-computer.env
refute "NO gateway registration baked in" test -e /var/lib/empyralis/agent-computer/gateway/registration.json
refute "NO gateway state files baked in" bash -c 'find /var/lib/empyralis/agent-computer -mindepth 1 -type f | grep -q .'
refute "NO self-updated gateway release baked in" test -e /var/lib/empyralis/gateway-releases
refute "NO EMPYRALIS_PAIRING_TOKEN anywhere under /etc" grep -rql 'EMPYRALIS_PAIRING_TOKEN' /etc
# The channel transport is baked; its per-box secrets must NOT be. Every
# droplet boots from this one image, so a secrets file resolved at bake time is
# a single credential shared across the whole fleet while looking per-box.
# 70-channel-transport.sh passes --runtime-only precisely to prevent this, and
# this is the assertion that keeps that true.
refute "NO baked channel-transport secrets" \
  test -e /var/lib/empyralis/agent-computer/gateway/openclaw/local-secrets.json
refute "NO git checkout under the install root" test -d /opt/empyralis/agent-computer/current/.git

echo
if [[ "${FAILURES}" -gt 0 ]]; then
  echo "[image-build] ERROR: ${FAILURES} verification check(s) failed — refusing to snapshot" >&2
  exit 1
fi

echo "[image-build] all image verification checks passed"
