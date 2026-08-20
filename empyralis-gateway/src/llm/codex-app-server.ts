// Phase 1 (latency): a persistent `codex app-server` daemon, kept WARM and
// reused across turns, instead of spawning a fresh `codex exec` per message.
//
// `codex exec` pays a full cold start every turn (load binary, read config,
// resolve auth, spin the model runtime) — measured ~1-2s of pure overhead
// before any generation. `codex app-server` is a long-lived JSON-RPC daemon
// (stdio, line-delimited): we spawn it ONCE, do the initialize handshake once,
// and every turn is just `thread/start` + `turn/start` on the already-warm
// process. It also streams token deltas (`AgentMessageDelta`) as they generate,
// which the exec path cannot — that's what Phase 2 forwards to the chat.
//
// Protocol validated against codex 0.144.1's own `app-server generate-ts`
// bindings + OpenClaw's proven client. Handshake: initialize (with
// capabilities.experimentalApi) THEN an `initialized` notification — without
// that notification the server stays in a legacy parse mode and rejects v2
// turn input ("missing field `text`"). Turn: thread/start (model,
// baseInstructions=system, reasoning effort) -> turn/start (input) -> stream
// AgentMessageDelta -> turn/completed | error.
//
// This module is ADDITIVE and OFF by default: runtime.ts only routes codex
// through it when EMPYRALIS_GATEWAY_CODEX_APP_SERVER=1, else the working
// `codex exec` path (cli-runner.ts) is used unchanged.

import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { createInterface, type Interface } from "readline";
import { CliRunError, type CliRunResult } from "./cli-runner";

const INITIALIZE_TIMEOUT_MS = 20_000;
// Reap the daemon after this long with no turns — free the resources, but keep
// it alive across the bursts of turns that matter for latency.
const IDLE_REAP_MS = 10 * 60 * 1_000;

export interface CodexAppServerParams {
  prompt: string;
  systemPrompt?: string;
  model?: string;
  /** off | minimal | low | medium | high | xhigh | max (codex ReasoningEffort). */
  reasoningEffort?: string;
  timeoutMs: number;
}

/** One entry from codex app-server's own `model/list` RPC — the account's
 *  REAL, currently-usable model catalog (already scoped by whatever auth
 *  mode/plan this box's Codex is logged in under; the server computes this
 *  itself, we just relay it). Field names match the protocol's own `Model`
 *  type (see codex app-server generate-ts's v2/Model.ts) minus the parts we
 *  don't use (service tiers, upgrade metadata) — never hand-typed, always
 *  this shape or nothing. Includes the live reasoning-effort catalog
 *  (defaultReasoningEffort / supportedReasoningEfforts) — this varies per
 *  model and per account (verified live: gpt-5.6-terra offers a distinct
 *  set from gpt-5.6-luna, including "ultra", a level absent from every
 *  static table and every doc page), so it must be relayed from the RPC
 *  itself rather than transcribed once and left to rot. */
export interface CodexModelListEntry {
  id: string;
  displayName: string;
  description: string;
  hidden: boolean;
  isDefault: boolean;
  defaultReasoningEffort: string;
  supportedReasoningEfforts: { reasoningEffort: string; description: string }[];
}

export interface CodexModelListResult {
  /** "apikey" | "chatgpt" | "chatgptAuthTokens" | "headers" | "agentIdentity"
   *  | "personalAccessToken" | "bedrockApiKey" | null (not authenticated /
   *  unknown) — codex's own AuthMode, verbatim, never our own guess. */
  authMethod: string | null;
  models: CodexModelListEntry[];
}

const MODEL_LIST_TIMEOUT_MS = 15_000;

/** Called with each streamed text delta as the model generates (Phase 2). */
export type CodexDeltaSink = (delta: string) => void;

