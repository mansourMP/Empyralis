#!/bin/sh
# Stage the gateway's PRODUCTION dependencies for the desktop app bundle.
#
# WHY THIS EXISTS: the bundle shipped `empyralis-gateway/dist` and nothing
# else, so the very first `require` in the compiled gateway failed —
# `Cannot find module 'ws'` — and the app died with "the gateway exited
# immediately after starting (status exit status: 1)". That was true on
# EVERY machine, not just one missing a dependency: compiled JavaScript is
# not a runnable program, and `dist/` without `node_modules/` never was.
# It read as an environment problem because the message names Node, so it
# was chased on the customer's machine instead of in the packaging.
#
# devDependencies are omitted: TypeScript and the test harness are build
# tooling and have no business inside a customer's app bundle.
#
# The install runs under the BUNDLED Node, not whatever `node` happens to be
# first on PATH. The gateway has native addons, and a module compiled
# against the developer's Node ABI cannot be loaded by the Node we actually
# ship — that mismatch is silent until the app is on somebody else's Mac.
set -eu

cd "$(dirname "$0")/.."

STAGE=src-tauri/resources/gateway-node-modules
RUNTIME_BIN="$(pwd)/src-tauri/resources/node-runtime/bin"

if [ ! -x "$RUNTIME_BIN/node" ]; then
  echo "The portable Node runtime is not staged yet, so production" >&2
  echo "dependencies cannot be installed against the Node we ship." >&2
  echo "" >&2
  echo "  npm run desktop:node-runtime" >&2
  exit 1
fi

if [ ! -f empyralis-gateway/package-lock.json ]; then
  echo "empyralis-gateway/package-lock.json is missing; refusing to install" >&2
  echo "unpinned dependencies into a shipped app bundle." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp empyralis-gateway/package.json empyralis-gateway/package-lock.json "$WORK/"

# `npm ci` rather than `npm install`: the lockfile is the whole point of
# shipping someone else's code, and `ci` fails loudly when it disagrees with
# package.json instead of quietly resolving something newer.
( cd "$WORK" && PATH="$RUNTIME_BIN:$PATH" npm ci --omit=dev --no-audit --no-fund )

rm -rf "$STAGE"
mkdir -p "$(dirname "$STAGE")"
mv "$WORK/node_modules" "$STAGE"

# A staged tree with no `ws` in it is a bundle that cannot open a socket, and
# the failure lands on a customer rather than here. Fail now instead.
if [ ! -d "$STAGE/ws" ]; then
  echo "Staged dependencies are missing 'ws' — the gateway cannot connect." >&2
  exit 1
fi

echo "Staged production gateway dependencies at $STAGE"
