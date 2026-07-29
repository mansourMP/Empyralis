#!/usr/bin/env bash
# Pre-snapshot cleanup. Follows DigitalOcean's own image-creation guidance
# (digitalocean/marketplace-partners, scripts/90-cleanup.sh), with two additions
# their script omits and one of their steps made optional.
#
# WHY THIS MATTERS MORE THAN IT LOOKS: every droplet ever created from this
# image inherits whatever identity is sitting on disk right now. Get it wrong
# and every customer box shares one machine-id and one set of SSH host keys —
# a correctness problem (systemd-journald, DHCP client identity, anything keyed
# on machine-id) and a security problem (host-key reuse makes host
# authentication meaningless across the whole fleet, and one leaked private
# host key impersonates every box).
#
# ADDED beyond DO's script:
#   * /etc/machine-id truncated (NOT deleted). systemd regenerates it on first
#     boot only when the file exists and is empty; deleting it outright leaves
#     systemd to guess, and /var/lib/dbus/machine-id can silently resurrect the
#     old value. DO's own cleanup script does not do this at all.
#   * `cloud-init clean --logs --seed`, the supported way to reset cloud-init,
#     rather than only rm-ing /var/lib/cloud/instances/*. Without a clean reset,
#     per-instance modules (including the one that regenerates SSH host keys)
#     may consider themselves already run.
#
# MADE OPTIONAL: the zero-fill (`dd if=/dev/zero of=/zerofile`). See the
# zero_fill_free_space variable in agent-computer.pkr.hcl for the reasoning.
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[image-build] cleaning up before snapshot"

# ── Package manager ─────────────────────────────────────────────────────────
apt-get -o DPkg::Lock::Timeout=300 -y autoremove
apt-get -o DPkg::Lock::Timeout=300 -y autoclean
apt-get -o DPkg::Lock::Timeout=300 clean
rm -rf /var/lib/apt/lists/*

# ── Transient files and history ─────────────────────────────────────────────
rm -rf /tmp/* /var/tmp/*
# /tmp must exist with the sticky bit, or the next boot's package tooling and
# PrivateTmp= setup misbehave (digitalocean/marketplace-partners issue #94).
mkdir -p /tmp
chmod 1777 /tmp

: > /root/.bash_history || true
rm -f /home/*/.bash_history 2>/dev/null || true
unset HISTFILE || true

# ── Logs ────────────────────────────────────────────────────────────────────
find /var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true
rm -rf /var/log/*.gz /var/log/*.[0-9] /var/log/*-????????
rm -rf /var/log/journal/* 2>/dev/null || true
# The gateway never ran here, but assert the log dir is empty and correctly
# owned rather than assuming.
rm -f /var/log/empyralis/* 2>/dev/null || true
chown -R empyralis:empyralis /var/log/empyralis

# ── cloud-init: every droplet from this image must look like a first boot ───
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init clean --logs --seed || true
fi
rm -rf /var/lib/cloud/instances/* /var/lib/cloud/instance /var/lib/cloud/data 2>/dev/null || true

# ── Machine identity ────────────────────────────────────────────────────────
# Truncate, do not delete: systemd's first-boot machine-id generation keys off
# an existing-but-empty /etc/machine-id.
: > /etc/machine-id
# dbus normally symlinks to /etc/machine-id; if a real file is there it would
# pin the old identity forever.
rm -f /var/lib/dbus/machine-id
mkdir -p /var/lib/dbus
ln -sf /etc/machine-id /var/lib/dbus/machine-id

# ── SSH ─────────────────────────────────────────────────────────────────────
# Host keys are regenerated per-instance by cloud-init's ssh module (which the
# cloud-init clean above re-arms). Leaving them in the image would give the
# entire fleet one host identity.
rm -f /etc/ssh/ssh_host_* /etc/ssh/*key*
touch /etc/ssh/revoked_keys
chmod 600 /etc/ssh/revoked_keys

# Packer injected its own ephemeral keypair to reach this droplet. The private
# half is discarded when the build ends, but the public half would otherwise sit
# in the image's authorized_keys forever. Removing it does not drop the live SSH
# session — this is the last provisioner, and the droplet is powered off next.
rm -f /root/.ssh/authorized_keys
rm -rf /root/.ssh/known_hosts /home/*/.ssh/authorized_keys 2>/dev/null || true

# ── Post-cleanup assertions ─────────────────────────────────────────────────
# These run HERE rather than in a later provisioner on purpose: a script step
# after authorized_keys is gone would depend on Packer never needing to
# re-establish the SSH connection.
cleanup_failures=0
assert_absent() {
  local description="$1" path="$2"
  if compgen -G "${path}" >/dev/null 2>&1; then
    printf '  FAIL  %s (%s still present)\n' "${description}" "${path}" >&2
    cleanup_failures=$((cleanup_failures + 1))
  else
    printf '  ok    %s\n' "${description}"
  fi
}

assert_absent "no SSH host keys" '/etc/ssh/ssh_host_*'
assert_absent "no root authorized_keys" '/root/.ssh/authorized_keys'
assert_absent "no cloud-init instance state" '/var/lib/cloud/instances/*'
assert_absent "no Empyralis env file" '/etc/empyralis/agent-computer.env'

if [[ -s /etc/machine-id ]]; then
  echo "  FAIL  /etc/machine-id is not empty — every droplet from this image would share it" >&2
  cleanup_failures=$((cleanup_failures + 1))
else
  echo "  ok    /etc/machine-id is empty"
fi

if [[ "${cleanup_failures}" -gt 0 ]]; then
  echo "[image-build] ERROR: ${cleanup_failures} cleanup assertion(s) failed — refusing to snapshot" >&2
  exit 1
fi

# ── Optional secure erase of free space ─────────────────────────────────────
if [[ "${EMPYRALIS_ZERO_FILL:-0}" == "1" ]]; then
  echo "[image-build] zero-filling free space (this fills the disk on purpose; 'No space left on device' is the success condition)"
  dd if=/dev/zero of=/zerofile bs=4096 || true
  rm -f /zerofile
  sync
fi

echo "[image-build] cleanup complete — ready to snapshot"
