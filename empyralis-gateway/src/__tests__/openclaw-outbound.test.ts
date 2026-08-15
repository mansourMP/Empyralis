import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { AddressInfo } from "node:net";
import { WebSocketServer, type WebSocket as WsSocket } from "ws";

import {
  OPENCLAW_CANCEL_PREDICATE_MARKERS,
  OPENCLAW_ERROR_CODES,
  OPENCLAW_MESSAGE_ACTION_METHOD,
  OPENCLAW_REQUIRED_SCOPES,
  classifyOpenClawSendResponse,
  mapOpenClawOutboundPayload,
  openClawChannelIdFromChannelKey,
  wouldBridgePluginCancel,
} from "../openclaw/outbound-payload";
import {
  OpenClawGatewayClient,
  assertLoopbackWebSocketUrl,
  redactOpenClawToken,
} from "../openclaw/openclaw-gateway-client";
import {
  OpenClawPersonalChannelRuntime,
  buildOpenClawPersonalChannelRuntimes,
} from "../openclaw/outbound-runtime";
import { openClawTransportChannelKeys } from "../openclaw/capabilities";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import type { GatewayChannelOutboundPayload, GatewayRequestEnvelope } from "../protocol/types";

function outboundFrame(
  overrides: Partial<GatewayChannelOutboundPayload> = {},
): GatewayRequestEnvelope<GatewayChannelOutboundPayload> {
  return {
    kind: "request",
    protocolVersion: 1,
    id: "req-1",
    type: "channel.outbound",
    ts: new Date().toISOString(),
    payload: {
      channel_key: "openclaw_line",
      provider: "openclaw",
      remote_jid: "C-999",
      text: "On it — I filed that as MAN-401.",
      idempotency_key: "openclaw_line:M-7",
      ...overrides,
    },
  } as unknown as GatewayRequestEnvelope<GatewayChannelOutboundPayload>;
}

// ── mapping ───────────────────────────────────────────────────────────────

test("openclaw channel key maps to the openclaw channel id, and only for this lane", () => {
  assert.equal(openClawChannelIdFromChannelKey("openclaw_feishu"), "feishu");
  assert.equal(openClawChannelIdFromChannelKey("openclaw_msteams"), "msteams");
  // A first-party key must never be reshaped into an OpenClaw channel id.
  assert.equal(openClawChannelIdFromChannelKey("telegram_personal"), undefined);
  assert.equal(openClawChannelIdFromChannelKey("signal_personal"), undefined);
  assert.equal(openClawChannelIdFromChannelKey("openclaw_"), undefined);
  assert.equal(openClawChannelIdFromChannelKey("openclaw_../etc"), undefined);
});

test("a well-formed outbound payload maps to message.action send params", () => {
  const mapped = mapOpenClawOutboundPayload(outboundFrame().payload);
  assert.equal(mapped.ok, true);
  if (!mapped.ok) return;
  assert.equal(mapped.request.channel, "line");
  assert.equal(mapped.request.action, "send");
  assert.equal(mapped.request.idempotencyKey, "openclaw_line:M-7");
  assert.equal(mapped.request.params.to, "C-999");
  assert.equal(mapped.request.params.message, "On it — I filed that as MAN-401.");
  // Never invent a reply target or a thread.
  assert.equal("replyTo" in mapped.request.params, false);
  assert.equal("threadId" in mapped.request.params, false);
  assert.deepEqual(mapped.droppedMedia, []);
});

test("replyTo and threadId are passed through only when the cloud supplied them", () => {
  const mapped = mapOpenClawOutboundPayload(
    outboundFrame({
      reply_to_external_message_id: "M-7",
      metadata: { thread_id: "42" },
    }).payload,
  );
  assert.equal(mapped.ok, true);
  if (!mapped.ok) return;
  assert.equal(mapped.request.params.replyTo, "M-7");
  assert.equal(mapped.request.params.threadId, "42");
});

