#!/usr/bin/env bash
# Node 20 LTS — port of install_node20() from scripts/install-agent-computer.sh
# (lines 181-199), with one deliberate change.
#
# The installer does:   curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
# We download first, check it, then run it.
#
# Under `set -o pipefail` a curl-into-bash pipe does report curl's failure, so
# this is not strictly the same bug as the cloud-init one. But the whole point
# of this repo's recent history is that "the pipeline probably propagates the
# error" is not a claim worth betting a provision on. Downloading first makes
# curl's exit code and the HTTP status directly inspectable, and makes a
# NodeSource outage produce a build log that names the cause.
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive

if command -v node >/dev/null 2>&1 && node --version 2>/dev/null | grep -Eq '^v20\.'; then
  echo "[image-build] Node 20 already installed: $(node --version)"
else
  echo "[image-build] installing Node 20 LTS"

  setup_script="$(mktemp)"
  http_status=""
  curl_exit=0
  http_status="$(curl -sS -L -m 120 -w '%{http_code}' \
    -o "${setup_script}" https://deb.nodesource.com/setup_20.x)" || curl_exit=$?

  if [[ "${curl_exit}" -ne 0 || "${http_status}" != "200" || ! -s "${setup_script}" ]]; then
    echo "[image-build] ERROR: could not download the NodeSource setup script (HTTP ${http_status:-none}, curl exit ${curl_exit})" >&2
    exit 1
  fi

  # Explicit bash. The NodeSource script is bash, not POSIX sh.
  bash "${setup_script}"
  rm -f "${setup_script}"

  apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends nodejs
fi

# Same assertions the installer makes, kept because a half-installed Node is
# exactly the state that produces a box which boots and then does nothing.
if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v20\.'; then
  echo "[image-build] ERROR: Node 20 install failed (got: $(node --version 2>&1 || echo 'no node'))" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[image-build] ERROR: npm was not installed with Node 20" >&2
  exit 1
fi

echo "[image-build] node $(node --version), npm $(npm --version)"
