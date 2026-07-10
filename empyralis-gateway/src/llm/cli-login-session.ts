import { spawn } from "child_process";

// cli.login (BYO-brain onboarding, Build F) — the interactive login session
// primitive. This is deliberately a DIFFERENT execution shape from
// cli-runner.ts's runCliSubscription: that one closes stdin ("ignore") and
// waits for one bounded, non-interactive completion. A real subscription
// login needs the opposite — a held-open process with stdin PIPED, because
// Claude Code's own documented fallback (when it can't reach its localhost
// OAuth callback, the common case for a box managed remotely) has the
// browser show a code that the user must paste back into the terminal at a
// "Paste code here if prompted" prompt. Codex's device-auth flow needs no
// such paste-back (the code is entered on the authorization page instead),
// but both flows are modeled the same way here for one uniform primitive.
//
// SAFETY-CRITICAL: what gets forwarded as output.
// `claude setup-token` prints the long-lived OAuth token to stdout as its
// OWN final success output — that is the one thing the Gateway must never
// transmit, full stop. So this module does NOT forward stdout as a raw
// passthrough. It only ever forwards a line that positively matches one of
// two known-safe shapes: a URL to open, or the literal "paste code" prompt
// text. Everything else — including whatever prints after a successful
// exchange — is buffered locally (bounded, for failure diagnostics only)
// and is NEVER handed to the event publisher. This is the enforcement point
// for "the Gateway never reads or transmits the credential": not a promise
// in a comment, an allowlist a line must match before it ever leaves this
// process.

export type CliLoginRuntime = "claude_code" | "codex";

export type CliLoginFailureKind = "not_installed" | "timeout" | "crash" | "cancelled";

export class CliLoginError extends Error {
  readonly kind: CliLoginFailureKind;

  constructor(kind: CliLoginFailureKind, message: string) {
    super(message);
    this.name = "CliLoginError";
    this.kind = kind;
  }
}

export type CliLoginOutputKind = "url" | "code_prompt";

export interface CliLoginOutputEvent {
  run_id: string;
  runtime: CliLoginRuntime;
  event: "output";
  kind: CliLoginOutputKind;
  text: string;
}

export interface CliLoginDoneEvent {
  run_id: string;
  runtime: CliLoginRuntime;
  event: "done";
  ok: boolean;
  error?: string;
  error_kind?: CliLoginFailureKind;
}

export type CliLoginEvent = CliLoginOutputEvent | CliLoginDoneEvent;

export type CliLoginEventPublisher = (event: CliLoginEvent) => void | Promise<void>;

interface LoginCommandSpec {
  binaryEnvVar: string;
  defaultBinary: string;
  args: string[];
}

const LOGIN_COMMAND: Record<CliLoginRuntime, LoginCommandSpec> = {
  // Prints a one-time OAuth URL, then either completes via localhost
  // callback or (the realistic remote-box case) falls back to printing
  // "Paste code here if prompted:" once the browser shows a code the user
  // copies back. On success, its FINAL stdout is the long-lived token this
  // module must never forward — see the module doc comment above.
  claude_code: { binaryEnvVar: "CLAUDE_CLI_PATH", defaultBinary: "claude", args: ["setup-token"] },
  // Standard OAuth device-authorization grant: prints a URL + a short user
  // code together; the user enters the code on the authorization page
  // itself. Nothing needs to be typed back into this process's stdin for
  // Codex — cli.login.input exists for Claude's case, and is simply unused
  // for a Codex session.
  codex: { binaryEnvVar: "CODEX_CLI_PATH", defaultBinary: "codex", args: ["login", "--device-auth"] },
};

// Matches the product's existing multi-minute poll caps (GatewayPairPanel,
// cloud-vps-setup-panel both cap at 5 minutes) — enough time for a human to
// switch to a browser, sign in, and come back.
const DEFAULT_SESSION_TIMEOUT_MS = 5 * 60_000;
const FORCE_KILL_GRACE_MS = 5_000;
const MAX_BUFFERED_CHARS_FOR_DIAGNOSTICS = 4_000;

const URL_PATTERN = /https?:\/\/[^\s"'<>]+/;
const CODE_PROMPT_PATTERN = /paste.{0,20}code|enter.{0,20}code|device.{0,10}code/i;

/** Minimal structural subset of node:child_process's ChildProcess — same
 *  spirit as cli-runner.ts's CliChildProcessLike, extended with a writable
 *  stdin (piped here, unlike cli-runner.ts's ignored one) so tests can
 *  inject a plain EventEmitter-based double without a real process. */
export interface CliLoginChildProcessLike {
  stdin: { write(chunk: string, callback: (err?: Error | null) => void): unknown };
  stdout: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown };
  stderr: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown };
  on(event: "error", listener: (err: NodeJS.ErrnoException) => void): unknown;
  on(event: "close", listener: (code: number | null) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type CliLoginSpawnImpl = (
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["pipe", "pipe", "pipe"] },
) => CliLoginChildProcessLike;

function defaultSpawn(
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["pipe", "pipe", "pipe"] },
): CliLoginChildProcessLike {
  return spawn(command, args, options) as unknown as CliLoginChildProcessLike;
}

