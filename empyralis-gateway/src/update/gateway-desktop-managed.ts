/**
 * Whether this gateway is one the Empyralis DESKTOP APP owns.
 *
 * WHY THIS DISTINCTION HAS TO EXIST AT ALL. There are now two completely
 * different answers to "how does this box get a newer gateway":
 *
 * ```text
 * VPS / installer box   the gateway updates ITSELF: it downloads a tarball,
 *                       stages it into <stateDir>/../gateway-releases,
 *                       swaps `current`, and the supervisor unit — which
 *                       resolves through that symlink — starts the new build.
 *
 * DESKTOP APP box       the gateway lives INSIDE Empyralis.app, at
 *                       Contents/Resources/gateway/dist. It is shipped,
 *                       versioned and replaced BY THE APP. Nothing it could
 *                       download for itself would ever be launched, because
 *                       both launch paths (the app's own child process and
 *                       the login item) point into the bundle.
 * ```
 *
 * Without a marker, the second box looks to every existing check exactly like
 * a first box that is broken: `gateway-launch-updatability.ts` finds a login
 * item whose ProgramArguments do not resolve through `gateway-releases/current`
 * and correctly reports `not_updatable`, the backend refuses with
 * `launch_path_not_updatable`, and the Hardware page shows the customer a
 * block of `launchctl` commands to repair a machine that is not broken and
 * that they could not run anyway.
 *
 * CLAUDE.md's MAN-331 rule is what makes the marker the right fix rather than
 * a special case: never advertise an update whose success could not be
 * observed. A gateway self-update on a desktop box is precisely that — it
 * would complete, report success, and the next start would run the same
 * bundled build, forever. So the honest answer is a refusal that names the
 * real update path, and this is the fact the backend needs to give it.
 */

/** Set to "1" by the desktop app, on both launch paths, and by nothing else.
 *  A VPS box never sets it, which is what keeps every existing box's
 *  behaviour and supervisor unit byte-identical. */
export const GATEWAY_DESKTOP_MANAGED_ENV = "EMPYRALIS_GATEWAY_DESKTOP_MANAGED";

export function isDesktopManagedGateway(env: NodeJS.ProcessEnv = process.env): boolean {
  return String(env[GATEWAY_DESKTOP_MANAGED_ENV] || "").trim() === "1";
}

export interface DesktopSupervisorEnvironmentInput {
  /** The state dir THIS process resolved — the one holding the pairing
   *  credentials, not the default. */
  stateDir: string;
  /** The control plane THIS process is talking to. */
  apiBaseUrl: string;
  env?: NodeJS.ProcessEnv;
}

/**
 * The environment the desktop app's login item must carry, or `undefined`
 * when this is not a desktop-managed gateway.
 *
 * THIS FIXES A LIVE BUG, and it is worth stating because it is not what this
 * module was created for. `resolveExpectedSupervisorUnit` passed NO
 * environment on macOS, so the LaunchAgent the desktop app installs started a
 * gateway with neither `EMPYRALIS_GATEWAY_STATE_DIR` nor
 * `EMPYRALIS_GATEWAY_API_URL`. Both then fell back to their defaults —
 * `~/.empyralis/gateway` and `http://127.0.0.1:8001/api` — which is a
 * DIFFERENT state directory holding none of this machine's pairing
 * credentials, pointed at a control plane that does not exist on a
 * customer's Mac. So the gateway that starts at login could never register.
 * The Agent Computer only ever worked while the app itself was open and had
 * spawned its own correctly configured child.
 *
 * Returning `undefined` off the desktop path is the load-bearing half: every
 * VPS box renders its unit byte-for-byte as before, so none of them drifts,
 * and none of them tries to rewrite a root-owned unit it cannot write.
 */
export function desktopManagedSupervisorEnvironment(
  input: DesktopSupervisorEnvironmentInput,
): Record<string, string> | undefined {
  const env = input.env ?? process.env;
  if (!isDesktopManagedGateway(env)) {
    return undefined;
  }
  const stateDir = String(input.stateDir || "").trim();
  const apiBaseUrl = String(input.apiBaseUrl || "").trim();
  if (!stateDir || !apiBaseUrl) {
    // A half-filled environment is worse than none: it would pin one value
    // and silently default the other, which is the bug above wearing a
    // different mask.
    return undefined;
  }
  return {
    EMPYRALIS_GATEWAY_API_URL: apiBaseUrl,
    EMPYRALIS_GATEWAY_STATE_DIR: stateDir,
    // Carried forward so the gateway the LOGIN ITEM starts reports itself as
    // desktop-managed too. Without this the app-spawned gateway and the
    // login-item gateway would give the backend two different answers about
    // the same machine, depending only on which one happened to connect last.
    [GATEWAY_DESKTOP_MANAGED_ENV]: "1",
  };
}
