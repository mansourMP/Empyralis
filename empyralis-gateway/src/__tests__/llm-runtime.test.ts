import test from "node:test";
import assert from "node:assert/strict";

import { GatewayLLMRuntime, LLM_GENERATE_CAPABILITY } from "../llm/runtime";
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

test("llm.generate rejects a non-ollama runtime (CLI subscription is a later phase)", async () => {
  const runtime = new GatewayLLMRuntime({
    fetchImpl: async () => {
      throw new Error("fetch should not be called");
    },
  });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ model: "x", prompt: "hi", runtime: "claude_code" })),
    /not supported on this Gateway yet/,
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
