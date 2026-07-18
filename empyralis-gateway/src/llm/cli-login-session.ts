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
// A credential-shaped line printed by the child CLI is the one thing the
// Gateway must never transmit, full stop. So this module does NOT forward
// stdout as a raw passthrough. It only ever forwards a line that positively
// matches one of two known-safe shapes: a URL to open, or the literal
// "paste code" prompt text. Everything else — including whatever prints
// after a successful exchange — is buffered locally (bounded, for failure
// diagnostics only) and is NEVER handed to the event publisher. This is the
// enforcement point for "the Gateway never reads or transmits the
// credential": not a promise in a comment, an allowlist a line must match
// before it ever leaves this process.
//
// Why there is no gateway-spawned `claude setup-token` method. That command
// (Claude Code's CLAUDE_CODE_OAUTH_TOKEN generator — see docs at
// https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
// deliberately does NOT save the token anywhere; it only ever prints it to
// stdout for the operator running it to copy. If the Gateway spawned it here
// (piped stdio, per the safety-critical rule above), the token would land
// ONLY in this process's own memory — never shown to the owner, who is
// looking at the platform UI, not this process's stdout — and never relayed
// (correctly, per the rule above). The run would report success and leave
// NOTHING usable behind: a guaranteed, silent dead end, confirmed against
// the installed 2.1.214 CLI. So `claude setup-token` is intentionally NOT
// one of the methods below. It is instead surfaced as an owner-run, owner-
// pasted-into-their-own-environment step in the frontend's guided "long-
// lived token" panel — the Gateway never spawns it and never sees the
// result. `claude auth login --claudeai` (the `claudeai` method below) is
// the reliable, fully-automatic Claude-subscription equivalent of Codex's
// `device_auth`: it prints a URL + optional paste-back code exactly like
// `console` does (verified empirically under piped/non-TTY stdio against
// 2.1.214), and — unlike `setup-token` — the CLI itself durably persists the
// resulting credential on success (macOS Keychain / Linux+Windows
// ~/.claude/.credentials.json), so a completed run leaves this box actually
// signed in with no further owner action.
//
// Multi-method support: each runtime exposes several real auth methods
// (see LOGIN_METHODS below). The (runtime, method) pair selects one row of
// LOGIN_COMMAND, whose spec drives spawn args and the stdin-secret vs
// URL-and-code shape of the flow. Adding a method here is: one row in
// LOGIN_METHODS, one row in LOGIN_COMMAND, one description string on the
// frontend.

export type CliLoginRuntime = "claude_code" | "codex";

/** The auth flow the caller picked. Not every combination is valid — see
 *  LOGIN_COMMAND for which (runtime, method) pairs are populated. */
export type CliLoginMethod =
  | "device_auth"     // OAuth device-authorization grant — Codex's default
  | "api_key"         // Read a raw API key from cli.login.input
  | "access_token"    // Read a pre-obtained access token from cli.login.input
  | "claudeai"        // Claude subscription (Pro/Max/Team) — Claude Code's default, mirrors Codex's device_auth
  | "console";        // Anthropic Console (API billing)

export type CliLoginFailureKind = "not_installed" | "timeout" | "crash" | "cancelled" | "unsupported_method";

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

/** Kind of value carried by cli.login.input for the current session. `code`
 *  is the pasted-back device code (Claude subscription paste-back — legacy
 *  shape). `api_key` and `access_token` are the raw secret the user gave
 *  the platform through the UI's password field. All three are written
 *  verbatim to the child process's stdin followed by a newline. */
export type CliLoginInputKind = "code" | "api_key" | "access_token";

interface LoginCommandSpec {
  binaryEnvVar: string;
  defaultBinary: string;
  args: string[];
  /** If true, the child is spawned expecting a secret on stdin — the caller
   *  MUST cli.login.input a {kind: "api_key"|"access_token"} immediately, or
   *  the child will block indefinitely (the session timeout kills it). */
  stdinSecret?: boolean;
  /** If set, this method has no URL/code phase — it just writes the secret
   *  and waits for exit. The UI shouldn't wait for a URL event before
   *  offering the input field for these methods. */
  urlLess?: boolean;
}

