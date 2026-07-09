import test from "node:test";
import assert from "node:assert/strict";

import { GatewayLLMRuntime, LLM_GENERATE_CAPABILITY } from "../llm/runtime";
import { CliRunError } from "../llm/cli-runner";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

function makeInvokeFrame(
  args: Record<string, unknown>,
  extra: Partial<GatewayToolInvokePayload> = {},
): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: LLM_GENERATE_CAPABILITY,
      arguments: args,
      run_id: "run-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
      ...extra,
    },
  };
}

interface Capture {
  url?: string;
  body?: Record<string, unknown>;
}

function stubFetch(response: unknown, capture: Capture, ok = true, status = 200) {
  return async (url: string, init: Record<string, unknown>) => {
    capture.url = url;
    capture.body = JSON.parse(String(init.body));
    return {
      ok,
      status,
      json: async () => response,
      text: async () => JSON.stringify(response),
    };
  };
}

test("llm.generate proxies to the box's Ollama /api/chat and returns the completion", async () => {
  const capture: Capture = {};
  const runtime = new GatewayLLMRuntime({
    ollamaBaseUrl: "http://127.0.0.1:11434",
    fetchImpl: stubFetch(
      { model: "llama3.2", message: { content: "hello from ollama" }, prompt_eval_count: 12, eval_count: 7 },
      capture,
    ),
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ model: "llama3.2", system: "You are a bot.", prompt: "hi", runtime: "ollama" }),
  );
  assert.equal(capture.url, "http://127.0.0.1:11434/api/chat");
  assert.equal((capture.body as Record<string, unknown>).stream, false);
  assert.equal((capture.body as Record<string, unknown>).model, "llama3.2");
  assert.deepEqual((capture.body as Record<string, unknown>).messages, [
    { role: "system", content: "You are a bot." },
    { role: "user", content: "hi" },
  ]);
  assert.equal(result.text, "hello from ollama");
  assert.equal(result.runtime, "ollama");
  assert.equal(result.model, "llama3.2");
  assert.deepEqual(result.usage, { input_tokens: 12, output_tokens: 7 });
  assert.equal(result.source, "gateway_ollama");
});

test("llm.generate prefers an explicit messages array (system + history + user)", async () => {
  const capture: Capture = {};
  const runtime = new GatewayLLMRuntime({ fetchImpl: stubFetch({ message: { content: "ok" } }, capture) });
  await runtime.handleCapabilityInvoke(
    makeInvokeFrame({
      model: "llama3.2",
      messages: [
        { role: "system", content: "S" },
        { role: "user", content: "U1" },
        { role: "assistant", content: "A1" },
        { role: "user", content: "U2" },
      ],
    }),
  );
  assert.equal((capture.body as { messages: unknown[] }).messages.length, 4);
});

test("llm.generate rejects a genuinely unknown runtime (not ollama/claude_code/codex)", async () => {
  const runtime = new GatewayLLMRuntime({
    fetchImpl: async () => {
      throw new Error("fetch should not be called");
    },
  });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ model: "x", prompt: "hi", runtime: "gpt-5-direct" })),
    /not supported on this Gateway/,
  );
});

test("llm.generate requires a non-empty prompt", async () => {
  const runtime = new GatewayLLMRuntime({
    fetchImpl: async () => {
      throw new Error("fetch should not be called");
    },
  });
  await assert.rejects(() => runtime.handleCapabilityInvoke(makeInvokeFrame({ model: "x" })), /non-empty prompt/);
});

test("llm.generate surfaces an honest error when the local Ollama is unreachable", async () => {
  const runtime = new GatewayLLMRuntime({
    ollamaBaseUrl: "http://127.0.0.1:9",
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
  });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ model: "x", prompt: "hi" })),
    /unreachable/,
  );
});

test("llm.generate surfaces a non-200 Ollama response as an error, never a silent empty reply", async () => {
  const capture: Capture = {};
  const runtime = new GatewayLLMRuntime({ fetchImpl: stubFetch("model not found", capture, false, 404) });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ model: "nope", prompt: "hi" })),
    /Ollama returned HTTP 404/,
  );
});