test("mapping refuses, with a stable code, everything it cannot deliver honestly", () => {
  const cases: Array<[Partial<GatewayChannelOutboundPayload>, string]> = [
    [{ channel_key: "telegram_personal" }, "openclaw_outbound_channel_key_not_openclaw"],
    [{ provider: "signal_local_bridge" }, "openclaw_outbound_provider_mismatch"],
    [{ remote_jid: "  " }, "openclaw_outbound_target_missing"],
    [{ idempotency_key: "" }, "openclaw_outbound_idempotency_key_missing"],
    [{ text: "   " }, "openclaw_outbound_text_missing"],
    [{ operation: "draft_delta" }, "openclaw_outbound_draft_operation_unsupported"],
    [
      { text: "", media: [{ kind: "image", source_path: "/tmp/a.png", mime_type: "image/png" }] as never },
      "openclaw_outbound_media_only_unsupported",
    ],
  ];
  for (const [overrides, expected] of cases) {
    const mapped = mapOpenClawOutboundPayload(outboundFrame(overrides).payload);
    assert.equal(mapped.ok, false, `expected refusal for ${JSON.stringify(overrides)}`);
    if (mapped.ok) continue;
    assert.equal(mapped.error, expected);
  }
  assert.equal(mapOpenClawOutboundPayload(undefined).ok, false);
});

test("media alongside text is reported as dropped, never silently discarded", () => {
  const mapped = mapOpenClawOutboundPayload(
    outboundFrame({
      media: [{ kind: "image", source_path: "/tmp/a.png", mime_type: "image/png" }] as never,
    }).payload,
  );
  assert.equal(mapped.ok, true);
  if (!mapped.ok) return;
  assert.equal(mapped.droppedMedia.length, 1);
  assert.equal(mapped.request.params.message, "On it — I filed that as MAN-401.");
});

test("a send_final operation is accepted (it is the ordinary non-draft send)", () => {
  const mapped = mapOpenClawOutboundPayload(outboundFrame({ operation: "send_final" }).payload);
  assert.equal(mapped.ok, true);
});

// ── outcome classification ────────────────────────────────────────────────

test("classification reads the structured error code, never the sentence", () => {
  // Same prose, different codes -> different outcomes. This is the whole
  // point: CLAUDE.md's "stale string matching" failure mode cannot recur
  // here, because rewording the message changes nothing.
  const prose = "unsupported channel: line";
  assert.equal(
    classifyOpenClawSendResponse({ ok: false, error: { code: OPENCLAW_ERROR_CODES.INVALID_REQUEST, message: prose } }).status,
    "rejected",
  );
  assert.equal(
    classifyOpenClawSendResponse({ ok: false, error: { code: OPENCLAW_ERROR_CODES.UNAVAILABLE, message: prose } }).status,
    "transient",
  );
  // ...and rewording, with the code held constant, changes nothing.
  for (const message of ["unsupported channel: line", "Channel line is not configured", ""]) {
    assert.equal(
      classifyOpenClawSendResponse({ ok: false, error: { code: OPENCLAW_ERROR_CODES.INVALID_REQUEST, message } }).status,
      "rejected",
    );
  }
});

test("classification honours OpenClaw's explicit retryable flag over the code default", () => {
  const promoted = classifyOpenClawSendResponse({
    ok: false,
    error: { code: OPENCLAW_ERROR_CODES.INVALID_REQUEST, message: "x", retryable: true, retryAfterMs: 750 },
  });
  assert.equal(promoted.status, "transient");
  assert.equal(promoted.status === "transient" ? promoted.retryAfterMs : undefined, 750);

  const demoted = classifyOpenClawSendResponse({
    ok: false,
    error: { code: OPENCLAW_ERROR_CODES.UNAVAILABLE, message: "x", retryable: false },
  });
  assert.equal(demoted.status, "rejected");
});

test("an unknown or missing error code is permanent, not an infinite retry", () => {
  assert.equal(classifyOpenClawSendResponse({ ok: false, error: { code: "SOMETHING_NEW" } }).status, "rejected");
  assert.equal(classifyOpenClawSendResponse({ ok: false }).status, "rejected");
  assert.equal(classifyOpenClawSendResponse(undefined).status, "rejected");
});

