import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

// BYO-brain Phase 2: the on-box LLM capability. This runs on the USER's paired
// box and forwards a turn to the box's OWN local Ollama endpoint
// (127.0.0.1:11434), returning the completion over the existing
// execute_tool_via_gateway rail. It is a sibling to the browser/shell
// executors on the capability-router. It NEVER touches any subscription
// credential — Ollama is local, open-weights, and needs no login. This proves
// the box-dispatch rail on a zero-compliance-risk payload.

export const LLM_GENERATE_CAPABILITY = "llm.generate";

const SUPPORTED_CAPABILITIES = [LLM_GENERATE_CAPABILITY];

const DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434";
const DEFAULT_MODEL = "llama3.2";
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;

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

export interface GatewayLLMRuntimeConfig {
  /** Base URL for the local Ollama endpoint. Defaults to the env override
   *  (ORION_LOCAL_WORKER_OLLAMA_URL) or 127.0.0.1:11434. */
  ollamaBaseUrl?: string;
  /** Injectable for tests. Defaults to the global fetch. */
  fetchImpl?: FetchImpl;
  /** Injectable for tests. Defaults to Date.now-based timeouts. */
  defaultTimeoutMs?: number;
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

export class GatewayLLMRuntime {
  private readonly ollamaBaseUrl: string;
  private readonly fetchImpl: FetchImpl;
  private readonly defaultTimeoutMs: number;

  constructor(config: GatewayLLMRuntimeConfig = {}) {
    this.ollamaBaseUrl = (
      config.ollamaBaseUrl
      || String(process.env.ORION_LOCAL_WORKER_OLLAMA_URL || "").trim()
      || DEFAULT_OLLAMA_BASE_URL
    ).replace(/\/+$/, "");
    this.fetchImpl = config.fetchImpl ?? ((globalThis.fetch as unknown) as FetchImpl);
    this.defaultTimeoutMs = config.defaultTimeoutMs ?? DEFAULT_TIMEOUT_MS;
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
    if (runtime !== "ollama") {
      // Phase 2 only wires the local Ollama runtime. CLI-subscription runtimes
      // (claude_code/codex) ride the same rail but are a later phase — fail
      // honestly rather than silently doing the wrong thing.
      throw new Error(`llm.generate runtime "${runtime}" is not supported on this Gateway yet (only "ollama").`);
    }
    const model = token(args.model) || DEFAULT_MODEL;
    const messages = buildMessages(args);
    if (messages.length === 0) {
      throw new Error("llm.generate requires a non-empty prompt (messages, or system + prompt).");
    }
    const timeoutMs = positiveIntOr(
      typeof args.timeout_ms !== "undefined" ? args.timeout_ms : (Number(args.timeout_seconds) || 0) * 1000,
      this.defaultTimeoutMs,
      MAX_TIMEOUT_MS,
    );
    const options = (args.options && typeof args.options === "object" && !Array.isArray(args.options))
      ? (args.options as Record<string, unknown>)
      : undefined;

    return this.generateViaOllama({ model, messages, timeoutMs, options, runtime });
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
}
