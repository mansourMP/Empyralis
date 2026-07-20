#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_USER="${EMPYRALIS_SERVICE_USER:-empyralis}"
INSTALL_ROOT="${EMPYRALIS_INSTALL_ROOT:-/opt/empyralis/agent-computer}"
STATE_ROOT="${EMPYRALIS_STATE_ROOT:-/var/lib/empyralis/agent-computer}"
CONFIG_DIR="${EMPYRALIS_CONFIG_DIR:-/etc/empyralis}"
LOG_DIR="${EMPYRALIS_LOG_DIR:-/var/log/empyralis}"
RUN_DIR="${EMPYRALIS_RUN_DIR:-/run/empyralis}"
ENV_FILE="${CONFIG_DIR}/agent-computer.env"
BIN_DIR="${INSTALL_ROOT}/bin"
CURRENT_DIR="${INSTALL_ROOT}/current"
GATEWAY_SERVICE="empyralis-gateway.service"

DEFAULT_API_URL="https://empyralis.ai/api"
API_URL="${EMPYRALIS_API_URL:-${EMPYRALIS_GATEWAY_API_URL:-${DEFAULT_API_URL}}}"
PAIRING_TOKEN="${EMPYRALIS_PAIRING_TOKEN:-${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}}"
AGENT_COMPUTER_VERSION="${EMPYRALIS_AGENT_COMPUTER_VERSION:-latest}"
# The gateway ships as a prebuilt, self-contained artifact (dist/ +
# node_modules) published to our own release host — the box only downloads
# and runs it, never clones or builds. This is the zero-credential public
# installer: no repo token ever lands on a provisioned box, so a compromised
# box cannot read the private source. (Supersedes the interim git-clone
# build; that path is retained below only behind an explicit opt-in for a
# repo collaborator installing on their own machine.)
ARTIFACT_BASE_URL="${EMPYRALIS_ARTIFACT_BASE_URL:-https://empyralis.ai/releases/agent-computer/${AGENT_COMPUTER_VERSION}}"
GATEWAY_ARTIFACT_URL="${EMPYRALIS_GATEWAY_ARTIFACT_URL:-${ARTIFACT_BASE_URL}/empyralis-gateway-linux-x64.tar.gz}"
# Opt-in source build (collaborator-only): set EMPYRALIS_GATEWAY_BUILD_FROM_SOURCE=1
# and supply EMPYRALIS_REPO_TOKEN. Off by default — the artifact path above
# is the norm.
BUILD_FROM_SOURCE="${EMPYRALIS_GATEWAY_BUILD_FROM_SOURCE:-0}"
REPO_URL="${EMPYRALIS_REPO_URL:-https://github.com/mansourMP/Empyralis.git}"
REPO_REF="${EMPYRALIS_REPO_REF:-verify}"
REPO_TOKEN="${EMPYRALIS_REPO_TOKEN:-}"
DISPLAY_NAME="${EMPYRALIS_GATEWAY_DISPLAY_NAME:-$(hostname -f 2>/dev/null || hostname)}"
# This installer only ever provisions a dedicated Agent Computer box (no
# "local dev machine" use case exists for it, unlike agent_computer.sh) —
# full-account Telegram/WhatsApp channels are the model this product runs,
# so they default on here. Overridable for an operator who explicitly
# wants a channel-free box.
PERSONAL_CHANNELS_ENABLED="${EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED:-true}"
REGISTRATION_TIMEOUT_SECONDS="${EMPYRALIS_REGISTRATION_TIMEOUT_SECONDS:-180}"

log() {
  printf '[empyralis-agent-computer] %s\n' "$*"
}

fail() {
  printf '[empyralis-agent-computer] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "$(id -u)" != "0" ]]; then
    fail "run this installer as root, for example: curl -fsSL https://empyralis.ai/install/agent-computer.sh | sudo bash"
  fi
}

detect_ubuntu() {
  local ID VERSION_ID PRETTY_NAME
  if [[ ! -r /etc/os-release ]]; then
    fail "unsupported OS: /etc/os-release is missing"
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    fail "unsupported OS: ${PRETTY_NAME:-unknown}. Ubuntu 22.04 or 24.04 is required"
  fi
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *) fail "unsupported Ubuntu version: ${VERSION_ID:-unknown}. Ubuntu 22.04 or 24.04 is required" ;;
  esac
}