test("only ok:true counts as delivery, and an external id is read when present", () => {
  assert.equal(classifyOpenClawSendResponse({ ok: true }).status, "delivered");
  const withId = classifyOpenClawSendResponse({ ok: true, payload: { messageId: "X-1" } });
  assert.equal(withId.status === "delivered" ? withId.externalMessageId : undefined, "X-1");
  const nested = classifyOpenClawSendResponse({ ok: true, payload: { result: { id: "X-2" } } });
  assert.equal(nested.status === "delivered" ? nested.externalMessageId : undefined, "X-2");
  // Never fabricate an id when the adapter did not return one.
  const none = classifyOpenClawSendResponse({ ok: true, payload: { delivered: true } });
  assert.equal(none.status === "delivered" ? none.externalMessageId : "unset", undefined);
});

// ── cancel-predicate collision ────────────────────────────────────────────

test("the bridge plugin's cancel markers still exist in its source (drift guard)", () => {
  // The two markers are duplicated in outbound-payload.ts because the plugin
  // is a separately compiled package loaded inside OpenClaw's process; there
  // is no sound import path. This reads the plugin's real source so a rename
  // there fails HERE instead of silently un-guarding the outbound path.
  const predicateSource = fs.readFileSync(
    path.resolve(__dirname, "../../openclaw-bridge-plugin/src/cancel-predicate.ts"),
    "utf8",
  );
  for (const marker of OPENCLAW_CANCEL_PREDICATE_MARKERS) {
    assert.ok(
      predicateSource.includes(`"${marker}"`),
      `cancel-predicate.ts no longer contains the marker ${JSON.stringify(marker)} — the outbound collision guard is now stale.`,
    );
  }
  // And it must still require BOTH together (an `&&`, not an `||`): an `||`
  // would make the predicate fire on ordinary text and start cancelling real
  // replies.
  assert.match(predicateSource, /FAILOVER_ERROR_MARKER\)\s*&&\s*content\.includes\(MISSING_API_KEY_MARKER\)/);
});

test("a real Empyralis reply is never mistaken for the suppressed failure reply", () => {
  const realReplies = [
    "On it — I filed that as MAN-401.",
    "That deploy failed. The error was: connection refused.",
    // Adversarial: mentions ONE marker, which must not be enough.
    "Your provider had an outage; No API key found for provider was in the logs.",
    "The stack trace mentions FailoverError but I have it handled.",
    "",
  ];
  for (const reply of realReplies) {
    assert.equal(wouldBridgePluginCancel(reply), false, `would have been cancelled: ${reply}`);
  }
  // The one thing that IS the suppressed reply: both markers together, the
  // exact shape OpenClaw's own formatter produces when no provider
  // credentials are configured.
  assert.equal(
    wouldBridgePluginCancel("FailoverError: No API key found for provider anthropic"),
    true,
  );
});

test("a reply that WOULD be cancelled is refused loudly instead of silently dropped", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  const runtime = new OpenClawPersonalChannelRuntime("line", stubClient({ ok: true }), async (type, payload) => {
    journal.push([type, payload]);
  });
  await assert.rejects(
    () =>
      runtime.handleChannelOutbound(
        outboundFrame({ text: "FailoverError: No API key found for provider anthropic" }),
      ),
    /suppression predicate/,
  );
  assert.ok(journal.some(([type, payload]) => type === "openclaw.outbound.rejected" && payload.reason === "openclaw_outbound_collides_with_cancel_predicate"));
});

// ── runtime ───────────────────────────────────────────────────────────────

type StubOutcome = { ok: true; externalMessageId?: string } | { ok: false; code: string; transient?: boolean };

function stubClient(outcome: { ok: boolean; externalMessageId?: string; code?: string; transient?: boolean }): OpenClawGatewayClient {
  const calls: unknown[] = [];
  const client = {
    calls,
    async start() {},
    async stop() {},
    isConnected: () => true,
    getState: () => ({ configured: true as const, connected: true, reconnectAttempts: 0 }),
    async sendMessageAction(request: unknown) {
      calls.push(request);
      if (outcome.ok) {
        return outcome.externalMessageId
          ? { status: "delivered" as const, externalMessageId: outcome.externalMessageId }
          : { status: "delivered" as const };
      }
      return {
        status: outcome.transient ? ("transient" as const) : ("rejected" as const),
        code: outcome.code ?? OPENCLAW_ERROR_CODES.INVALID_REQUEST,
        message: "unsupported channel: line",
      };
    },
  };
  return client as unknown as OpenClawGatewayClient;
}

