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

/** Distinguishes WHY the CLI didn't produce a completion, so the caller (and
 *  eventually the control plane's platform-voice error mapper) can react
 *  differently to "not installed" vs "not logged in" vs "timed out" vs
 *  "crashed" instead of one blanket failure. */
export class CliRunError extends Error {
  readonly kind: CliFailureKind;

  constructor(kind: CliFailureKind, message: string) {
    super(message);
    this.name = "CliRunError";
    this.kind = kind;
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
}

const FORCE_KILL_GRACE_MS = 5_000;
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
 *  completion, not a license to mutate the box's filesystem. */
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
    return { command, args };
  }
  const args = ["exec", params.prompt, "--json", "--skip-git-repo-check", "--sandbox", "read-only"];
  if (params.model) {
    args.push("--model", params.model);
  }
  return { command, args };
}

interface RawSpawnOutcome {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  spawnError: NodeJS.ErrnoException | null;
  timedOut: boolean;
}

function spawnAndCollect(
  command: string,
  args: string[],
  opts: { timeoutMs: number; spawnImpl: CliSpawnImpl; env: NodeJS.ProcessEnv; killGraceMs: number },
): Promise<RawSpawnOutcome> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let forceKillTimer: ReturnType<typeof setTimeout> | null = null;

    // stdin is explicitly closed ("ignore"): neither CLI should ever block
    // waiting on interactive input in a headless spawn — Codex in particular
    // announces "Reading additional input from stdin..." and will happily
    // wait forever on a stdin that's attached but never closed.
    const child = opts.spawnImpl(command, args, { env: opts.env, stdio: ["ignore", "pipe", "pipe"] });

    const finish = (outcome: Omit<RawSpawnOutcome, "timedOut">) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(killTimer);
      if (forceKillTimer) {
        clearTimeout(forceKillTimer);
      }
      resolve({ ...outcome, timedOut });
    };

    const killTimer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGTERM");
      } catch {
        // Already gone — nothing to do.
      }
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
    }, opts.timeoutMs);
    if (typeof (killTimer as unknown as { unref?: () => void }).unref === "function") {
      (killTimer as unknown as { unref: () => void }).unref();
    }

    child.stdout?.on("data", (chunk) => {
      if (stdout.length < MAX_BUFFERED_OUTPUT_CHARS) {
        stdout += String(chunk);
      }
    });
    child.stderr?.on("data", (chunk) => {
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
    throw new CliRunError("crash", detail);
  }
  if (!resultEvent) {
    if (looksLikeAuthFailure) {
      throw new CliRunError("not_authenticated", "Claude Code reported an authentication failure");
    }
    throw new CliRunError(
      "crash",
      `claude exited with code ${outcome.exitCode ?? "null"} and no parsable result`
      + (outcome.stderr ? ` (stderr: ${truncate(outcome.stderr)})` : ""),
    );
  }

  const text = String(resultEvent.result || "").trim();
  if (!text) {
    throw new CliRunError("crash", "claude returned an empty completion");
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
      throw new CliRunError("crash", "codex completed the turn but produced no agent_message text");
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
    throw new CliRunError("crash", codexErrorText(failed.error));
  }
  throw new CliRunError(
    "crash",
    `codex exited with code ${outcome.exitCode ?? "null"} and no parsable result`
    + (outcome.stderr ? ` (stderr: ${truncate(outcome.stderr)})` : ""),
  );
}

/** Spawns the requested CLI headlessly for exactly one completion. Never
 *  reads or transmits a credential file itself — auth (or its absence) is
 *  whatever the spawned process discovers in its own inherited environment. */
export async function runCliSubscription(
  params: CliRunParams,
  config: CliRunnerConfig = {},
): Promise<CliRunResult> {
  const env = config.env ?? process.env;
  const spawnImpl = config.spawnImpl ?? defaultSpawn;
  const killGraceMs = config.killGraceMs ?? FORCE_KILL_GRACE_MS;
  const { command, args } = buildInvocation(params, env);

  const outcome = await spawnAndCollect(command, args, { timeoutMs: params.timeoutMs, spawnImpl, env, killGraceMs });

  if (outcome.spawnError) {
    if (outcome.spawnError.code === "ENOENT") {
      throw new CliRunError("not_installed", `binary "${command}" was not found on PATH`);
    }
    throw new CliRunError("crash", outcome.spawnError.message || String(outcome.spawnError));
  }
  if (outcome.timedOut) {
    throw new CliRunError("timeout", `no response within ${params.timeoutMs}ms (SIGTERM/SIGKILL sent)`);
  }

  return params.runtime === "claude_code" ? parseClaudeCodeOutput(outcome) : parseCodexOutput(outcome);
}
