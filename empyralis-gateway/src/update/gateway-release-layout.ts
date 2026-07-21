import path from "path";

/**
 * Where self-update stages new gateway builds and swaps the "current" build
 * a launcher resolves through.
 *
 * Layout mirrors scripts/install-agent-computer.sh's existing production
 * shape (install-agent-computer.sh:299-319): `<installRoot>/releases/<version>/
 * empyralis-gateway/{dist,node_modules,package.json}` plus a `gateway ->
 * empyralis-gateway` convenience symlink inside each release dir, and a
 * top-level `<installRoot>/current -> releases/<version>` symlink that a
 * launcher resolves through.
 *
 * installRoot deliberately defaults to a directory *next to* the gateway's
 * own state dir, NOT to install-agent-computer.sh's real INSTALL_ROOT
 * (/opt/empyralis/agent-computer) — that tree is `chown root:root` with
 * `chmod 644/755` (install-agent-computer.sh:316-318), so the gateway's own
 * unprivileged systemd service user (`User=empyralis`, install-agent-
 * computer.sh:374) cannot write there even though systemd's
 * `ReadWritePaths=` includes it (that only grants mount-level access; POSIX
 * file permissions still say no). STATE_ROOT, by contrast, IS
 * `chown -R "${SERVICE_USER}:${SERVICE_USER}"` (install-agent-computer.sh:151)
 * and already sits in ReadWritePaths — so a directory next to the gateway's
 * own state dir is the one location this process can actually write to
 * on the one real (systemd) deployment that exists today, with zero
 * changes to the installer's ownership/hardening. See gateway-self-update-
 * runtime.ts's module doc comment for the one-time bootstrap this implies.
 */
export interface GatewayReleaseLayout {
  installRoot: string;
  releasesDir: string;
  currentSymlinkPath: string;
}

export function resolveGatewayReleaseLayout(params: {
  stateDir: string;
  env?: NodeJS.ProcessEnv;
}): GatewayReleaseLayout {
  const env = params.env ?? process.env;
  const override = String(env.EMPYRALIS_GATEWAY_INSTALL_ROOT || "").trim();
  const installRoot = path.resolve(
    override || path.join(path.dirname(params.stateDir), "gateway-releases"),
  );
  return {
    installRoot,
    releasesDir: path.join(installRoot, "releases"),
    currentSymlinkPath: path.join(installRoot, "current"),
  };
}

/** Guards against a version string that could escape releasesDir via `..`
 *  or a path separator — target_version ultimately comes from a capability-
 *  invoke argument dispatched by the backend, so it's untrusted input by the
 *  time it reaches a filesystem path. */
export function sanitizeGatewayVersionSegment(version: string): string {
  const token = String(version || "").trim();
  if (!token || token === "." || token === ".." || /[\\/]/.test(token)) {
    throw new Error(`Invalid gateway version "${version}".`);
  }
  return token;
}

export function releaseDirFor(layout: GatewayReleaseLayout, version: string): string {
  return path.join(layout.releasesDir, sanitizeGatewayVersionSegment(version));
}

/** The dist/index.js entrypoint a release dir resolves to, through the
 *  `gateway` convenience symlink — mirrors the shape run-gateway (install-
 *  agent-computer.sh:322-361) already expects. */
export function gatewayEntrypointFor(releaseDir: string): string {
  return path.join(releaseDir, "gateway", "dist", "index.js");
}
