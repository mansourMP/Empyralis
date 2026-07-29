#!/usr/bin/env bash
# Wait for the build droplet's own first boot to finish before we touch apt.
#
# A freshly booted Ubuntu droplet is still running cloud-init and, right behind
# it, unattended-upgrades. Both hold the dpkg/apt lock. apt-get does not fail
# when the lock is held — it BLOCKS, indefinitely, which is the same
# indistinguishable-silence failure mode that made the boot-time installer so
# hard to debug (see apt_install_system_deps in scripts/install-agent-computer.sh).
# Here we get to do the honest thing: wait for the box to settle first, and set
# a bounded lock timeout on every apt call afterwards.
set -Eeuo pipefail

echo "[image-build] waiting for cloud-init on the build droplet"

if command -v cloud-init >/dev/null 2>&1; then
  # `cloud-init status --wait` exits non-zero when the final status is "error".
  # A degraded cloud-init on the BUILD droplet is not automatically fatal to the
  # image, so don't kill the build on it — but do print the status so a real
  # problem is visible in the log rather than swallowed.
  cloud-init status --wait >/dev/null 2>&1 || true
  cloud-init status --long || true
else
  echo "[image-build] cloud-init not present; continuing"
fi

# Belt and braces: wait for the dpkg frontend lock to actually be free.
for _ in $(seq 1 60); do
  if ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
    break
  fi
  echo "[image-build] dpkg lock still held, waiting..."
  sleep 5
done

echo "[image-build] build droplet is ready"