test("runtimes are registered for exactly the advertised OpenClaw channel keys", () => {
  const runtimes = buildOpenClawPersonalChannelRuntimes(null);
  const advertised = openClawTransportChannelKeys();
  assert.equal(runtimes.length, advertised.length);
  for (const channelKey of advertised) {
    assert.ok(runtimes.some((runtime) => runtime.supportsChannel(channelKey)), `no runtime for ${channelKey}`);
  }
  // And never for a first-party channel.
  for (const runtime of runtimes) {
    assert.equal(runtime.supportsChannel("telegram_personal"), false);
    assert.equal(runtime.supportsChannel("signal_personal"), false);
  }
});

test("the registry GatewayWsClient consults actually resolves an OpenClaw channel", () => {
  // This is the exact lookup handleServerRequest performs for a
  // `channel.outbound` frame. Before this build it returned undefined and
  // every OpenClaw reply died as "Unsupported personal channel key".
  const registry = new PersonalChannelRuntimeRegistry([
    ...buildOpenClawPersonalChannelRuntimes(null),
  ]);
  for (const channelKey of openClawTransportChannelKeys()) {
    assert.ok(registry.runtimeForChannel(channelKey), `registry cannot route ${channelKey}`);
  }
  assert.equal(registry.runtimeForChannel("telegram_personal"), undefined);
  // The manifests the registry publishes are what
  // _assert_gateway_advertised_personal_channel reads on the cloud side.
  assert.equal(registry.channelManifests().length, openClawTransportChannelKeys().length);
});

test("index.ts actually wires the OpenClaw runtimes into the registry (zero-callers guard)", () => {
  // CLAUDE.md's #1 recurring defect is complete, correct, tested code with
  // no callers. Every other test in this file would pass with the builder
  // never invoked by the real process, so this one reads the real entry
  // point and asserts the wiring exists.
  const indexSource = fs.readFileSync(path.resolve(__dirname, "../../src/index.ts"), "utf8");
  assert.match(indexSource, /import \{ buildOpenClawPersonalChannelRuntimes \} from "\.\/openclaw\/outbound-runtime"/);
  assert.match(indexSource, /import \{ OpenClawGatewayClient \} from "\.\/openclaw\/openclaw-gateway-client"/);
  // Constructed, and constructed INSIDE the PersonalChannelRuntimeRegistry
  // argument list — a builder called into a variable nobody registers would
  // be exactly the defect this guards against.
  const registryStart = indexSource.indexOf("new PersonalChannelRuntimeRegistry([");
  assert.notEqual(registryStart, -1, "PersonalChannelRuntimeRegistry is no longer constructed from an array literal");
  const registryEnd = indexSource.indexOf("\n  ]);", registryStart);
  assert.notEqual(registryEnd, -1, "could not find the end of the registry construction");
  assert.match(
    indexSource.slice(registryStart, registryEnd),
    /buildOpenClawPersonalChannelRuntimes\(/,
  );
  // And the shared session is torn down exactly once, in cleanup.
  assert.match(indexSource, /openclawGatewayClient\?\.stop\(\)/);
});

test("the manifest declares the provider the cloud lane contract expects", () => {
  for (const runtime of buildOpenClawPersonalChannelRuntimes(null)) {
    const manifest = runtime.getManifest();
    // personal_channels_service asserts manifest provider == payload provider.
    assert.equal(manifest.provider, "openclaw");
    assert.equal(manifest.liveCapable, true);
    assert.equal(manifest.media.images, false);
  }
});

test("health never reports an inbound-blocking status while configured", () => {
  // _assert_gateway_advertised_personal_channel REJECTS inbound when health
  // status is one of these. Inbound does not travel over the outbound socket,
  // so an idle or reconnecting socket must never suppress it.
  const inboundBlocking = new Set(["offline", "error", "failed", "disconnected", "unavailable", "disabled"]);
  const disconnected = {
    async start() {},
    isConnected: () => false,
    getState: () => ({ configured: true as const, connected: false, reconnectAttempts: 4, lastError: "ECONNREFUSED" }),
  } as unknown as OpenClawGatewayClient;
  const runtime = new OpenClawPersonalChannelRuntime("line", disconnected);
  const snapshot = runtime.getHealthSnapshot();
  assert.equal(inboundBlocking.has(String(snapshot.status)), false);
  assert.equal(snapshot.connected, false);
  assert.equal(snapshot.reconnectAttempts, 4);
  assert.deepEqual(snapshot.issues, ["openclaw_gateway_session_not_established"]);

  // Unconfigured reports not_configured, which is also not inbound-blocking.
  const unconfigured = new OpenClawPersonalChannelRuntime("line", null).getHealthSnapshot();
  assert.equal(unconfigured.status, "not_configured");
  assert.equal(inboundBlocking.has(String(unconfigured.status)), false);
});

test("a delivered send returns the local-bridge-shaped result and journals it", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  const runtime = new OpenClawPersonalChannelRuntime("line", stubClient({ ok: true, externalMessageId: "X-9" }), async (type, payload) => {
    journal.push([type, payload]);
  });
  const result = await runtime.handleChannelOutbound(outboundFrame());
  assert.equal(result.delivered, true);
  assert.equal(result.channel_key, "openclaw_line");
  assert.equal(result.provider, "openclaw");
  assert.equal(result.external_message_id, "X-9");
  assert.equal(result.status, "sent");
  assert.ok(journal.some(([type]) => type === "openclaw.outbound.delivered"));
});