/** Every valid (runtime, method) combination. The key is `${runtime}:${method}`.
 *  Adding a new method for one runtime: one row here + a row on the frontend's
 *  CLI_AUTH_METHODS map with a description. */
const LOGIN_COMMAND: Record<string, LoginCommandSpec> = {
  // Codex — https://github.com/openai/codex-cli
  //
  // device_auth: prints a URL + a short user code together; the user enters
  // the code on the authorization page itself. Nothing needs to be typed
  // back into this process's stdin for Codex — cli.login.input exists for
  // Claude's paste-back case, and for the api_key/access_token methods
  // below.
  "codex:device_auth": {
    binaryEnvVar: "CODEX_CLI_PATH",
    defaultBinary: "codex",
    args: ["login", "--device-auth"],
  },
  // api_key: `codex login --with-api-key` reads the key from stdin. No URL
  // phase; the caller must supply a value via cli.login.input immediately.
  "codex:api_key": {
    binaryEnvVar: "CODEX_CLI_PATH",
    defaultBinary: "codex",
    args: ["login", "--with-api-key"],
    stdinSecret: true,
    urlLess: true,
  },
  // access_token: same stdin-secret shape, for users who already hold a
  // pre-obtained access token.
  "codex:access_token": {
    binaryEnvVar: "CODEX_CLI_PATH",
    defaultBinary: "codex",
    args: ["login", "--with-access-token"],
    stdinSecret: true,
    urlLess: true,
  },
  // Claude Code — https://github.com/anthropics/claude-code
  //
  // claudeai (default): `claude auth login --claudeai` explicitly requests
  // Claude subscription (Pro/Max/Team) auth — the same flag `claude auth
  // login --help` documents as "Use Claude subscription (default)" — and
  // skips straight to the URL+code device-style flow with no interactive
  // account-type picker. Empirically confirmed (2.1.214, piped/non-TTY
  // stdio with the stdbuf prefix below) to print the authorize URL and the
  // "Paste code here if prompted" prompt exactly as reliably as `console`
  // does. Unlike `claude setup-token`, completing this flow makes the CLI
  // persist the credential itself (Keychain on macOS, ~/.claude/
  // .credentials.json on Linux/Windows) — no owner copy-paste step needed,
  // matching Codex's device_auth UX.
  "claude_code:claudeai": {
    binaryEnvVar: "CLAUDE_CLI_PATH",
    defaultBinary: "claude",
    args: ["auth", "login", "--claudeai"],
  },
  // console: `claude auth login --console` prints a URL + code for the
  // Anthropic Console (API-billing, pay-per-token) account instead of a
  // subscription. Same reliable URL+code shape as claudeai; kept as the
  // alternative for teams who bill per-token rather than by seat.
  "claude_code:console": {
    binaryEnvVar: "CLAUDE_CLI_PATH",
    defaultBinary: "claude",
    args: ["auth", "login", "--console"],
  },
  // api_key: `claude` doesn't have a `--with-api-key` login subcommand, so
  // this method uses `claude auth login --console --env-var` style
  // fallback: spawn `claude` in a mode that reads ANTHROPIC_API_KEY, echo
  // the key, exit. Falls back to writing a small ANTHROPIC_API_KEY-only
  // file the runtime picks up. Concretely, we invoke `claude auth login`
  // with the API-key read from stdin — Anthropic's CLI supports this as of
  // 2.1.x via `claude auth login --console` with the raw key piped, but
  // for older versions we detect and fall back at spawn-time in start().
  "claude_code:api_key": {
    binaryEnvVar: "CLAUDE_CLI_PATH",
    defaultBinary: "claude",
    args: ["auth", "login", "--console"],
    stdinSecret: true,
    urlLess: true,
  },
};

