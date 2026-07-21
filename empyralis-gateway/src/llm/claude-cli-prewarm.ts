// Phase 1 (latency), claude_code variant: a small pool of pre-spawned,
// already-past-cold-start `claude` CLI processes, keyed by (model, system
// prompt), each handed exactly ONE turn before being retired.
//
// Codex's app-server lets one long-lived daemon serve MANY independent turns
// on the same process — `thread/start` gives a fresh, isolated context every
// call (see codex-app-server.ts), so the daemon can be a true singleton.
// Claude's `--input-format stream-json` has no equivalent: a second stdin
// message sent to the same process is a CONTINUATION of the same
// conversation, not a fresh one (verified live against the installed binary —
// it echoes the identical session_id across two turns sent to one process).
// Empyralis's own backend already re-flattens the FULL conversation into one
// prompt every turn (cli-runner.ts's existing cold-spawn contract), so
// reusing one process across turns would hand Claude the same history twice
// — once from its own memory, once redundantly as new input text — and,
// without a stable per-conversation key threaded through the wire protocol
// (there isn't one today: `handleCapabilityInvoke` below only ever sees
// runtime/model/messages/timeout, never an agent or conversation id — see
// docs/PLATFORM-MAP.md Part 26), reusing a process by system-prompt alone
// risks one caller's turn landing on a process that still remembers a
// DIFFERENT conversation for the same agent.
//
// So this pool never reuses a process for a second turn — each entry is
// spawned, serves exactly one turn, and is torn down: IDENTICAL semantics to
// today's cli-runner.ts path, zero cross-conversation risk. The only thing
// this buys is speed: the moment a turn consumes an entry, a replacement for
// that same (model, systemPrompt) key is spawned in the background
// immediately, so the NEXT turn for that agent often finds a process that's
// already had a head start on its own cold-start/auth/init work instead of
// starting from zero. Worst case (no spare entry yet) is exactly today's
// latency — never worse.
//
// ADDITIVE and OFF by default: runtime.ts only routes claude_code through
// this when EMPYRALIS_GATEWAY_CLAUDE_PREWARM=1, else the existing
// `claude -p` cold-spawn path (cli-runner.ts) is used unchanged.

import { createHash } from "crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { createInterface, type Interface } from "readline";
import { CliRunError, type CliRunResult, type CliUsage } from "./cli-runner";

// Reap a spawned-but-never-used entry after this long — free the resources,
// but keep the bet alive across the bursts of turns that matter for latency.
const IDLE_REAP_MS = 10 * 60 * 1_000;
// Soft cap on concurrently live (claimed + spare) processes for this pool.
// Only gates the SPECULATIVE background refill, never a real turn — a real
// turn always gets a process, cap or not.
const MAX_POOL_ENTRIES = 8;

export interface ClaudePrewarmParams {
  prompt: string;
  systemPrompt?: string;
  model?: string;
  /** Same "--effort <level>" control as cli-runner.ts's CliRunParams.
   *  reasoningEffort — kept in sync here since this pool must mirror
   *  buildInvocation's argv exactly (see file header). Empty means "let the
   *  CLI use its own configured default". */
  reasoningEffort?: string;
  timeoutMs: number;
}

function claudeBinary(env: NodeJS.ProcessEnv): string {
  return String(env.CLAUDE_CLI_PATH || "").trim() || "claude";
}

function poolKey(model: string | undefined, systemPrompt: string | undefined, reasoningEffort: string | undefined): string {
  return createHash("sha256").update(`${model || ""} ${systemPrompt || ""} ${reasoningEffort || ""}`).digest("hex");
}

function buildArgs(model: string | undefined, systemPrompt: string | undefined, reasoningEffort: string | undefined): string[] {
  // Mirrors cli-runner.ts's buildInvocation exactly (--verbose required with
  // --print + --output-format=stream-json; --tools "" keeps this a bounded,
  // tool-free completion, matching today's cold-spawn safety posture) plus
  // --input-format stream-json so the prompt can arrive over stdin AFTER
  // spawn — required for pre-spawning ahead of knowing the prompt at all.
  const args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--tools", ""];
  if (systemPrompt) {
    args.push("--system-prompt", systemPrompt);
  }
  if (model) {
    args.push("--model", model);
  }
  if (reasoningEffort) {
    args.push("--effort", reasoningEffort);
  }
  return args;
}

function userInputLine(prompt: string): string {
  return `${JSON.stringify({
    type: "user",
    session_id: "",
    parent_tool_use_id: null,
    message: { role: "user", content: prompt },
  })}\n`;
}

