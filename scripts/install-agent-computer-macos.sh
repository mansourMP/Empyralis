#!/usr/bin/env bash
set -Eeuo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Empyralis Agent Computer — macOS installer.
#
# The macOS sibling of scripts/install-agent-computer.sh, which is Ubuntu-only
# (apt-get, /etc/systemd/system, a system service user) and cannot run here at
# all. Until this existed, Settings -> Connections -> "add your own computer"
# defaulted its Platform picker to macOS and then handed every Mac customer the
# Linux command, which dies on its first `apt-get`.
#
# WHAT IS DELIBERATELY DIFFERENT FROM THE LINUX INSTALLER, and why — each of
# these is a decision, not an omission:
#
#   no root          A LaunchAgent runs as the logged-in user, so nothing here
#                    needs sudo and this script REFUSES to run as root: a
#                    root-run install would resolve $HOME to /var/root and put
#                    the plist somewhere the user's login session never loads.
#                    Everything lives under ~/.empyralis/agent-computer.
#
#   no service user  There is one user, and it is the person who ran this. The
#                    Linux box invents `empyralis` because a droplet is a
#                    dedicated machine; a Mac is somebody's own computer.
#
#   no Docker install  Docker Desktop is a signed .app behind a GUI installer
#                    and an admin prompt — there is no non-interactive install,
#                    and pretending otherwise would be a step that always
#                    fails. We PROBE it instead: if it is running, the gateway
#                    advertises shell.execute / filesystem.read_write; if not,
#                    the pairing panel already says so in the customer's own
#                    words (GatewayPairPanel.tsx's gatewayNeedsDocker, whose
#                    copy is already macOS-shaped: "Start Docker Desktop").
#
#   no channel-transport unit  openclaw-install-plan-cli.ts returns `unit:
#                    null` on darwin ON PURPOSE — its own doc comment: "on
#                    macOS the gateway installs its own LaunchAgent perfectly
#                    well without root, so the installer has nothing to do".
#                    The Linux installer only does that step because a systemd
#                    unit needs root and the gateway (running unprivileged
#                    under ProtectSystem=strict) cannot write one. Here the
#                    gateway's own boot pass (ensureProvisionedAtBoot) installs
#                    the transport, its config and its LaunchAgent by itself.
#                    Doing it here too would be a second implementation of
#                    something that already works.
#
#   ONE Node, not two  The Linux box carries Node 20 (the gateway's prebuilt
#                    native ABI) plus a separate Node 22 for the transport,
#                    because moving the box to 22 would break every published
#                    artifact on every existing box. That constraint does not
#                    reach a fresh Mac install: nothing here predates this
#                    script, so we install ONE Node — the version the artifact
#                    itself pins for the transport (>= 22.19, above the
#                    gateway's own `engines: >=20` floor) — and both processes
#                    use it. EMPYRALIS_OPENCLAW_NODE_BIN_DIR is therefore left
#                    unset, which openclaw-node-runtime.ts's own doc comment
#                    already names as the correct macOS shape ("absent config
#                    leaves PATH untouched, which is correct for macOS").
#
# WHAT IS DELIBERATELY THE SAME: the pairing env contract
# (EMPYRALIS_GATEWAY_PAIRING_TOKEN / _DISPLAY_NAME / EMPYRALIS_WORKSPACE_ID),
# the prebuilt-artifact download (never a source build), the INSTALL_PHASE
# progress beacons and the failure phone-home to /gateway/provisioning-events —
# so the existing provisioning-watch UI (vps-provision-watch.ts's
# INSTALL_PHASE_LABELS) renders a Mac install with no changes at all. Every
# phase name below is one that file already knows.
# ─────────────────────────────────────────────────────────────────────────────

INSTALL_ROOT="${EMPYRALIS_INSTALL_ROOT:-${HOME}/.empyralis/agent-computer}"
STATE_ROOT="${EMPYRALIS_STATE_ROOT:-${INSTALL_ROOT}/state}"
LOG_DIR="${EMPYRALIS_LOG_DIR:-${INSTALL_ROOT}/logs}"
ENV_FILE="${EMPYRALIS_ENV_FILE:-${INSTALL_ROOT}/agent-computer.env}"
BIN_DIR="${INSTALL_ROOT}/bin"
CURRENT_DIR="${INSTALL_ROOT}/current"
NODE_DIR="${INSTALL_ROOT}/node"