require_pairing_token() {
  if [[ -z "${PAIRING_TOKEN}" ]]; then
    fail "EMPYRALIS_PAIRING_TOKEN is required"
  fi
}

apt_install_system_deps() {
  export DEBIAN_FRONTEND=noninteractive
  log "installing system dependencies"
  apt-get update -y
  apt-get install -y --no-install-recommends \
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
}

install_node20() {
  if [[ "${EMPYRALIS_INSTALL_SKIP_NODE:-0}" == "1" ]]; then
    log "skipping Node install because EMPYRALIS_INSTALL_SKIP_NODE=1"
    return
  fi
  if command -v node >/dev/null 2>&1 && node --version 2>/dev/null | grep -Eq '^v20\.'; then
    log "Node 20 already installed"
    return
  fi
  log "installing Node 20 LTS"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y --no-install-recommends nodejs
  if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v20\.'; then
    fail "Node 20 install failed"
  fi
  if ! command -v npm >/dev/null 2>&1; then
    fail "npm was not installed with Node 20"
  fi
}

create_service_user() {
  if id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "service user ${SERVICE_USER} already exists"
    return
  fi
  log "creating service user ${SERVICE_USER}"
  useradd --system \
    --home-dir /var/lib/empyralis \
    --create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
}

prepare_directories() {
  mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}" "${STATE_ROOT}/gateway" "${CONFIG_DIR}" "${LOG_DIR}" "${RUN_DIR}"
  # BYO-brain: writable npm global prefix for cli.install (@openai/codex etc.).
  # /usr/lib/node_modules is EACCES under this service's strict sandbox
  # (ProtectSystem=strict). This dir sits under INSTALL_ROOT which is already
  # in the gateway service's ReadWritePaths, and its bin/ is prepended to
  # PATH via the env file below, so cli.install lands binaries where the
  # gateway can find and exec them without further wiring.
  mkdir -p "${INSTALL_ROOT}/cli/bin" "${INSTALL_ROOT}/cli/lib"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}" "${INSTALL_ROOT}/cli"
  chmod 0750 "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}"
  chmod 0755 "${INSTALL_ROOT}" "${BIN_DIR}" "${CONFIG_DIR}" "${INSTALL_ROOT}/cli" "${INSTALL_ROOT}/cli/bin"
}