interface PendingRequest {
  resolve: (result: Record<string, unknown>) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

// One in-flight turn, keyed by threadId. We register this right after
// thread/start (BEFORE turn/start), because `item/agentMessage/delta` and
// `turn/completed` notifications stream in before turn/start's own response
// necessarily resolves — keying by threadId (unique per turn here) means no
// delta is ever missed.
interface ThreadTurnState {
  sink?: CodexDeltaSink;
  buffer: string;
  resolve: (r: CliRunResult) => void;
  reject: (e: Error) => void;
  settled: boolean;
}

function codexBinary(env: NodeJS.ProcessEnv): string {
  return String(env.CODEX_CLI_PATH || "").trim() || "codex";
}

/** One persistent codex app-server process + JSON-RPC client. Singleton per
 *  gateway process (see sharedCodexAppServer). */
export class CodexAppServerDaemon {
  private child: ChildProcessWithoutNullStreams | null = null;
  private lines: Interface | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, PendingRequest>();
  private readonly threadStates = new Map<string, ThreadTurnState>(); // threadId -> in-flight turn
  private initializing: Promise<void> | null = null;
  private initialized = false;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private activeTurns = 0;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly spawnImpl: typeof spawn = spawn,
  ) {}

  /** Spawn + handshake if not already warm. Idempotent and concurrency-safe. */
  private async ensureReady(): Promise<void> {
    if (this.initialized && this.child) {
      return;
    }
    if (this.initializing) {
      return this.initializing;
    }
    this.initializing = this.startAndHandshake().finally(() => {
      this.initializing = null;
    });
    return this.initializing;
  }

