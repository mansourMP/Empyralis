import { execFileWithTimeout } from "./exec-file-with-timeout";
import { resolveCommandPath } from "./user-install-dirs";
import { invalidatePassiveInventoryCache } from "../health/service-inventory";

/**
 * The founder's own report: "what if [Docker] is not running, why should it
 * be without commands? ... while a user is running this thing they won't
 * have any agents to keep everything fixed." Before this module, nothing in
 * this repository ever attempted to START Docker — `shell/runtime.ts`'s
 * isDockerReady() and `health/service-inventory.ts`'s probeDocker() only
 * ever PROBED it, and a not-ready daemon was a dead end with no lever: the
 * one thing that could fix it (the agent, via shell.execute) is the exact
 * capability that Docker being down takes away.
 *
 * This module is the lever. `ensureDockerReady()` probes, and if Docker is
 * installed but not running, attempts to start it — Docker Desktop on
 * macOS (`open -a Docker`, a GUI app that does not launch at login unless
 * the user opted in), the docker service on Linux (`systemctl start
 * docker`, belt-and-braces: provisioned Agent Computers already enable this
 * at install time via install-agent-computer.sh / deploy/packer/scripts/
 * 60-docker.sh, so this mostly recovers a daemon that died after a healthy
 * boot) — then polls briefly for the daemon socket to come up.
 *
 * THREE OUTCOMES, NEVER COLLAPSED (see CLAUDE.md's standing "failed" vs.
 * "could not confirm" law): `not_installed` (nothing to start — Docker
 * itself is absent), a start ATTEMPT that failed or timed out (installed,
 * asked to start, still not up), and `started` (it worked). Every caller
 * that turns this into a customer-facing message must say which one — never
 * a single "Docker isn't running" for all three, and never tell a Linux box
 * to "open Docker Desktop" or a Mac to "run systemctl".
 *
 * NEVER A FALLBACK TO UNSANDBOXED EXECUTION. This module only ever answers
 * "is Docker ready, and if not, did starting it help" — it has no opinion on
 * execution mode. shell/runtime.ts's full_access path exists for a
 * different, separately-granted reason (see resolveExecutionMode) and stays
 * completely untouched by anything here; a failed autostart still ends in
 * the same honest refusal sandbox mode already gave.
 *
 * SINGLE-FLIGHTED AND COOLDOWN-GATED, module-level, on purpose. Both the
 * blocking tool-invoke-time caller (shell/runtime.ts, at most once per call)
 * and the non-blocking heartbeat-time caller (cloud/ws-client.ts, once per
 * heartbeat tick) call this SAME function — sharing one piece of state is
 * what stops a wedged daemon from becoming a fork bomb of `open -a Docker`/
 * `systemctl start docker` calls, exactly the shape CLAUDE.md's own
 * exec-file-with-timeout entry warns about for a probe that fires on every
 * shell.execute AND from the heartbeat. Every process spawn goes through
 * execFileWithTimeout, never execFile's own (non-)timeout option, for the
 * same reason.
 */

export interface DockerAutostartQueryResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/** Same shape as gateway-launch-updatability.ts's GatewayLaunchCommandRunner
 *  — the house pattern for injecting a read/write-capable subprocess call so
 *  a test never spawns a real process. */
export type DockerAutostartCommandRunner = (
  command: string,
  args: string[],
  timeoutMs: number,
) => Promise<DockerAutostartQueryResult>;

export type DockerAutostartOutcome =
  /** Docker was already responding — nothing was started. */
  | { kind: "already_ready" }
  /** No `docker` binary found on this machine at all. Nothing to start;
   *  the honest next step is installing Docker, not retrying. */
  | { kind: "not_installed" }
  /** This OS has no known start command wired up (anything but macOS/Linux
   *  today), or Linux without `systemctl` on PATH. */
  | { kind: "unsupported_platform"; platform: string }
  /** The start command itself failed or hung — Docker Desktop's bundle is
   *  missing despite the CLI being present, `systemctl start docker` was
   *  refused (e.g. no permission), etc. `detail` is the real stderr/stdout,
   *  truncated. */
  | { kind: "start_command_failed"; detail: string }
  /** The start command ran successfully but the daemon had not answered
   *  `docker info` by the end of the bounded poll window. It may still be
   *  coming up in the background — Docker Desktop in particular can take
   *  well past this module's own budget on a cold boot. */
  | { kind: "start_timed_out" }
  /** The start command ran and the daemon answered `docker info` before the
   *  poll window ran out. */
  | { kind: "started" }
  /** A start attempt is already in flight, or one completed inside the
   *  cooldown window and we didn't spawn another. `previous` is what that
   *  attempt concluded, so a caller can still report something specific. */
  | { kind: "cooldown"; previous: DockerAutostartOutcome; retryAfterMs: number };

