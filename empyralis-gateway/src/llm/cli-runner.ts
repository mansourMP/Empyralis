import { spawn } from "child_process";

// G3 (cli_subscription Phase 3): spawns the box's OWN Claude Code / Codex CLI
// non-interactively and returns one completion. This is the sibling of
// generateViaOllama in runtime.ts — same llm.generate capability, same
// {text, usage} contract — but the "model" is the owner's own subscription,
// running headlessly on THEIR box under THEIR login. The Gateway never reads
// or transmits a credential: the CLI reads its own auth from the environment
// it is spawned in (~/.claude/, ~/.codex/), exactly like it would for an
// interactive user on that machine.
//
// child_process.spawn with an argv ARRAY only — never exec()/a shell string.
// The prompt is arbitrary agent/user-generated text; a shell string would be
// an injection vector.
//
// Reliability (G-reliability-3): a real CLI binary crashes, rate-limits,
// gets overloaded, and expires auth just like any other LLM backend — see
// docs/design/reliability-audit-3-cli-subscriptions.md for the full audit
// this module now closes. The pattern (error taxonomy from exit code +
// stderr, bounded retry, bounded session-expired recovery, a no-output
// watchdog distinct from the overall timeout) mirrors OpenClaw's
// ClaudeLiveSession / embedded-agent-helpers (/Users/mansur/openclaw/src/
// agents/cli-runner/claude-live-session.ts, embedded-agent-helpers/errors.ts,
// failover-matches.ts — read-only reference, never imported), adapted to
// this module's single-shot-per-turn architecture: OpenClaw reuses a
// long-lived stdio session and can retry "with history" against a fresh
// process; this module has no session/resume concept at all (Phase 3 never
// implemented it — see runtime.ts's buildCliPrompt), so every retry here is
// simply "spawn the CLI fresh again," which is also what makes it a valid,
// non-interactive path to observe a credential the CLI silently refreshed on
// its own between attempts.

export type CliSubscriptionRuntime = "claude_code" | "codex";

export interface CliUsage {
  input_tokens: number;
  output_tokens: number;
}

export interface CliRunResult {
  text: string;
  usage: CliUsage;
}

export type CliFailureKind = "not_installed" | "not_authenticated" | "timeout" | "crash";

/** A finer-grained classification of WHY a CLI failure happened, derived
 *  from the exit code + stderr/stdout signal (never from a structured API
 *  response — the CLI is the only thing that ever talks to the provider).
 *  This is what actually drives retry/recovery policy in
 *  runCliSubscription(), analogous to OpenClaw's isAuthErrorMessage /
 *  isRateLimitAssistantError / isOverloadedErrorMessage / isTransientHttpError
 *  taxonomy — adapted here to a locally-spawned process's exit code + text
 *  output instead of a provider SDK's typed HTTP error.
 *
 *  - "auth_expired": the CLI reported it isn't logged in / its token was
 *    rejected. Recovered via ONE bounded, silent re-spawn (never a retry
 *    loop) — see attemptSessionExpiredRecovery below.
 *  - "rate_limited": the CLI's own request hit a provider rate limit.
 *    Retried with a longer backoff than overloaded/transient.
 *  - "overloaded": the provider reported it is temporarily at capacity.
 *    Retried with a short backoff.
 *  - "transient": a network/process-level hiccup with no provider-specific
 *    signal (ECONNRESET, a bad-gateway/5xx-shaped message, or the CLI
 *    producing genuinely zero output before the no-output watchdog killed
 *    it). Retried with a short backoff.
 *  - "fatal": anything else — an unrecognized crash, a bad-request-shaped
 *    error, or the CLI simply not being on PATH. Never retried; retrying a
 *    deterministic failure just burns the same time three times over. */
export type CliFailureClass = "auth_expired" | "rate_limited" | "overloaded" | "transient" | "fatal";

function defaultFailureClassFor(kind: CliFailureKind): CliFailureClass {
  if (kind === "not_authenticated") {
    return "auth_expired";
  }
  // not_installed and timeout default to fatal (a pre-spawn PATH miss, or an
  // overall-budget timeout on a process that WAS producing output, are both
  // treated as non-retryable by default) — callers that discover a more
  // specific class (e.g. the no-output watchdog, which is a "timeout" kind
  // but a "transient" class) pass it explicitly instead of relying on this.
  return "fatal";
}

