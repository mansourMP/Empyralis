import { spawn } from "child_process";

// Ported from server_modules/docker_execution_sandbox.py's Hermes-derived
// hardened container posture (Hermes-agent, MIT, Copyright (c) 2025 Nous
// Research — see THIRD_PARTY_LICENSES). Same flags, same defaults. The
// Python original also routes the command build/launch decision through the
// Rust runtime kernel (rust_runtime_kernel_client) as its hard execution
// boundary — that kernel binary is not deployed on arbitrary user Gateways,
// so it is deliberately NOT called here. On this path the command/path
// policy (see command-policy.ts) plus the container boundary itself ARE the
// enforcement layer.
//
// Deviation from the Python contract, noted deliberately: docker_sandbox_
// command()/run_docker_worker() pipe a JSON payload via stdin to a custom
// worker image (server_modules.hosted_secure_worker) that executes a whole
// agent turn and writes a JSON result to an output file in the mounted
// workspace. This Gateway executor runs exactly ONE capability call per
// container against a stock base image (no custom worker baked in) — the
// command is passed directly as container argv, and stdout/stderr/exit code
// are captured straight from the spawned `docker` process via Node's
// child_process API. That is simpler and more robust than hand-rolling JSON
// construction inside a POSIX-shell wrapper (arbitrary command output can
// contain quotes/newlines/binary bytes that are genuinely unsafe to escape
// by hand in shell), while preserving the real substance of the contract:
// one ephemeral, hardened, `--rm` container per call, workspace-scoped
// volume mount, structured JSON result handed back to the caller.

export const DOCKER_WORKSPACE_PATH = "/workspace";
export const DEFAULT_SANDBOX_IMAGE = "debian:bookworm-slim";

const _HERMES_BASE_SECURITY_ARGS: readonly string[] = [
  "--cap-drop",
  "ALL",
  "--cap-add",
  "DAC_OVERRIDE",
  "--cap-add",
  "CHOWN",
  "--cap-add",
  "FOWNER",
  "--security-opt",
  "no-new-privileges",
  "--pids-limit",
  "256",
  "--tmpfs",
  "/tmp:rw,nosuid,size=512m",
  "--tmpfs",
  "/var/tmp:rw,noexec,nosuid,size=256m",
  "--tmpfs",
  "/run:rw,noexec,nosuid,size=64m",
];
const _HERMES_PRIVDROP_CAP_ARGS: readonly string[] = ["--cap-add", "SETUID", "--cap-add", "SETGID"];

export interface HardenedRunFlagsOptions {
  networkEnabled?: boolean;
  runAsUid?: number;
  runAsGid?: number;
  memoryMb?: number;
  cpus?: number;
}

/**
 * The full Hermes-derived flag set for a hardened `docker run`. Network is
 * `none` unless networkEnabled opts into egress; memory is hard-capped with
 * swap pinned to the same size (no swap escape); a non-root --user and
 * --init (zombie reaping) are added when available.
 */
export function hardenedRunFlags(options: HardenedRunFlagsOptions = {}): string[] {
  const hasUser = options.runAsUid !== undefined && options.runAsGid !== undefined;
  const memoryMb = Math.max(32, Math.floor(options.memoryMb ?? 512));
  const cpus = Math.max(0.25, options.cpus ?? 1.0);
  const flags: string[] = ["--init"];
  flags.push("--network", options.networkEnabled ? "bridge" : "none");
  if (hasUser) {
    flags.push("--user", `${options.runAsUid}:${options.runAsGid}`);
  }
  flags.push("--memory", `${memoryMb}m`, "--memory-swap", `${memoryMb}m`);
  flags.push("--cpus", cpus.toFixed(2));
  flags.push(..._HERMES_BASE_SECURITY_ARGS);
  if (!hasUser) {
    // Extra caps only needed when the container starts as root and an
    // entrypoint must drop privileges. Skipped when --user is set, since the
    // container already starts unprivileged and never switches.
    flags.push(..._HERMES_PRIVDROP_CAP_ARGS);
  }
  return flags;
}

export interface BuildDockerRunArgsOptions {
  image: string;
  workspaceHostPath: string;
  innerArgs: string[];
  networkEnabled?: boolean;
  runAsUid?: number;
  runAsGid?: number;
  memoryMb?: number;
  cpus?: number;
  readOnly?: boolean;
  containerName?: string;
}

