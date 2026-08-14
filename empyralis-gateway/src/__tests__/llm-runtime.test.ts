import test from "node:test";
import assert from "node:assert/strict";

import { GatewayLLMRuntime, LLM_GENERATE_CAPABILITY, LLM_MODELS_LIST_CAPABILITY } from "../llm/runtime";
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

test("supportsCapability matches exactly llm.generate and llm.models.list, nothing else", () => {
  const runtime = new GatewayLLMRuntime();
  assert.equal(runtime.supportsCapability("llm.generate"), true);
  assert.equal(runtime.supportsCapability(LLM_MODELS_LIST_CAPABILITY), true);
  assert.equal(runtime.supportsCapability("shell.execute"), false);
  assert.deepEqual(runtime.requestedCapabilities(), ["llm.generate", "llm.models.list"]);
});

// ── cli_subscription (Phase 3): claude_code / codex via the injected cliRunner ──

interface CliCapture {
  runtime?: string;
  prompt?: string;
  systemPrompt?: string;
  model?: string;
  reasoningEffort?: string;
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

test("llm.generate (grok_build): dispatches via cliRunner, keeps system prompt separate (grok_build has its own --rules flag)", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "hello from grok", usage: { input_tokens: 5, output_tokens: 2 } };
    },
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "grok_build", model: "grok-build", system: "Be terse.", prompt: "hi" }),
  );
  assert.equal(capture.runtime, "grok_build");
  assert.equal(capture.systemPrompt, "Be terse.", "grok_build must keep systemPrompt separate — cli-runner.ts forwards it via --rules");
  assert.equal(capture.prompt, "hi");
  assert.equal(result.text, "hello from grok");
  assert.equal(result.source, "gateway_grok_build");
});

test("llm.generate (cursor_cli): folds system prompt into the prompt body (no system-prompt flag is documented for cursor-agent)", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "hello from cursor", usage: { input_tokens: 0, output_tokens: 0 } };
    },
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "cursor_cli", model: "auto", system: "Be terse.", prompt: "hi" }),
  );
  assert.equal(capture.runtime, "cursor_cli");
  // Unlike grok_build/claude_code, cursor_cli has no system-prompt-equivalent
  // CLI flag — the system content must be folded inline into the prompt
  // body instead of silently dropped (this is the exact bug this test
  // guards against).
  assert.equal(capture.systemPrompt, "");
  assert.equal(capture.prompt, "Be terse.\n\nhi");
  assert.equal(result.text, "hello from cursor");
  assert.equal(result.source, "gateway_cursor_cli");
});

// ── Reasoning effort (Phase 1: reasoning-effort control) — the backend's
// _dispatch_cli_subscription_gateway_brain sends this as
// arguments.reasoning_effort; this Gateway handler must forward it into
// cliRunner's reasoningEffort param unchanged, for either runtime.

test("llm.generate (claude_code): forwards reasoning_effort into cliRunner", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "ok", usage: { input_tokens: 1, output_tokens: 1 } };
    },
  });
  await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "claude_code", prompt: "hi", reasoning_effort: "xhigh" }),
  );
  assert.equal(capture.reasoningEffort, "xhigh");
});

test("llm.generate (codex): forwards reasoning_effort into cliRunner", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "ok", usage: { input_tokens: 1, output_tokens: 1 } };
    },
  });
  await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "codex", prompt: "hi", reasoning_effort: "off" }),
  );
  assert.equal(capture.reasoningEffort, "off");
});