function numberField(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Reads usage off either a `result` line's top-level `usage`, or an
 *  `assistant` line's `message.usage` — Claude's stream-json puts it in
 *  different spots depending on line type. */
function extractUsage(parsed: Record<string, unknown>): CliUsage {
  const direct = parsed.usage;
  if (direct && typeof direct === "object") {
    const u = direct as Record<string, unknown>;
    return { input_tokens: numberField(u, "input_tokens"), output_tokens: numberField(u, "output_tokens") };
  }
  const message = parsed.message;
  if (message && typeof message === "object") {
    const nested = (message as Record<string, unknown>).usage;
    if (nested && typeof nested === "object") {
      const u = nested as Record<string, unknown>;
      return { input_tokens: numberField(u, "input_tokens"), output_tokens: numberField(u, "output_tokens") };
    }
  }
  return { input_tokens: 0, output_tokens: 0 };
}

interface PoolEntry {
  key: string;
  child: ChildProcessWithoutNullStreams;
  lines: Interface;
  stderr: string;
  claimed: boolean;
  lastUsage: CliUsage;
  turn: { resolve: (r: CliRunResult) => void; reject: (e: Error) => void } | null;
  idleTimer: ReturnType<typeof setTimeout> | null;
}

/** Pool of single-use, pre-spawned `claude` CLI processes. See file header
 *  for why this is single-use rather than a true multi-turn live session. */
export class ClaudeCliPrewarmPool {
  // At most one SPARE (unclaimed) entry per key at a time.
  private readonly spare = new Map<string, PoolEntry>();
  private liveCount = 0;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly spawnImpl: typeof spawn = spawn,
  ) {}

  private spawnEntry(
    key: string,
    model: string | undefined,
    systemPrompt: string | undefined,
    reasoningEffort: string | undefined,
  ): PoolEntry {
    const child = this.spawnImpl(claudeBinary(this.env), buildArgs(model, systemPrompt, reasoningEffort), {
      env: this.env,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
    this.liveCount += 1;
    const entry: PoolEntry = {
      key,
      child,
      lines: createInterface({ input: child.stdout }),
      stderr: "",
      claimed: false,
      lastUsage: { input_tokens: 0, output_tokens: 0 },
      turn: null,
      idleTimer: null,
    };
    entry.lines.on("line", (line) => this.handleLine(entry, line));
    child.stderr.on("data", (chunk) => {
      entry.stderr += String(chunk);
    });
    child.on("error", (err) => this.retire(entry, new CliRunError("not_installed", err.message)));
    child.on("exit", (code) => {
      this.liveCount = Math.max(0, this.liveCount - 1);
      if (this.spare.get(entry.key) === entry) {
        this.spare.delete(entry.key);
      }
      if (entry.idleTimer) {
        clearTimeout(entry.idleTimer);
        entry.idleTimer = null;
      }
      if (entry.turn) {
        const message = entry.stderr.trim() || `claude CLI exited unexpectedly (code=${code ?? "null"})`;
        const turn = entry.turn;
        entry.turn = null;
        turn.reject(new CliRunError("crash", message));
      }
    });
    return entry;
  }

  /** Handles one JSONL line from a pool entry's stdout. Single-turn-per-process,
   *  so the terminal `result` line is always this entry's whole job. */
  private handleLine(entry: PoolEntry, rawLine: string): void {
    const trimmed = rawLine.trim();
    if (!trimmed || trimmed[0] !== "{") {
      return;
    }
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return; // stray non-JSON line — never let it corrupt the stream
    }
    if (parsed.type === "assistant" || parsed.type === "result") {
      const usage = extractUsage(parsed);
      if (usage.input_tokens || usage.output_tokens) {
        entry.lastUsage = usage;
      }
    }
    if (parsed.type !== "result") {
      return;
    }
    const turn = entry.turn;
    if (!turn) {
      return; // a result line on a still-spare (never claimed) entry — ignore
    }
    entry.turn = null;
    const text = String(parsed.result || "");
    if (parsed.is_error === true) {
      turn.reject(new CliRunError("crash", text || "Claude CLI failed."));
    } else if (!text) {
      turn.reject(new CliRunError("crash", "Claude CLI produced no result text"));
    } else {
      turn.resolve({ text, usage: entry.lastUsage });
    }
    this.retireAfterTurn(entry);
  }

  private retire(entry: PoolEntry, error?: Error): void {
    if (this.spare.get(entry.key) === entry) {
      this.spare.delete(entry.key);
    }
    if (entry.idleTimer) {
      clearTimeout(entry.idleTimer);
      entry.idleTimer = null;
    }
    if (entry.turn && error) {
      const turn = entry.turn;
      entry.turn = null;
      turn.reject(error);
    }
    try {
      entry.lines.close();
    } catch {
      // ignore
    }
    try {
      entry.child.kill("SIGTERM");
    } catch {
      // ignore — process may already be gone
    }
  }

  /** A process is single-use: once it has served its one turn, always tear it
   *  down (never returned to `spare`) — this is what keeps conversations from
   *  ever bleeding into each other. */
  private retireAfterTurn(entry: PoolEntry): void {
    this.retire(entry);
  }

  private scheduleIdleReap(entry: PoolEntry): void {
    entry.idleTimer = setTimeout(() => {
      this.retire(entry);
    }, IDLE_REAP_MS);
    if (typeof (entry.idleTimer as unknown as { unref?: () => void }).unref === "function") {
      (entry.idleTimer as unknown as { unref: () => void }).unref();
    }
  }

  /** Best-effort: spawn the next spare entry for a key in the background so
   *  the NEXT caller for this (model, systemPrompt) has a head start. Never
   *  throws — a failed speculative spawn just means the next real turn pays
   *  the normal cold-start cost, same as today. */
  private refillSpare(
    key: string,
    model: string | undefined,
    systemPrompt: string | undefined,
    reasoningEffort: string | undefined,
  ): void {
    if (this.spare.has(key) || this.liveCount >= MAX_POOL_ENTRIES) {
      return;
    }
    try {
      const entry = this.spawnEntry(key, model, systemPrompt, reasoningEffort);
      this.spare.set(key, entry);
      this.scheduleIdleReap(entry);
    } catch {
      // best-effort only
    }
  }

  /** Run one turn: reuse a spare pre-spawned process for this (model,
   *  systemPrompt) if one is sitting ready, else spawn one right now — either
   *  way the process is retired after this single turn, and a replacement is
   *  queued in the background for whoever calls next with the same key. */
  async generate(params: ClaudePrewarmParams): Promise<CliRunResult> {
    const key = poolKey(params.model, params.systemPrompt, params.reasoningEffort);
    let entry = this.spare.get(key) || null;
    if (entry) {
      this.spare.delete(key);
      if (entry.idleTimer) {
        clearTimeout(entry.idleTimer);
        entry.idleTimer = null;
      }
    } else {
      entry = this.spawnEntry(key, params.model, params.systemPrompt, params.reasoningEffort);
    }
    entry.claimed = true;
    const claimed = entry;

    const outcome = new Promise<CliRunResult>((resolve, reject) => {
      claimed.turn = { resolve, reject };
      try {
        claimed.child.stdin.write(userInputLine(params.prompt));
      } catch (err) {
        claimed.turn = null;
        reject(new CliRunError("crash", err instanceof Error ? err.message : "failed to write to claude CLI stdin"));
      }
    });
    const deadline = new Promise<CliRunResult>((_, reject) => {
      const t = setTimeout(
        () => reject(new CliRunError("timeout", `claude CLI turn timed out after ${params.timeoutMs}ms`)),
        params.timeoutMs,
      );
      if (typeof (t as unknown as { unref?: () => void }).unref === "function") {
        (t as unknown as { unref: () => void }).unref();
      }
    });
    try {
      return await Promise.race([outcome, deadline]);
    } finally {
      // If `claimed.turn` is still set here, neither a `result` line
      // (handleLine's own retireAfterTurn path) nor the child's own
      // "exit"/"error" handler ever cleared it — i.e. `deadline` won the
      // race above (this turn timed out) while the child is presumably
      // still alive. Every OTHER terminal path already tears the process
      // down; without this, a genuinely hung `claude` child (no output,
      // never exits) is orphaned forever — the OS process and this pool's
      // own `liveCount` both leak permanently. See
      // claude-cli-prewarm-hang-leak.test.ts.
      if (claimed.turn) {
        claimed.turn = null;
        this.retire(claimed);
      }
      // Refill regardless of success/failure/timeout — a bad turn on this
      // process doesn't mean the NEXT turn for this agent should also pay
      // full cold-start.
      this.refillSpare(key, params.model, params.systemPrompt, params.reasoningEffort);
    }
  }
}

let shared: ClaudeCliPrewarmPool | null = null;

/** Process-wide prewarm pool. */
export function sharedClaudeCliPrewarmPool(env: NodeJS.ProcessEnv = process.env): ClaudeCliPrewarmPool {
  if (!shared) {
    shared = new ClaudeCliPrewarmPool(env);
  }
  return shared;
}

/** Feature flag: only route claude_code through the prewarm pool when
 *  explicitly on, so the proven cold-spawn path (cli-runner.ts) stays the
 *  default until this is validated. */
export function claudeCliPrewarmEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return String(env.EMPYRALIS_GATEWAY_CLAUDE_PREWARM || "").trim() === "1";
}
