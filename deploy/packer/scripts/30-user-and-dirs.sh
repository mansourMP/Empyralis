#!/usr/bin/env bash
# Service user and directory tree — port of create_service_user() and
# prepare_directories() from scripts/install-agent-computer.sh (lines 201-236).
#
# The installer parameterises every path through an environment variable
# (EMPYRALIS_INSTALL_ROOT and friends). On a shared base image those are fixed
# constants — there is no caller to override them at bake time — so they are
# hard-coded here to the installer's own defaults. They must not drift.
set -Eeuo pipefail

SERVICE_USER="empyralis"
INSTALL_ROOT="/opt/empyralis/agent-computer"
STATE_ROOT="/var/lib/empyralis/agent-computer"
CONFIG_DIR="/etc/empyralis"
LOG_DIR="/var/log/empyralis"
BIN_DIR="${INSTALL_ROOT}/bin"

# NOTE ON /run/empyralis: the installer also creates RUN_DIR=/run/empyralis
# here. /run is a tmpfs — it is wiped on every boot — so creating it at bake
# time would accomplish nothing, and the gateway unit's
# `ReadWritePaths=/run/empyralis` would then fail at first boot with
# "Failed to set up mount namespacing: No such file or directory".
# Instead the baked unit uses systemd's RuntimeDirectory=empyralis, which
# recreates /run/empyralis with the correct owner and mode on every start.
# See files/empyralis-gateway.service.

if id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "[image-build] service user ${SERVICE_USER} already exists"
else
  echo "[image-build] creating service user ${SERVICE_USER}"
  useradd --system \
    --home-dir /var/lib/empyralis \
    --create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi

mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}" "${STATE_ROOT}/gateway" "${CONFIG_DIR}" "${LOG_DIR}"

# BYO-brain: writable npm global prefix for cli.install (@openai/codex etc.).
# /usr/lib/node_modules is EACCES under the gateway service's strict sandbox
# (ProtectSystem=strict). These live under INSTALL_ROOT, which is in the unit's
# ReadWritePaths, and cli/bin is prepended to PATH by the env file the
# provisioner writes at first boot.
mkdir -p "${INSTALL_ROOT}/cli/bin" "${INSTALL_ROOT}/cli/lib"

# npm's cache and the installed CLIs' own config resolve off $HOME. The service
# $HOME from /etc/passwd is /var/lib/empyralis, which is NOT in ReadWritePaths
# (only its STATE_ROOT subdir is), so `npm install -g` dies with EACCES creating
# ~/.npm/_cacache. Give the service its own writable HOME + npm cache under
# INSTALL_ROOT rather than widening the sandbox.
mkdir -p "${INSTALL_ROOT}/cli/home" "${INSTALL_ROOT}/cli/npm-cache"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STATE_ROOT}" "${LOG_DIR}" "${INSTALL_ROOT}/cli"
chmod 0750 "${STATE_ROOT}" "${LOG_DIR}" "${INSTALL_ROOT}/cli/home" "${INSTALL_ROOT}/cli/npm-cache"
chmod 0755 "${INSTALL_ROOT}" "${BIN_DIR}" "${CONFIG_DIR}" "${INSTALL_ROOT}/cli" "${INSTALL_ROOT}/cli/bin"

echo "[image-build] user and directory tree ready"