test("a refused send throws (so the cloud never records it as delivered) and journals the code", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  const runtime = new OpenClawPersonalChannelRuntime(
    "line",
    stubClient({ ok: false, code: OPENCLAW_ERROR_CODES.INVALID_REQUEST }),
    async (type, payload) => {
      journal.push([type, payload]);
    },
  );
  await assert.rejects(() => runtime.handleChannelOutbound(outboundFrame()), /INVALID_REQUEST/);
  const failure = journal.find(([type]) => type === "openclaw.outbound.failed");
  assert.ok(failure);
  assert.equal(failure?.[1].code, OPENCLAW_ERROR_CODES.INVALID_REQUEST);
  assert.equal(failure?.[1].outcome, "rejected");
});

test("an unconfigured outbound leg fails with a named reason, not a generic unsupported-channel error", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  const runtime = new OpenClawPersonalChannelRuntime("line", null, async (type, payload) => {
    journal.push([type, payload]);
  });
  await assert.rejects(
    () => runtime.handleChannelOutbound(outboundFrame()),
    /EMPYRALIS_OPENCLAW_GATEWAY_TOKEN/,
  );
  assert.ok(journal.some(([type]) => type === "openclaw.outbound.not_configured"));
});

test("media alongside text is journaled and surfaced on the dispatch result", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  const runtime = new OpenClawPersonalChannelRuntime("line", stubClient({ ok: true }), async (type, payload) => {
    journal.push([type, payload]);
  });
  const result = await runtime.handleChannelOutbound(
    outboundFrame({ media: [{ kind: "image", source_path: "/tmp/a.png", mime_type: "image/png" }] as never }),
  );
  assert.equal(result.media_dropped, 1);
  assert.ok(journal.some(([type]) => type === "openclaw.outbound.media_dropped"));
});

// ── client: security posture ──────────────────────────────────────────────

