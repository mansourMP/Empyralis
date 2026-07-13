import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";
import { runCliSubscription, CliRunError, type CliRunResult, type CliSubscriptionRuntime } from "./cli-runner";
import { sharedCodexAppServer, codexAppServerEnabled } from "./codex-app-server";
import { sharedClaudeCliPrewarmPool, claudeCliPrewarmEnabled } from "./claude-cli-prewarm";

// BYO-brain Phase 2: the on-box LLM capability. This runs on the USER's paired
// box and forwards a turn to the box's OWN local Ollama endpoint
// (127.0.0.1:11434), returning the completion over the existing
// execute_tool_via_gateway rail. It is a sibling to the browser/shell
// executors on the capability-router. It NEVER touches any subscription
// credential — Ollama is local, open-weights, and needs no login. This proves
// the box-dispatch rail on a zero-compliance-risk payload.
//
// cli_subscription Phase 3: the SAME llm.generate capability also carries the
// owner's own Claude Code / Codex subscription. Those two runtimes spawn the
// box's own CLI (cli-runner.ts) instead of calling a local HTTP endpoint —
// still zero credential transmission, since the CLI reads its own auth from
// the environment it's spawned in, never handed to the Gateway.

export const LLM_GENERATE_CAPABILITY = "llm.generate";

const SUPPORTED_CAPABILITIES = [LLM_GENERATE_CAPABILITY];

const DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434";
const DEFAULT_MODEL = "llama3.2";
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;
const CLI_SUBSCRIPTION_RUNTIMES = new Set(["claude_code", "codex"]);

export interface OllamaChatMessage {
  role: string;
  content: string;
}

type FetchImpl = (url: string, init: Record<string, unknown>) => Promise<{
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}>;

/** Same shape as runCliSubscription from cli-runner.ts — kept as a separate
 *  type alias so this file doesn't need to import CliRunParams/CliRunnerConfig
 *  just to describe the injection point. */
type CliRunnerImpl = (params: {
  runtime: CliSubscriptionRuntime;
  prompt: string;
  systemPrompt?: string;
  model?: string;
  timeoutMs: number;
}) => Promise<CliRunResult>;

export interface GatewayLLMRuntimeConfig {
  /** Base URL for the local Ollama endpoint. Defaults to the env override
   *  (ORION_LOCAL_WORKER_OLLAMA_URL) or 127.0.0.1:11434. */
  ollamaBaseUrl?: string;
  /** Injectable for tests. Defaults to the global fetch. */
  fetchImpl?: FetchImpl;
  /** Injectable for tests. Defaults to Date.now-based timeouts. */
  defaultTimeoutMs?: number;
  /** Injectable for tests. Defaults to cli-runner.ts's runCliSubscription
   *  (spawns the real claude/codex binary). */
  cliRunner?: CliRunnerImpl;
}

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function token(value: unknown): string {
  return String(value ?? "").trim();
}

function positiveIntOr(value: unknown, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(Math.floor(parsed), max);
}

/** Normalizes an inbound messages array into Ollama chat messages, dropping
 *  anything that isn't a {role, content} pair with real content. */
function normalizeMessages(value: unknown): OllamaChatMessage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: OllamaChatMessage[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const role = token((item as Record<string, unknown>).role).toLowerCase();
    const content = token((item as Record<string, unknown>).content);
    if (!content) {
      continue;
    }
    out.push({ role: role === "assistant" || role === "system" ? role : "user", content });
  }
  return out;
}

/** Builds the chat messages from the invoke arguments. Prefers an explicit
 *  `messages` array; otherwise assembles system + history + user prompt. */
function buildMessages(args: Record<string, unknown>): OllamaChatMessage[] {
  const explicit = normalizeMessages(args.messages);
  if (explicit.length > 0) {
    return explicit;
  }
  const messages: OllamaChatMessage[] = [];
  const system = token(args.system) || token(args.system_prompt);
  if (system) {
    messages.push({ role: "system", content: system });
  }
  for (const prior of normalizeMessages(args.prior_messages)) {
    messages.push(prior);
  }
  const user = token(args.prompt) || token(args.user_message) || token(args.message);
  if (user) {
    messages.push({ role: "user", content: user });
  }
  return messages;
}

/** Flattens the chat messages into what a single non-interactive CLI turn
 *  needs: a system prompt (kept separate for runtimes with their own
 *  --system-prompt flag) and one prompt body. A lone non-system message is
 *  passed through as-is (the common single-turn case); more than one is
 *  rendered with role labels so the CLI still sees the full exchange in its
 *  one shot — cli_subscription turns are a single bounded request, never a
 *  multi-turn tool loop (same constraint the "local" Ollama brain runs
 *  under). `includeSystemInline` folds the system content into the prompt
 *  body itself for runtimes with no separate system-prompt flag (codex). */