export interface EnsureDockerReadyDeps {
  platform?: NodeJS.Platform;
  env?: NodeJS.ProcessEnv;
  /** Defaults to the same standardUserInstallDirs-aware lookup every other
   *  CLI probe in this codebase uses (resolveCommandPath). */
  commandExists?: (command: string) => string | null;
  runCommand?: DockerAutostartCommandRunner;
  now?: () => number;
  /** Defaults to a real setTimeout-based sleep. Tests inject an
   *  instant-resolving fake so the bounded poll loop doesn't burn real
   *  wall-clock time. */
  sleep?: (ms: number) => Promise<void>;
}

// `docker info` while a daemon is genuinely wedged can take a while to fail
// on its own — matches DOCKER_COMMAND_TIMEOUT_MS in health/service-
// inventory.ts's probeDocker so this module and the passive probe agree on
// what "docker isn't answering" means.
const READY_PROBE_TIMEOUT_MS = 10_000;
// `open -a Docker` returns almost instantly (it only launches the app, it
// doesn't wait for it). `systemctl start docker` blocks until the unit
// reports active/failed, which for a cold daemon can genuinely take a few
// seconds — bounded generously so a slow-but-real start isn't mistaken for
// a hang.
const START_COMMAND_TIMEOUT_MS = 15_000;
// After the start command returns, poll for the daemon socket rather than
// trusting the start command's own exit code — `open -a Docker` exiting 0
// only means the app was launched, not that it's ready yet.
const POST_START_POLL_INTERVAL_MS = 2_000;
const POST_START_POLL_ATTEMPTS = 6; // ~12s of polling on top of the start command
const POST_START_POLL_PROBE_TIMEOUT_MS = 4_000;
// At most one real start attempt per window. Chosen to be comfortably
// longer than this module's own worst-case single attempt (~15s start +
// ~12s poll ≈ 27s) so a caller can never observe two attempts racing, and
// short enough that a Mac where the user manually launches Docker mid-window
// is picked up by the very next probe rather than staying stuck on a stale
// "cooldown" outcome for long.
const COOLDOWN_MS = 45_000;

function truncate(value: string, maxLength = 300): string {
  const token = value.replace(/\s+/g, " ").trim();
  return token.length > maxLength ? `${token.slice(0, maxLength - 3)}...` : token;
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  });
}

const defaultRunCommand: DockerAutostartCommandRunner = async (command, args, timeoutMs) => {
  const result = await execFileWithTimeout(command, args, timeoutMs);
  return {
    // A spawn failure (ENOENT and friends) never reached a real "docker" —
    // surfaced as a distinct non-zero/null code so callers don't confuse it
    // with the target actually running and refusing.
    exitCode: result.error && typeof result.error.code === "string" ? null : result.exitCode,
    stdout: result.stdout,
    stderr: result.timedOut
      ? `Timed out after ${timeoutMs}ms; the process did not exit on its own and was killed.`
      : result.error
        ? result.error.message
        : result.stderr,
    timedOut: result.timedOut,
  };
};

async function probeDockerInfo(runCommand: DockerAutostartCommandRunner, timeoutMs: number): Promise<boolean> {
  const result = await runCommand("docker", ["info", "--format", "{{.ServerVersion}}"], timeoutMs);
  return !result.timedOut && result.exitCode === 0;
}

interface PlatformStartCommand {
  command: string;
  args: string[];
  /** Human-readable name for the thing being started, used only in
   *  messages — never the raw command, so a Linux customer is never told to
   *  run `open -a Docker` and vice versa. */
  description: string;
}