test("supportsCapability only matches llm.generate", () => {
  const runtime = new GatewayLLMRuntime();
  assert.equal(runtime.supportsCapability("llm.generate"), true);
  assert.equal(runtime.supportsCapability("shell.execute"), false);
  assert.deepEqual(runtime.requestedCapabilities(), ["llm.generate"]);
});

// ── cli_subscription (Phase 3): claude_code / codex via the injected cliRunner ──

interface CliCapture {
  runtime?: string;
  prompt?: string;
  systemPrompt?: string;
  model?: string;
  timeoutMs?: number;
}

test("llm.generate (claude_code): dispatches via cliRunner, keeps system prompt separate", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "hello from claude", usage: { input_tokens: 10, output_tokens: 3 } };
    },
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "claude_code", model: "claude-sonnet-4-6", system: "Be terse.", prompt: "hi" }),
  );
  assert.equal(capture.runtime, "claude_code");
  assert.equal(capture.systemPrompt, "Be terse.");
  assert.equal(capture.prompt, "hi");
  assert.equal(capture.model, "claude-sonnet-4-6");
  assert.equal(result.text, "hello from claude");
  assert.equal(result.runtime, "claude_code");
  assert.equal(result.model, "claude-sonnet-4-6");
  assert.deepEqual(result.usage, { input_tokens: 10, output_tokens: 3 });
  assert.equal(result.source, "gateway_claude_code");
});

test("llm.generate (codex): dispatches via cliRunner, folds system prompt into the prompt body", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "PONG", usage: { input_tokens: 100, output_tokens: 1 } };
    },
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "codex", system: "Be terse.", prompt: "ping" }),
  );
  assert.equal(capture.runtime, "codex");
  // Codex has no separate system-prompt flag — the caller folds it in.
  assert.equal(capture.systemPrompt, "");
  assert.equal(capture.prompt, "Be terse.\n\nping");
  assert.equal(result.source, "gateway_codex");
  assert.equal(result.model, "default");
});

test("llm.generate (claude_code/codex): never inherits Ollama's default model name", async () => {
  const captures: CliCapture[] = [];
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      captures.push(params);
      return { text: "ok", usage: { input_tokens: 1, output_tokens: 1 } };
    },
  });
  await runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" }));
  await runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "codex", prompt: "hi" }));
  for (const capture of captures) {
    assert.equal(capture.model, "", "no model override means empty, never the Ollama default model name");
  }
});

test("llm.generate (claude_code): multi-turn history is flattened with role labels", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "ok", usage: { input_tokens: 1, output_tokens: 1 } };
    },
  });
  await runtime.handleCapabilityInvoke(
    makeInvokeFrame({
      runtime: "claude_code",
      messages: [
        { role: "system", content: "S" },
        { role: "user", content: "U1" },
        { role: "assistant", content: "A1" },
        { role: "user", content: "U2" },
      ],
    }),
  );
  assert.equal(capture.systemPrompt, "S");
  assert.equal(capture.prompt, "User: U1\n\nAssistant: A1\n\nUser: U2");
});

const CLI_FAILURE_CASES: Array<{ kind: "not_installed" | "not_authenticated" | "timeout" | "crash"; expect: RegExp }> = [
  { kind: "not_installed", expect: /not installed/ },
  { kind: "not_authenticated", expect: /not signed in/ },
  { kind: "timeout", expect: /timed out/ },
  { kind: "crash", expect: /exited unexpectedly/ },
];

for (const runtime of ["claude_code", "codex"] as const) {
  for (const { kind, expect } of CLI_FAILURE_CASES) {
    test(`llm.generate (${runtime}): a ${kind} cliRunner failure surfaces a distinct, honest message`, async () => {
      const gateway = new GatewayLLMRuntime({
        cliRunner: async () => {
          throw new CliRunError(kind, "detail from the CLI");
        },
      });
      await assert.rejects(
        () => gateway.handleCapabilityInvoke(makeInvokeFrame({ runtime, prompt: "hi" })),
        expect,
      );
    });
  }
}

test("llm.generate (claude_code): a plain (non-CliRunError) cliRunner throw still surfaces honestly", async () => {
  const runtime = new GatewayLLMRuntime({
    cliRunner: async () => {
      throw new Error("unexpected wiring bug");
    },
  });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" })),
    /generation failed on this Gateway \(unexpected wiring bug\)/,
  );
});