function buildCliPrompt(
  messages: OllamaChatMessage[],
  { includeSystemInline }: { includeSystemInline: boolean },
): { systemPrompt: string; promptText: string } {
  const systemPrompt = messages.filter((m) => m.role === "system").map((m) => m.content).join("\n\n");
  const conversational = messages.filter((m) => m.role !== "system");
  const promptBody = conversational.length === 1
    ? conversational[0].content
    : conversational.map((m) => `${m.role === "assistant" ? "Assistant" : "User"}: ${m.content}`).join("\n\n");
  if (!includeSystemInline) {
    return { systemPrompt, promptText: promptBody };
  }
  return { systemPrompt: "", promptText: systemPrompt ? `${systemPrompt}\n\n${promptBody}` : promptBody };
}

/** Maps a CLI failure into a precise, honest message. This text is what the
 *  control plane's platform-voice error mapper pattern-matches on (mirroring
 *  how the Ollama path's "unreachable"/"HTTP 4xx" wording already gets
 *  matched by sage_agent_runtime_service._friendly_gateway_brain_error) — so
 *  the distinct phrases below ("not installed", "not signed in", "timed
 *  out", "exited unexpectedly") matter, not just the human readability. */
function cliErrorMessage(runtime: CliSubscriptionRuntime, error: unknown): string {
  const label = runtime === "claude_code" ? "Claude Code" : "Codex";
  if (error instanceof CliRunError) {
    if (error.kind === "not_installed") {
      return `${label} is not installed on this Gateway (${error.message}).`;
    }
    if (error.kind === "not_authenticated") {
      return `${label} on this Gateway is not signed in (${error.message}).`;
    }
    if (error.kind === "timeout") {
      return `${label} generation timed out on this Gateway (${error.message}).`;
    }
    return `${label} exited unexpectedly on this Gateway (${error.message}).`;
  }
  const reason = error instanceof Error ? error.message : String(error);
  return `${label} generation failed on this Gateway (${reason}).`;
}

export class GatewayLLMRuntime {
  private readonly ollamaBaseUrl: string;
  private readonly fetchImpl: FetchImpl;
  private readonly defaultTimeoutMs: number;
  private readonly cliRunner: CliRunnerImpl;
  // Phase 2 (streaming): set post-construction by index.ts once the
  // GatewayWsClient exists (same "setter after the fact" pattern already
  // used for cliSetupRuntime.setEventPublisher — the ws client and the
  // capability router/runtimes have a circular construction order).
  // Undefined until wired, and safely a no-op if it never is.
  private publishChunk?: (payload: { request_id: string; delta: string }) => Promise<void>;

  constructor(config: GatewayLLMRuntimeConfig = {}) {
    this.ollamaBaseUrl = (
      config.ollamaBaseUrl
      || String(process.env.ORION_LOCAL_WORKER_OLLAMA_URL || "").trim()
      || DEFAULT_OLLAMA_BASE_URL
    ).replace(/\/+$/, "");
    this.fetchImpl = config.fetchImpl ?? ((globalThis.fetch as unknown) as FetchImpl);
    this.defaultTimeoutMs = config.defaultTimeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.cliRunner = config.cliRunner ?? runCliSubscription;
  }

  /** Wires the ability to stream partial-text `tool.invoke.chunk` events for
   *  an in-flight turn. Best-effort by design: a chunk that fails to publish
   *  (e.g. a momentary reconnect) is dropped, never retried — the durable
   *  tool.invoke/response pair remains the sole source of truth for the
   *  actual reply; this only affects how "live" it looks while streaming. */
  setEventPublisher(publish: (payload: { request_id: string; delta: string }) => Promise<void>): void {
    this.publishChunk = publish;
  }

  requestedCapabilities(): string[] {
    return [...SUPPORTED_CAPABILITIES];
  }

  supportsCapability(capabilityId: string): boolean {
    return SUPPORTED_CAPABILITIES.includes(token(capabilityId));
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload;
    const capabilityId = token(payload.capability_id);
    if (capabilityId !== LLM_GENERATE_CAPABILITY) {
      throw new Error(`Unsupported llm_runtime capability: ${capabilityId || "unknown"}`);
    }
    const args = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    const runtime = token(args.runtime) || "ollama";
    const messages = buildMessages(args);
    if (messages.length === 0) {
      throw new Error("llm.generate requires a non-empty prompt (messages, or system + prompt).");
    }
    const timeoutMs = positiveIntOr(
      typeof args.timeout_ms !== "undefined" ? args.timeout_ms : (Number(args.timeout_seconds) || 0) * 1000,
      this.defaultTimeoutMs,
      MAX_TIMEOUT_MS,
    );

    if (runtime === "ollama") {
      const model = token(args.model) || DEFAULT_MODEL;
      const options = (args.options && typeof args.options === "object" && !Array.isArray(args.options))
        ? (args.options as Record<string, unknown>)
        : undefined;
      return this.generateViaOllama({ model, messages, timeoutMs, options, runtime });
    }
    if (CLI_SUBSCRIPTION_RUNTIMES.has(runtime)) {
      // No DEFAULT_MODEL fallback here on purpose — that constant is an Ollama
      // model name. An unset model means "let the CLI use its own configured
      // default", never a fabricated model id the CLI wouldn't recognize.
      const model = token(args.model);
      return this.generateViaCli({
        runtime: runtime as CliSubscriptionRuntime,
        model,
        messages,
        timeoutMs,
        requestId: token(frame.id),
      });
    }
    throw new Error(
      `llm.generate runtime "${runtime}" is not supported on this Gateway (expected "ollama", "claude_code", or "codex").`,
    );
  }

