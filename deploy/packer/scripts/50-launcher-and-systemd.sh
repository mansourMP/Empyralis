#!/usr/bin/env bash
# Launcher + systemd unit — port of write_launcher_scripts() and
# write_systemd_units() from scripts/install-agent-computer.sh (lines 412-525).
#
# The unit is ENABLED but NOT STARTED. There is no configuration on this image
# to start it with: no pairing token, no API URL, no env file. The unit carries
# ConditionPathExists=/etc/empyralis/agent-computer.env so that an unconfigured
# first boot skips it cleanly instead of burning through systemd's start rate
# limit — see the long comment in files/empyralis-gateway.service.
set -Eeuo pipefail

FILES_DIR="/tmp/empyralis-image-files"
INSTALL_ROOT="/opt/empyralis/agent-computer"
BIN_DIR="${INSTALL_ROOT}/bin"
GATEWAY_SERVICE="empyralis-gateway.service"

install -m 0755 -o root -g root "${FILES_DIR}/run-gateway" "${BIN_DIR}/run-gateway"

# First-boot configuration helper. Inert until the provisioning service is
# rewired to call it; see the header of files/empyralis-configure.
install -m 0755 -o root -g root "${FILES_DIR}/empyralis-configure" /usr/local/sbin/empyralis-configure

install -m 0644 -o root -g root "${FILES_DIR}/empyralis-gateway.service" \
  "/etc/systemd/system/${GATEWAY_SERVICE}"

systemctl daemon-reload
systemctl enable "${GATEWAY_SERVICE}"

# Explicitly assert what we just claimed, rather than assuming it.
if ! systemctl is-enabled "${GATEWAY_SERVICE}" >/dev/null 2>&1; then
  echo "[image-build] ERROR: ${GATEWAY_SERVICE} did not enable" >&2
  exit 1
fi

if systemctl is-active --quiet "${GATEWAY_SERVICE}"; then
  echo "[image-build] ERROR: ${GATEWAY_SERVICE} is RUNNING on the build droplet. It must never start at bake time — there is no config, and anything it wrote would be baked into every customer box." >&2
  exit 1
fi

rm -rf "${FILES_DIR}"

echo "[image-build] launcher installed; ${GATEWAY_SERVICE} enabled and stopped"