/** Distinguishes WHY the CLI didn't produce a completion, so the caller (and
 *  eventually the control plane's platform-voice error mapper) can react
 *  differently to "not installed" vs "not logged in" vs "timed out" vs
 *  "crashed" instead of one blanket failure. `failureClass` carries the
 *  richer taxonomy above for retry/recovery decisions; `kind` and its four
 *  values are unchanged from before this module had retries, since
 *  runtime.ts's cliErrorMessage() (and the platform-voice string matching it
 *  feeds, server_modules/sage_agent_runtime_service.py's
 *  _friendly_cli_subscription_error) key off exactly those four buckets. */
export class CliRunError extends Error {
  readonly kind: CliFailureKind;
  readonly failureClass: CliFailureClass;

  constructor(kind: CliFailureKind, message: string, opts: { failureClass?: CliFailureClass } = {}) {
    super(message);
    this.name = "CliRunError";
    this.kind = kind;
    this.failureClass = opts.failureClass ?? defaultFailureClassFor(kind);
  }
}

export interface CliRunParams {
  runtime: CliSubscriptionRuntime;
  /** The user-facing prompt (already flattened from any prior turns). */
  prompt: string;
  /** Passed via --system-prompt for runtimes that support it (claude_code).
   *  Empty for runtimes where the caller already folded it into `prompt`. */
  systemPrompt?: string;
  /** Explicit model override. Empty means "let the CLI use its own default"
   *  — never fabricate a model name the CLI wouldn't recognize. */
  model?: string;
  /** Reasoning-effort override, verified live against each CLI's own --help.
   *  The two runtimes are DIFFERENT controls with different flags and
   *  different accepted vocabularies — never flattened to one shape:
   *    - claude_code: "low" | "medium" | "high" | "xhigh" | "max"
   *      (no "off"/"minimal" — the Claude CLI's --effort has no such value).
   *    - codex:       "off" | "minimal" | "low" | "medium" | "high" |
   *                   "xhigh" | "max" (codex's own ReasoningEffort enum —
   *                   see codex-app-server.ts's identical comment).
   *  Validation of which value is legal for which runtime happens upstream
   *  (server_modules/fleet_tools.py, server_modules/sage_agent_runtime_
   *  service.py) — this module trusts what it's given and only decides
   *  WHETHER to append a flag at all (empty/unset means "let the CLI use its
   *  own configured default", same convention as `model` above). */
  reasoningEffort?: string;
  timeoutMs: number;
}

/** Minimal structural subset of node:child_process's ChildProcess that this
 *  module actually needs. A real ChildProcess satisfies this directly; tests
 *  can inject a plain EventEmitter-based double without touching a real
 *  process — the same "injectable dependency" shape runtime.ts already uses
 *  for fetchImpl on the Ollama path. */