existing_env_value() {
  local key="$1"
  if [[ ! -r "${ENV_FILE}" ]]; then
    return 1
  fi
  awk -F= -v key="${key}" '
    $1 == key {
      value = substr($0, length(key) + 2)
      gsub(/^"/, "", value)
      gsub(/"$/, "", value)
      print value
      exit 0
    }
  ' "${ENV_FILE}"
}

shell_quote_env() {
  local value="$1"
  printf '"%s"' "${value//\"/\\\"}"
}

write_env_file() {
  local existing_gateway_token existing_gateway_id existing_device_id previous_umask
  existing_gateway_token="$(existing_env_value EMPYRALIS_GATEWAY_TOKEN || true)"
  existing_gateway_id="$(existing_env_value EMPYRALIS_GATEWAY_ID || true)"
  existing_device_id="$(existing_env_value EMPYRALIS_GATEWAY_DEVICE_ID || true)"

  log "writing ${ENV_FILE}"
  previous_umask="$(umask)"
  umask 0077
  {
    printf 'EMPYRALIS_PAIRING_TOKEN=%s\n' "$(shell_quote_env "${PAIRING_TOKEN}")"
    printf 'EMPYRALIS_GATEWAY_PAIRING_TOKEN=%s\n' "$(shell_quote_env "${PAIRING_TOKEN}")"
    printf 'EMPYRALIS_API_URL=%s\n' "$(shell_quote_env "${API_URL}")"
    printf 'EMPYRALIS_GATEWAY_API_URL=%s\n' "$(shell_quote_env "${API_URL}")"
    printf 'NODE_ENV="production"\n'
    printf 'EMPYRALIS_DEPLOY_ENV="agent-computer"\n'
    printf 'EMPYRALIS_GATEWAY_STATE_DIR=%s\n' "$(shell_quote_env "${STATE_ROOT}/gateway")"
    printf 'EMPYRALIS_STATE_HOME=%s\n' "$(shell_quote_env "${STATE_ROOT}/.empyralis/state")"
    printf 'EMPYRALIS_GATEWAY_DISPLAY_NAME=%s\n' "$(shell_quote_env "${DISPLAY_NAME}")"
    printf 'EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT=%s\n' "$(shell_quote_env "${CURRENT_DIR}")"
    printf 'EMPYRALIS_GATEWAY_BROWSER_PYTHON="python3"\n'
    printf 'EMPYRALIS_AGENT_COMPUTER_INSTALL_DIR=%s\n' "$(shell_quote_env "${CURRENT_DIR}")"
    printf 'EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED=%s\n' "$(shell_quote_env "${PERSONAL_CHANNELS_ENABLED}")"
    # BYO-brain: enable cli.install / cli.login.* by default on a
    # user-paired gateway. The flag exists for a hardening story that never
    # applied to a self-paired box; leaving it off dead-ends every user's
    # first Install click with a "your operator has to turn this on" wall
    # they can't reach without SSH.
    printf 'EMPYRALIS_GATEWAY_CLI_SETUP_ENABLED="true"\n'
    # BYO-brain: point npm's global prefix at a directory already in the
    # gateway service's ReadWritePaths, so `npm install -g @openai/codex`
    # succeeds instead of EACCES against /usr/lib/node_modules. Also
    # prepend that bin/ to PATH so the gateway process can find the CLI
    # after cli.install lands it. See cli-installer.ts for the paired
    # gateway-side --prefix defense-in-depth.
    printf 'NPM_CONFIG_PREFIX=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/cli")"
    printf 'PATH=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/cli/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")"
    if [[ -n "${existing_gateway_token}" ]]; then
      printf 'EMPYRALIS_GATEWAY_TOKEN=%s\n' "$(shell_quote_env "${existing_gateway_token}")"
    fi
    if [[ -n "${existing_gateway_id}" ]]; then
      printf 'EMPYRALIS_GATEWAY_ID=%s\n' "$(shell_quote_env "${existing_gateway_id}")"
    fi
    if [[ -n "${existing_device_id}" ]]; then
      printf 'EMPYRALIS_GATEWAY_DEVICE_ID=%s\n' "$(shell_quote_env "${existing_device_id}")"
    fi
  } > "${ENV_FILE}"
  umask "${previous_umask}"
  chown root:"${SERVICE_USER}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
}

clone_gateway_source() {
  local clone_dir="$1"
  log "cloning ${REPO_URL} (ref ${REPO_REF})"
  if [[ -n "${REPO_TOKEN}" ]]; then
    # Token travels as an HTTP header, never in the remote URL — a URL-
    # embedded credential gets written verbatim into .git/config, which
    # would leave it sitting in cleartext on disk indefinitely.
    if ! git -c "http.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "${REPO_TOKEN}" | base64 | tr -d '\n')" \
      clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${clone_dir}"; then
      fail "could not clone ${REPO_URL} — check EMPYRALIS_REPO_TOKEN has read access and EMPYRALIS_REPO_REF (${REPO_REF}) exists"
    fi
  else
    if ! git clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${clone_dir}"; then
      fail "could not clone ${REPO_URL} — if this repo is private, set EMPYRALIS_REPO_TOKEN"
    fi
  fi
}

install_gateway_from_source() {
  # Collaborator-only opt-in (EMPYRALIS_GATEWAY_BUILD_FROM_SOURCE=1). Clones
  # the private repo and builds on the box — needs EMPYRALIS_REPO_TOKEN and
  # leaves a repo-read credential in cloud-init env, so it is NOT the default.
  local stage_dir="$1"
  rm -rf "${stage_dir}"
  clone_gateway_source "${stage_dir}"
  log "building the gateway from source"
  ( cd "${stage_dir}/empyralis-gateway" && npm install && npm run build ) \
    || fail "gateway build failed"
  # The running gateway has no use for git history, and .git/config can
  # carry credential material (e.g. a credential-helper cache) — drop it
  # rather than leave it sitting under the gateway's own ReadWritePaths.
  rm -rf "${stage_dir}/.git"
}

install_gateway_from_artifact() {
  # Default path: download the prebuilt, self-contained gateway artifact
  # (dist/ + node_modules + package.json) and unpack it. No git, no build,
  # no credential of any kind on the box.
  local stage_dir="$1"
  local tmp_dir gateway_archive
  tmp_dir="$(mktemp -d)"
  gateway_archive="${tmp_dir}/gateway.tar.gz"

  log "downloading prebuilt gateway artifact ${GATEWAY_ARTIFACT_URL}"
  if ! curl -fsSL "${GATEWAY_ARTIFACT_URL}" -o "${gateway_archive}"; then
    rm -rf "${tmp_dir}"
    fail "could not download gateway artifact from ${GATEWAY_ARTIFACT_URL}"
  fi

  rm -rf "${stage_dir}"
  mkdir -p "${stage_dir}/empyralis-gateway"
  # The archive root holds dist/, node_modules/, package.json — unpack it
  # into empyralis-gateway/ so the layout matches the source-build path (and
  # the `gateway` symlink / run-gateway fast path resolve identically).
  if ! tar -xzf "${gateway_archive}" -C "${stage_dir}/empyralis-gateway"; then
    rm -rf "${tmp_dir}" "${stage_dir}"
    fail "could not extract gateway artifact"
  fi
  rm -rf "${tmp_dir}"

  if [[ ! -f "${stage_dir}/empyralis-gateway/dist/index.js" ]]; then
    fail "gateway artifact is missing dist/index.js — the published archive is malformed"
  fi
}

install_release_artifacts() {
  local release_dir stage_dir
  release_dir="${INSTALL_ROOT}/releases/${AGENT_COMPUTER_VERSION}"
  stage_dir="${release_dir}.tmp"

  if [[ "${BUILD_FROM_SOURCE}" == "1" ]]; then
    install_gateway_from_source "${stage_dir}"
  else
    install_gateway_from_artifact "${stage_dir}"
  fi

  rm -rf "${release_dir}"
  mv "${stage_dir}" "${release_dir}"
  # Convenience alias so run-gateway's `${INSTALL_DIR}/gateway/...` fast
  # path resolves directly, matching the layout callers already expect.
  ln -sfn empyralis-gateway "${release_dir}/gateway"
  ln -sfn "${release_dir}" "${CURRENT_DIR}"
  chown -R root:root "${INSTALL_ROOT}/releases" "${CURRENT_DIR}"
  find "${release_dir}" -type d -exec chmod 0755 {} +
  find "${release_dir}" -type f -exec chmod u=rw,go=r {} +
}

write_launcher_scripts() {
  cat > "${BIN_DIR}/run-gateway" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
ENV_FILE="${EMPYRALIS_ENV_FILE:-/etc/empyralis/agent-computer.env}"
if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi
INSTALL_DIR="${EMPYRALIS_AGENT_COMPUTER_INSTALL_DIR:-/opt/empyralis/agent-computer/current}"
entry=""
for candidate in \
  "${INSTALL_DIR}/gateway/dist/index.js" \
  "${INSTALL_DIR}/gateway/index.js" \
  "${INSTALL_DIR}/gateway/build/index.js"; do
  if [[ -f "${candidate}" ]]; then
    entry="${candidate}"
    break
  fi
done
if [[ -z "${entry}" ]]; then
  entry="$(find "${INSTALL_DIR}/gateway" -type f -path '*/dist/index.js' 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${entry}" ]]; then
  entry="$(find "${INSTALL_DIR}" -type f -path '*/dist/index.js' 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${entry}" ]]; then
  entry="$(find "${INSTALL_DIR}" -type f -name 'index.js' 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${entry}" || ! -f "${entry}" ]]; then
  echo "gateway Node entrypoint not found under ${INSTALL_DIR}" >&2
  exit 127
