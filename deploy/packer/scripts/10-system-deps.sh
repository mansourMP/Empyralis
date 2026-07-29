#!/usr/bin/env bash
# System dependencies — a faithful port of apt_install_system_deps() from
# scripts/install-agent-computer.sh (lines 156-179).
#
# The package list below is byte-for-byte the installer's list. If the installer
# ever gains a package, it must be added here too, or a droplet booted from this
# image will be missing something the fallback path has. That divergence is the
# main standing risk of pre-baking, so keep the two lists literally identical.
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[image-build] installing system dependencies"

# DPkg::Lock::Timeout is not optional — same reasoning as the installer: a held
# lock makes apt block forever rather than fail, and a hang reports nothing.
apt-get -o DPkg::Lock::Timeout=300 update -y

apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends \
  ca-certificates \
  curl \
  git \
  build-essential \
  openssl \
  sudo \
  tar \
  gzip \
  xz-utils \
  python3 \
  python3-minimal

# Fold in security updates at bake time. On the boot-install architecture this
# was impossible to do without adding minutes to every provision; here it costs
# nothing at boot. Mirrors DigitalOcean's own marketplace image guidance.
apt-get -o DPkg::Lock::Timeout=300 \
  -o Dpkg::Options::="--force-confold" \
  upgrade -y

echo "[image-build] system dependencies installed"