  private async generateViaOllama(params: {
    model: string;
    messages: OllamaChatMessage[];
    timeoutMs: number;
    options?: Record<string, unknown>;
    runtime: string;
  }): Promise<Record<string, unknown>> {
    const url = `${this.ollamaBaseUrl}/api/chat`;
    const body: Record<string, unknown> = {
      model: params.model,
      messages: params.messages,
      stream: false,
    };
    if (params.options) {
      body.options = params.options;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), params.timeoutMs);
    if (typeof (timer as { unref?: () => void }).unref === "function") {
      (timer as { unref: () => void }).unref();
    }
    let response: Awaited<ReturnType<FetchImpl>>;
    try {
      response = await this.fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Local Ollama endpoint unreachable at ${this.ollamaBaseUrl} (${reason}). `
        + "Start Ollama on this box, or unbind the agent's local runtime.",
      );
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`Ollama returned HTTP ${response.status}: ${String(detail).slice(0, 300)}`);
    }

    const parsed = (await response.json().catch(() => null)) as {
      model?: string;
      message?: { content?: string };
      prompt_eval_count?: number;
      eval_count?: number;
      done?: boolean;
    } | null;
    const text = token(parsed?.message?.content);
    if (!text) {
      throw new Error("Ollama returned an empty completion.");
    }
    return {
      text,
      runtime: params.runtime,
      model: token(parsed?.model) || params.model,
      usage: {
        input_tokens: Number(parsed?.prompt_eval_count) || 0,
        output_tokens: Number(parsed?.eval_count) || 0,
      },
      source: "gateway_ollama",
    };
  }

  /** cli_subscription (Phase 3): spawns the owner's own Claude Code / Codex
   *  CLI for one completion. Same return shape as generateViaOllama so
   *  nothing downstream (the control plane's dispatch, the turn ledger) needs
   *  to branch on which runtime actually produced the text. */
  private async generateViaCli(params: {
    runtime: CliSubscriptionRuntime;
    model: string;
    messages: OllamaChatMessage[];
    timeoutMs: number;
    requestId: string;
  }): Promise<Record<string, unknown>> {
    // Phase 1 (latency): route codex through the warm app-server daemon when
    // enabled — no per-turn `codex exec` cold start. The daemon takes the
    // system prompt separately (thread/start baseInstructions), so DON'T inline
    // it for that path; the exec path still inlines system for codex as before.
    const useCodexDaemon = params.runtime === "codex" && codexAppServerEnabled();
    // Phase 1 (latency), claude_code variant: route through the single-use
    // prewarm pool when enabled (see claude-cli-prewarm.ts for why this is a
    // pool of one-shot processes, not a reused multi-turn daemon like codex's).
    const useClaudePrewarm = params.runtime === "claude_code" && claudeCliPrewarmEnabled();
    const { systemPrompt, promptText } = buildCliPrompt(params.messages, {
      includeSystemInline: params.runtime === "codex" && !useCodexDaemon,
    });
    if (!promptText) {
      throw new Error("llm.generate requires a non-empty prompt (messages, or system + prompt).");
    }
    // Phase 2 (streaming): only the codex daemon path streams real deltas
    // today (it's the only backend that emits them pre-completion — the
    // Claude pool and both cold-spawn paths only ever produce one final
    // text). A publish failure (e.g. a momentary reconnect) must never fail
    // or slow the turn itself — chunks are strictly best-effort.
    const onDelta = (useCodexDaemon && this.publishChunk)
      ? (delta: string) => {
        void this.publishChunk?.({ request_id: params.requestId, delta }).catch(() => {
          // best-effort — the durable tool.invoke/response pair is authoritative
        });
      }
      : undefined;
    let result: CliRunResult;
    try {
      if (useCodexDaemon) {
        result = await sharedCodexAppServer().generate({
          prompt: promptText,
          systemPrompt,
          model: params.model,
          timeoutMs: params.timeoutMs,
        }, onDelta);
      } else if (useClaudePrewarm) {
        result = await sharedClaudeCliPrewarmPool().generate({
          prompt: promptText,
          systemPrompt,
          model: params.model,
          timeoutMs: params.timeoutMs,
        });
      } else {
        result = await this.cliRunner({
          runtime: params.runtime,
          prompt: promptText,
          systemPrompt,
          model: params.model,
          timeoutMs: params.timeoutMs,
        });
      }
    } catch (error) {
      throw new Error(cliErrorMessage(params.runtime, error));
    }
    return {
      text: result.text,
      runtime: params.runtime,
      // Echo back what was actually requested; an unset model means the CLI
      // used its own configured default, which the CLI doesn't report back
      // to us — "default" here describes OUR request, not a real model id.
      model: params.model || "default",
      usage: result.usage,
      source: params.runtime === "claude_code" ? "gateway_claude_code" : "gateway_codex",
    };
  }
}