fi
cd "$(dirname "${entry}")"
exec node "${entry}"
EOF

  chmod 0755 "${BIN_DIR}/run-gateway"
}

write_systemd_units() {
  log "writing systemd units"
  cat > "/etc/systemd/system/${GATEWAY_SERVICE}" <<SYSEOF
[Unit]
Description=Empyralis Agent Computer Gateway
Documentation=https://empyralis.ai
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${CURRENT_DIR}
ExecStart=${BIN_DIR}/run-gateway
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
NoNewPrivileges=true

# ── OS CONFINEMENT ────────────────────────────────────────────────────────
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
PrivateDevices=true
ProtectProc=invisible
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
# NOTE: MemoryDenyWriteExecute is intentionally NOT set. The gateway is a
# Node.js process, and V8's JIT allocates writable+executable memory on
# startup; with MemoryDenyWriteExecute=true, Node aborts immediately with a
# V8 fatal error ("SetPermissionsOnExecutableMemoryChunk" / W^X), so the
# gateway never starts and the box hangs at "provisioning" forever. Verified
# on ubuntu 24.04 / node v20. Every other hardening directive here is
# Node-compatible and stays; this one is fundamentally incompatible.
RestrictRealtime=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=

# Gateway needs write access to state, logs, and its install root
ReadWritePaths=${STATE_ROOT} ${INSTALL_ROOT} ${LOG_DIR} ${RUN_DIR}
ReadOnlyPaths=${CONFIG_DIR}

[Install]
WantedBy=multi-user.target
SYSEOF
}