export interface CliChildProcessLike {
  stdout: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  stderr: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  on(event: "error", listener: (err: NodeJS.ErrnoException) => void): unknown;
  on(event: "close", listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type CliSpawnImpl = (
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["ignore", "pipe", "pipe"] },
) => CliChildProcessLike;

export interface CliRunnerConfig {
  /** Injectable for tests. Defaults to node:child_process's real spawn. */
  spawnImpl?: CliSpawnImpl;
  /** Injectable for tests. Defaults to process.env (full inheritance — the
   *  CLI needs PATH to find its own dependencies and HOME/env vars to find
   *  its own auth; the Gateway never substitutes or reads that auth itself). */
  env?: NodeJS.ProcessEnv;
  /** Grace period between SIGTERM and SIGKILL on timeout. Defaults to 5s. */
  killGraceMs?: number;
  /** How long the CLI may produce ZERO stdout/stderr bytes before being
   *  killed early, distinct from the overall per-attempt timeoutMs budget —
   *  a process that has gone completely silent is a different failure mode
   *  than one that is slow-but-alive (mirrors OpenClaw's separate
   *  noOutputTimer vs timeoutTimer, claude-live-session.ts:54-55,1182-1202).
   *  Only takes effect when strictly less than the attempt's timeoutMs —
   *  otherwise the overall timeout alone already bounds worst-case silence
   *  and a redundant watchdog would just race it (see spawnAndCollect).
   *  Defaults to 45s. */
  noOutputTimeoutMs?: number;
  /** Bounded extra attempts (beyond the first) for a failure classified
   *  "rate_limited" | "overloaded" | "transient". Defaults to 2, i.e. up to
   *  3 attempts total. Never applied to "auth_expired" (see
   *  attemptSessionExpiredRecovery, a separate single-shot path) or "fatal". */
  maxRetries?: number;
  /** Injectable sleep for backoff/recovery delays. Defaults to a real
   *  setTimeout-based wait. Tests substitute a zero-delay stub so the suite
   *  doesn't actually sleep through backoff windows. */
  delayImpl?: (ms: number) => Promise<void>;
}

const FORCE_KILL_GRACE_MS = 5_000;
const DEFAULT_NO_OUTPUT_TIMEOUT_MS = 45_000;
const DEFAULT_MAX_RETRIES = 2;
// Before the ONE bounded session-expired recovery re-spawn, so a credential
// the CLI is already in the middle of refreshing (or another concurrent CLI
// invocation just refreshed) has a moment to land before we try again.
const AUTH_RECOVERY_DELAY_MS = 750;
// Guard against a runaway/looping CLI process filling memory with output —
// well past anything a real completion or error stream would ever produce.
const MAX_BUFFERED_OUTPUT_CHARS = 2_000_000;

function defaultSpawn(
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; stdio: ["ignore", "pipe", "pipe"] },
): CliChildProcessLike {
  return spawn(command, args, options) as unknown as CliChildProcessLike;
}

function defaultDelay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    if (typeof (timer as unknown as { unref?: () => void }).unref === "function") {
      (timer as unknown as { unref: () => void }).unref();
    }
  });
}

function truncate(value: string, maxLength = 400): string {
  const token = String(value || "").trim();
  return token.length > maxLength ? `${token.slice(0, maxLength - 3)}...` : token;
}

function binaryFor(runtime: CliSubscriptionRuntime, env: NodeJS.ProcessEnv): string {
  if (runtime === "claude_code") {
    return String(env.CLAUDE_CLI_PATH || "").trim() || "claude";
  }
  return String(env.CODEX_CLI_PATH || "").trim() || "codex";
}

/** Builds the argv for a single non-interactive, tool-free completion.
 *
 *  Claude Code: `-p <prompt> --output-format stream-json --verbose --tools ""`
 *  `--verbose` is REQUIRED by this CLI when combining --print with
 *  --output-format=stream-json (verified against the installed 2.1.205
 *  binary — it refuses to start otherwise). `--tools ""` disables the full
 *  tool set so this is a bounded single-turn completion, not an open-ended
 *  agentic session; there is no `--max-turns` flag on this CLI version (the
 *  spec predates it) — a tool-free `-p` turn is inherently single-turn since
 *  there is nothing left for the model to call.
 *
 *  Codex: `exec <prompt> --json --skip-git-repo-check --sandbox read-only`.
 *  Codex has no "disable tools" flag (it is a coding agent by design), so
 *  --sandbox read-only is the closest safety net: this capability is a text
 *  completion, not a license to mutate the box's filesystem.
 *
 *  Reasoning effort (verified against each CLI's own --help — two distinct
 *  flags/enums, never flattened to one): Claude Code takes a top-level
 *  `--effort <level>` flag; Codex takes a config override,
 *  `-c model_reasoning_effort=<level>`, since it has no dedicated CLI flag
 *  for this on the `exec` subcommand. Both are appended ONLY when
 *  params.reasoningEffort is set — an unset value means "let the CLI use
 *  its own configured default", same convention as `model` above. */