  private async startAndHandshake(): Promise<void> {
    const bin = codexBinary(this.env);
    const child = this.spawnImpl(bin, ["app-server"], {
      env: this.env,
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
    this.child = child;
    this.lines = createInterface({ input: child.stdout });
    this.lines.on("line", (line) => this.handleLine(line));
    child.on("exit", (code, signal) => this.handleExit(code, signal));
    child.on("error", () => this.teardown("app-server spawn error"));

    // initialize (request) -> then `initialized` notification. The notification
    // is REQUIRED — without it turn input is parsed in a legacy mode.
    await this.request(
      "initialize",
      {
        clientInfo: { name: "empyralis-gateway", title: "Empyralis Gateway", version: "1.0" },
        capabilities: { experimentalApi: true },
      },
      INITIALIZE_TIMEOUT_MS,
    );
    this.notify("initialized");
    this.initialized = true;
  }

  private handleExit(code: number | null, signal: NodeJS.Signals | null): void {
    this.teardown(`app-server exited (code=${code ?? "null"} signal=${signal ?? "null"})`);
  }

  private teardown(reason: string): void {
    this.initialized = false;
    const err = new CliRunError("crash", `codex app-server unavailable: ${reason}`);
    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
    for (const [, s] of this.threadStates) {
      if (!s.settled) {
        s.settled = true;
        s.reject(err);
      }
    }
    this.threadStates.clear();
    try {
      this.lines?.close();
    } catch {
      // ignore
    }
    this.lines = null;
    this.child = null;
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }

  private scheduleIdleReap(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
    }
    this.idleTimer = setTimeout(() => {
      if (this.activeTurns === 0) {
        const child = this.child;
        this.teardown("idle");
        try {
          child?.kill("SIGTERM");
        } catch {
          // ignore
        }
      }
    }, IDLE_REAP_MS);
    if (typeof (this.idleTimer as unknown as { unref?: () => void }).unref === "function") {
      (this.idleTimer as unknown as { unref: () => void }).unref();
    }
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed || trimmed[0] !== "{") {
      return;
    }
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return; // stray non-JSON line — never let it corrupt the stream
    }
    // Response to one of our requests.
    if (typeof msg.id === "number" && ("result" in msg || "error" in msg)) {
      const p = this.pending.get(msg.id);
      if (!p) {
        return;
      }
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) {
        const e = msg.error as { message?: string };
        p.reject(new CliRunError("crash", String(e?.message || "codex app-server request failed")));
      } else {
        p.resolve((msg.result as Record<string, unknown>) || {});
      }
      return;
    }
    // Server notification (streaming events, etc.).
    if (typeof msg.method === "string") {
      this.handleNotification(msg.method, (msg.params as Record<string, unknown>) || {});
    }
  }

  private handleNotification(method: string, params: Record<string, unknown>): void {
    const threadId = String(params.threadId || "");
    if (!threadId) {
      return;
    }
    const state = this.threadStates.get(threadId);
    if (!state) {
      return;
    }

    // Token-by-token assistant text — the streaming payload (Phase 2).
    if (method === "item/agentMessage/delta") {
      const delta = String(params.delta || "");
      if (delta) {
        state.buffer += delta;
        if (state.sink) {
          try {
            state.sink(delta);
          } catch {
            // a sink error must never break the daemon
          }
        }
      }
      return;
    }

    // Turn finished successfully — resolve with the accumulated text + usage.
    if (method === "turn/completed") {
      if (state.settled) {
        return;
      }
      state.settled = true;
      const turn = (params.turn as Record<string, unknown>) || {};
      const text = state.buffer.trim() || extractTurnText({ turn }).trim();
      if (!text) {
        state.reject(new CliRunError("crash", "codex app-server produced no agent message text"));
      } else {
        state.resolve({ text, usage: extractUsage({ turn }) });
      }
      return;
    }

    // Turn error (usage limit, rate limit, model error, …) — surface the CLI's
    // own message so the backend can show it transparently.
    if (method === "error") {
      if (state.settled) {
        return;
      }
      state.settled = true;
      const e = (params.error as { message?: string }) || {};
      state.reject(new CliRunError("crash", String(e?.message || "codex turn failed")));
      return;
    }
  }

  private request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<Record<string, unknown>> {
    const child = this.child;
    if (!child || !child.stdin.writable) {
      return Promise.reject(new CliRunError("crash", "codex app-server is not running"));
    }
    const id = this.nextId++;
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new CliRunError("timeout", `codex app-server ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      if (typeof (timer as unknown as { unref?: () => void }).unref === "function") {
        (timer as unknown as { unref: () => void }).unref();
      }
      this.pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  }

  private notify(method: string, params?: Record<string, unknown>): void {
    const child = this.child;
    if (!child || !child.stdin.writable) {
      return;
    }
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params: params ?? {} })}\n`);
  }

  /** Run one turn on the warm daemon. Streams deltas to onDelta (if given) and
   *  resolves with the full text + usage, matching the exec path's shape. */
  async generate(params: CodexAppServerParams, onDelta?: CodexDeltaSink): Promise<CliRunResult> {
    await this.ensureReady();
    this.activeTurns += 1;
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    let threadId = "";
    try {
      // thread/start — carries model + system prompt (reasoning effort is set
      // per-turn on turn/start below).
      const threadParams: Record<string, unknown> = { sandbox: "read-only" };
      if (params.model) {
        threadParams.model = params.model;
      }
      if (params.systemPrompt) {
        threadParams.baseInstructions = params.systemPrompt;
      }
      const threadRes = await this.request("thread/start", threadParams, INITIALIZE_TIMEOUT_MS);
      const thread = (threadRes.thread as { id?: string } | undefined) || {};
      threadId = String(thread.id || threadRes.threadId || "");
      if (!threadId) {
        throw new CliRunError("crash", "codex app-server thread/start returned no thread id");
      }

      // Register the in-flight turn BEFORE turn/start so no streamed delta or
      // the turn/completed notification is missed.
      const completion = new Promise<CliRunResult>((resolve, reject) => {
        this.threadStates.set(threadId, { sink: onDelta, buffer: "", resolve, reject, settled: false });
      });

      // turn/start — the user input; reasoning effort override lands here.
      const turnParams: Record<string, unknown> = {
        threadId,
        input: [{ type: "text", text: params.prompt, text_elements: [] }],
      };
      if (params.reasoningEffort) {
        turnParams.effort = params.reasoningEffort;
      }
      // turn/start's own response only means the turn was ACCEPTED; the reply
      // arrives via streamed deltas + the turn/completed notification, which
      // `completion` awaits. A rejected turn/start (bad params) still throws.
      await this.request("turn/start", turnParams, INITIALIZE_TIMEOUT_MS);

      const deadline = new Promise<CliRunResult>((_, reject) => {
        const t = setTimeout(
          () => reject(new CliRunError("timeout", `codex app-server turn timed out after ${params.timeoutMs}ms`)),
          params.timeoutMs,
        );
        if (typeof (t as unknown as { unref?: () => void }).unref === "function") {
          (t as unknown as { unref: () => void }).unref();
        }
      });
      return await Promise.race([completion, deadline]);
    } finally {
      if (threadId) {
        this.threadStates.delete(threadId);
      }
      this.activeTurns = Math.max(0, this.activeTurns - 1);
      if (this.activeTurns === 0) {
        this.scheduleIdleReap();
      }
    }
  }

  /** The account's REAL model catalog + auth mode, straight from codex's own
   *  `getAuthStatus` + `model/list` RPCs — never a hand-typed list. This is
   *  the fix for the live bug this module's own daemon caused: an agent
   *  configured with a model id that codex has since retired (their catalog
   *  moves — "gpt-5.4" existed when this product's own picker was built and
   *  is gone from a live `model/list` response today, replaced by
   *  gpt-5.6-terra/luna) got a raw provider JSON error instead of a working
   *  turn or an honest refusal. Querying this list is metadata, not
   *  inference — no prompt is sent, nothing is billed, and it's safe to call
   *  as often as the caller needs (still routed through the shared warm
   *  daemon so it never double-spawns against a live turn).
   *
   *  `includeHidden: true` because the caller (fleet_configure_agent's
   *  save-time validation) needs to know about a model that's real but
   *  deliberately absent from the default picker, not just the ones meant
   *  for a dropdown — `hidden` on each entry is exactly that distinction,
   *  so nothing here has to reinvent it. */
  async listModels(): Promise<CodexModelListResult> {
    await this.ensureReady();
    const authRes = await this.request("getAuthStatus", { includeToken: false, refreshToken: false }, MODEL_LIST_TIMEOUT_MS);
    const authMethod = typeof authRes.authMethod === "string" ? authRes.authMethod : null;
    const modelsRes = await this.request("model/list", { includeHidden: true }, MODEL_LIST_TIMEOUT_MS);
    const data = Array.isArray(modelsRes.data) ? modelsRes.data : [];
    const models: CodexModelListEntry[] = data
      .filter((m): m is Record<string, unknown> => !!m && typeof m === "object")
      .map((m) => ({
        id: String(m.id || m.model || ""),
        displayName: String(m.displayName || m.id || m.model || ""),
        description: String(m.description || ""),
        hidden: Boolean(m.hidden),
        isDefault: Boolean(m.isDefault),
        defaultReasoningEffort: String(m.defaultReasoningEffort || ""),
        supportedReasoningEfforts: parseSupportedReasoningEfforts(m.supportedReasoningEfforts),
      }))
      .filter((m) => m.id);
    return { authMethod, models };
  }
}

