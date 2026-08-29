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
  # SUPERSEDED 2026-08-22 and the beacons below were still saying the old
  # thing: Docker now decides HOW a command runs, never WHETHER. No Docker
  # means the command runs on the host and works. Every failure here is a
  # change of isolation, not a lost capability, and none of them may be
  # reported as one.
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
      report_beacon 0 "docker.io install failed; commands on this computer will run directly on it instead of inside a container"
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
    report_beacon 0 "could not add ${SERVICE_USER} to the docker group; commands on this computer will run directly on it instead of inside a container"
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
  report_beacon 0 "docker installed but 'docker info' did not succeed as ${SERVICE_USER} after ${attempt} attempts; commands will run directly on this computer until it does — check 'systemctl status docker' on the server"
  return 1
}

install_data_toolchain() {
  # WHAT A BOOKKEEPING AGENT NEEDS ON THE MACHINE, AND WHY IT IS NOT A DOCKER
  # IMAGE.
  #
  # Founder's ruling, 2026-08-28: *"I don't think we should do something about
  # it, it's going to be just there if this specific person needs it. I don't
  # want to do this shit anymore about this Docker."* So the toolchain is a
  # property of the BOX — installed once, present for every agent on it —
  # rather than a custom sandbox image somebody has to build, publish, pin and
  # keep current.
  #
  # The thing it buys, and it is the whole product claim: a PDF invoice
  # carries a text layer holding the exact characters the supplier's system
  # wrote. `pdftotext -layout` reads them. An agent without it opens the page
  # as an image and transcribes what the digits look like, which at nine point
  # type is a coin flip between 3 and 8 on every figure, and is also about
  # five times the tokens. Measured on a real 10-page, 260-line invoice, the
  # text path recovered 1,040 of 1,040 numbers exactly.
  #
  # server_modules/agent_job_skills.py names each of these commands in a real
  # procedure. test_agent_computer_toolchain.py reads THIS function's source
  # and fails if a body ever names something this function does not install —
  # a skill that tells an agent to run a command the machine does not have is
  # a lie, and it is a silent one.
  #
  # APT WHERE APT HAS IT, pip only for the two it does not. An apt package is
  # dpkg-managed, upgrades with the box, and does not have to argue with
  # PEP 668. `--break-system-packages` is reserved for duckdb and pypdf, which
  # ship no Debian package: this is a single-purpose appliance box we
  # provision and own, not a developer's laptop, and the alternative — a venv
  # plus a PATH entry in /etc/profile.d — silently disappears the moment
  # anything invokes a NON-login shell, which is exactly the failure this
  # whole task is about not shipping.
  #
  # DELIBERATELY NON-FATAL, same posture as install_docker: an agent that
  # cannot read invoices must still come up able to do everything else. Each
  # failure returns 1 and reports an advisory (terminal=0) beacon.
  #
  # NOT ON macOS. A Mac gets its gateway from the desktop app, which never
  # runs this script, so a paired Mac has whatever its owner already has.
  # That gap is real and is not closed here.
  if [[ "${EMPYRALIS_INSTALL_SKIP_TOOLCHAIN:-0}" == "1" ]]; then
    log "skipping the data toolchain because EMPYRALIS_INSTALL_SKIP_TOOLCHAIN=1"
    return 0
  fi

  log "installing the document and data toolchain"
  # poppler-utils    pdftotext/pdfinfo — reading a PDF's own text layer
  # python3-pandas   tables, dates, money columns
  # python3-openpyxl .xlsx, which is what a bank or an accountant actually
  #                  sends; without it pandas.read_excel raises on every file
  # python3-pip      the two below have no Debian package
  if ! apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends \
    poppler-utils \
    python3-pandas \
    python3-openpyxl \
    python3-pip; then
    log "WARNING: the document and data toolchain failed to install"
    report_beacon 0 "the document and data toolchain (pdftotext, pandas) failed to install; this Agent Computer works, but an agent on it cannot read invoices or spreadsheets until it is installed"
    return 1
  fi

  # duckdb  queries a CSV or Parquet file where it sits. pandas loads a whole
  #         file into memory at several times its size, and on a box with a
  #         memory limit that ends as a command killed with no output — the
  #         reconciliation procedure tells an agent to reach for this above
  #         about 50 MB for exactly that reason.
  # pypdf   page counts, splitting, and the one honest answer to "does this
  #         PDF have a text layer at all".
  if ! pip3 install --break-system-packages --no-input --quiet duckdb pypdf; then
    log "WARNING: duckdb/pypdf install failed"
    report_beacon 0 "duckdb and pypdf failed to install; an agent on this computer can still read PDFs and spreadsheets, but has no way to work with a data file too large to load into memory"
    return 1
  fi

  # Verify what is actually there, the way the agent will reach it — as
  # SERVICE_USER, through a shell, not as root and not through the
  # interpreter this script happens to be running under. An install that
  # exits 0 and leaves an import broken is the one outcome worth catching
  # here, because everything downstream of it fails much later and says
  # something else.
  local missing=""
  sudo -u "${SERVICE_USER}" sh -lc 'command -v pdftotext >/dev/null 2>&1' || missing="${missing} pdftotext"
  local mod
  for mod in pandas openpyxl duckdb pypdf; do
    sudo -u "${SERVICE_USER}" sh -lc "python3 -c 'import ${mod}'" >/dev/null 2>&1 || missing="${missing} ${mod}"
  done
  if [[ -n "${missing}" ]]; then
    log "WARNING: toolchain installed but unavailable to ${SERVICE_USER}:${missing}"
    report_beacon 0 "the document and data toolchain installed but is not reachable by the agent (missing:${missing}); an agent on this computer will say it cannot read a document rather than guessing at one"
    return 1
  fi

  log "document and data toolchain is installed and reachable"
  return 0
}