function buildInvocation(
  params: CliRunParams,
  env: NodeJS.ProcessEnv,
): { command: string; args: string[] } {
  const command = binaryFor(params.runtime, env);
  if (params.runtime === "claude_code") {
    const args = ["-p", params.prompt, "--output-format", "stream-json", "--verbose", "--tools", ""];
    if (params.systemPrompt) {
      args.push("--system-prompt", params.systemPrompt);
    }
    if (params.model) {
      args.push("--model", params.model);
    }
    if (params.reasoningEffort) {
      args.push("--effort", params.reasoningEffort);
    }
    return { command, args };
  }
  const args = ["exec", params.prompt, "--json", "--skip-git-repo-check", "--sandbox", "read-only"];
  if (params.model) {
    args.push("--model", params.model);
  }
  if (params.reasoningEffort) {
    args.push("-c", `model_reasoning_effort=${params.reasoningEffort}`);
  }
  return { command, args };
}

interface RawSpawnOutcome {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  spawnError: NodeJS.ErrnoException | null;
  /** The overall per-attempt timeoutMs budget elapsed (process may have been
   *  alive and producing output the whole time — just took too long). */
  timedOut: boolean;
  /** The no-output watchdog elapsed first: zero stdout/stderr bytes for
   *  noOutputTimeoutMs. A distinct signal from `timedOut` — see
   *  CliRunnerConfig.noOutputTimeoutMs. */
  noOutputTimedOut: boolean;
}

function spawnAndCollect(
  command: string,
  args: string[],
  opts: {
    timeoutMs: number;
    spawnImpl: CliSpawnImpl;
    env: NodeJS.ProcessEnv;
    killGraceMs: number;
    noOutputTimeoutMs: number;
  },
): Promise<RawSpawnOutcome> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let noOutputTimedOut = false;
    let forceKillTimer: ReturnType<typeof setTimeout> | null = null;
    let noOutputTimer: ReturnType<typeof setTimeout> | null = null;

    // stdin is explicitly closed ("ignore"): neither CLI should ever block
    // waiting on interactive input in a headless spawn — Codex in particular
    // announces "Reading additional input from stdin..." and will happily
    // wait forever on a stdin that's attached but never closed.
    const child = opts.spawnImpl(command, args, { env: opts.env, stdio: ["ignore", "pipe", "pipe"] });

    const finish = (outcome: Omit<RawSpawnOutcome, "timedOut" | "noOutputTimedOut">) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(killTimer);
      if (noOutputTimer) {
        clearTimeout(noOutputTimer);
      }
      if (forceKillTimer) {
        clearTimeout(forceKillTimer);
      }
      resolve({ ...outcome, timedOut, noOutputTimedOut });
    };

    const forceKillAfterGrace = () => {
      forceKillTimer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {
          // Already gone — nothing to do.
        }
      }, opts.killGraceMs);
      if (typeof (forceKillTimer as unknown as { unref?: () => void }).unref === "function") {
        (forceKillTimer as unknown as { unref: () => void }).unref();
      }
    };

    const killTimer = setTimeout(() => {
      timedOut = true;
      if (noOutputTimer) {
        clearTimeout(noOutputTimer);
      }
      try {
        child.kill("SIGTERM");
      } catch {
        // Already gone — nothing to do.
      }
      forceKillAfterGrace();
    }, opts.timeoutMs);
    if (typeof (killTimer as unknown as { unref?: () => void }).unref === "function") {
      (killTimer as unknown as { unref: () => void }).unref();
    }

    // Only arm the watchdog when it's a REAL early-warning ahead of the
    // overall budget — if noOutputTimeoutMs isn't strictly shorter than
    // timeoutMs, the overall killTimer above already bounds total silence
    // and a second timer at (or past) the same delay would just race it for
    // no benefit, non-deterministically depending on setTimeout ordering.
    const armNoOutputWatchdog = opts.noOutputTimeoutMs < opts.timeoutMs;

    const startNoOutputTimer = () => {
      noOutputTimer = setTimeout(() => {
        noOutputTimedOut = true;
        clearTimeout(killTimer);
        try {
          child.kill("SIGTERM");
        } catch {
          // Already gone — nothing to do.
        }
        forceKillAfterGrace();
      }, opts.noOutputTimeoutMs);
      if (typeof (noOutputTimer as unknown as { unref?: () => void }).unref === "function") {
        (noOutputTimer as unknown as { unref: () => void }).unref();
      }
    };
    if (armNoOutputWatchdog) {
      startNoOutputTimer();
    }

    const resetNoOutputTimer = () => {
      if (!armNoOutputWatchdog || settled) {
        return;
      }
      if (noOutputTimer) {
        clearTimeout(noOutputTimer);
      }
      startNoOutputTimer();
    };

    child.stdout?.on("data", (chunk) => {
      resetNoOutputTimer();
      if (stdout.length < MAX_BUFFERED_OUTPUT_CHARS) {
        stdout += String(chunk);
      }
    });
    child.stderr?.on("data", (chunk) => {
      resetNoOutputTimer();
      if (stderr.length < MAX_BUFFERED_OUTPUT_CHARS) {
        stderr += String(chunk);
      }
    });
    child.on("error", (err) => {
      finish({ exitCode: null, signal: null, stdout, stderr, spawnError: err });
    });
    child.on("close", (code, signal) => {
      finish({ exitCode: code, signal: signal ?? null, stdout, stderr, spawnError: null });
    });
  });
}

