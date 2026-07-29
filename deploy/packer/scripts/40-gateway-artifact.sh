#!/usr/bin/env bash
# Bake the gateway artifact into the image.
#
# DECISION: the gateway IS baked in, not fetched at boot.
#
# The single most likely silent killer of a provision was this exact download
# (see install_gateway_from_artifact in scripts/install-agent-computer.sh:350) —
# a wrong version, a moved path, a 404, a rate limit, or a captive proxy on a
# thirty-second-old box, all reported to the control plane as nothing at all.
# Removing boot-time downloads is the entire point of this image. Doing the
# download here means a bad release fails a CI job that a human is already
# watching, with the URL and HTTP status in the log, and costs nothing to retry.
#
# The trade-off is honest and worth naming: the image is now pinned to one
# gateway build, so shipping a new gateway means either rebuilding the image or
# letting the gateway's own self-update path (empyralis-gateway/src/update/*)
# take over on a running box. The launcher already prefers a self-updated
# release over the baked one, so an old image does not trap a box on old code.
#
# Ported from install_gateway_from_artifact() + install_release_artifacts()
# (scripts/install-agent-computer.sh:350-410), with checksum verification added
# — a build-time-only luxury the boot path could not afford.
set -Eeuo pipefail

INSTALL_ROOT="/opt/empyralis/agent-computer"
CURRENT_DIR="${INSTALL_ROOT}/current"
CONFIG_DIR="/etc/empyralis"

VERSION="${EMPYRALIS_GATEWAY_VERSION:?EMPYRALIS_GATEWAY_VERSION is required}"
BASE_URL="${EMPYRALIS_ARTIFACT_BASE_URL:?EMPYRALIS_ARTIFACT_BASE_URL is required}"
VERIFY_CHECKSUM="${EMPYRALIS_VERIFY_CHECKSUM:-1}"
BASE_IMAGE="${EMPYRALIS_BASE_IMAGE:-unknown}"
SOURCE_COMMIT="${EMPYRALIS_SOURCE_COMMIT:-}"

TARBALL="empyralis-gateway-linux-x64.tar.gz"
ARTIFACT_URL="${BASE_URL%/}/${VERSION}/${TARBALL}"
CHECKSUM_URL="${ARTIFACT_URL}.sha256"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

download() {
  # download <url> <dest> ; prints a specific reason and fails hard.
  local url="$1" dest="$2" http_status="" curl_exit=0
  http_status="$(curl -sS -L -m 300 -w '%{http_code}' -o "${dest}" "${url}")" || curl_exit=$?
  if [[ "${curl_exit}" -ne 0 || "${http_status}" != "200" || ! -s "${dest}" ]]; then
    echo "[image-build] ERROR: could not download ${url} (HTTP ${http_status:-none}, curl exit ${curl_exit})." >&2
    echo "[image-build]        The release host must serve this exact object for version '${VERSION}'." >&2
    echo "[image-build]        Publish it with .github/workflows/release-gateway-linux.yml first." >&2
    return 1
  fi
}

echo "[image-build] downloading gateway artifact ${ARTIFACT_URL}"
download "${ARTIFACT_URL}" "${tmp_dir}/${TARBALL}"

artifact_sha256=""
if [[ "${VERIFY_CHECKSUM}" == "1" ]]; then
  echo "[image-build] verifying checksum ${CHECKSUM_URL}"
  download "${CHECKSUM_URL}" "${tmp_dir}/${TARBALL}.sha256"
  # release-gateway-linux.yml writes `sha256sum <tarball>`, so the sidecar names
  # the file relative to its own directory — check it from there.
  ( cd "${tmp_dir}" && sha256sum -c "${TARBALL}.sha256" ) || {
    echo "[image-build] ERROR: gateway artifact failed checksum verification." >&2
    exit 1
  }
fi
artifact_sha256="$(sha256sum "${tmp_dir}/${TARBALL}" | awk '{print $1}')"

release_dir="${INSTALL_ROOT}/releases/${VERSION}"
stage_dir="${release_dir}.tmp"

rm -rf "${stage_dir}"
mkdir -p "${stage_dir}/empyralis-gateway"

# The archive ROOT holds dist/, node_modules/, package.json — no wrapper
# directory. Both consumers (the installer and the gateway's own self-update
# installer) create the empyralis-gateway/ level themselves; this matches.
if ! tar -xzf "${tmp_dir}/${TARBALL}" -C "${stage_dir}/empyralis-gateway"; then
  rm -rf "${stage_dir}"
  echo "[image-build] ERROR: could not extract the gateway artifact" >&2
  exit 1
fi

if [[ ! -f "${stage_dir}/empyralis-gateway/dist/index.js" ]]; then
  rm -rf "${stage_dir}"
  echo "[image-build] ERROR: gateway artifact is missing dist/index.js — the published archive is malformed" >&2
  exit 1
fi

rm -rf "${release_dir}"
mv "${stage_dir}" "${release_dir}"

# Convenience alias so run-gateway's ${INSTALL_DIR}/gateway/... fast path
# resolves directly, matching the layout callers already expect.
ln -sfn empyralis-gateway "${release_dir}/gateway"
ln -sfn "${release_dir}" "${CURRENT_DIR}"

chown -R root:root "${INSTALL_ROOT}/releases"
find "${release_dir}" -type d -exec chmod 0755 {} +
find "${release_dir}" -type f -exec chmod u=rw,go=r {} +

# Provenance the box can report about itself. No secrets: a version string, a
# URL, a hash, a commit. World-readable on purpose — support needs it, and
# "what is this box actually running" was unanswerable during the outage.
cat > "${CONFIG_DIR}/image-manifest.json" <<MANIFEST
{
  "image": "empyralis-agent-computer",
  "base_image": "${BASE_IMAGE}",
  "gateway_version": "${VERSION}",
  "gateway_artifact_url": "${ARTIFACT_URL}",
  "gateway_artifact_sha256": "${artifact_sha256}",
  "node_version": "$(node --version)",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source_commit": "${SOURCE_COMMIT}"
}
MANIFEST
chmod 0644 "${CONFIG_DIR}/image-manifest.json"

echo "[image-build] gateway ${VERSION} baked in (sha256 ${artifact_sha256})"
