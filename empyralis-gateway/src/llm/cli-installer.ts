import { spawn } from "child_process";
import fs from "fs";
import path from "path";

// cli.install (BYO-brain onboarding, Build F): installs the box's OWN Claude
// Code / Codex CLI via its real global npm install, run directly on the
// host — never inside shell_sandbox's throwaway Docker container, which
// would leave the binary nowhere cli-runner.ts's later spawn (also
// host-side) could ever find it on PATH.
//
// The install command is NOT caller-supplied. `runtime` selects one of
// exactly two hardcoded npm packages baked into this file (INSTALL_PACKAGE
// below) — there is no code path from a WSS payload to an arbitrary shell
// string here, unlike shell.execute's full_access mode. That is the whole
// safety argument for not needing full_access's heavier dual opt-in gate:
// the capability itself is the boundary, not a runtime policy check.

export type CliInstallRuntime = "claude_code" | "codex";

export type CliInstallFailureKind =
  | "npm_missing"
  | "permission_denied"
  | "network_error"
  | "timeout"
  | "crash";

/** Distinguishes WHY an install didn't complete, mirroring cli-runner.ts's
 *  CliRunError so the control plane's platform-voice error mapper can react
 *  to "npm is missing" vs. "permission denied" vs. "timed out" instead of one
 *  blanket failure. */
export class CliInstallError extends Error {
  readonly kind: CliInstallFailureKind;

  constructor(kind: CliInstallFailureKind, message: string) {
    super(message);
    this.name = "CliInstallError";
    this.kind = kind;
  }
}

export interface CliInstallResult {
  runtime: CliInstallRuntime;
  package: string;
  installed: true;
  os: NodeJS.Platform;
  /** Best-effort — parsed from `npm ls -g --depth=0` after install; absent
   *  rather than fabricated if parsing doesn't find a version line. */
  version?: string;
}

export interface CliInstallParams {
  runtime: CliInstallRuntime;
  timeoutMs: number;
}

const INSTALL_PACKAGE: Record<CliInstallRuntime, string> = {
  claude_code: "@anthropic-ai/claude-code",
  codex: "@openai/codex",
};

const BINARY_NAME: Record<CliInstallRuntime, string> = {
  claude_code: "claude",
  codex: "codex",
};

// npm installs can be genuinely slow on a cold cache / slow network — this
// is deliberately longer than cli-runner.ts's generation timeout.
const DEFAULT_TIMEOUT_MS = 180_000;
const FORCE_KILL_GRACE_MS = 5_000;
const MAX_BUFFERED_OUTPUT_CHARS = 500_000;

/** Minimal structural subset of node:child_process's ChildProcess — same
 *  shape as cli-runner.ts's CliChildProcessLike, so tests can inject a plain
 *  EventEmitter-based double without touching a real process. */
export interface CliInstallChildProcessLike {
  stdout: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  stderr: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  on(event: "error", listener: (err: NodeJS.ErrnoException) => void): unknown;
  on(event: "close", listener: (code: number | null) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type CliInstallSpawnImpl = (
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["ignore", "pipe", "pipe"] },
) => CliInstallChildProcessLike;

export interface CliInstallerConfig {
  /** Injectable for tests. Defaults to node:child_process's real spawn. */
  spawnImpl?: CliInstallSpawnImpl;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  /** Injectable for tests. Defaults to a real PATH scan (same logic as
   *  health/service-inventory.ts's defaultCommandExists — duplicated here,
   *  not imported, since that function is module-private there and this is
   *  a small, self-contained ~20 lines). */
  commandExists?: (command: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform) => string | null;
}

function defaultSpawn(
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["ignore", "pipe", "pipe"] },
): CliInstallChildProcessLike {
  return spawn(command, args, options) as unknown as CliInstallChildProcessLike;
}

function defaultCommandExists(command: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string | null {
  const candidates: string[] = [];
  if (path.isAbsolute(command) || command.includes("/") || command.includes("\\")) {
    candidates.push(command);
  } else {
    const pathValue = env.PATH || "";
    const extensions = platform === "win32"
      ? String(env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean)
      : [""];
    for (const directory of pathValue.split(path.delimiter).filter(Boolean)) {
      for (const extension of extensions) {
        candidates.push(path.join(directory, `${command}${extension}`));
      }
    }
  }
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    } catch {
      // Ignore inaccessible PATH entries.
    }
  }
  return null;
}

interface SpawnOutcome {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  spawnError: NodeJS.ErrnoException | null;
  timedOut: boolean;
}

function spawnAndCollect(
  command: string,
  args: string[],
  opts: { timeoutMs: number; spawnImpl: CliInstallSpawnImpl; env: NodeJS.ProcessEnv },
): Promise<SpawnOutcome> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let forceKillTimer: ReturnType<typeof setTimeout> | null = null;

    // Same reasoning as cli-runner.ts: stdin is closed. An install command
    // must never sit blocked on interactive input (npm can prompt in rare
    // cases, e.g. a broken registry auth) — this is a headless, unattended
    // spawn, not a session a human can answer.
    const child = opts.spawnImpl(command, args, { env: opts.env, stdio: ["ignore", "pipe", "pipe"] });

    const finish = (outcome: Omit<SpawnOutcome, "timedOut">) => {
      if (settled) return;
      settled = true;
      clearTimeout(killTimer);
      if (forceKillTimer) clearTimeout(forceKillTimer);
      resolve({ ...outcome, timedOut });
    };

    const killTimer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGTERM");
      } catch {
        // Already gone.
      }
      forceKillTimer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {
          // Already gone.
        }
      }, FORCE_KILL_GRACE_MS);
      if (typeof (forceKillTimer as unknown as { unref?: () => void }).unref === "function") {
        (forceKillTimer as unknown as { unref: () => void }).unref();
      }
    }, opts.timeoutMs);
    if (typeof (killTimer as unknown as { unref?: () => void }).unref === "function") {
      (killTimer as unknown as { unref: () => void }).unref();
    }

    child.stdout?.on("data", (chunk) => {
      if (stdout.length < MAX_BUFFERED_OUTPUT_CHARS) stdout += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      if (stderr.length < MAX_BUFFERED_OUTPUT_CHARS) stderr += String(chunk);
    });
    child.on("error", (err) => {
      finish({ exitCode: null, stdout, stderr, spawnError: err as NodeJS.ErrnoException });
    });
    child.on("close", (code) => {
      finish({ exitCode: code, stdout, stderr, spawnError: null });
    });
  });
}

