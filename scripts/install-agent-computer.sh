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
# The box operator's LOCAL half of the shell_sandbox full_access opt-in (see
# empyralis-gateway/src/config.ts's shellFullAccessLocallyEnabled doc
# comment for the other, server-asserted half). Off by default — sandbox
# stays the floor unless the pairing command this came from explicitly
# exported it (GatewayPairPanel.tsx only ever adds that export line when the
# owner checked the full_access box in the pairing UI). Written into the
# systemd EnvironmentFile below like every other setting here, not just left
# in this install script's own one-shot process environment — otherwise a
# `curl | sh` export would silently vanish the moment this script exits and
# systemd starts the actual long-running gateway service.
SHELL_FULL_ACCESS_ENABLED="${EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED:-false}"
# How long to wait, after the gateway service has been started, for it to
# phone home and land registration.json. This clock starts AFTER apt, Node 20
# and the artifact download are already done, so it is purely gateway
# boot -> POST /gateway/registrations -> write state file.
#
# MAN-121: this was 180s. On the smallest droplets that is tight enough to be
# a coin flip — a cold Node process on 1 shared vCPU, plus a first outbound TLS
# handshake to the platform, plus systemd's Restart=always/RestartSec=5 backoff
# if the very first attempt loses a race with cloud-init's network setup, can
# comfortably eat past three minutes. Widened to 600s, which sits deliberately
# under the pairing intent's 15-minute TTL (DEFAULT_GATEWAY_PAIRING_TTL_SECONDS
# in server_modules/gateway_pairing_service.py) and under the backend's own
# 20-minute watch window (VPS_CONNECT_TIMEOUT_SECONDS), so this timer can never
# be the first thing to give up — the authoritative clocks stay authoritative.
# Note this timer does not stop anything: the systemd unit keeps running and
# retrying past it, which is why the beacon it sends is advisory, not terminal.
REGISTRATION_TIMEOUT_SECONDS="${EMPYRALIS_REGISTRATION_TIMEOUT_SECONDS:-600}"

# MAN-121 failure channel. Until now the box only ever reported SUCCESS (via
# POST /gateway/registrations); every install failure died here in stderr,
# readable only by SSHing into the droplet. The platform could therefore only
# infer "timed out", never explain anything. These few lines give the box a way
# to say what went wrong, using the pairing token and API URL it already has.
INSTALL_PHASE="startup"
BEACON_SENT=0

json_escape() {
  # Pure bash so this works before apt has installed anything.
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\r'/}"
  s="${s//$'\n'/ }"
  s="${s//$'\t'/ }"
  printf '%s' "${s}"
}