function parseJsonLines(raw: string): Record<string, unknown>[] {
  const events: Record<string, unknown>[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed[0] !== "{") {
      continue;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === "object") {
        events.push(parsed as Record<string, unknown>);
      }
    } catch {
      // Stray non-JSON noise on stdout — never let one bad line corrupt an
      // otherwise-valid stream. The events we key off are looked up below;
      // if none are found, that's handled as a crash, not swallowed.
    }
  }
  return events;
}

// Adapted (trimmed, re-verified against THIS module's actual CLI-produced
// text, not a provider SDK's structured error) from OpenClaw's
// embedded-agent-helpers/failover-matches.ts ERROR_PATTERNS.{rateLimit,
// overloaded,serverError/timeout} — read-only reference, never imported;
// OpenClaw's version is a much larger, provider-agnostic vocabulary (HTTP
// APIs across a dozen providers) than a locally-spawned claude/codex process
// ever surfaces in its own stdout/stderr, so this is intentionally a small,
// targeted subset, not a port.
const RATE_LIMIT_PATTERNS: ReadonlyArray<RegExp | string> = [
  /rate[_ -]?limit/i,
  /too many requests/i,
  /\b429\b/,
  "quota exceeded",
  "resource_exhausted",
  "usage limit",
  /\btokens per minute\b/i,
  /\btpm\b/i,
];
const OVERLOADED_PATTERNS: ReadonlyArray<RegExp | string> = [
  /overloaded/i,
  /\bat capacity\b/i,
  /high demand/i,
  /\b529\b/,
];
const TRANSIENT_PATTERNS: ReadonlyArray<RegExp | string> = [
  /econnreset/i,
  /econnrefused/i,
  /etimedout/i,
  /socket hang up/i,
  /network error/i,
  /fetch failed/i,
  /connection reset/i,
  /internal server error/i,
  /bad gateway/i,
  /gateway timeout/i,
  /\bhttp\s*5\d\d\b/i,
  /service unavailable/i,
];

function matchesAny(haystack: string, patterns: ReadonlyArray<RegExp | string>): boolean {
  return patterns.some((pattern) => (pattern instanceof RegExp ? pattern.test(haystack) : haystack.includes(pattern)));
}

/** Classifies a non-auth CLI failure's text (already-lowercased stdout+stderr
 *  haystack) into the retry-relevant taxonomy. Order matters: rate-limit and
 *  overload phrasing are checked before the generic transient bucket since
 *  "service unavailable ... overloaded" should read as overloaded, not a
 *  generic 5xx. Falls back to "fatal" — an unrecognized failure is NOT
 *  assumed retryable; that would silently triple the cost of every genuinely
 *  deterministic crash (bad prompt, real bug, etc). */
function classifyFailureText(haystack: string): CliFailureClass {
  if (matchesAny(haystack, RATE_LIMIT_PATTERNS)) {
    return "rate_limited";
  }
  if (matchesAny(haystack, OVERLOADED_PATTERNS)) {
    return "overloaded";
  }
  if (matchesAny(haystack, TRANSIENT_PATTERNS)) {
    return "transient";
  }
  return "fatal";
}