# Matches gateway-supervisor-install.ts's DEFAULT_LAUNCHD_LABEL, so the
# gateway's own doctor audits the same job this installed rather than a second
# one under a different name. Overridable so a second, deliberately isolated
# install (a test box, a throwaway) never collides with the real one.
LAUNCHD_LABEL="${EMPYRALIS_LAUNCHD_LABEL:-ai.empyralis.agent-computer}"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LAUNCHD_LABEL}.plist"

DEFAULT_API_URL="https://empyralis.ai/api"
API_URL="${EMPYRALIS_API_URL:-${EMPYRALIS_GATEWAY_API_URL:-${DEFAULT_API_URL}}}"
PAIRING_TOKEN="${EMPYRALIS_PAIRING_TOKEN:-${EMPYRALIS_GATEWAY_PAIRING_TOKEN:-}}"
AGENT_COMPUTER_VERSION="${EMPYRALIS_AGENT_COMPUTER_VERSION:-latest}"
ARTIFACT_BASE_URL="${EMPYRALIS_ARTIFACT_BASE_URL:-https://empyralis.ai/releases/agent-computer/${AGENT_COMPUTER_VERSION}}"
DISPLAY_NAME="${EMPYRALIS_GATEWAY_DISPLAY_NAME:-$(scutil --get ComputerName 2>/dev/null || hostname)}"
PERSONAL_CHANNELS_ENABLED="${EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED:-true}"
# The box operator's LOCAL half of the shell_sandbox full_access opt-in (the
# other, server-asserted half rides on the pairing intent). Off unless the
# pairing command exported it, which GatewayPairPanel.tsx only ever does when
# the owner ticked the box. Written into the env file the LaunchAgent reads,
# never left in this script's own process environment — launchd does not
# inherit a login shell's env, so an `export` that only lived here would
# vanish the moment this script exits.
SHELL_FULL_ACCESS_ENABLED="${EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED:-false}"
NODE_DIST_BASE_URL="${EMPYRALIS_NODE_DIST_BASE_URL:-https://nodejs.org/dist}"
# Same reasoning as the Linux installer's own timer: this clock starts after
# the Node and artifact downloads are done, so it is purely gateway boot ->
# POST /gateway/registrations -> registration.json. It sits under the pairing
# intent's TTL so it can never be the first thing to give up, and it stops
# nothing — the LaunchAgent keeps running and retrying past it.
REGISTRATION_TIMEOUT_SECONDS="${EMPYRALIS_REGISTRATION_TIMEOUT_SECONDS:-600}"

INSTALL_PHASE="startup"
BEACON_SENT=0

json_escape() {
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
  # First FAILURE beacon wins — the first failure is the useful one. Progress
  # beacons are exempt (reporting more than once is their whole point), and a
  # failure may still land after them.
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
  curl -fsS -m 15 -X POST "${API_URL%/}/gateway/provisioning-events" \
    -H 'content-type: application/json' \
    --data-binary "${payload}" >/dev/null 2>&1 || true
}

set_phase() {
  # Every name passed here is one vps-provision-watch.ts's INSTALL_PHASE_LABELS
  # already renders. A new phase name would show up in the UI as a raw
  # underscored token, so this installer deliberately reuses that vocabulary
  # rather than inventing a macOS one.
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
  local code=$?
  report_beacon 1 "installer aborted during '${INSTALL_PHASE}' (exit ${code}, line ${BASH_LINENO[0]:-unknown})"
  exit "${code}"
}

trap on_unexpected_error ERR

require_not_root() {
  # The opposite of the Linux installer's require_root, and for a real reason:
  # a LaunchAgent is loaded into the logged-in user's GUI session
  # (`launchctl bootstrap gui/<uid>`), and $HOME under sudo is /var/root. A
  # root-run install would write the plist, the state and the credentials
  # somewhere the user's own session never looks at, and report success.
  if [[ "$(id -u)" == "0" ]]; then
    fail "run this without sudo — it installs into your own account (no administrator password needed)"
  fi
}

