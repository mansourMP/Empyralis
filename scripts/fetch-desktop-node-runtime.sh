#!/bin/sh
# Fetch the pinned portable Node runtime the desktop app bundles.
#
# WHY THIS EXISTS: without it, a local `cargo tauri build` produces an app
# that cannot start an Agent Computer at all. `resolve_gateway_launcher`
# (src-tauri/src/lib.rs) requires BOTH bundled resources — the gateway AND a
# Node to run it under — and falls back to a repo-relative dev path when
# either is missing. On a customer's machine that path does not exist, so the
# app fails with a message telling them to run an npm command. CI has fetched
# this since the app was built (.github/workflows/build.yml, "Fetch portable
# Node runtime"); there was simply no way to do the same thing by hand, which
# meant every locally-built app was quietly broken.
#
# The version is READ FROM THE BUILT GATEWAY, never typed here — same shape
# as the CI step, and for the reason its own source comment gives: it is the
# version the gateway's native addons were compiled against, so a literal in
# a second place is a drift bug waiting to happen.
set -eu

cd "$(dirname "$0")/.."

if [ ! -f empyralis-gateway/dist/update/desktop-node-version.js ]; then
  echo "The gateway is not built, so the pinned Node version cannot be read." >&2
  echo "Build it first:" >&2
  echo "" >&2
  echo "  npm ci --prefix empyralis-gateway && npm run build --prefix empyralis-gateway" >&2
  exit 1
fi

VERSION="$(node -e "process.stdout.write(require('./empyralis-gateway/dist/update/desktop-node-version.js').DESKTOP_GATEWAY_NODE_VERSION)")"
if [ -z "$VERSION" ]; then
  echo "Could not resolve DESKTOP_GATEWAY_NODE_VERSION from the built gateway." >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin) PLATFORM=darwin; EXT=tar.gz; TAR_FLAG=-xzf ;;
  Linux)  PLATFORM=linux;  EXT=tar.xz; TAR_FLAG=-xJf ;;
  # Windows is explicitly out of scope for this app — CLAUDE.md's "Windows
  # is out" entry. Refuse rather than emit something that cannot work.
  *) echo "Unsupported platform: $(uname -s). This app ships macOS and Linux only." >&2; exit 1 ;;
esac

case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64 ;;
  x86_64)        ARCH=x64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

DEST=src-tauri/resources/node-runtime
TARBALL="node-v${VERSION}-${PLATFORM}-${ARCH}.${EXT}"
URL="${EMPYRALIS_NODE_DIST_BASE_URL:-https://nodejs.org/dist}/v${VERSION}/${TARBALL}"

if [ -x "$DEST/bin/node" ] && [ "$("$DEST/bin/node" --version 2>/dev/null)" = "v${VERSION}" ]; then
  echo "Node v${VERSION} (${PLATFORM}-${ARCH}) is already in place."
  exit 0
fi

echo "Fetching $URL"
curl -fsSL -o "${TMPDIR:-/tmp}/$TARBALL" "$URL"

# Keep the tracked files (.gitignore, bin/README.placeholder) — everything
# else in here is fetched content and is replaced wholesale.
rm -rf "$DEST"
mkdir -p "$DEST"
tar "$TAR_FLAG" "${TMPDIR:-/tmp}/$TARBALL" -C "$DEST" --strip-components=1
git checkout -- "$DEST/.gitignore" "$DEST/bin/README.placeholder" 2>/dev/null || true

test -x "$DEST/bin/node"
echo "Bundled Node runtime ready: $("$DEST/bin/node" --version) (${PLATFORM}-${ARCH})"