const CLAUDE_AUTH_MARKERS = ["not logged in", "please run /login", "invalid api key", "authentication_failed"];

/** Claude Code's --output-format stream-json is JSONL: a "system"/"init"
 *  event, one or more "assistant" events, and a final "result" event that
 *  carries the completed text (`result`) + `is_error` + `usage`. Verified
 *  empirically against the installed 2.1.205 binary's unauthenticated
 *  response shape: {"type":"assistant",...,"error":"authentication_failed"}
 *  followed by {"type":"result","is_error":true,"result":"Not logged in ·
 *  Please run /login",...}. The authenticated SUCCESS shape is not
 *  independently verified in this environment (no working login here) but
 *  follows the same documented "result" event contract. */
function parseClaudeCodeOutput(outcome: RawSpawnOutcome): CliRunResult {
  const events = parseJsonLines(outcome.stdout);
  const resultEvent = [...events].reverse().find((e) => e.type === "result");
  const haystack = `${outcome.stdout}\n${outcome.stderr}`.toLowerCase();
  const looksLikeAuthFailure =
    events.some((e) => e.error === "authentication_failed")
    || CLAUDE_AUTH_MARKERS.some((marker) => haystack.includes(marker));

  if (resultEvent && resultEvent.is_error) {
    const detail = String(resultEvent.result || `claude exited with subtype ${String(resultEvent.subtype || "error")}`);
    if (looksLikeAuthFailure) {
      throw new CliRunError("not_authenticated", detail);
    }
    throw new CliRunError("crash", detail, { failureClass: classifyFailureText(haystack) });
  }
  if (!resultEvent) {
    if (looksLikeAuthFailure) {
      throw new CliRunError("not_authenticated", "Claude Code reported an authentication failure");
    }
    throw new CliRunError(
      "crash",
      `claude exited with code ${outcome.exitCode ?? "null"} and no parsable result`
      + (outcome.stderr ? ` (stderr: ${truncate(outcome.stderr)})` : ""),
      { failureClass: classifyFailureText(haystack) },
    );
  }

  const text = String(resultEvent.result || "").trim();
  if (!text) {
    throw new CliRunError("crash", "claude returned an empty completion", { failureClass: classifyFailureText(haystack) });
  }
  const usage = (resultEvent.usage && typeof resultEvent.usage === "object")
    ? (resultEvent.usage as Record<string, unknown>)
    : {};
  return {
    text,
    usage: {
      input_tokens: Number(usage.input_tokens) || 0,
      output_tokens: Number(usage.output_tokens) || 0,
    },
  };
}

const CODEX_AUTH_MARKERS = [
  "401 unauthorized", "unauthorized", "not logged in", "not authenticated", "missing bearer",
];

function codexErrorText(error: unknown): string {
  if (!error) {
    return "codex reported an unknown error";
  }
  if (typeof error === "string") {
    return error;
  }
  if (typeof error === "object" && error && "message" in error) {
    return String((error as { message?: unknown }).message || "codex reported an unknown error");
  }
  return String(error);
}

/** Codex's --json is JSONL: "thread.started" / "turn.started", then either a
 *  "turn.completed" event carrying `usage` (success — the actual text lives
 *  in the preceding "item.completed" event with item.type=="agent_message")
 *  or a "turn.failed" event carrying `error.message` (failure). Both the
 *  success shape and the two failure shapes (bad model/generic error, and a
 *  401-unauthorized reconnect loop) were captured empirically against a real
 *  installed 0.144.0 binary in this environment. */