detect_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    fail "this installer is for macOS. On Ubuntu use https://empyralis.ai/install/agent-computer.sh instead"
  fi
  local major
  major="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
  if [[ -n "${major}" && "${major}" =~ ^[0-9]+$ && "${major}" -lt 12 ]]; then
    fail "macOS 12 (Monterey) or newer is required; this Mac reports $(sw_vers -productVersion 2>/dev/null || echo unknown)"
  fi
}

require_pairing_token() {
  if [[ -z "${PAIRING_TOKEN}" ]]; then
    fail "EMPYRALIS_GATEWAY_PAIRING_TOKEN is required — copy the whole command from Empyralis, not just its last line"
  fi
}

# Results are published into globals rather than echoed on stdout, and every
# failure calls fail() in THIS shell. A `x="$(helper)"` helper that calls
# fail() fails inside a subshell: its beacon is sent from a copy of this
# process, BEACON_SENT never updates here, and the outer shell then sends a
# second, less specific "installer aborted" beacon for the same event. Two
# reports of one failure is exactly the kind of noise the first-failure-wins
# rule exists to prevent.
HOST_ARCH=""
resolve_host_arch() {
  case "$(uname -m)" in
    arm64|aarch64) HOST_ARCH="arm64" ;;
    x86_64|amd64) HOST_ARCH="x64" ;;
    *) fail "unsupported processor: $(uname -m)" ;;
  esac
}

prepare_directories() {
  mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}" "${STATE_ROOT}" "${LOG_DIR}" "${LAUNCH_AGENTS_DIR}"
  # BYO-brain: a writable npm global prefix for cli.install (@openai/codex and
  # friends). Without sudo there is no writing to /usr/local/lib/node_modules,
  # and the bin/ below is prepended to the LaunchAgent's PATH so the gateway
  # can exec whatever cli.install lands there.
  mkdir -p "${INSTALL_ROOT}/cli/bin" "${INSTALL_ROOT}/cli/lib"
  # 0700 on anything holding secrets or conversation state. The env file gets
  # its own 0600 below.
  chmod 0700 "${INSTALL_ROOT}" "${STATE_ROOT}" "${LOG_DIR}" 2>/dev/null || true
}

# ── Gateway artifact ─────────────────────────────────────────────────────────

ARTIFACT_VERIFIED_SHA=""
download_artifact() {
  # <url> <destination>. Sets ARTIFACT_VERIFIED_SHA to the checksum this
  # download was verified against, or empty when the host served no sidecar.
  #
  # Three outcomes, never two — "not published here" and "published but
  # corrupt" are different facts and the caller must be able to tell them
  # apart, because one of them is a reason to try the next URL and the other
  # is a reason to stop:
  #    0  downloaded (and verified, when a sidecar existed)
  #    1  nothing usable answered at this URL   -> caller may try another
  #    2  downloaded but FAILED its checksum    -> caller must abort
  local url="$1" dest="$2"
  local tmp_sha="${dest}.sha256" expected="" status="" curl_exit=0
  ARTIFACT_VERIFIED_SHA=""

  # CACHE-BUST, same shape as install-agent-computer.sh's own fix for the
  # 2026-08-13 artifact-staleness incident: Cloudflare fronts this host and
  # caches per FULL URL, and this script always asks for the same literal path
  # forever. The .sha256 sidecar is ~90 bytes, so a stale cached sidecar costs
  # seconds of staleness rather than a stale multi-megabyte binary — and it
  # buys a real integrity check at the same time.
  if [[ -z "${EMPYRALIS_GATEWAY_ARTIFACT_URL:-}" ]]; then
    status="$(curl -sS -L -m 30 -w '%{http_code}' -o "${tmp_sha}" "${url}.sha256" 2>/dev/null)" || curl_exit=$?
    if [[ "${curl_exit}" -eq 0 && "${status}" == "200" && -s "${tmp_sha}" ]]; then
      expected="$(awk '{print $1}' "${tmp_sha}" | head -c 64)"
      if [[ "${expected}" =~ ^[0-9a-f]{64}$ ]]; then
        url="${url}?v=${expected:0:12}"
      else
        expected=""
      fi
    fi
    rm -f "${tmp_sha}"
  fi

  curl_exit=0
  status="$(curl -sS -L -m 300 -w '%{http_code}' -o "${dest}" "${url}" 2>/dev/null)" || curl_exit=$?
  if [[ ! -s "${dest}" || "${status}" != "200" ]]; then
    rm -f "${dest}"
    return 1
  fi

  if [[ -n "${expected}" ]]; then
    local actual
    # macOS has `shasum`, not GNU `sha256sum`.
    actual="$(shasum -a 256 "${dest}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
      rm -f "${dest}"
      ARTIFACT_INTEGRITY_DETAIL="expected ${expected}, got ${actual}"
      return 2
    fi
  fi
  ARTIFACT_VERIFIED_SHA="${expected}"
  return 0
}
ARTIFACT_INTEGRITY_DETAIL=""