function resolvePlatformStartCommand(
  platform: NodeJS.Platform,
  commandExists: (command: string) => string | null,
): PlatformStartCommand | null {
  if (platform === "darwin") {
    // MACOS NEVER LAUNCHES DOCKER. Founder's decision, 2026-08-26, after
    // watching Docker Desktop open itself on his own laptop when he had
    // merely opened his app: *"this agent thing, the menu application must
    // be running without Docker… Docker is shit, it should be removed."*
    //
    // This used to `open -a Docker`, which is why the app appeared
    // unbidden and then began downloading an update. On a personal Mac that
    // is a heavyweight GUI application hijacking the machine to buy an
    // optimisation the customer never asked for — and it is only ever an
    // OPTIMISATION, because the 2026-08-22 ruling already guarantees a host
    // run when the sandbox is unavailable. Nothing breaks by not starting
    // it; the command simply runs on the computer instead.
    //
    // Returning null here means `ensureDockerReady` reports
    // `unsupported_platform`, `ensureDockerAvailable` reports not-ready, and
    // `resolveRun` falls through to the host path that already exists and is
    // already labelled honestly to the customer. Docker that is ALREADY
    // running is still used — `probeDockerInfo` runs before this and returns
    // `already_ready` — so someone who wants the sandbox just leaves Docker
    // open. What is removed is the DEPENDENCY, not the capability.
    //
    // STATE THE CONSEQUENCE, do not soften it: on a Mac with Docker closed,
    // an agent's commands now run directly on that Mac. What still
    // constrains them is shell/command-policy.ts — the hard-blocked command
    // list and the protected paths (vault, ~/.ssh, ~/.gnupg, /etc/empyralis,
    // the agent's own state dir) — which is checked in EVERY mode, before
    // the isolation decision, and is not bypassable. The founder was told
    // this trade-off explicitly and chose it twice.
    //
    // Linux is UNCHANGED: `systemctl start docker` starts a background
    // daemon nobody sees, on a box whose whole purpose is to run the agent.
    // The objection was to a GUI app taking over a personal computer, and
    // that objection does not transfer to a VPS.
    return null;
  }
  if (platform === "linux") {
    if (!commandExists("systemctl")) {
      return null;
    }
    return { command: "systemctl", args: ["start", "docker"], description: "the docker service" };
  }
  return null;
}

async function performStartAttempt(
  platform: NodeJS.Platform,
  commandExists: (command: string) => string | null,
  runCommand: DockerAutostartCommandRunner,
  sleep: (ms: number) => Promise<void>,
): Promise<DockerAutostartOutcome> {
  const startCommand = resolvePlatformStartCommand(platform, commandExists);
  if (!startCommand) {
    return { kind: "unsupported_platform", platform };
  }
  const startResult = await runCommand(startCommand.command, startCommand.args, START_COMMAND_TIMEOUT_MS);
  if (startResult.timedOut) {
    return {
      kind: "start_command_failed",
      detail: `Asking this computer to start ${startCommand.description} did not respond within ${START_COMMAND_TIMEOUT_MS}ms.`,
    };
  }
  if (startResult.exitCode !== 0) {
    return {
      kind: "start_command_failed",
      detail: truncate(
        startResult.stderr || startResult.stdout || `starting ${startCommand.description} exited with code ${startResult.exitCode}.`,
      ),
    };
  }
  for (let attempt = 0; attempt < POST_START_POLL_ATTEMPTS; attempt += 1) {
    await sleep(POST_START_POLL_INTERVAL_MS);
    if (await probeDockerInfo(runCommand, POST_START_POLL_PROBE_TIMEOUT_MS)) {
      return { kind: "started" };
    }
  }
  return { kind: "start_timed_out" };
}

// Module-level, deliberately shared by every caller in this process — see
// this file's header comment for why a per-call instance would defeat the
// cooldown/single-flight guarantee.
let inFlightAttempt: Promise<DockerAutostartOutcome> | null = null;
let lastAttempt: { atMs: number; outcome: DockerAutostartOutcome } | null = null;