function parseCodexOutput(outcome: RawSpawnOutcome): CliRunResult {
  const events = parseJsonLines(outcome.stdout);
  const completed = [...events].reverse().find((e) => e.type === "turn.completed");
  const failed = [...events].reverse().find((e) => e.type === "turn.failed");
  const haystack = `${outcome.stdout}\n${outcome.stderr}`.toLowerCase();
  const looksLikeAuthFailure = CODEX_AUTH_MARKERS.some((marker) => haystack.includes(marker));

  if (completed && !failed) {
    const message = [...events].reverse().find(
      (e) => e.type === "item.completed"
        && typeof e.item === "object" && e.item !== null
        && (e.item as Record<string, unknown>).type === "agent_message",
    );
    const item = (message?.item && typeof message.item === "object") ? (message.item as Record<string, unknown>) : {};
    const text = String(item.text || "").trim();
    if (!text) {
      throw new CliRunError("crash", "codex completed the turn but produced no agent_message text", {
        failureClass: classifyFailureText(haystack),
      });
    }
    const usage = (completed.usage && typeof completed.usage === "object")
      ? (completed.usage as Record<string, unknown>)
      : {};
    return {
      text,
      usage: {
        input_tokens: Number(usage.input_tokens) || 0,
        output_tokens: Number(usage.output_tokens) || 0,
      },
    };
  }

  if (looksLikeAuthFailure) {
    throw new CliRunError("not_authenticated", failed ? codexErrorText(failed.error) : "codex reported an authentication failure");
  }
  if (failed) {
    throw new CliRunError("crash", codexErrorText(failed.error), { failureClass: classifyFailureText(haystack) });
  }
  throw new CliRunError(
    "crash",
    `codex exited with code ${outcome.exitCode ?? "null"} and no parsable result`
    + (outcome.stderr ? ` (stderr: ${truncate(outcome.stderr)})` : ""),
    { failureClass: classifyFailureText(haystack) },
  );
}

// EAGAIN/EMFILE/ENFILE/ENOMEM are local resource-exhaustion errors from the
// OS's own process-spawn syscall (too many open file descriptors, momentarily
// out of memory, etc.) — genuinely transient conditions distinct from
// ENOENT ("this binary doesn't exist," always fatal) or a permissions error
// ("this binary can't be run here," also always fatal since retrying without
// a permission change never helps).
const TRANSIENT_SPAWN_ERROR_CODES = new Set(["EAGAIN", "EMFILE", "ENFILE", "ENOMEM"]);

/** Turns one raw spawn outcome into either a CliRunResult or a classified
 *  CliRunError. Split out of runCliSubscription so the retry loop below can
 *  call it once per attempt without duplicating the spawn-error/timeout/
 *  parse branching. */
function evaluateOutcome(
  runtime: CliSubscriptionRuntime,
  outcome: RawSpawnOutcome,
  command: string,
  timeoutMs: number,
  noOutputTimeoutMs: number,
): CliRunResult {
  if (outcome.spawnError) {
    if (outcome.spawnError.code === "ENOENT") {
      throw new CliRunError("not_installed", `binary "${command}" was not found on PATH`);
    }
    const code = outcome.spawnError.code;
    throw new CliRunError("crash", outcome.spawnError.message || String(outcome.spawnError), {
      failureClass: code && TRANSIENT_SPAWN_ERROR_CODES.has(code) ? "transient" : "fatal",
    });
  }
  if (outcome.noOutputTimedOut) {
    // A process producing literally nothing is a materially different
    // failure than one that was slow-but-alive (below) — it's the kind of
    // hang a retry (fresh process, fresh stdio pipes) plausibly fixes, so
    // it's classified "transient" rather than the overall timeout's "fatal".
    throw new CliRunError(
      "timeout",
      `no output for ${noOutputTimeoutMs}ms and was terminated (no-output watchdog; SIGTERM/SIGKILL sent)`,
      { failureClass: "transient" },
    );
  }
  if (outcome.timedOut) {
    // The process WAS alive (it just exceeded the full budget) — retrying
    // with the same timeout would likely just spend that same budget again
    // on what's probably a deterministically slow prompt/model, so this one
    // is "fatal" (no retry), matching OpenClaw's own stance (its reliability
    // suite has a test explicitly titled "does not retry a resumed CLI
    // session after the hard overall timeout").
    throw new CliRunError("timeout", `no response within ${timeoutMs}ms (SIGTERM/SIGKILL sent)`, { failureClass: "fatal" });
  }
  return runtime === "claude_code" ? parseClaudeCodeOutput(outcome) : parseCodexOutput(outcome);
}

