#!/usr/bin/env bash
# Bake Docker onto the image — port of install_docker() from
# scripts/install-agent-computer.sh:251-322.
#
# Cloud Agent Computer boxes run shell.execute and filesystem.read_write
# inside a Docker sandbox; the gateway only advertises those two capabilities
# once a live `docker info` probe succeeds (empyralis-gateway/src/health/
# service-inventory.ts's probeDocker). This step did not exist until
# 2026-08-13 — this file's own numbering slot sat empty between
# 50-launcher-and-systemd.sh and 70-channel-transport.sh, and 80-verify.sh had
# no Docker check either, so a baked image "passed" its own build while
# shipping boxes that could never run a single shell command. OpenClaw got a
# bake-time port (70-channel-transport.sh); Docker did not, until now.
#
# DELIBERATE DEVIATION from install_docker()'s boot-time contract: that
# function is non-fatal by design (a box that cannot get Docker must still
# finish provisioning with every other capability, since there is no later
# chance to fix it without SSH). This script has the opposite obligation —
# 80-verify.sh's whole job is "fail the build, not the customer" — so every
# failure path below is fatal (set -Eeuo pipefail, no `|| true`, no advisory
# beacon: there is no live box yet to report to). EMPYRALIS_INSTALL_SKIP_DOCKER
# is also dropped: it exists so an operator can accept a degraded box rather
# than lose it entirely, and there is no equivalent "half a build" concept at
# bake time — an image is either safe to snapshot or it is not.
set -Eeuo pipefail

SERVICE_USER="empyralis"

if ! command -v docker >/dev/null 2>&1; then
  echo "[image-build] installing Docker (docker.io)"
  # Same DPkg::Lock::Timeout guard as 10-system-deps.sh, for the same reason:
  # unattended-upgrades can be holding the lock even on a build droplet.
  apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends docker.io
else
  echo "[image-build] Docker already installed"
fi

systemctl enable --now docker

# docker.io's socket is root:docker 0660 by default; the gateway runs as
# SERVICE_USER, not root (see files/empyralis-gateway.service's User=). Doing
# this at bake time, before the image is snapshotted, means the first real
# boot's gateway process picks up the group membership immediately — no
# restart needed, unlike the boot-time installer which races a freshly
# started gateway against a freshly modified group file.
usermod -aG docker "${SERVICE_USER}"

# Verify the way the gateway itself will: as SERVICE_USER, not root. A
# freshly exec'd `sudo -u` process re-reads group membership, so this sees
# the usermod above without a new login session.
attempt=0
ready=0
for attempt in 1 2 3 4 5; do
  if sudo -u "${SERVICE_USER}" docker info >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "${ready}" != "1" ]]; then
  echo "[image-build] ERROR: docker was installed but 'docker info' did not succeed as ${SERVICE_USER} after ${attempt} attempts" >&2
  exit 1
fi

echo "[image-build] Docker is installed and ready as ${SERVICE_USER}"