report_beacon() {
  # report_beacon <terminal:0|1> <message> [kind]
  # kind: "problem" (default) or "progress" — see set_phase.
  local terminal="$1"
  local message="$2"
  local kind="${3:-problem}"
  local payload
  if [[ "${EMPYRALIS_DISABLE_INSTALL_BEACON:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -z "${PAIRING_TOKEN}" || -z "${API_URL}" ]]; then
    return 0
  fi
  # First FAILURE beacon wins: the first failure is the useful one, and later
  # noise from unwinding shouldn't overwrite it. Progress beacons (terminal=0
  # from set_phase) are exempt — they are the whole point of reporting more
  # than once — and a failure is still allowed to land after them.
  if [[ "${terminal}" == "1" ]]; then
    if (( BEACON_SENT )); then
      return 0
    fi
    BEACON_SENT=1
  fi
  payload="$(printf '{"pairing_token":"%s","phase":"%s","message":"%s","terminal":%s,"kind":"%s"}' \
    "$(json_escape "${PAIRING_TOKEN}")" \
    "$(json_escape "${INSTALL_PHASE}")" \
    "$(json_escape "${message}")" \
    "$([[ "${terminal}" == "1" ]] && printf 'true' || printf 'false')" \
    "${kind}")"
  # Never let reporting a failure cause a further failure.
  curl -fsS -m 15 -X POST "${API_URL%/}/gateway/provisioning-events" \
    -H 'content-type: application/json' \
    --data-binary "${payload}" >/dev/null 2>&1 || true
}

set_phase() {
  # set_phase <phase> [human message]
  #
  # The platform's ONLY window into a running install. Until this existed the
  # beacon fired solely on failure, so a box that was merely slow and a box
  # that was wedged looked identical from the control plane: "Installing Agent
  # Computer…" and nothing else, for as long as it took. Every provision in
  # this product's history therefore recorded an empty install_phase.
  #
  # Progress is reported with terminal=false, so record_vps_install_event
  # annotates the record without failing it. The frontend already renders
  # these phase names (see INSTALL_PHASE_LABELS in vps-provision-watch.ts);
  # they simply never arrived. Beaconing is best-effort and time-boxed, so a
  # slow or unreachable control plane can never wedge an install that is
  # otherwise fine.
  INSTALL_PHASE="$1"
  report_beacon 0 "${2:-started ${1}}" progress
}

log() {
  printf '[empyralis-agent-computer] %s\n' "$*"
}

fail() {
  printf '[empyralis-agent-computer] ERROR: %s\n' "$*" >&2
  report_beacon 1 "$*"
  exit 1
}

on_unexpected_error() {
  # set -Eeuo pipefail kills the installer on any unchecked command failure;
  # without this those deaths are invisible to the platform too. `exit` from
  # fail() does not trigger ERR, so this only catches what fail() missed.
  local code=$?
  report_beacon 1 "installer aborted during '${INSTALL_PHASE}' (exit ${code}, line ${BASH_LINENO[0]:-unknown})"
  exit "${code}"
}

trap on_unexpected_error ERR

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
  # DPkg::Lock::Timeout is not optional here. A fresh Ubuntu droplet runs
  # unattended-upgrades on first boot; if apt is invoked while that holds the
  # lock, apt BLOCKS INDEFINITELY rather than failing. An indefinite block
  # fires no failure beacon (only a crash does), so the box goes silent and
  # the control plane can only report an opaque timeout — the same
  # indistinguishable-silence class of bug as the dash/bash regression.
  # Bounded wait turns a hang into a real, reportable error.
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

install_docker() {
  # Cloud Agent Computer boxes run `shell.execute` and `filesystem.read_write`
  # inside a Docker sandbox — the gateway only ever advertises those two
  # capabilities once a live `docker info` probe succeeds (see
  # empyralis-gateway/src/health/service-inventory.ts's probeDocker, which
  # feeds runtime/desktop-permissions.ts's shellSandboxDockerReady). Nothing
  # in this installer used to put Docker on the box at all, so every
  # provisioned droplet came up paired and "online" while unable to run a
  # single shell command.
  #
  # Distro packaging (docker.io), not Docker's get.docker.com convenience
  # script: one already-mirrored apt package, no extra apt repo, no GPG key
  # fetch, no curl-pipe-to-root — and it is everything the sandboxed
  # `docker run` shell.execute path needs.
  #
  # DELIBERATELY NON-FATAL. A box that cannot get Docker must still finish
  # provisioning and come up with every OTHER capability (cli.install,
  # llm.generate, ...) rather than dying here — see the `|| log ...` wrapper
  # around this call in main(). Every failure path below returns 1 and
  # reports an advisory (terminal=0) beacon instead of calling fail().
  if [[ "${EMPYRALIS_INSTALL_SKIP_DOCKER:-0}" == "1" ]]; then
    log "skipping Docker install because EMPYRALIS_INSTALL_SKIP_DOCKER=1"
    return 0
  fi

  if ! command -v docker >/dev/null 2>&1; then
    log "installing Docker (docker.io)"
    # Same DPkg::Lock::Timeout guard as apt_install_system_deps, for the same
    # reason: unattended-upgrades can already be holding the lock on a fresh
    # droplet, and an indefinite block here would be silent exactly like it
    # would be there.
    if ! apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends docker.io; then
      log "WARNING: docker.io install failed"
      report_beacon 0 "docker.io install failed; shell.execute and filesystem.read_write need Docker and will stay unavailable until it is installed manually"
      return 1
    fi
  else
    log "Docker already installed"
  fi

  if systemd_available; then
    systemctl enable --now docker >/dev/null 2>&1 || true
  else
    service docker start >/dev/null 2>&1 || true
  fi

  # The gateway runs as SERVICE_USER, not root (see write_systemd_units'
  # User=${SERVICE_USER}), and docker.io's socket is root:docker 0660 by
  # default — a perfectly running daemon still answers `docker info` with
  # permission denied for a user outside that group. docker.io's postinst
  # creates the `docker` group; adding SERVICE_USER to it here — before
  # start_services() ever spawns the gateway process (see main()) — means the
  # very first gateway process picks up the membership, no restart needed.
  if ! usermod -aG docker "${SERVICE_USER}"; then
    log "WARNING: could not add ${SERVICE_USER} to the docker group"
    report_beacon 0 "could not add ${SERVICE_USER} to the docker group; shell.execute and filesystem.read_write will stay unavailable"
    return 1
  fi

  # Verify the way the gateway itself will: as SERVICE_USER, not root. A
  # freshly exec'd `sudo -u` process re-reads group membership, so this sees
  # the usermod above without needing a new login session — the same
  # guarantee the freshly-started gateway process gets.
  local attempt
  for attempt in 1 2 3 4 5; do
    if sudo -u "${SERVICE_USER}" docker info >/dev/null 2>&1; then
      log "Docker is installed and ready"
      return 0
    fi
    sleep 2
  done

  log "WARNING: docker was installed but 'docker info' did not succeed as ${SERVICE_USER} after ${attempt} attempts"
  report_beacon 0 "docker installed but 'docker info' did not succeed as ${SERVICE_USER} after ${attempt} attempts; shell.execute and filesystem.read_write may take longer to come online, or check 'systemctl status docker' on the server"
  return 1
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
  # npm's cache (~/.npm) and the installed CLIs' own config (~/.claude,
  # ~/.codex) resolve off $HOME. Under systemd's User= the service $HOME is
  # /var/lib/empyralis (from the passwd entry) — NOT in ReadWritePaths (only
  # its STATE_ROOT subdir is), so ProtectSystem=strict makes it read-only and
  # `npm install -g` dies with EACCES creating ~/.npm/_cacache, even though
  # NPM_CONFIG_PREFIX was already writable. Verified: read-only $HOME -> npm
  # EACCES; writable $HOME + NPM_CONFIG_CACHE -> install succeeds. Give the
  # service its own writable HOME + npm cache under INSTALL_ROOT (already in
  # ReadWritePaths) instead of widening the sandbox to the real home dir.
  mkdir -p "${INSTALL_ROOT}/cli/home" "${INSTALL_ROOT}/cli/npm-cache"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}" "${INSTALL_ROOT}/cli"
  chmod 0750 "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}" "${INSTALL_ROOT}/cli/home" "${INSTALL_ROOT}/cli/npm-cache"
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
    printf 'EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED=%s\n' "$(shell_quote_env "${SHELL_FULL_ACCESS_ENABLED}")"
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
    # Writable HOME + npm cache so `npm install -g` (and the CLIs' own
    # ~/.claude / ~/.codex writes at login) don't EACCES against the
    # read-only real home under ProtectSystem=strict. See prepare_directories.
    printf 'HOME=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/cli/home")"
    printf 'NPM_CONFIG_CACHE=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/cli/npm-cache")"
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
  # MAN-121: this download is the single most likely silent killer of a
  # provision — if the release host does not serve this exact object (wrong or
  # unpublished ${AGENT_COMPUTER_VERSION}, moved path, 404, 403, rate limit,
  # captive proxy) the box can never start the gateway, and before the beacon
  # existed nothing anywhere said so. Capture the HTTP status and curl's own
  # exit code so the reported reason names the cause rather than just the URL.
  local http_status="" curl_exit=0
  http_status="$(curl -sS -L -m 180 -w '%{http_code}' \
    -o "${gateway_archive}" "${GATEWAY_ARTIFACT_URL}" 2>/dev/null)" || curl_exit=$?
  if [[ ! -s "${gateway_archive}" || "${http_status}" != "200" ]]; then
    rm -rf "${tmp_dir}"
    fail "could not download the gateway artifact from ${GATEWAY_ARTIFACT_URL} (HTTP ${http_status:-none}, curl exit ${curl_exit}). The release host must serve this exact file for version '${AGENT_COMPUTER_VERSION}'; check EMPYRALIS_AGENT_COMPUTER_VERSION / EMPYRALIS_ARTIFACT_BASE_URL."
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
# Self-update (empyralis-gateway/src/update/*) writes new releases under a
# directory it can actually get write access to as the unprivileged service
# user — INSTALL_DIR above is root-owned (see prepare_directories/
# install_release_artifacts) — defaulting to a `gateway-releases` dir next to
# EMPYRALIS_GATEWAY_STATE_DIR, or EMPYRALIS_GATEWAY_INSTALL_ROOT when set
# explicitly (see gateway-release-layout.ts's resolveGatewayReleaseLayout,
# which this block mirrors). Checked FIRST: once a self-update has ever
# swapped this symlink, it is the freshest known-good build on the box and
# should win over the installer-provisioned INSTALL_DIR, which self-update
# never touches (and structurally cannot, without the ownership change this
# script deliberately does not make — see gateway-release-layout.ts's doc
# comment).
SELF_UPDATE_INSTALL_ROOT="${EMPYRALIS_GATEWAY_INSTALL_ROOT:-}"
if [[ -z "${SELF_UPDATE_INSTALL_ROOT}" && -n "${EMPYRALIS_GATEWAY_STATE_DIR:-}" ]]; then
  SELF_UPDATE_INSTALL_ROOT="$(dirname "${EMPYRALIS_GATEWAY_STATE_DIR}")/gateway-releases"
fi
entry=""
if [[ -n "${SELF_UPDATE_INSTALL_ROOT}" && -f "${SELF_UPDATE_INSTALL_ROOT}/current/gateway/dist/index.js" ]]; then
  entry="${SELF_UPDATE_INSTALL_ROOT}/current/gateway/dist/index.js"
fi
if [[ -z "${entry}" ]]; then
  for candidate in \
    "${INSTALL_DIR}/gateway/dist/index.js" \
    "${INSTALL_DIR}/gateway/index.js" \
    "${INSTALL_DIR}/gateway/build/index.js"; do
    if [[ -f "${candidate}" ]]; then
      entry="${candidate}"
      break
    fi
  done
fi
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
StartLimitIntervalSec=0

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

# The gateway is the ONLY thing that can report this box's state, so it must
# outlive both memory pressure and systemd's restart rate limit. Both of these
# mirror E2B's envd unit, and both close a silent-death path:
#   OOMScoreAdjust  - the smallest offered droplet is s-1vcpu-1gb; an OOM kill
#                     leaves no beacon and no log the control plane can see.
#   StartLimitIntervalSec=0 (above) - without it, Restart=always is honoured
#                     only until 5 starts in 10s, after which systemd gives up
#                     PERMANENTLY and a later `systemctl start` answers
#                     "start request repeated too quickly" and does nothing.
OOMScoreAdjust=-1000
Nice=-20

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
  # ADVISORY, not terminal (MAN-121). Everything the installer had to do has
  # succeeded by this point; the gateway service is installed, enabled and
  # running under systemd with Restart=always, so it keeps trying to pair long
  # after this script exits — and often succeeds. Reporting this as terminal
  # would let the backend give up early and DESTROY a box that was about to
  # come up. So: tell the platform what we saw, then exit non-zero for
  # cloud-init's log, and let the backend's own 20-minute window decide.
  report_beacon 0 "the gateway service was installed and started, but had not confirmed registration ${REGISTRATION_TIMEOUT_SECONDS}s later. It is still running and retrying; check 'journalctl -u ${GATEWAY_SERVICE}' on the server if it never connects."
  fail "Gateway did not confirm registration within ${REGISTRATION_TIMEOUT_SECONDS}s"
}

final_status() {
  wait_for_registration
  log "Agent Computer connected"
}

main() {
  # INSTALL_PHASE names the step for the failure beacon, so a report says
  # "died downloading the gateway" rather than just "died".
  set_phase preflight "checking the server"
  require_root
  detect_ubuntu
  require_pairing_token
  set_phase system_dependencies "installing system packages"
  apt_install_system_deps
  set_phase node_install "installing Node.js 20"
  install_node20
  set_phase prepare_host "preparing the host"
  create_service_user
  prepare_directories
  write_env_file
  set_phase docker_install "installing Docker"
  # Non-fatal by design (see install_docker's own header comment): a box that
  # cannot get Docker still finishes provisioning with every other
  # capability. install_docker already reports its own advisory beacon on
  # failure, so this is just a local log line for whoever reads the console.
  install_docker || log "continuing without a confirmed-ready Docker sandbox — shell.execute and filesystem.read_write will stay unavailable until this is resolved"
  set_phase gateway_download "downloading Agent Computer"
  install_release_artifacts
  set_phase service_setup "setting up the service"
  write_launcher_scripts
  write_systemd_units
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${STATE_ROOT}" "${LOG_DIR}" "${RUN_DIR}"
  set_phase service_start "starting the service"
  start_services
  set_phase registration_wait "waiting for it to connect"
  final_status
}

main "$@"