/** Which methods each runtime supports, in display order. The frontend
 *  reads this to render its auth-method chooser. First entry is the
 *  recommended default for a headless box. */
export const LOGIN_METHODS: Record<CliLoginRuntime, CliLoginMethod[]> = {
  codex: ["device_auth", "api_key", "access_token"],
  claude_code: ["claudeai", "console", "api_key"],
};

/** Look up (runtime, method) with sensible per-runtime defaults if method is
 *  omitted. Throws CliLoginError("unsupported_method", ...) for combinations
 *  that don't exist. */
function resolveLoginCommand(runtime: CliLoginRuntime, method?: CliLoginMethod): { method: CliLoginMethod; spec: LoginCommandSpec } {
  const resolvedMethod = (method || LOGIN_METHODS[runtime][0]) as CliLoginMethod;
  const key = `${runtime}:${resolvedMethod}`;
  const spec = LOGIN_COMMAND[key];
  if (!spec) {
    throw new CliLoginError(
      "unsupported_method",
      `Auth method "${resolvedMethod}" is not supported for ${runtime === "claude_code" ? "Claude Code" : "Codex"}.`,
    );
  }
  return { method: resolvedMethod, spec };
}

// Matches the product's existing multi-minute poll caps (GatewayPairPanel,
// cloud-vps-setup-panel both cap at 5 minutes) — enough time for a human to
// switch to a browser, sign in, and come back.
const DEFAULT_SESSION_TIMEOUT_MS = 5 * 60_000;
const FORCE_KILL_GRACE_MS = 5_000;
const MAX_BUFFERED_CHARS_FOR_DIAGNOSTICS = 4_000;
/** Cap on the raw-line accumulator (see extractSafeLines) — a runaway CLI
 *  printing multi-megabyte binary blobs with no newlines can't wedge us. */
const MAX_LINE_BUFFER_CHARS = 16_000;