interface ActiveSession {
  runId: string;
  runtime: CliLoginRuntime;
  child: CliLoginChildProcessLike;
  killTimer: ReturnType<typeof setTimeout>;
  forceKillTimer: ReturnType<typeof setTimeout> | null;
  settled: boolean;
  diagnosticBuffer: string;
  sawUrl: boolean;
}

export interface CliLoginSessionConfig {
  /** Injectable for tests. Defaults to node:child_process's real spawn. */
  spawnImpl?: CliLoginSpawnImpl;
  env?: NodeJS.ProcessEnv;
  sessionTimeoutMs?: number;
  /** Injectable for tests. Defaults to a real PATH scan. */
  commandExists?: (command: string, env: NodeJS.ProcessEnv) => string | null;
}

function defaultCommandExists(command: string, env: NodeJS.ProcessEnv): string | null {
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");
  const pathValue = env.PATH || "";
  for (const directory of pathValue.split(path.delimiter).filter(Boolean)) {
    const candidate = path.join(directory, command);
    try {
      if (fs.existsSync(candidate)) return candidate;
    } catch {
      // Ignore inaccessible PATH entries.
    }
  }
  return null;
}

function binaryFor(runtime: CliLoginRuntime, env: NodeJS.ProcessEnv): string {
  const spec = LOGIN_COMMAND[runtime];
  return String(env[spec.binaryEnvVar] || "").trim() || spec.defaultBinary;
}

/** Splits a raw stdout/stderr chunk into lines and classifies each one. Only
 *  ever returns entries for lines that positively match a known-safe shape —
 *  see the module doc comment for why silence is the safe default here, not
 *  a raw passthrough. */
function extractSafeLines(chunk: string): { kind: CliLoginOutputKind; text: string }[] {
  const out: { kind: CliLoginOutputKind; text: string }[] = [];
  for (const rawLine of chunk.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const urlMatch = URL_PATTERN.exec(line);
    if (urlMatch) {
      out.push({ kind: "url", text: urlMatch[0] });
      continue;
    }
    if (CODE_PROMPT_PATTERN.test(line)) {
      out.push({ kind: "code_prompt", text: line.slice(0, 300) });
    }
    // Anything else is deliberately dropped — never forwarded, never
    // returned from this function.
  }
  return out;
}

/** Manages held-open, stdin-piped login sessions, one per run_id. A sibling
 *  to cli-runner.ts's one-shot spawn, not a replacement — generation stays
 *  non-interactive; only login needs a live process across a human-paced,
 *  multi-minute round trip. */
export class CliLoginSessionManager {
  private readonly sessions = new Map<string, ActiveSession>();
  private readonly env: NodeJS.ProcessEnv;
  private readonly spawnImpl: CliLoginSpawnImpl;
  private readonly sessionTimeoutMs: number;
  private readonly commandExists: (command: string, env: NodeJS.ProcessEnv) => string | null;
  private publisher: CliLoginEventPublisher | null = null;

  constructor(config: CliLoginSessionConfig = {}) {
    this.env = config.env ?? process.env;
    this.spawnImpl = config.spawnImpl ?? defaultSpawn;
    this.sessionTimeoutMs = config.sessionTimeoutMs || DEFAULT_SESSION_TIMEOUT_MS;
    this.commandExists = config.commandExists ?? defaultCommandExists;
  }

  /** Set once, after the ws-client (which owns the actual WSS connection)
   *  exists — see index.ts. Sessions started before a publisher is attached
   *  simply have their output buffered in the diagnostic buffer only, same
   *  as any other unmatched line; nothing is lost in a way that could leak,
   *  it just isn't relayed live. */
  setEventPublisher(publisher: CliLoginEventPublisher): void {
    this.publisher = publisher;
  }

  private async emit(event: CliLoginEvent): Promise<void> {
    if (!this.publisher) return;
    try {
      await this.publisher(event);
    } catch {
      // A relay failure must never crash the login session itself — the
      // process keeps running, the next matched line (or the final done
      // event) gets another chance to reach the control plane.
    }
  }

  hasSession(runId: string): boolean {
    return this.sessions.has(runId);
  }

