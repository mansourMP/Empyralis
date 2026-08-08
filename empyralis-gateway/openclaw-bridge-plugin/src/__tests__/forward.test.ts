import { test } from "node:test";
import assert from "node:assert/strict";

import { forwardInboundEvent, ForwardError } from "../forward.js";
import type { BridgeConfig } from "../config.js";
import type { EmpyralisInboundPayload } from "../inbound-mapping.js";

function baseConfig(overrides: Partial<BridgeConfig> = {}): BridgeConfig {
  return {
    endpointUrl: "http://127.0.0.1:8790/openclaw/inbound",
    token: "test-token",
    timeoutMs: 1000,
    queueFilePath: "/tmp/unused.json",
    queueMaxItems: 500,
    queueMaxAttempts: 8,
    queueMinBackoffMs: 2000,
    queueMaxBackoffMs: 300000,
    queueFlushIntervalMs: 3000,
    ...overrides,
  };
}

function samplePayload(): EmpyralisInboundPayload {
  return {
    schema: "empyralis.openclaw_bridge.inbound.v1",
    channel: "telegram",
    accountId: "a1",
    conversationId: "c1",
    senderId: "s1",
    messageId: "m1",
    content: "hi",
    timestamp: undefined,
    threadId: undefined,
    isGroup: undefined,
    wasMentioned: undefined,
    isReply: false,
    replyToId: undefined,
    replyToSender: undefined,
    media: { path: undefined, url: undefined, type: undefined, paths: undefined, urls: undefined, types: undefined },
    rawMetadata: undefined,
    openclawSessionKey: undefined,
    openclawRunId: undefined,
    receivedAt: "2026-08-08T00:00:00.000Z",
  };
}

test("forwardInboundEvent sends a bearer token and never the raw payload text in a header", async () => {
  let capturedUrl: string | undefined;
  let capturedInit: RequestInit | undefined;
  const fakeFetch = (async (url: string | URL, init?: RequestInit) => {
    capturedUrl = String(url);
    capturedInit = init;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }) as typeof fetch;

  await forwardInboundEvent(baseConfig(), samplePayload(), fakeFetch);

  assert.equal(capturedUrl, "http://127.0.0.1:8790/openclaw/inbound");
  const headers = capturedInit?.headers as Record<string, string>;
  assert.equal(headers.authorization, "Bearer test-token");
  assert.equal(headers["content-type"], "application/json");
  const body = JSON.parse(String(capturedInit?.body));
  assert.equal(body.channel, "telegram");
});

test("forwardInboundEvent omits the authorization header when no token is configured", async () => {
  let capturedInit: RequestInit | undefined;
  const fakeFetch = (async (_url: string | URL, init?: RequestInit) => {
    capturedInit = init;
    return new Response("{}", { status: 200 });
  }) as typeof fetch;

  await forwardInboundEvent(baseConfig({ token: undefined }), samplePayload(), fakeFetch);
  const headers = capturedInit?.headers as Record<string, string>;
  assert.equal(headers.authorization, undefined);
});

test("forwardInboundEvent throws ForwardError on a non-2xx response", async () => {
  const fakeFetch = (async () => new Response("nope", { status: 503 })) as typeof fetch;
  await assert.rejects(
    () => forwardInboundEvent(baseConfig(), samplePayload(), fakeFetch),
    (err: unknown) => err instanceof ForwardError && err.status === 503,
  );
});

test("forwardInboundEvent aborts after the configured timeout", async () => {
  const fakeFetch = (async (_url: string | URL, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
  }) as typeof fetch;

  await assert.rejects(() => forwardInboundEvent(baseConfig({ timeoutMs: 20 }), samplePayload(), fakeFetch));
});