/** Test-only: clears the module-level single-flight/cooldown state so tests
 *  don't leak state into one another. Never call this from production code. */
export function resetDockerAutostartStateForTests(): void {
  inFlightAttempt = null;
  lastAttempt = null;
}

/**
 * Ensures Docker is ready, starting it if it is installed but not running.
 * Never throws — every failure mode is a distinct `DockerAutostartOutcome`,
 * for the caller to turn into an honest, specific message.
 */
export async function ensureDockerReady(deps: EnsureDockerReadyDeps = {}): Promise<DockerAutostartOutcome> {
  const platform = deps.platform ?? process.platform;
  const env = deps.env ?? process.env;
  const commandExists = deps.commandExists ?? ((command: string) => resolveCommandPath(command, env, platform));
  const runCommand = deps.runCommand ?? defaultRunCommand;
  const now = deps.now ?? Date.now;
  const sleep = deps.sleep ?? defaultSleep;

  if (!commandExists("docker")) {
    return { kind: "not_installed" };
  }

  if (await probeDockerInfo(runCommand, READY_PROBE_TIMEOUT_MS)) {
    return { kind: "already_ready" };
  }

  if (inFlightAttempt) {
    return inFlightAttempt;
  }

  if (lastAttempt) {
    const elapsedMs = now() - lastAttempt.atMs;
    if (elapsedMs >= 0 && elapsedMs < COOLDOWN_MS) {
      return { kind: "cooldown", previous: lastAttempt.outcome, retryAfterMs: COOLDOWN_MS - elapsedMs };
    }
  }

  inFlightAttempt = performStartAttempt(platform, commandExists, runCommand, sleep)
    .then((outcome) => {
      lastAttempt = { atMs: now(), outcome };
      if (outcome.kind === "started") {
        // The passive probe (health/service-inventory.ts) caches its own
        // "docker: offline" read for up to 60s — without this, a customer
        // who just watched their agent fix Docker would still see the
        // Hardware page and the next heartbeat report it as down for
        // however long is left on that cache.
        invalidatePassiveInventoryCache();
      }
      return outcome;
    })
    .finally(() => {
      inFlightAttempt = null;
    });
  return inFlightAttempt;
}

/** Maps a Node platform token to what a customer actually calls their
 *  computer's OS — `outcome.platform` on `unsupported_platform` carries the
 *  raw `NodeJS.Platform` value (e.g. "win32") because that is the correct,
 *  stable, machine-readable field for a caller to branch on (see this
 *  file's own test asserting the OUTCOME OBJECT carries "win32" verbatim);
 *  it was never meant to be read out loud to a person. Falls back to the
 *  raw token for any platform not named here, so an unmapped future value
 *  still degrades to something true rather than throwing. */
function describePlatformForHuman(platform: string): string {
  switch (platform) {
    case "win32":
      return "Windows";
    case "darwin":
      return "macOS";
    case "linux":
      return "Linux";
    default:
      return platform;
  }
}

/** Renders a DockerAutostartOutcome into a short, honest clause for a
 *  human — never names a command the reader can't or shouldn't run
 *  themselves (no "run systemctl", no "open Docker Desktop" on Linux), and
 *  never suggests a retry that can't work. */
export function describeDockerAutostartOutcome(outcome: DockerAutostartOutcome): string {
  switch (outcome.kind) {
    case "already_ready":
    case "started":
      return "Docker is ready.";
    case "not_installed":
      return "Docker is not installed on this computer, so there is nothing to start — install Docker, then retry.";
    case "unsupported_platform":
      return `This gateway does not know how to start Docker automatically on ${describePlatformForHuman(outcome.platform)}.`;
    case "start_command_failed":
      return `Docker is installed but could not be started automatically (${outcome.detail})`;
    case "start_timed_out":
      return "Docker was asked to start and may still be starting up — it did not finish in time. Wait a bit and try again.";
    case "cooldown":
      return `Docker was already asked to start a moment ago and has not finished yet (retry in about ${Math.ceil(outcome.retryAfterMs / 1000)}s). ${describeDockerAutostartOutcome(outcome.previous)}`;
    default: {
      const exhaustive: never = outcome;
      return String(exhaustive);
    }
  }
}