/** Self-contained hardened `docker run` argv (mirrors build_hardened_docker_command). */
export function buildDockerRunArgs(options: BuildDockerRunArgsOptions): string[] {
  // `-i` is required for spawnDockerRun's `stdin` option to reach the
  // container at all. Without it, `docker run` closes the container's
  // stdin immediately regardless of what the parent Node process writes to
  // the `docker` CLI's own stdin pipe — verified directly against the
  // daemon: `echo x | docker run --rm alpine sh -c 'cat > f; wc -c f'`
  // writes a 0-byte file without `-i`, 2 bytes with it. That silently broke
  // filesystem.read_write's sandbox WRITE/APPEND mode (runtime.ts's
  // filesystemInnerArgs uses `cat > "$1"` / `cat >> "$1"`, both stdin-fed):
  // the call reported success with the real exit code and no stderr, while
  // writing an empty file — an "empty string is not a decision" failure,
  // undetected because shell.execute (this function's only OTHER caller)
  // never uses stdin, so no real-Docker test exercised this path. Harmless
  // for shell.execute and filesystem READ: spawnDockerRun always calls
  // `child.stdin.end()`, so an unused, always-open stdin just gets EOF.
  const args: string[] = ["run", "--rm", "-i"];
  if (options.readOnly ?? true) {
    args.push("--read-only");
  }
  args.push(
    ...hardenedRunFlags({
      networkEnabled: options.networkEnabled,
      runAsUid: options.runAsUid,
      runAsGid: options.runAsGid,
      memoryMb: options.memoryMb,
      cpus: options.cpus,
    }),
  );
  if (options.containerName) {
    args.push("--name", options.containerName);
  }
  args.push("-v", `${options.workspaceHostPath}:${DOCKER_WORKSPACE_PATH}:rw`);
  args.push("-w", DOCKER_WORKSPACE_PATH);
  args.push(options.image);
  args.push(...options.innerArgs);
  return args;
}

export interface DockerRunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/**
 * Spawns `docker` with the given argv via child_process.spawn — matching
 * docker_execution_sandbox.py's approach of shelling out to the docker CLI
 * via subprocess rather than a Docker SDK. No new dependency to vet.
 */
export function spawnDockerRun(
  args: string[],
  options: {
    timeoutMs: number;
    stdin?: string;
    containerNameForTimeoutKill?: string;
    /** Externally triggered early-stop (e.g. a batch's own per-command
     *  watchdog deciding ONE command in a shared container overran its
     *  budget, well before the outer `timeoutMs` backstop would fire).
     *  Runs the identical kill sequence as a natural timeout. */
    signal?: AbortSignal;
  } = { timeoutMs: 30_000 },
): Promise<DockerRunResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;

    // SIGKILLing the LOCAL `docker` CLI process is not the same as
    // stopping the REMOTE container the daemon is running — the CLI is a
    // thin RPC frontend, and killing it does not propagate to the
    // container (SIGKILL can't be caught to relay, and `docker run`
    // without `-d` does not guarantee the daemon tears the container down
    // just because its client disconnected). Left alone, a batch
    // container that outlives its own deadline keeps running (and, for a
    // shared batch container, keeps holding whatever command was mid-flight)
    // until something else notices and cleans it up — nothing does today.
    // Ask the daemon directly to stop the one container we know the name
    // of. Best-effort and fire-and-forget: a hung `docker kill` must never
    // delay settling this promise (the caller's deadline already fired),
    // and if this box's Docker is wedged enough that `docker kill` also
    // hangs, there is nothing more this call can do from here anyway —
    // `killer.unref()` keeps it from holding the event loop open either way.
    const killNow = (): void => {
      timedOut = true;
      child.kill("SIGKILL");
      if (options.containerNameForTimeoutKill) {
        try {
          const killer = spawn("docker", ["kill", options.containerNameForTimeoutKill], { stdio: "ignore" });
          killer.unref?.();
          killer.on("error", () => {
            // Nothing to do — this is best-effort insurance on top of --rm,
            // not the primary cleanup mechanism.
          });
        } catch {
          // Same.
        }
      }
    };

    const timer = setTimeout(killNow, options.timeoutMs);
    timer.unref?.();

    const onAbort = (): void => killNow();
    if (options.signal) {
      if (options.signal.aborted) {
        killNow();
      } else {
        options.signal.addEventListener("abort", onAbort, { once: true });
      }
    }

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    if (options.stdin !== undefined) {
      child.stdin.write(options.stdin, "utf8");
    }
    child.stdin.end();
    const cleanup = (): void => {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onAbort);
    };
    child.on("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve({ exitCode: code, stdout, stderr, timedOut });
    });
  });
}