systemd_available() {
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

start_with_systemd() {
  log "starting services with systemd"
  systemctl daemon-reload
  systemctl enable "${GATEWAY_SERVICE}" >/dev/null
  systemctl restart "${GATEWAY_SERVICE}"
}

direct_service_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1
}

start_direct_service() {
  local name="$1"
  local runner="$2"
  local pid_file="${RUN_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"

  if direct_service_running "${pid_file}"; then
    log "${name} is already running without systemd"
    return
  fi

  rm -f "${pid_file}"
  touch "${log_file}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${log_file}"
  log "starting ${name} without systemd"
  sudo -u "${SERVICE_USER}" \
    env -i HOME="/var/lib/empyralis" USER="${SERVICE_USER}" PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "${runner}" >> "${log_file}" 2>&1 &
  echo "$!" > "${pid_file}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${pid_file}"
}

start_without_systemd() {
  log "WARNING: systemd is not active; using direct process fallback"
  log ""
  log "!!! DIRECT PROCESS MODE — OS CONFINEMENT IS NOT ACTIVE !!!"
  log "When running outside systemd, ProtectSystem, ProtectHome, PrivateDevices,"
  log "MemoryDenyWriteExecute, CapabilityBoundingSet, and all other systemd-level"
  log "protections are UNAVAILABLE.  The agent has full filesystem access."
  log "For production deployments, systemd is REQUIRED."
  log ""
  start_direct_service "empyralis-gateway" "${BIN_DIR}/run-gateway"
}

start_services() {
  if systemd_available; then
    start_with_systemd
  else
    start_without_systemd
  fi
}

print_recent_logs() {
  if systemd_available; then
    journalctl -u "${GATEWAY_SERVICE}" -n 80 --no-pager || true
  else
    tail -n 80 "${LOG_DIR}/empyralis-gateway.log" 2>/dev/null || true
  fi
}

registration_file_present() {
  [[ -s "${STATE_ROOT}/gateway/registration.json" ]]
}

wait_for_registration() {
  local deadline
  deadline=$((SECONDS + REGISTRATION_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if registration_file_present; then
      return 0
    fi
    sleep 2
  done
  print_recent_logs
  fail "Gateway did not confirm registration within ${REGISTRATION_TIMEOUT_SECONDS}s"
}

final_status() {
  wait_for_registration
  log "Agent Computer connected"
}

main() {
  require_root
  detect_ubuntu
  require_pairing_token
  apt_install_system_deps
  install_node20
  create_service_user
  prepare_directories
  write_env_file
  install_release_artifacts
  write_launcher_scripts
  write_systemd_units
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}"
  start_services
  final_status
}

main "$@"