test("llm.generate: an omitted reasoning_effort forwards as empty (CLI's own default applies)", async () => {
  const capture: CliCapture = {};
  const runtime = new GatewayLLMRuntime({
    cliRunner: async (params) => {
      Object.assign(capture, params);
      return { text: "ok", usage: { input_tokens: 1, output_tokens: 1 } };
    },
  });
  await runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" }));
  assert.equal(capture.reasoningEffort, "");
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

// ── Live readiness (G-reliability-3): invalidate the passive install/auth
// cache exactly when a turn's own outcome just proved it stale — never on
// every turn (service-inventory.ts documents invalidatePassiveInventoryCache
// as "never call from a hot path"), so only not_installed/not_authenticated
// failures should trigger it. ──────────────────────────────────────────────

for (const kind of ["not_installed", "not_authenticated"] as const) {
  test(`llm.generate (claude_code): a ${kind} cliRunner failure invalidates the readiness cache so the next heartbeat re-probes`, async () => {
    let calls = 0;
    const runtime = new GatewayLLMRuntime({
      cliRunner: async () => {
        throw new CliRunError(kind, "detail from the CLI");
      },
      invalidateReadinessCache: () => {
        calls += 1;
      },
    });
    await assert.rejects(() => runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" })));
    assert.equal(calls, 1, `${kind} must invalidate the readiness cache exactly once`);
  });
}

for (const kind of ["timeout", "crash"] as const) {
  test(`llm.generate (claude_code): a ${kind} cliRunner failure does NOT invalidate the readiness cache (not a readiness-relevant signal)`, async () => {
    let calls = 0;
    const runtime = new GatewayLLMRuntime({
      cliRunner: async () => {
        throw new CliRunError(kind, "detail from the CLI");
      },
      invalidateReadinessCache: () => {
        calls += 1;
      },
    });
    await assert.rejects(() => runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" })));
    assert.equal(calls, 0);
  });
}

test("llm.generate: a successful CLI turn never invalidates the readiness cache (this must never run on the hot path)", async () => {
  let calls = 0;
  const runtime = new GatewayLLMRuntime({
    cliRunner: async () => ({ text: "ok", usage: { input_tokens: 1, output_tokens: 1 } }),
    invalidateReadinessCache: () => {
      calls += 1;
    },
  });
  await runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "claude_code", prompt: "hi" }));
  assert.equal(calls, 0);
});

test("llm.generate: an Ollama failure never invalidates the CLI readiness cache (unrelated runtime)", async () => {
  let calls = 0;
  const capture: Capture = {};
  const runtime = new GatewayLLMRuntime({
    fetchImpl: stubFetch({}, capture, false, 500),
    invalidateReadinessCache: () => {
      calls += 1;
    },
  });
  await assert.rejects(() => runtime.handleCapabilityInvoke(makeInvokeFrame({ prompt: "hi" })));
  assert.equal(calls, 0);
});

// ── llm.models.list (URGENT fix, 2026-08-14) ────────────────────────────────
// The live bug this closes: a hand-typed model catalog offered "gpt-5.4",
// which OpenAI had already retired from Codex's own account-scoped catalog.
// This capability is the honest replacement — ask the CLI, never guess.

test("llm.models.list: codex runtime calls the injected listModels impl and relays its result", async () => {
  let calls = 0;
  const runtime = new GatewayLLMRuntime({
    codexModelsListImpl: async () => {
      calls += 1;
      return {
        authMethod: "chatgpt",
        models: [
          { id: "gpt-5.6-terra", displayName: "GPT-5.6-Terra", description: "Balanced.", hidden: false, isDefault: true },
          { id: "codex-auto-review", displayName: "Codex Auto Review", description: "Internal.", hidden: true, isDefault: false },
        ],
      };
    },
  });
  const result = await runtime.handleCapabilityInvoke(
    makeInvokeFrame({ runtime: "codex" }, { capability_id: LLM_MODELS_LIST_CAPABILITY }),
  );
  assert.equal(calls, 1);
  assert.equal(result.supported, true);
  assert.equal(result.auth_method, "chatgpt");
  const models = result.models as Array<Record<string, unknown>>;
  assert.equal(models.length, 2);
  assert.equal(models[0].id, "gpt-5.6-terra");
  assert.equal(models[0].is_default, true);
  assert.equal(models[1].hidden, true);
});

test("llm.models.list: a runtime with no verified live catalog gets an honest supported:false, never a guess", async () => {
  let calls = 0;
  const runtime = new GatewayLLMRuntime({
    codexModelsListImpl: async () => {
      calls += 1;
      return { authMethod: null, models: [] };
    },
  });
  for (const rt of ["claude_code", "grok_build", "cursor_cli", "ollama", ""]) {
    const result = await runtime.handleCapabilityInvoke(
      makeInvokeFrame({ runtime: rt }, { capability_id: LLM_MODELS_LIST_CAPABILITY }),
    );
    assert.equal(result.supported, false);
    assert.deepEqual(result.models, []);
  }
  // Never spawned codex for a runtime it can't answer for.
  assert.equal(calls, 0);
});

test("llm.models.list: a genuine CLI failure (not installed/authenticated/timeout) propagates as a real error, not a swallowed empty list", async () => {
  const runtime = new GatewayLLMRuntime({
    codexModelsListImpl: async () => {
      throw new CliRunError("not_authenticated", "Codex reported an authentication failure");
    },
  });
  await assert.rejects(
    () => runtime.handleCapabilityInvoke(makeInvokeFrame({ runtime: "codex" }, { capability_id: LLM_MODELS_LIST_CAPABILITY })),
    (err: unknown) => err instanceof CliRunError && err.kind === "not_authenticated",
  );
});

test("llm.models.list is advertised alongside llm.generate", () => {
  const runtime = new GatewayLLMRuntime();
  assert.ok(runtime.requestedCapabilities().includes(LLM_MODELS_LIST_CAPABILITY));
  assert.ok(runtime.supportsCapability(LLM_MODELS_LIST_CAPABILITY));
});