install_release_artifacts() {
  local arch tmp_dir archive stage_dir release_dir
  arch="${HOST_ARCH}"
  tmp_dir="$(mktemp -d)"
  archive="${tmp_dir}/gateway.tar.gz"
  release_dir="${INSTALL_ROOT}/releases/${AGENT_COMPUTER_VERSION}"
  stage_dir="${release_dir}.tmp"

  # PREFER a real darwin artifact; accept the linux one when there is none.
  #
  # This is not a guess. The published archive is `dist/` (plain compiled
  # JavaScript), `package.json`, `openclaw-bridge-plugin/` (raw .ts) and
  # `node_modules/` — and node_modules is the only part that can be
  # platform-specific. Measured against the live
  # empyralis-gateway-linux-arm64.tar.gz: its two ABI-native packages,
  # `bufferutil` and `utf-8-validate`, ship MULTI-PLATFORM prebuild trees that
  # already contain darwin-arm64/darwin-x64 binaries, and the one genuinely
  # Linux-only package, `@img/sharp-linux-<arch>`, is an OPTIONAL peer of
  # baileys that `ws`-style optional requires skip on failure. Verified by
  # running that exact archive's dist/index.js on macOS: the gateway boots,
  # stays up and mints its state with channels enabled.
  #
  # The fallback is a bridge, not a destination: the moment
  # empyralis-gateway-darwin-<arch>.tar.gz is published, every Mac picks it up
  # on its next install with no change here. Which one was used is logged and,
  # when it was the fallback, beaconed as advisory — a substitution nobody can
  # see is the thing this repo has been bitten by, not the substitution itself.
  local url="" used_fallback=0 rc=0
  if [[ -n "${EMPYRALIS_GATEWAY_ARTIFACT_URL:-}" ]]; then
    url="${EMPYRALIS_GATEWAY_ARTIFACT_URL}"
    log "downloading Agent Computer from ${url}"
    download_artifact "${url}" "${archive}" || rc=$?
    if (( rc == 2 )); then
      rm -rf "${tmp_dir}"
      fail "the Agent Computer download failed its integrity check (${ARTIFACT_INTEGRITY_DETAIL}) — it may have been corrupted in transit. Try again."
    fi
    if (( rc != 0 )); then
      rm -rf "${tmp_dir}"
      fail "could not download the Agent Computer from ${url} — check EMPYRALIS_GATEWAY_ARTIFACT_URL."
    fi
  else
    url="${ARTIFACT_BASE_URL}/empyralis-gateway-darwin-${arch}.tar.gz"
    log "downloading Agent Computer (darwin-${arch})"
    download_artifact "${url}" "${archive}" || rc=$?
    if (( rc == 2 )); then
      rm -rf "${tmp_dir}"
      fail "the Agent Computer download failed its integrity check (${ARTIFACT_INTEGRITY_DETAIL}) — it may have been corrupted in transit. Try again."
    fi
    if (( rc != 0 )); then
      used_fallback=1
      rc=0
      url="${ARTIFACT_BASE_URL}/empyralis-gateway-linux-${arch}.tar.gz"
      log "no darwin-${arch} build published yet; using the portable ${arch} build"
      download_artifact "${url}" "${archive}" || rc=$?
      if (( rc == 2 )); then
        rm -rf "${tmp_dir}"
        fail "the Agent Computer download failed its integrity check (${ARTIFACT_INTEGRITY_DETAIL}) — it may have been corrupted in transit. Try again."
      fi
      if (( rc != 0 )); then
        rm -rf "${tmp_dir}"
        fail "could not download the Agent Computer for ${arch} (nothing answered at ${ARTIFACT_BASE_URL}). The release host must serve version '${AGENT_COMPUTER_VERSION}'."
      fi
    fi
  fi
  if [[ -z "${ARTIFACT_VERIFIED_SHA}" ]]; then
    log "note: no checksum was served for this download, so it could not be verified"
  fi

  rm -rf "${stage_dir}"
  mkdir -p "${stage_dir}/empyralis-gateway"
  if ! tar -xzf "${archive}" -C "${stage_dir}/empyralis-gateway"; then
    rm -rf "${tmp_dir}" "${stage_dir}"
    fail "could not unpack the Agent Computer download"
  fi
  rm -rf "${tmp_dir}"

  if [[ ! -f "${stage_dir}/empyralis-gateway/dist/index.js" ]]; then
    rm -rf "${stage_dir}"
    fail "the Agent Computer download is missing dist/index.js — the published archive is malformed"
  fi

  rm -rf "${release_dir}"
  mkdir -p "${INSTALL_ROOT}/releases"
  mv "${stage_dir}" "${release_dir}"
  # Same `gateway` alias the Linux layout uses, so run-gateway's fast path and
  # everything that resolves `${INSTALL_DIR}/gateway/...` matches on both.
  ln -sfn empyralis-gateway "${release_dir}/gateway"
  ln -sfn "${release_dir}" "${CURRENT_DIR}"

  if (( used_fallback )); then
    report_beacon 0 "installed the portable ${arch} build because no macOS-specific build is published yet"
  fi
}