function backoffMsFor(failureClass: CliFailureClass, retryIndex: number): number {
  // retryIndex is 0 for the delay before the FIRST retry (i.e. immediately
  // after the initial attempt failed), 1 before the second, etc. Rate limits
  // get a longer schedule than overloaded/transient — a 429 usually needs
  // real wall-clock time to clear, while an overload/network blip often
  // clears within a second.
  const schedule = failureClass === "rate_limited" ? [1_000, 4_000] : [500, 2_000];
  return schedule[retryIndex] ?? schedule[schedule.length - 1];
}

/** Spawns the requested CLI headlessly for exactly one completion, retrying
 *  a bounded number of times on transient/overloaded/rate_limited failures
 *  and attempting ONE bounded, silent session-expired ("auth_expired")
 *  recovery re-spawn before surfacing an auth failure to the caller. Never
 *  reads or transmits a credential file itself — auth (or its absence, or
 *  its silent refresh) is whatever the spawned process discovers in its own
 *  inherited environment on each fresh invocation; this module has no
 *  concept of provider fallback and never switches claude_code<->codex on
 *  failure (that rule lives one layer up, in server_modules/sage_agent_
 *  runtime_service.py's dispatch, and is out of scope here — this module
 *  only ever retries the SAME runtime it was asked to run). */
export async function runCliSubscription(
  params: CliRunParams,
  config: CliRunnerConfig = {},
): Promise<CliRunResult> {
  const env = config.env ?? process.env;
  const spawnImpl = config.spawnImpl ?? defaultSpawn;
  const killGraceMs = config.killGraceMs ?? FORCE_KILL_GRACE_MS;
  const noOutputTimeoutMs = config.noOutputTimeoutMs ?? DEFAULT_NO_OUTPUT_TIMEOUT_MS;
  const maxRetries = config.maxRetries ?? DEFAULT_MAX_RETRIES;
  const delayImpl = config.delayImpl ?? defaultDelay;
  const { command, args } = buildInvocation(params, env);

  let attemptedAuthRecovery = false;
  let retryAttempt = 0;

  for (;;) {
    const outcome = await spawnAndCollect(command, args, {
      timeoutMs: params.timeoutMs,
      spawnImpl,
      env,
      killGraceMs,
      noOutputTimeoutMs,
    });

    let error: CliRunError;
    try {
      return evaluateOutcome(params.runtime, outcome, command, params.timeoutMs, noOutputTimeoutMs);
    } catch (thrown) {
      if (!(thrown instanceof CliRunError)) {
        throw thrown;
      }
      error = thrown;
    }

    // not_installed is a PATH problem discovered before any generation even
    // started — spawning again can never fix it, and it isn't part of the
    // retry-relevant taxonomy at all (it has no failureClass-driven policy).
    if (error.kind === "not_installed") {
      throw error;
    }

    if (error.failureClass === "auth_expired") {
      if (attemptedAuthRecovery) {
        // The recovery re-spawn ALSO saw an auth failure: this isn't a
        // momentary "token mid-refresh" race, it genuinely needs a human to
        // re-run sign-in. Surface it now — never loop on this.
        throw error;
      }
      attemptedAuthRecovery = true;
      // A fresh process re-reads ~/.claude or ~/.codex from scratch. If the
      // failure was a stale in-memory/cached token and the CLI's own startup
      // logic (or a concurrent CLI invocation) had already refreshed the
      // on-disk credential, this second spawn silently succeeds with zero
      // human involvement — the actual "recovery" mechanism IS the CLI's own
      // non-interactive refresh path, which this module never reimplements
      // or touches directly (it never reads credential files itself, see
      // module header). The short delay gives an in-flight refresh a moment
      // to land before we ask again.
      await delayImpl(AUTH_RECOVERY_DELAY_MS);
      continue;
    }

    const retryable = error.failureClass === "rate_limited" || error.failureClass === "overloaded" || error.failureClass === "transient";
    if (retryable && retryAttempt < maxRetries) {
      const delayMs = backoffMsFor(error.failureClass, retryAttempt);
      retryAttempt += 1;
      await delayImpl(delayMs);
      continue;
    }

    throw error;
  }
}