openclaw_node_dir() {
  printf '%s' "${INSTALL_ROOT}/openclaw-node"
}

install_openclaw_node() {
  # The version is asked of the gateway artifact (openclaw-version.ts), never
  # typed here — same rule as the OpenClaw pin itself. A version literal in a
  # script that boxes fetch once and never fetch again is a copy that drifts
  # silently.
  local node_dir bin_dir want have arch tarball url tmp
  node_dir="$(openclaw_node_dir)"
  bin_dir="${node_dir}/bin"

  want="$(sudo -u "${SERVICE_USER}" env -i \
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      node -e 'process.stdout.write(require(process.argv[1]).OPENCLAW_PINNED_NODE_VERSION)' \
      "${CURRENT_DIR}/gateway/dist/openclaw/provisioning/openclaw-version.js" 2>/dev/null)" || want=""
  if [[ -z "${want}" ]]; then
    log "WARNING: could not read the channel transport's Node version from the gateway build"
    return 1
  fi

  if [[ -x "${bin_dir}/node" ]]; then
    have="$("${bin_dir}/node" --version 2>/dev/null | tr -d 'v')"
    if [[ "${have}" == "${want}" ]]; then
      log "channel transport Node ${want} already installed"
      return 0
    fi
  fi

  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      log "WARNING: no channel transport Node build for $(uname -m)"
      return 1
      ;;
  esac

  tarball="node-v${want}-linux-${arch}.tar.xz"
  url="${EMPYRALIS_NODE_DIST_BASE_URL:-https://nodejs.org/dist}/v${want}/${tarball}"
  tmp="$(mktemp -d)"
  log "installing Node ${want} for the channel transport"
  if ! curl -fsSL -m 300 -o "${tmp}/${tarball}" "${url}"; then
    rm -rf "${tmp}"
    log "WARNING: could not download ${url}"
    return 1
  fi
  rm -rf "${node_dir}"
  mkdir -p "${node_dir}"
  if ! tar -xJf "${tmp}/${tarball}" -C "${node_dir}" --strip-components=1; then
    rm -rf "${tmp}" "${node_dir}"
    log "WARNING: could not extract ${tarball}"
    return 1
  fi
  rm -rf "${tmp}"
  chown -R root:root "${node_dir}"
  if [[ ! -x "${bin_dir}/node" ]]; then
    log "WARNING: the extracted Node has no bin/node"
    return 1
  fi
  return 0
}