# ── Node ─────────────────────────────────────────────────────────────────────

PINNED_NODE_VERSION=""
resolve_pinned_node_version() {
  # ASKED OF THE ARTIFACT, NEVER TYPED HERE — the same rule the Linux
  # installer follows for this exact value. It reads it by running
  # `node -e require(...)`; on a Mac that is not available by construction,
  # because this runs BEFORE any Node exists on a machine that may have none
  # at all. So the value is extracted textually from the same compiled file
  # the Linux path require()s, with a canary: an unmatched pattern is a loud
  # failure, never a silent default, or this becomes a check that only
  # confirms itself.
  local src
  src="${CURRENT_DIR}/gateway/dist/openclaw/provisioning/openclaw-version.js"
  if [[ ! -f "${src}" ]]; then
    fail "this Agent Computer build does not declare which Node.js it needs (${src} is missing)"
  fi
  PINNED_NODE_VERSION="$(sed -n 's/^exports\.OPENCLAW_PINNED_NODE_VERSION = "\([0-9][0-9.]*\)";$/\1/p' "${src}" | head -n 1)"
  if [[ ! "${PINNED_NODE_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    fail "could not read which Node.js this Agent Computer build needs from ${src} — refusing to guess a version"
  fi
}

install_node() {
  # ONE Node for this Mac, at the version the artifact pins for the channel
  # transport (>= 22.19). The gateway's own floor is `engines: >=20`, so a
  # single runtime satisfies both and there is no second PATH to keep in sync
  # — see this file's header for why the Linux box cannot do the same.
  #
  # Installed under INSTALL_ROOT rather than reusing a Homebrew/nvm Node on
  # PATH: the LaunchAgent must keep working after the user upgrades, switches
  # or removes their own toolchain, and a box whose runtime can be swapped out
  # from under it is a box that breaks for a reason nobody will connect to
  # this install.
  local want arch tarball url tmp have
  resolve_pinned_node_version
  want="${PINNED_NODE_VERSION}"
  arch="${HOST_ARCH}"

  if [[ -x "${NODE_DIR}/bin/node" ]]; then
    have="$("${NODE_DIR}/bin/node" --version 2>/dev/null | tr -d 'v')"
    if [[ "${have}" == "${want}" ]]; then
      log "Node ${want} already installed"
      return 0
    fi
  fi

  tarball="node-v${want}-darwin-${arch}.tar.gz"
  url="${NODE_DIST_BASE_URL%/}/v${want}/${tarball}"
  tmp="$(mktemp -d)"
  log "installing Node ${want}"
  if ! curl -fsSL -m 300 -o "${tmp}/${tarball}" "${url}"; then
    rm -rf "${tmp}"
    fail "could not download Node ${want} from ${url} — check this Mac's internet connection"
  fi
  rm -rf "${NODE_DIR}"
  mkdir -p "${NODE_DIR}"
  if ! tar -xzf "${tmp}/${tarball}" -C "${NODE_DIR}" --strip-components=1; then
    rm -rf "${tmp}" "${NODE_DIR}"
    fail "could not unpack Node ${want}"
  fi
  rm -rf "${tmp}"
  if [[ ! -x "${NODE_DIR}/bin/node" ]]; then
    fail "the Node ${want} download did not contain a runnable node"
  fi
  if ! "${NODE_DIR}/bin/node" --version >/dev/null 2>&1; then
    fail "Node ${want} was installed but will not run on this Mac"
  fi
}

# ── Docker (probe only) ──────────────────────────────────────────────────────

probe_docker() {
  # Never installs anything. Docker Desktop has no non-interactive install, so
  # the honest options are "it is there" or "it is not", and the second one is
  # already reported to the customer in their own words by the pairing panel
  # once the box connects without shell.execute. Advisory beacon only.
  if [[ "${EMPYRALIS_INSTALL_SKIP_DOCKER:-0}" == "1" ]]; then
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    log "Docker is running — this computer can run commands in a sandbox"
    return 0
  fi
  log "Docker Desktop is not running — this computer will connect, but agents cannot run commands or read/write files here until it is started"
  report_beacon 0 "Docker Desktop is not running on this Mac, so running commands and reading/writing files stay unavailable until it is started"
  return 0
}

# ── Configuration ────────────────────────────────────────────────────────────

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
  # The gateway's whole configuration, in a file the launcher sources. NOT in
  # the plist's EnvironmentVariables: ~/Library/LaunchAgents is world-readable
  # by default and this file carries the pairing token and, later, the gateway
  # token. 0600 here, and the plist stays free of secrets.
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
    printf 'EMPYRALIS_GATEWAY_CLI_SETUP_ENABLED="true"\n'
    # Also a launchd hint (gateway-restart-handoff.ts's LAUNCHD_HINT_ENV_VARS),
    # which is load-bearing twice over: the gateway reports itself supervised
    # so its own doctor skips the supervisor_presence repair — a repair that
    # would otherwise rewrite the plist below with the module's default
    # definition, which execs node directly and carries NO environment, i.e.
    # would strip every setting in this file.
    printf 'EMPYRALIS_LAUNCHD_LABEL=%s\n' "$(shell_quote_env "${LAUNCHD_LABEL}")"
    # A writable npm global prefix, since there is no sudo here. cli.install
    # lands binaries in cli/bin, which PATH below already carries.
    printf 'NPM_CONFIG_PREFIX=%s\n' "$(shell_quote_env "${INSTALL_ROOT}/cli")"
    # HOME is deliberately the person's REAL home, unlike the Linux unit which
    # redirects it purely to escape ProtectSystem=strict. This is their own
    # Mac: a BYO-subscription login should write ~/.claude and ~/.codex where
    # they can see it, not into a hidden copy under an install directory.
    printf 'HOME=%s\n' "$(shell_quote_env "${HOME}")"
    # launchd hands a child a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so
    # every directory the gateway needs to exec out of has to be named here.
    # Our own Node first; Homebrew and /usr/local so the `docker` CLI that
    # Docker Desktop installs is reachable.
    printf 'PATH=%s\n' "$(shell_quote_env "${NODE_DIR}/bin:${INSTALL_ROOT}/cli/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin")"
    # Named explicitly as well as put on PATH, so run-gateway execs the exact
    # runtime this installer downloaded and verified rather than whatever a
    # later PATH edit happens to resolve first.
    printf 'EMPYRALIS_NODE_BIN=%s\n' "$(shell_quote_env "${NODE_DIR}/bin/node")"
    # EMPYRALIS_OPENCLAW_NODE_BIN_DIR is deliberately NOT written — see this
    # file's header. One Node serves both, and openclaw-node-runtime.ts treats
    # an absent value as "nothing to prepend", which is exactly right here.
    #
    # ── The two-installs-on-one-Mac hole ────────────────────────────────────
    # EMPYRALIS_INSTALL_ROOT and EMPYRALIS_LAUNCHD_LABEL already promise that a
    # second, deliberately separate Agent Computer can live on one Mac. Those
    # two alone do not deliver it: the channel transport's profile
    # (~/.openclaw-<profile>) and the loopback bridge port are resolved from
    # the gateway's own DEFAULTS, not from the install root, so a second
    # install silently shares — and therefore rewrites — the first one's
    # channel configuration and fights it for a port. These pass through when
    # set and are absent otherwise, so an ordinary single install is
    # byte-identical to before and keeps every gateway default.
    if [[ -n "${EMPYRALIS_OPENCLAW_PROFILE:-}" ]]; then
      printf 'EMPYRALIS_OPENCLAW_PROFILE=%s\n' "$(shell_quote_env "${EMPYRALIS_OPENCLAW_PROFILE}")"
    fi
    if [[ -n "${EMPYRALIS_BRIDGE_PORT:-}" ]]; then
      printf 'EMPYRALIS_BRIDGE_PORT=%s\n' "$(shell_quote_env "${EMPYRALIS_BRIDGE_PORT}")"
    fi
    if [[ -n "${EMPYRALIS_OPENCLAW_GATEWAY_URL:-}" ]]; then
      printf 'EMPYRALIS_OPENCLAW_GATEWAY_URL=%s\n' "$(shell_quote_env "${EMPYRALIS_OPENCLAW_GATEWAY_URL}")"
    fi
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
  chmod 0600 "${ENV_FILE}"
}

write_launcher_script() {
  # The plist points here rather than straight at node, for two reasons: the
  # env file above (launchd inherits nothing, and secrets must not live in a
  # world-readable plist), and self-update — which swaps a `current` symlink
  # the plist would otherwise have to be rewritten to follow.
  cat > "${BIN_DIR}/run-gateway" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
ENV_FILE="${EMPYRALIS_ENV_FILE:-${HOME}/.empyralis/agent-computer/agent-computer.env}"
if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi
INSTALL_DIR="${EMPYRALIS_AGENT_COMPUTER_INSTALL_DIR:-${HOME}/.empyralis/agent-computer/current}"
# Self-update writes new releases beside the state dir and swaps its own
# `current` symlink; once it ever has, that is the freshest known-good build on
# this Mac and must win over the installer-provisioned tree, which self-update
# never touches. Mirrors resolveGatewayReleaseLayout() and the Linux
# run-gateway's identical block.
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
  entry="$(find "${INSTALL_DIR}" -type f -path '*/dist/index.js' 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${entry}" || ! -f "${entry}" ]]; then
  echo "gateway Node entrypoint not found under ${INSTALL_DIR}" >&2
  exit 127