test("the OpenClaw gateway URL must be loopback, with no escape hatch", () => {
  assert.doesNotThrow(() => assertLoopbackWebSocketUrl("ws://127.0.0.1:18789"));
  assert.doesNotThrow(() => assertLoopbackWebSocketUrl("ws://localhost:18789"));
  for (const url of ["ws://10.0.0.5:18789", "ws://gateway.example.com:18789", "wss://1.2.3.4:443", "http://127.0.0.1:18789"]) {
    assert.throws(() => assertLoopbackWebSocketUrl(url), /loopback|ws:\/\//);
  }
});

test("the client refuses to exist without a token", () => {
  assert.throws(
    () => new OpenClawGatewayClient({ url: "ws://127.0.0.1:18789", token: "   " }),
    /without a gateway auth token/,
  );
});

test("the token is stripped from anything that could be logged", () => {
  assert.equal(redactOpenClawToken("connect failed for token s3cr3t", "s3cr3t"), "connect failed for token <redacted>");
  assert.equal(redactOpenClawToken("nothing here", ""), "nothing here");
});

// ── client: real wire format against a fake OpenClaw gateway ──────────────

interface FakeGateway {
  url: string;
  close: () => Promise<void>;
  connectParams: () => Record<string, unknown> | null;
  messageActionParams: () => Record<string, unknown> | null;
}

async function startFakeOpenClawGateway(options: {
  expectedToken: string;
  respond: (params: Record<string, unknown>) => Record<string, unknown>;
}): Promise<FakeGateway> {
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  let connectParams: Record<string, unknown> | null = null;
  let messageActionParams: Record<string, unknown> | null = null;

  server.on("connection", (socket: WsSocket) => {
    // Exactly OpenClaw's own opening move (server-ws-runtime-*.js).
    socket.send(JSON.stringify({ type: "event", event: "connect.challenge", payload: { nonce: "nonce-1", ts: Date.now() } }));
    socket.on("message", (raw) => {
      const frame = JSON.parse(String(raw)) as { type?: string; id?: string; method?: string; params?: Record<string, unknown> };
      if (frame.type !== "req") return;
      if (frame.method === "connect") {
        connectParams = frame.params ?? {};
        const auth = (connectParams.auth || {}) as Record<string, unknown>;
        if (String(auth.token || "") !== options.expectedToken) {
          socket.send(JSON.stringify({ type: "res", id: frame.id, ok: false, error: { code: "INVALID_REQUEST", message: "unauthorized" } }));
          return;
        }
        socket.send(
          JSON.stringify({
            type: "res",
            id: frame.id,
            ok: true,
            payload: {
              type: "hello-ok",
              protocol: 4,
              features: { methods: ["message.action", "send"], events: [] },
              auth: { role: "operator", scopes: ["operator.write"] },
              policy: { maxPayload: 1, maxBufferedBytes: 1, tickIntervalMs: 30000 },
            },
          }),
        );
        return;
      }
      if (frame.method === OPENCLAW_MESSAGE_ACTION_METHOD) {
        messageActionParams = frame.params ?? {};
        socket.send(JSON.stringify({ type: "res", id: frame.id, ...options.respond(frame.params ?? {}) }));
      }
    });
  });

  await new Promise<void>((resolve) => server.once("listening", () => resolve()));
  const port = (server.address() as AddressInfo).port;
  return {
    url: `ws://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
    connectParams: () => connectParams,
    messageActionParams: () => messageActionParams,
  };
}

test("the client completes OpenClaw's challenge/connect handshake and invokes message.action", async () => {
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: () => ({ ok: true, payload: { messageId: "line-1" } }),
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "openclaw-token" });
  try {
    await client.start();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-1",
    });
    assert.equal(outcome.status, "delivered");
    assert.equal(outcome.status === "delivered" ? outcome.externalMessageId : undefined, "line-1");

    const connect = gateway.connectParams();
    assert.ok(connect, "connect frame was never sent");
    assert.equal(connect?.minProtocol, 4);
    assert.equal(connect?.role, "operator");
    // Least privilege: operator.write only. operator.admin is the scope
    // under which OpenClaw would honour a client-asserted senderIsOwner.
    assert.deepEqual(connect?.scopes, [...OPENCLAW_REQUIRED_SCOPES]);
    assert.equal((connect?.scopes as string[]).includes("operator.admin"), false);
    // No device-identity block: this client authenticates with the token.
    assert.equal("device" in (connect as Record<string, unknown>), false);

    const action = gateway.messageActionParams();
    assert.equal(action?.channel, "line");
    assert.equal(action?.action, "send");
    assert.equal(action?.idempotencyKey, "k-1");
    assert.deepEqual(action?.params, { to: "C-999", message: "hello" });
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("a permanent rejection from message.action is returned structurally and never retried", async () => {
  let attempts = 0;
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: () => {
      attempts += 1;
      return { ok: false, error: { code: "INVALID_REQUEST", message: "unsupported channel: line" } };
    },
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "openclaw-token" });
  try {
    await client.start();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-2",
    });
    assert.equal(outcome.status, "rejected");
    assert.equal(outcome.status === "rejected" ? outcome.code : "", "INVALID_REQUEST");
    assert.equal(attempts, 1, "a permanent rejection must not be retried");
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("a transient failure is retried under the SAME idempotency key, then succeeds", async () => {
  const seenKeys: string[] = [];
  let attempts = 0;
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: (params) => {
      attempts += 1;
      seenKeys.push(String(params.idempotencyKey || ""));
      if (attempts === 1) {
        return { ok: false, error: { code: "UNAVAILABLE", message: "adapter threw", retryAfterMs: 10 } };
      }
      return { ok: true, payload: { messageId: "line-2" } };
    },
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "openclaw-token" });
  try {
    await client.start();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-3",
    });
    assert.equal(outcome.status, "delivered");
    assert.equal(attempts, 2);
    // Identical key on every attempt is the ONLY thing that makes the retry
    // safe — OpenClaw dedupes on it (resolveGatewayInflightRequest).
    assert.deepEqual(seenKeys, ["k-3", "k-3"]);
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("a rejected token never yields a session, and a send reports it rather than hanging", async () => {
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "the-right-token",
    respond: () => ({ ok: true }),
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "the-wrong-token" });
  try {
    await client.start();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-4",
    });
    assert.equal(outcome.status, "transient");
    assert.equal(outcome.status === "transient" ? outcome.code : "", "OPENCLAW_GATEWAY_DISCONNECTED");
    assert.equal(client.isConnected(), false);
  } finally {
    await client.stop();
    await gateway.close();
  }
});

// ── client: a server-supplied backoff is a FLOOR, never a ceiling ─────────
//
// These four cover the one thing this transport can do to make a platform
// rate-limit WORSE: retry earlier than OpenClaw told us to. Everything else
// on the outbound path (chunking, per-channel throttling, the plugin's own
// retry-after handling) happens INSIDE OpenClaw and is inherited identically
// by `message.action` and by their own agent's reply — verified against the
// pinned v2026.6.10 bundle, see openclaw-gateway-client.ts's waitBeforeRetry.
// The retry interval is the only pacing lever this seam actually owns, so it
// is the only one asserted here.

test("a retryAfterMs longer than one in-band wait STOPS the retry instead of retrying early", async () => {
  const journal: Array<[string, Record<string, unknown>]> = [];
  let attempts = 0;
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: () => {
      attempts += 1;
      // 30s is the shape a real platform flood-wait takes. The old code
      // clamped this to 2s and tried again — twice.
      return { ok: false, error: { code: "UNAVAILABLE", message: "flood wait", retryAfterMs: 30_000 } };
    },
  });
  const client = new OpenClawGatewayClient({
    url: gateway.url,
    token: "openclaw-token",
    record: async (type, payload) => {
      journal.push([type, payload]);
    },
  });
  try {
    await client.start();
    const startedAt = Date.now();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-backoff-long",
    });
    const elapsed = Date.now() - startedAt;

    assert.equal(outcome.status, "transient");
    // EXACTLY one attempt. A count assertion, not "a failure happened": the
    // old clamp also produced a transient outcome, just after three sends.
    assert.equal(attempts, 1, "a backoff longer than the budget must not be retried at all");
    // And it must not have slept the 30s either — refusing is cheaper than
    // waiting, and the cloud's at-least-once layer owns the real wait.
    assert.ok(elapsed < 5_000, `expected an immediate return, took ${elapsed}ms`);
    // The number OpenClaw asked for survives to the caller and the journal,
    // so "briefly busy" and "rate-limited" stay distinguishable downstream.
    assert.equal(outcome.status === "transient" ? outcome.retryAfterMs : undefined, 30_000);
    const deferred = journal.find(([type]) => type === "openclaw.outbound.retry_deferred");
    assert.ok(deferred, "the refusal to retry must be journaled, never silent");
    assert.equal(deferred?.[1].retry_after_ms, 30_000);
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("a retryAfterMs within budget is honoured as a FLOOR, not shortened to our own backoff", async () => {
  let attempts = 0;
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: () => {
      attempts += 1;
      // 1200ms is longer than our own first backoff (400ms) and, critically,
      // longer than the 2000ms ceiling would have mattered for — it is here
      // to prove the wait is driven by THEIR number, not ours.
      if (attempts === 1) return { ok: false, error: { code: "UNAVAILABLE", message: "busy", retryAfterMs: 1_200 } };
      return { ok: true, payload: { messageId: "line-9" } };
    },
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "openclaw-token" });
  try {
    await client.start();
    const startedAt = Date.now();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-backoff-floor",
    });
    const elapsed = Date.now() - startedAt;
    assert.equal(outcome.status, "delivered");
    assert.equal(attempts, 2);
    // Allow a small scheduling slack below the asked-for figure; the point is
    // that it is nowhere near our own 400ms first backoff.
    assert.ok(elapsed >= 1_100, `expected to wait the asked-for 1200ms, waited ${elapsed}ms`);
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("with no retryAfterMs the client still uses its own capped backoff and all attempts", async () => {
  let attempts = 0;
  const gateway = await startFakeOpenClawGateway({
    expectedToken: "openclaw-token",
    respond: () => {
      attempts += 1;
      // OpenClaw's real shape for anything the send path throws:
      // errorShape(ErrorCodes.UNAVAILABLE, String(err)) — no `retryable`,
      // no `retryAfterMs` (dist/send-BMn-S3XR.js,
      // createGatewayInflightUnavailableFailure).
      return { ok: false, error: { code: "UNAVAILABLE", message: "adapter threw" } };
    },
  });
  const client = new OpenClawGatewayClient({ url: gateway.url, token: "openclaw-token" });
  try {
    await client.start();
    const outcome = await client.sendMessageAction({
      channel: "line",
      action: "send",
      params: { to: "C-999", message: "hello" },
      idempotencyKey: "k-backoff-none",
    });
    assert.equal(outcome.status, "transient");
    assert.equal(attempts, 3, "an unqualified transient still gets the bounded self-backoff retry");
  } finally {
    await client.stop();
    await gateway.close();
  }
});

test("the client source contains no downward clamp of a server-supplied backoff", () => {
  // A behavioural test can only cover the shapes that exist today, and the
  // defect being guarded is a one-token change (`Math.min` in place of
  // `Math.max`) that type-checks, passes every existing assertion, and is
  // silent in production. So the shape itself is banned in source — the same
  // reason __tests__/exec-file-timeout-child-leak.test.ts bans a raw option.
  const source = fs.readFileSync(
    // Tests run from dist/__tests__, so ../../ is the package root — same
    // shape as the cancel-predicate drift assertion above.
    path.resolve(__dirname, "../../src/openclaw/openclaw-gateway-client.ts"),
    "utf8",
  );
  const body = source
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("*") && !line.trimStart().startsWith("//"))
    .join("\n");
  assert.equal(
    /Math\.min\([^)]*retryAfterMs/.test(body),
    false,
    "retryAfterMs must never be an argument to Math.min — a server-supplied backoff is a floor, never a ceiling",
  );
  assert.ok(
    /Math\.max\(\s*requested\s*\?\?\s*0\s*,\s*selfBackoff\s*\)/.test(body),
    "the retry wait must be the MAXIMUM of the asked-for backoff and our own",
  );
});

// ---------------------------------------------------------------------------
// The destination param name is OpenClaw's, not ours.
//
// `target` was sent for months. It type-checked, every unit test above
// asserted it, and it failed only against the live binary — three refusals
// per reply and then a drop:
//
//   openclaw.outbound.attempt_failed  code=UNAVAILABLE
//                                     detail="ToolInputError: to required"
//
// The turn had run, the model had answered, the answer was persisted, and
// the person got silence. `target` is a real param name elsewhere in their
// action surface, which is why it looked right.
//
// Pinned here against their own resolution order, read out of
// dist/message-action-runner-*.js:
//
//   readStringParam(actionParams, "to") ?? readStringParam(actionParams, "channelId")
//
// A unit test cannot reach the binary, so this asserts the SHAPE the binary
// requires and says where that requirement was read from. If OpenClaw ever
// renames it, this fails loudly instead of a customer's reply vanishing.
test("the send action addresses with `to` — never `target`, which OpenClaw's send never reads", () => {
  const mapped = mapOpenClawOutboundPayload(outboundFrame().payload);
  assert.equal(mapped.ok, true);
  if (!mapped.ok) return;
  assert.equal(mapped.request.params.to, "C-999");
  assert.equal("target" in mapped.request.params, false, "`target` is not read by OpenClaw's send action");
});