install_channel_transport() {
  # Channels. The customer never sees this step, never types a command, and
  # never learns the name of the software it installs — they press one button,
  # authorise their cloud provider, and come back to a box that can carry
  # messages.
  #
  # THIS SCRIPT KNOWS NOTHING. Every value below is computed by the gateway
  # artifact that was just unpacked, through
  # empyralis-gateway/src/openclaw/provisioning/openclaw-install-plan-cli.ts:
  # the pinned package spec, the systemd unit's path, its exact bytes, the
  # profile, the port and the child environment. A version literal or a unit
  # body typed into this file would be a second copy of something that must be
  # exact, on a script that boxes fetch once and never fetch again — the
  # silent-drift shape CLAUDE.md records twice over.
  #
  # WHY THE INSTALLER AT ALL, when the gateway installs the transport itself
  # on every boot: this is the only moment with root. A systemd unit lives in
  # /etc/systemd/system, and the gateway runs as ${SERVICE_USER} under
  # ProtectSystem=strict — it can install the software and write the config,
  # but it cannot install the thing that keeps it running. Doing the install
  # here too also gets the ordering right, since a unit whose ExecStart does
  # not exist yet is a restart loop.
  #
  # DELIBERATELY NON-FATAL, exactly like install_docker above. A box that
  # cannot reach the npm registry must still finish and come up as a fully
  # working Agent Computer; only channels degrade. Every path returns non-zero
  # with an advisory (terminal=0) beacon instead of calling fail().
  if [[ "${EMPYRALIS_INSTALL_SKIP_CHANNEL_TRANSPORT:-0}" == "1" ]]; then
    log "skipping channel transport install because EMPYRALIS_INSTALL_SKIP_CHANNEL_TRANSPORT=1"
    return 0
  fi

  local plan_entry plan
  plan_entry="${CURRENT_DIR}/gateway/dist/openclaw/provisioning/openclaw-install-plan-cli.js"
  if [[ ! -f "${plan_entry}" ]]; then
    log "WARNING: this gateway build has no channel transport installer (${plan_entry} missing)"
    report_beacon 0 "this Agent Computer build cannot set up messaging channels; the gateway is installed and working, but channels will stay unavailable"
    return 1
  fi

  # ── The transport's own Node ──────────────────────────────────────────────
  #
  # It needs a NEWER Node than the gateway, and the gateway cannot simply move
  # to it: the gateway ships prebuilt, with native modules compiled against
  # Node 20's ABI, so bumping install_node20() would break every published
  # artifact on every existing box. Two runtimes, side by side, and only the
  # transport's unit ever sees the new one on its PATH.
  #
  # Found by running this installer end to end on a real box: `npm install
  # --global` does NOT enforce `engines`, so installing the transport under
  # Node 20 SUCCEEDS, puts a binary on PATH, and then exits on every single
  # call with a suggestion to use nvm — and the supervised unit becomes a
  # five-second restart loop on a box that reported a clean install.
  if ! install_openclaw_node; then
    report_beacon 0 "could not set up messaging channels on this server; everything else is installed and working, and channels can be set up later"
    return 1
  fi

  # As ${SERVICE_USER}, never as root: this mints the gateway's own loopback
  # secrets into its state directory, and a root-owned copy there is a file
  # the gateway then cannot read — a transport that is dead in a way that
  # looks like nothing happened. --require-user makes that mistake loud
  # instead of silent.
  log "installing the channel transport"
  if ! plan="$(sudo -u "${SERVICE_USER}" \
      env -i HOME="${INSTALL_ROOT}/cli/home" \
        PATH="${INSTALL_ROOT}/cli/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        NPM_CONFIG_PREFIX="${INSTALL_ROOT}/cli" \
        NPM_CONFIG_CACHE="${INSTALL_ROOT}/cli/npm-cache" \
        EMPYRALIS_GATEWAY_STATE_DIR="${STATE_ROOT}/gateway" \
        EMPYRALIS_OPENCLAW_NODE_BIN_DIR="$(openclaw_node_dir)/bin" \
        node "${plan_entry}" --ensure-runtime --provision --require-user "${SERVICE_USER}" 2>/tmp/empyralis-channel-transport.err)"; then
    local err
    err="$(tail -c 400 /tmp/empyralis-channel-transport.err 2>/dev/null | tr '\n' ' ')"
    # The detail goes to the console for whoever is debugging the box; the
    # BEACON carries a fixed sentence. That text reaches a customer's screen,
    # and this failure's stderr is full of npm output and the name of a piece
    # of software they must never have to know about. Code in, frozen literal
    # out — the same posture platform_event.CHANNEL_OWNER_SAFE_CODES takes for
    # channel replies.
    log "WARNING: channel transport install failed: ${err}"
    report_beacon 0 "could not set up messaging channels on this server; everything else is installed and working, and channels can be set up later"
    return 1
  fi

  # ── Believe the PLAN'S VERDICT, not the exit code ────────────────────────
  #
  # The plan CLI exits 0 whenever it ran; whether the transport is USABLE is a
  # separate fact it reports. Writing and starting a unit on the strength of
  # "the command worked" is how the first live run of this code ended up with
  # a five-second restart loop and a console line that said "installed and
  # running". An empty string is not a decision, and neither is exit 0.
  local runtime_action provision_status
  runtime_action="$(printf '%s' "${plan}" | python3 -c 'import json,sys; p=json.load(sys.stdin).get("runtimeInstall") or {}; print(p.get("action",""))')"
  provision_status="$(printf '%s' "${plan}" | python3 -c 'import json,sys; p=json.load(sys.stdin).get("provision") or {}; print(p.get("status",""))')"
  if [[ "${runtime_action}" != "installed" && "${runtime_action}" != "already_installed" ]]; then
    log "WARNING: the channel transport was not installed (${runtime_action:-unknown})"
    log "         $(printf '%s' "${plan}" | python3 -c 'import json,sys; p=(json.load(sys.stdin).get("runtimeInstall") or {}).get("refusal") or {}; print(p.get("detail",""))')"
    report_beacon 0 "could not set up messaging channels on this server; everything else is installed and working, and channels can be set up later"
    return 1
  fi
  if [[ "${provision_status}" != "provisioned" ]]; then
    log "WARNING: the channel transport was installed but not configured (${provision_status:-unknown})"
    log "         $(printf '%s' "${plan}" | python3 -c 'import json,sys; p=(json.load(sys.stdin).get("provision") or {}).get("refusal") or {}; print(p.get("detail",""))')"
    report_beacon 0 "could not set up messaging channels on this server; everything else is installed and working, and channels can be set up later"
    return 1
  fi

  # `python3` is already a hard dependency of this installer (apt_install_
  # system_deps), so this needs nothing new — and unlike a shell JSON parse it
  # cannot mangle a multi-line unit body.
  local unit_path unit_name unit_contents
  unit_path="$(printf '%s' "${plan}" | python3 -c 'import json,sys; p=json.load(sys.stdin)["unit"]; print(p["path"] if p else "")')"
  unit_name="$(printf '%s' "${plan}" | python3 -c 'import json,sys; p=json.load(sys.stdin)["unit"]; print(p["name"] if p else "")')"
  if [[ -z "${unit_path}" ]]; then
    log "WARNING: the channel transport was installed but no supervisor unit was produced (is 'openclaw' on PATH?)"
    report_beacon 0 "messaging channel support was installed but could not be set to start automatically; channels may stop working after a reboot"
    return 1
  fi

  unit_contents="$(printf '%s' "${plan}" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["unit"]["contents"])')"
  printf '%s' "${unit_contents}" > "${unit_path}"
  chmod 0644 "${unit_path}"

  if systemd_available; then
    systemctl daemon-reload
    # enable, then start. Unlike the shared supervisor module — which never
    # touches a running job because it runs unattended from inside a live
    # process — this is install time, nothing is running yet, and leaving the
    # unit merely enabled would mean channels do not work until the first
    # reboot.
    systemctl enable "${unit_name}" >/dev/null 2>&1 || true
    if ! systemctl restart "${unit_name}"; then
      log "WARNING: ${unit_name} did not start"
      report_beacon 0 "messaging channel support was installed but did not start; check 'systemctl status ${unit_name}' on the server"
      return 1
    fi
  fi

  log "channel transport installed and running"
  return 0
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
    # The channel transport's own Node — NOT on the gateway's PATH above, and
    # deliberately so. The gateway ships prebuilt with native modules compiled
    # against Node 20's ABI; the transport needs Node 22+. Naming the directory
    # here (rather than prepending it) lets the gateway's own provisioning pass
    # put it first on the CHILD's PATH only. See empyralis-gateway/src/openclaw/
    # provisioning/openclaw-node-runtime.ts.
    printf 'EMPYRALIS_OPENCLAW_NODE_BIN_DIR=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/openclaw-node/bin")"
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
  local tmp_dir gateway_archive gateway_url expected_sha256=""
  tmp_dir="$(mktemp -d)"
  gateway_archive="${tmp_dir}/gateway.tar.gz"
  gateway_url="${GATEWAY_ARTIFACT_URL}"

  # CACHE-BUST THE DEFAULT ARTIFACT URL (2026-08-13 gateway-artifact-staleness
  # incident, same fix in shape as vps_provisioning_service.agent_installer_url()
  # for the installer script one level up). Cloudflare fronts this domain and
  # caches per FULL URL including query string; install-agent-computer.sh
  # always requests the exact same literal path
  # (".../releases/agent-computer/latest/empyralis-gateway-linux-x64.tar.gz")
  # on every boot, forever, so a freshly published tarball can sit behind a
  # cached response indefinitely at that one URL regardless of its own
  # cache-control. The release workflow (release-gateway-linux.yml) already
  # writes a `.sha256` sidecar beside every tarball for exactly this purpose
  # — fetch it first (it's ~90 bytes, so a stale CACHED sidecar costs seconds
  # of staleness, never a stale multi-megabyte binary) and use its hash to
  # bust the big download. A changed artifact is a changed sidecar is a
  # changed URL is a guaranteed cache miss, self-healing on every future
  # publish rather than a one-time purge. This also buys the boot-time
  # install a real integrity check it never had before — only the packer
  # image-bake path (deploy/packer/scripts/40-gateway-artifact.sh) verified
  # the checksum until now.
  #
  # An explicitly configured EMPYRALIS_GATEWAY_ARTIFACT_URL is passed through
  # UNTOUCHED, same rule agent_installer_url() applies to
  # EMPYRALIS_AGENT_INSTALLER_URL: an operator pointing at their own artifact
  # host has their own cache story, and appending a query string to someone
  # else's URL is not ours to do.
  if [[ -z "${EMPYRALIS_GATEWAY_ARTIFACT_URL:-}" ]]; then
    local checksum_file="${tmp_dir}/gateway.tar.gz.sha256" checksum_http_status="" checksum_curl_exit=0
    checksum_http_status="$(curl -sS -L -m 30 -w '%{http_code}' \
      -o "${checksum_file}" "${gateway_url}.sha256" 2>/dev/null)" || checksum_curl_exit=$?
    if [[ "${checksum_curl_exit}" -eq 0 && "${checksum_http_status}" == "200" && -s "${checksum_file}" ]]; then
      expected_sha256="$(awk '{print $1}' "${checksum_file}" | head -c 64)"
      if [[ "${expected_sha256}" =~ ^[0-9a-f]{64}$ ]]; then
        gateway_url="${gateway_url}?v=${expected_sha256:0:12}"
      else
        expected_sha256=""
      fi
    fi
    # A missing/unreadable sidecar must never block a provision — the box
    # just gets whatever the CDN happens to be serving at the un-busted URL,
    # exactly the behavior before this fix.
  fi

  log "downloading prebuilt gateway artifact ${gateway_url}"
  # MAN-121: this download is the single most likely silent killer of a
  # provision — if the release host does not serve this exact object (wrong or
  # unpublished ${AGENT_COMPUTER_VERSION}, moved path, 404, 403, rate limit,
  # captive proxy) the box can never start the gateway, and before the beacon
  # existed nothing anywhere said so. Capture the HTTP status and curl's own
  # exit code so the reported reason names the cause rather than just the URL.
  local http_status="" curl_exit=0
  http_status="$(curl -sS -L -m 180 -w '%{http_code}' \
    -o "${gateway_archive}" "${gateway_url}" 2>/dev/null)" || curl_exit=$?
  if [[ ! -s "${gateway_archive}" || "${http_status}" != "200" ]]; then
    rm -rf "${tmp_dir}"
    fail "could not download the gateway artifact from ${gateway_url} (HTTP ${http_status:-none}, curl exit ${curl_exit}). The release host must serve this exact file for version '${AGENT_COMPUTER_VERSION}'; check EMPYRALIS_AGENT_COMPUTER_VERSION / EMPYRALIS_ARTIFACT_BASE_URL."
  fi

  if [[ -n "${expected_sha256}" ]]; then
    local actual_sha256
    actual_sha256="$(sha256sum "${gateway_archive}" | awk '{print $1}')"
    if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
      rm -rf "${tmp_dir}"
      fail "gateway artifact failed checksum verification (expected ${expected_sha256}, got ${actual_sha256}) — the download may have been corrupted or the CDN served a mismatched object."
    fi
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
# A registration failure the gateway itself has determined can never succeed
# by retrying (a consumed/revoked/expired pairing token — see
# empyralis-gateway/src/cloud/registration-failure.ts's
# classifyRegistrationFailure and index.ts's EXIT_PERMANENT_REGISTRATION_
# FAILURE) exits with this exact code. Without this line, Restart=always +
# StartLimitIntervalSec=0 below retries such a box FOREVER with the exact
# same doomed token — observed live at 518 restarts and counting. Every
# OTHER failure (network errors, a transient 5xx, a genuine crash) still
# exits 1 and is still retried exactly as before; this line is deliberately
# narrow to the one class of failure retrying can never fix.
RestartPreventExitStatus=78
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
  install_docker || log "continuing without a Docker sandbox — commands will run directly on this computer instead"
  set_phase data_toolchain "installing document and data tools"
  # Non-fatal by design (see install_data_toolchain's own header): a box that
  # cannot get these still finishes provisioning with every capability it had
  # before they existed. The agent-side procedures check for each tool before
  # using it and say they cannot read a document rather than guessing at one.
  install_data_toolchain || log "continuing without the document and data toolchain — an agent here cannot read invoices or spreadsheets until it is installed"
  set_phase gateway_download "downloading Agent Computer"
  install_release_artifacts
  set_phase channel_transport "setting up messaging channels"
  # After the gateway artifact is unpacked (it supplies the whole plan) and
  # before the gateway service starts, so the transport is already up the
  # first time the gateway looks for it. Non-fatal by design — see the
  # function's own header.
  install_channel_transport || log "continuing without messaging channel support — the Agent Computer works, channels do not"
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