  /** Starts a login session and returns as soon as the process is spawned —
   *  it does NOT wait for login to complete. Output and the eventual result
   *  arrive later via the event publisher. */
  async start(params: { runId: string; runtime: CliLoginRuntime }): Promise<{ run_id: string; status: "started" }> {
    if (this.sessions.has(params.runId)) {
      throw new CliLoginError("crash", `A login session for run_id "${params.runId}" is already active.`);
    }
    const binary = binaryFor(params.runtime, this.env);
    const resolved = this.commandExists(binary, this.env);
    if (!resolved) {
      throw new CliLoginError(
        "not_installed",
        `"${binary}" was not found on PATH. Install ${params.runtime === "claude_code" ? "Claude Code" : "Codex"} first.`,
      );
    }
    const spec = LOGIN_COMMAND[params.runtime];
    const child = this.spawnImpl(resolved, spec.args, {
      env: this.env,
      // Piped, not ignored: this is the one property that makes a login
      // session different from every other spawn in this Gateway — Claude's
      // paste-back case needs to write to this process after it starts.
      stdio: ["pipe", "pipe", "pipe"],
    });

    const session: ActiveSession = {
      runId: params.runId,
      runtime: params.runtime,
      child,
      settled: false,
      diagnosticBuffer: "",
      sawUrl: false,
      killTimer: setTimeout(() => this.onTimeout(params.runId), this.sessionTimeoutMs),
      forceKillTimer: null,
    };
    if (typeof (session.killTimer as unknown as { unref?: () => void }).unref === "function") {
      (session.killTimer as unknown as { unref: () => void }).unref();
    }
    this.sessions.set(params.runId, session);

    const onData = (chunk: Buffer | string) => {
      const text = String(chunk);
      session.diagnosticBuffer = (session.diagnosticBuffer + text).slice(-MAX_BUFFERED_CHARS_FOR_DIAGNOSTICS);
      for (const line of extractSafeLines(text)) {
        if (line.kind === "url") session.sawUrl = true;
        void this.emit({ run_id: params.runId, runtime: params.runtime, event: "output", ...line });
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("error", (err) => this.onExit(params.runId, null, err as NodeJS.ErrnoException));
    child.on("close", (code) => this.onExit(params.runId, code, null));

    return { run_id: params.runId, status: "started" };
  }

  /** Relays the user's pasted-back code into the session's stdin — Claude
   *  Code's fallback flow only; a no-op-but-harmless call for a Codex
   *  session (nothing reads its stdin, so a stray write there is simply
   *  ignored by that process). */
  async input(params: { runId: string; code: string }): Promise<{ ok: boolean }> {
    const session = this.sessions.get(params.runId);
    if (!session) {
      throw new CliLoginError("crash", `No active login session for run_id "${params.runId}".`);
    }
    const code = String(params.code || "").trim();
    if (!code) {
      throw new CliLoginError("crash", "code is required.");
    }
    await new Promise<void>((resolve, reject) => {
      session.child.stdin.write(`${code}\n`, (err) => (err ? reject(err) : resolve()));
    }).catch((err) => {
      throw new CliLoginError("crash", `Failed to write to login session stdin: ${err instanceof Error ? err.message : String(err)}`);
    });
    return { ok: true };
  }

  /** Kills an in-flight session on request — wired through tool.interrupt
   *  (see supervisor/capability-router.ts), the same mechanism every other
   *  long-lived executor uses. */
  async cancel(runId: string): Promise<{ ok: boolean }> {
    const session = this.sessions.get(runId);
    if (!session) {
      return { ok: false };
    }
    this.finalize(session, { ok: false, error: "Cancelled by request.", error_kind: "cancelled" });
    return { ok: true };
  }

  private onTimeout(runId: string): void {
    const session = this.sessions.get(runId);
    if (!session || session.settled) return;
    this.finalize(session, {
      ok: false,
      error: `Login timed out after ${this.sessionTimeoutMs}ms with no completed sign-in.`,
      error_kind: "timeout",
    });
  }

  private onExit(runId: string, code: number | null, spawnError: NodeJS.ErrnoException | null): void {
    const session = this.sessions.get(runId);
    if (!session || session.settled) return;
    if (spawnError) {
      const kind: CliLoginFailureKind = spawnError.code === "ENOENT" ? "not_installed" : "crash";
      this.finalize(session, { ok: false, error: spawnError.message || String(spawnError), error_kind: kind });
      return;
    }
    if (code === 0) {
      this.finalize(session, { ok: true });
      return;
    }
    // Deliberately not including the diagnostic buffer's stdout content in
    // this failure message — see the module doc comment. Exit code and
    // signal are the only detail surfaced; that buffer exists for local
    // Gateway-log debugging only; it is never part of any event payload.
    this.finalize(session, { ok: false, error: `Login process exited with code ${code ?? "null"}.`, error_kind: "crash" });
  }

  private finalize(session: ActiveSession, outcome: { ok: boolean; error?: string; error_kind?: CliLoginFailureKind }): void {
    if (session.settled) return;
    session.settled = true;
    clearTimeout(session.killTimer);
    if (session.forceKillTimer) clearTimeout(session.forceKillTimer);
    if (!outcome.ok) {
      try {
        session.child.kill("SIGTERM");
        session.forceKillTimer = setTimeout(() => {
          try {
            session.child.kill("SIGKILL");
          } catch {
            // Already gone.
          }
        }, FORCE_KILL_GRACE_MS);
        if (typeof (session.forceKillTimer as unknown as { unref?: () => void }).unref === "function") {
          (session.forceKillTimer as unknown as { unref: () => void }).unref();
        }
      } catch {
        // Already gone.
      }
    }
    this.sessions.delete(session.runId);
    void this.emit({
      run_id: session.runId,
      runtime: session.runtime,
      event: "done",
      ok: outcome.ok,
      error: outcome.error,
      error_kind: outcome.error_kind,
    });
  }
}
