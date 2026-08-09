/**
 * Which Node runs the channel transport — deliberately NOT the one running
 * this gateway.
 *
 * openclaw@2026.6.10 requires Node >= 22.19. Every Agent Computer box installs
 * Node 20, and the gateway ships as a PREBUILT artifact whose native modules
 * (bufferutil, utf-8-validate, sharp) are compiled against Node 20's ABI — so
 * moving the box to Node 22 would break every published gateway on every
 * existing box. The two runtimes coexist:
 *
 *   /usr/bin/node                       Node 20   the Empyralis gateway
 *   ${INSTALL_ROOT}/openclaw-node/bin   Node 22   the channel transport only
 *
 * This module is the single place that decides that, and it does it by
 * PREPENDING one directory to a copy of PATH. Two consumers must agree
 * exactly, or the box installs under one Node and runs under another — which
 * presents as a transport that installed cleanly and then exits on every call:
 *
 *   1. the `npm install --global` child (the shebang of the installed
 *      `openclaw` bin is `#!/usr/bin/env node`, so the Node that RUNS it is
 *      whatever PATH resolves at run time — not the one that installed it);
 *   2. the supervised unit's own `Environment=PATH=`.
 *
 * Absent config leaves PATH untouched, which is correct for macOS and for any
 * box whose system Node already satisfies the floor — there is nothing to
 * prepend and no second runtime to install.
 */

import { execFileWithTimeout } from "../../shell/exec-file-with-timeout";

/** Where the installer puts the transport's own Node, when it needs one. */
export const OPENCLAW_NODE_BIN_DIR_ENV = "EMPYRALIS_OPENCLAW_NODE_BIN_DIR";

/**
 * `env` with the transport's Node first on PATH. Returns a NEW object; never
 * mutates. A blank/absent bin dir returns an unchanged copy rather than a
 * PATH with an empty leading entry — an empty PATH component means "the
 * current directory" to most resolvers, which is a footgun nobody wants on a
 * root-installed unit.
 */
export function withOpenClawNodeOnPath(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const binDir = String(env[OPENCLAW_NODE_BIN_DIR_ENV] || "").trim();
  if (!binDir) return { ...env };
  const currentPath = String(env.PATH || "");
  const already = currentPath.split(":").filter(Boolean)[0] === binDir;
  return { ...env, PATH: already || !currentPath ? currentPath || binDir : `${binDir}:${currentPath}` };
}

/** What `node --version` answers for the Node the transport will actually run
 *  on. Undefined when there is no runnable node at all — the caller reports
 *  that rather than assuming a version. */
export async function resolveOpenClawNodeVersion(env: NodeJS.ProcessEnv): Promise<string | undefined> {
  const childEnv = withOpenClawNodeOnPath(env);
  const result = await execFileWithTimeout("/usr/bin/env", ["sh", "-c", "node --version"], 15_000, {
    env: childEnv,
    encoding: "utf8" as const,
  });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim();
  return output.length > 0 ? output : undefined;
}