fi
# The env file names the Node this install put on the box. Falling back to
# whatever `node` PATH resolves is deliberate and last-resort only: it keeps a
# hand-edited or partially-migrated install runnable rather than dead, but the
# named one is what the installer verified.
NODE_BIN="${EMPYRALIS_NODE_BIN:-node}"
if [[ "${NODE_BIN}" != "node" && ! -x "${NODE_BIN}" ]]; then
  echo "configured Node (${NODE_BIN}) is missing; falling back to whatever is on PATH" >&2
  NODE_BIN="node"
fi
cd "$(dirname "${entry}")"
exec "${NODE_BIN}" "${entry}"
EOF
  chmod 0755 "${BIN_DIR}/run-gateway"
}

write_launchd_plist() {
  # StandardOutPath/StandardErrorPath's directory MUST exist before launchd is
  # asked to load this. launchd opens those files ITSELF, before exec'ing the
  # program, and does not create their parent — a missing directory kills the
  # job instantly with status 78 (EX_CONFIG) and writes nothing anywhere,
  # because the only place it could have written is the file that failed to
  # open. That exact failure has already been paid for once on this codebase's
  # own OpenClaw transport plist (2026-08-14); see the logPath field's comment
  # on GatewaySupervisorUnitDefinition in gateway-supervisor-install.ts.
  mkdir -p "${LOG_DIR}"
  local log_path="${LOG_DIR}/gateway-launchd.log"
  log "writing ${PLIST_PATH}"
  cat > "${PLIST_PATH}" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${BIN_DIR}/run-gateway</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>EMPYRALIS_ENV_FILE</key>
    <string>${ENV_FILE}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>${INSTALL_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>${log_path}</string>
  <key>StandardErrorPath</key>
  <string>${log_path}</string>
</dict>
</plist>
PLISTEOF
  chmod 0644 "${PLIST_PATH}"
}

start_services() {
  local target="gui/$(id -u)"
  log "starting the Agent Computer"
  # Unlike gateway-supervisor-install.ts — which never touches a running job
  # because it runs unattended from inside a live gateway — this is install
  # time and the job being replaced is unambiguously the one this installer
  # owns, under its own label. Booting it out first is what makes a re-run
  # pick up a new artifact, exactly like the Linux installer's
  # `systemctl restart`.
  launchctl bootout "${target}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
  if ! launchctl bootstrap "${target}" "${PLIST_PATH}" 2>/tmp/empyralis-launchctl.err; then
    local err
    err="$(tail -c 300 /tmp/empyralis-launchctl.err 2>/dev/null | tr '\n' ' ')"
    fail "could not start the Agent Computer (${err:-launchctl bootstrap failed})"
  fi
  launchctl enable "${target}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
  launchctl kickstart "${target}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
}

print_recent_logs() {
  tail -n 80 "${LOG_DIR}/gateway-launchd.log" 2>/dev/null || true
  launchctl print "gui/$(id -u)/${LAUNCHD_LABEL}" 2>/dev/null | head -n 40 || true
}

wait_for_registration() {
  local deadline
  deadline=$((SECONDS + REGISTRATION_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if [[ -s "${STATE_ROOT}/gateway/registration.json" ]]; then
      return 0
    fi
    sleep 2
  done
  print_recent_logs
  # ADVISORY, not terminal, for the same reason the Linux installer's is:
  # everything this script had to do has succeeded, and the LaunchAgent keeps
  # running and retrying long after this exits — often successfully. Reporting
  # it as terminal would let the platform give up on a box that is about to
  # come up.
  report_beacon 0 "the Agent Computer was installed and started, but had not confirmed the connection ${REGISTRATION_TIMEOUT_SECONDS}s later. It is still running and retrying."
  fail "the Agent Computer did not confirm the connection within ${REGISTRATION_TIMEOUT_SECONDS}s"
}

main() {
  set_phase preflight "checking this Mac"
  require_not_root
  detect_macos
  resolve_host_arch
  require_pairing_token
  set_phase prepare_host "preparing this Mac"
  prepare_directories
  set_phase gateway_download "downloading Agent Computer"
  install_release_artifacts
  set_phase node_install "installing Node.js"
  # After the artifact, because the artifact is what declares which Node this
  # build needs.
  install_node
  set_phase docker_install "checking Docker"
  probe_docker
  set_phase service_setup "setting up the service"
  write_env_file
  write_launcher_script
  write_launchd_plist
  set_phase service_start "starting the service"
  start_services
  set_phase registration_wait "waiting for it to connect"
  wait_for_registration
  log "Agent Computer connected"
  log ""
  log "It runs in the background and starts again when you log in."
  log "  logs:   ${LOG_DIR}/gateway-launchd.log"
  log "  remove: launchctl bootout gui/$(id -u)/${LAUNCHD_LABEL} && rm -f ${PLIST_PATH} && rm -rf ${INSTALL_ROOT}"
}

main "$@"