/** codex app-server's `supportedReasoningEfforts` is per-model and per-
 *  account (verified live: distinct sets for gpt-5.6-terra vs gpt-5.6-luna,
 *  including levels like "ultra" that exist in no doc and no static table
 *  here) — so this parses defensively rather than asserting a shape a future
 *  catalog change could break. A missing/malformed entry degrades to an
 *  empty array, never a throw; a malformed individual item is dropped, not
 *  the whole list. */
export function parseSupportedReasoningEfforts(raw: unknown): { reasoningEffort: string; description: string }[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((e): e is Record<string, unknown> => !!e && typeof e === "object")
    .map((e) => ({
      reasoningEffort: String(e.reasoningEffort || ""),
      description: String(e.description || ""),
    }))
    .filter((e) => e.reasoningEffort);
}

function extractTurnText(turnRes: Record<string, unknown>): string {
  const turn = turnRes.turn as { items?: unknown[] } | undefined;
  const items = Array.isArray(turn?.items) ? turn?.items : [];
  for (const item of items) {
    const it = item as Record<string, unknown>;
    if (it.type === "agent_message" && typeof it.text === "string") {
      return it.text;
    }
  }
  return "";
}

function extractUsage(turnRes: Record<string, unknown>): { input_tokens: number; output_tokens: number } {
  const turn = turnRes.turn as { usage?: Record<string, unknown> } | undefined;
  const u = turn?.usage || {};
  return {
    input_tokens: Number(u.input_tokens) || 0,
    output_tokens: Number(u.output_tokens) || 0,
  };
}

let shared: CodexAppServerDaemon | null = null;

/** Process-wide warm daemon. */
export function sharedCodexAppServer(env: NodeJS.ProcessEnv = process.env): CodexAppServerDaemon {
  if (!shared) {
    shared = new CodexAppServerDaemon(env);
  }
  return shared;
}

/** Feature flag: only route codex through the warm daemon when explicitly on,
 *  so the proven `codex exec` path stays the default until this is validated. */
export function codexAppServerEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return String(env.EMPYRALIS_GATEWAY_CODEX_APP_SERVER || "").trim() === "1";
}