/** Best-effort version scrape from `npm ls -g --depth=0` output, e.g. a line
 *  like `+-- @anthropic-ai/claude-code@2.1.205`. Absent (not fabricated) if
 *  the expected line shape isn't found — npm's own text output isn't a
 *  stable contract to over-trust. */
function parseVersion(pkg: string, lsOutput: string): string | undefined {
  const escaped = pkg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escaped}@([\\w.-]+)`).exec(lsOutput);
  return match ? match[1] : undefined;
}

function classifyNpmFailure(outcome: SpawnOutcome): CliInstallFailureKind {
  const haystack = `${outcome.stderr}\n${outcome.stdout}`.toLowerCase();
  if (haystack.includes("eacces") || haystack.includes("permission denied") || haystack.includes("eperm")) {
    return "permission_denied";
  }
  if (
    haystack.includes("enotfound")
    || haystack.includes("etimedout")
    || haystack.includes("network")
    || haystack.includes("econnreset")
  ) {
    return "network_error";
  }
  return "crash";
}

/** Installs one of the two known CLIs on the real host, headlessly. Never
 *  reads or transmits any credential — install has nothing to do with auth,
 *  it only puts the binary on PATH. */
export async function installCliSubscriptionRuntime(
  params: CliInstallParams,
  config: CliInstallerConfig = {},
): Promise<CliInstallResult> {
  const env = config.env ?? process.env;
  const platform = config.platform ?? process.platform;
  const spawnImpl = config.spawnImpl ?? defaultSpawn;
  const commandExists = config.commandExists ?? defaultCommandExists;
  const timeoutMs = params.timeoutMs || DEFAULT_TIMEOUT_MS;
  const pkg = INSTALL_PACKAGE[params.runtime];

  // Preflight: npm must be on PATH, or there's nothing to run at all — fail
  // fast with a clear, specific reason instead of a raw ENOENT from spawn.
  const npmPath = commandExists("npm", env, platform);
  if (!npmPath) {
    throw new CliInstallError(
      "npm_missing",
      `npm was not found on PATH (checked ${env.PATH ? "PATH" : "an empty PATH"}). Node.js (which bundles npm) must be installed on this computer before ${params.runtime === "claude_code" ? "Claude Code" : "Codex"} can be installed.`,
    );
  }

  // Defense-in-depth for the sandbox / prefix mismatch scenario: even
  // though npm honors NPM_CONFIG_PREFIX via env already, a stray .npmrc on
  // the box otherwise wins over the env-var. Passing --prefix explicitly
  // guarantees the write lands in the writable location the systemd unit
  // grants (see scripts/install-agent-computer.sh's ReadWritePaths).
  // Without it, `npm install -g` on a paired gateway hits EACCES on
  // /usr/lib/node_modules and fails every user's first Install click.
  const args = ["install", "-g", pkg];
  const explicitPrefix = String(env.NPM_CONFIG_PREFIX || "").trim();
  if (explicitPrefix) {
    args.push("--prefix", explicitPrefix);
  }
  const outcome = await spawnAndCollect(npmPath, args, { timeoutMs, spawnImpl, env });

  if (outcome.spawnError) {
    if (outcome.spawnError.code === "ENOENT") {
      throw new CliInstallError("npm_missing", `npm binary at "${npmPath}" could not be executed.`);
    }
    throw new CliInstallError("crash", outcome.spawnError.message || String(outcome.spawnError));
  }
  if (outcome.timedOut) {
    throw new CliInstallError("timeout", `npm install -g ${pkg} did not finish within ${timeoutMs}ms (SIGTERM/SIGKILL sent).`);
  }
  if (outcome.exitCode !== 0) {
    const kind = classifyNpmFailure(outcome);
    const detail = (outcome.stderr || outcome.stdout || `npm exited with code ${outcome.exitCode}`).trim().slice(-800);
    throw new CliInstallError(kind, detail);
  }

  // Confirm the binary actually landed on PATH — npm can exit 0 in some
  // edge configurations (e.g. a prefix mismatch) without the bin actually
  // being linked where this box's own PATH resolves it. Best-effort version
  // read piggybacks on the same confirmation call.
  const binaryName = BINARY_NAME[params.runtime];
  const lsOutcome = await spawnAndCollect(npmPath, ["ls", "-g", "--depth=0"], { timeoutMs: 15_000, spawnImpl, env });
  const landedOnPath = Boolean(commandExists(binaryName, env, platform));
  if (!landedOnPath) {
    throw new CliInstallError(
      "crash",
      `npm install -g ${pkg} exited 0, but "${binaryName}" is still not on PATH afterward. This can happen with a non-default npm global prefix — check "npm config get prefix" against this Gateway's PATH.`,
    );
  }

  return {
    runtime: params.runtime,
    package: pkg,
    installed: true,
    os: platform,
    version: parseVersion(pkg, lsOutcome.stdout),
  };
}