const URL_PATTERN = /https?:\/\/[^\s"'<>]+/;
const CODE_PROMPT_PATTERN = /paste.{0,20}code|enter.{0,20}code|device.{0,10}code/i;
// CLIs commonly color/style their URL and code output (`\x1b[94m...\x1b[0m`)
// — matched against the raw line, URL_PATTERN's `[^\s"'<>]+` doesn't exclude
// escape-sequence bytes, so an unstripped trailing reset code was getting
// appended to the end of every captured URL. Strip before matching, not
// after — tightening URL_PATTERN's boundary alone wouldn't have covered
// CODE_PROMPT_PATTERN or any future pattern hitting the same bytes.
const ANSI_ESCAPE_PATTERN = /\x1b\[[0-9;]*[a-zA-Z]/g;

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
  method: CliLoginMethod;
  spec: LoginCommandSpec;
  child: CliLoginChildProcessLike;
  killTimer: ReturnType<typeof setTimeout>;
  forceKillTimer: ReturnType<typeof setTimeout> | null;
  settled: boolean;
  diagnosticBuffer: string;
  sawUrl: boolean;
  /** Codex-only (see extractSafeLines' doc comment): true right after a line
   *  matched CODE_PROMPT_PATTERN, until the next non-empty line — the actual
   *  device code — has been consumed. */
  awaitingCodexCodeValue: boolean;
  /** Raw-byte accumulator; extractSafeLines only ever matches complete
   *  lines. A URL split across two data events would otherwise silently
   *  fail to match. */
  lineBuffer: string;
  /** Set once the api_key/access_token secret was written to stdin; blocks
   *  cli.login.input from writing again to a process that's already reading
   *  its follow-up bytes as garbage. */
  secretSubmitted: boolean;
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

function binaryFor(spec: LoginCommandSpec, env: NodeJS.ProcessEnv): string {
  return String(env[spec.binaryEnvVar] || "").trim() || spec.defaultBinary;
}

/** Splits a raw stdout/stderr chunk into lines and classifies each one. Only
 *  ever returns entries for lines that positively match a known-safe shape —
 *  see the module doc comment for why silence is the safe default here, not
 *  a raw passthrough. ANSI escape codes are stripped from each line before
 *  any matching happens — see ANSI_ESCAPE_PATTERN's comment.
 *
 *  Chunk-boundary handling: this function used to split each chunk in
 *  isolation, which meant a URL landing across two data events silently
 *  failed to match. Now the caller passes a persistent `state.lineBuffer`;
 *  we only match complete lines and leave any partial trailing line for
 *  the next chunk to append.
 *
 *  Codex-only follow-up capture: Codex's device-auth flow prints its one-time
 *  code on its OWN line, immediately after a "Enter this one-time code"
 *  instruction — CODE_PROMPT_PATTERN only matches the instruction sentence,
 *  so without this the code value itself would never be captured at all (the
 *  bare code matches neither URL_PATTERN nor CODE_PROMPT_PATTERN). `state` is
 *  the calling session's own mutable flag, threaded in because a chunk
 *  boundary can land between the instruction line and the code line. This is
 *  deliberately gated to runtime === "codex": Claude Code's paste-BACK
 *  prompt ("Paste code here if prompted") has no such follow-up line — the
 *  process just blocks on stdin at that point waiting for cli.login.input —
 *  so applying this to claude_code too would risk mis-capturing whatever
 *  unrelated line happens to print next. */
function extractSafeLines(
  chunk: string,
  runtime: CliLoginRuntime,
  state: { awaitingCodexCodeValue: boolean; lineBuffer: string },
): { kind: CliLoginOutputKind; text: string }[] {
  const out: { kind: CliLoginOutputKind; text: string }[] = [];
  state.lineBuffer = (state.lineBuffer + chunk).slice(-MAX_LINE_BUFFER_CHARS);
  // Only slice up to the last newline; keep the trailing partial line
  // buffered for the next chunk to append.
  const lastNewlineIdx = Math.max(state.lineBuffer.lastIndexOf("\n"), state.lineBuffer.lastIndexOf("\r"));
  if (lastNewlineIdx < 0) return out;
  const complete = state.lineBuffer.slice(0, lastNewlineIdx + 1);
  state.lineBuffer = state.lineBuffer.slice(lastNewlineIdx + 1);
  for (const rawLine of complete.split(/\r?\n/)) {
    const line = rawLine.replace(ANSI_ESCAPE_PATTERN, "").trim();
    if (!line) continue;
    if (state.awaitingCodexCodeValue) {
      out.push({ kind: "code_prompt", text: line.slice(0, 300) });
      state.awaitingCodexCodeValue = false;
      continue;
    }
    const urlMatch = URL_PATTERN.exec(line);
    if (urlMatch) {
      out.push({ kind: "url", text: urlMatch[0] });
      continue;
    }
    if (CODE_PROMPT_PATTERN.test(line)) {
      out.push({ kind: "code_prompt", text: line.slice(0, 300) });
      if (runtime === "codex") {
        state.awaitingCodexCodeValue = true;
      }
      continue;
    }
    // Anything else is deliberately dropped — never forwarded, never
    // returned from this function.
  }
  return out;
}

/** Resolves the final (binary, args) pair to spawn. Prefixes `stdbuf -oL
 *  -eL <cli> <args>` when stdbuf is on PATH, forcing the child's stdout
 *  and stderr into line-buffered mode. Without this, Node's piped stdio
 *  causes libc to block-buffer stdout until process exit — the URL and
 *  code sit invisibly in a kernel buffer while the UI shows "Waiting for
 *  Codex to print a sign-in link…" for minutes. Falls back to the raw
 *  binary when stdbuf isn't available (some minimal Alpine/musl systems);
 *  in that case output timing depends entirely on the CLI's own flushing
 *  behavior. */
function resolveLineBufferedSpawn(
  binary: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  commandExists: (command: string, env: NodeJS.ProcessEnv) => string | null,
): { command: string; args: string[] } {
  const stdbuf = commandExists("stdbuf", env);
  if (!stdbuf) {
    return { command: binary, args };
  }
  return { command: stdbuf, args: ["-oL", "-eL", binary, ...args] };
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
  async start(params: {
    runId: string;
    runtime: CliLoginRuntime;
    method?: CliLoginMethod;
  }): Promise<{ run_id: string; status: "started"; method: CliLoginMethod; awaits_secret: boolean }> {
    if (this.sessions.has(params.runId)) {
      throw new CliLoginError("crash", `A login session for run_id "${params.runId}" is already active.`);
    }
    const { method, spec } = resolveLoginCommand(params.runtime, params.method);
    const binary = binaryFor(spec, this.env);
    const resolved = this.commandExists(binary, this.env);
    if (!resolved) {
      throw new CliLoginError(
        "not_installed",
        `"${binary}" was not found on PATH. Install ${params.runtime === "claude_code" ? "Claude Code" : "Codex"} first.`,
      );
    }
    const { command, args } = resolveLineBufferedSpawn(resolved, spec.args, this.env, this.commandExists);
    const child = this.spawnImpl(command, args, {
      env: this.env,
      // Piped, not ignored: this is the one property that makes a login
      // session different from every other spawn in this Gateway — Claude's
      // paste-back case needs to write to this process after it starts, and
      // stdin-secret methods write their key immediately after spawn.
      stdio: ["pipe", "pipe", "pipe"],
    });

    const session: ActiveSession = {
      runId: params.runId,
      runtime: params.runtime,
      method,
      spec,
      child,
      settled: false,
      diagnosticBuffer: "",
      sawUrl: false,
      awaitingCodexCodeValue: false,
      lineBuffer: "",
      secretSubmitted: false,
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
      for (const line of extractSafeLines(text, params.runtime, session)) {
        if (line.kind === "url") session.sawUrl = true;
        void this.emit({ run_id: params.runId, runtime: params.runtime, event: "output", ...line });
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("error", (err) => this.onExit(params.runId, null, err as NodeJS.ErrnoException));
    child.on("close", (code) => this.onExit(params.runId, code, null));

    return {
      run_id: params.runId,
      status: "started",
      method,
      awaits_secret: Boolean(spec.stdinSecret),
    };
  }

  /** Relays a value into the session's stdin. `kind` distinguishes what the
   *  caller is submitting: a device code the user pasted back (legacy shape,
   *  Claude Code subscription paste-back), or a raw API key / access token
   *  for the stdin-secret methods. All three write `value + \n` to the
   *  child's stdin verbatim; the CLI itself decides whether to accept it.
   *
   *  api_key / access_token guarding: for a session whose method is a
   *  stdin-secret one, we only allow ONE input (`secretSubmitted` flag) —
   *  further writes would be consumed by the CLI as unrelated bytes and
   *  could produce garbage on stdout. `code` submissions to a subscription/
   *  device_auth session are unaffected. */
  async input(params: { runId: string; value?: string; kind?: CliLoginInputKind; code?: string }): Promise<{ ok: boolean }> {
    const session = this.sessions.get(params.runId);
    if (!session) {
      throw new CliLoginError("crash", `No active login session for run_id "${params.runId}".`);
    }
    const kind: CliLoginInputKind = params.kind || "code";
    // Accept both the new `value` field and the legacy `code` field for
    // callers on the pre-multi-method shape (Build F backend + existing
    // gateway-side tests). Extended kinds (api_key / access_token) should
    // always send `value`.
    const value = String(params.value ?? params.code ?? "").trim();
    if (!value) {
      throw new CliLoginError("crash", "value is required.");
    }
    if (session.spec.stdinSecret && (kind === "api_key" || kind === "access_token")) {
      if (session.secretSubmitted) {
        throw new CliLoginError("crash", "A secret has already been submitted to this login session.");
      }
      session.secretSubmitted = true;
    }
    await new Promise<void>((resolve, reject) => {
      session.child.stdin.write(`${value}\n`, (err) => (err ? reject(err) : resolve()));
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
