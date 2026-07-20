import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  ImsgRpcClient,
  buildImsgSendParams,
  mapImsgMessageToBridgeEvent,
  normalizeImsgFullDiskAccessError,
  parseImsgTarget,
  probeImsgIMessage,
  type ImsgChildProcessLike,
  type ImsgExecImpl,
  type ImsgSpawnImpl,
} from "../bridges/imsg-imessage-client";

// ---------------------------------------------------------------------
// Fake `imsg rpc --json` child process — same "inject a structural double,
// no mocking library" pattern llm/cli-runner.test.ts uses for the Claude
// Code/Codex CLI spawn, extended with a captured stdin (imsg rpc is a
// long-lived process the client writes JSON-RPC request lines INTO, not a
// one-shot run).
// ---------------------------------------------------------------------

interface FakeImsgChild {
  child: ImsgChildProcessLike;
  writtenLines: string[];
  emitStdoutLine: (payload: unknown) => void;
  emitStderrLine: (line: string) => void;
  emitClose: (code: number | null, signal: NodeJS.Signals | null) => void;
  emitError: (err: NodeJS.ErrnoException) => void;
  killCalls: (NodeJS.Signals | undefined)[];
}

function makeFakeImsgChild(): FakeImsgChild {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const stdin = new EventEmitter();
  const proc = new EventEmitter();
  const writtenLines: string[] = [];
  const killCalls: (NodeJS.Signals | undefined)[] = [];
  const fake: FakeImsgChild = {
    child: {
      stdin: {
        write: (chunk: string, callback?: (err?: Error | null) => void) => {
          writtenLines.push(chunk);
          callback?.();
          return true;
        },
        end: () => undefined,
        on: (event: string, listener: (...args: unknown[]) => void) => stdin.on(event, listener),
      },
      stdout: {
        on: (event: string, listener: (...args: unknown[]) => void) => stdout.on(event, listener),
      },
      stderr: {
        on: (event: string, listener: (...args: unknown[]) => void) => stderr.on(event, listener),
      },
      on: (event: string, listener: (...args: unknown[]) => void) => proc.on(event, listener),
      kill: (signal?: NodeJS.Signals) => {
        killCalls.push(signal);
        return true;
      },
      killed: false,
    } as unknown as ImsgChildProcessLike,
    writtenLines,
    emitStdoutLine: (payload) => stdout.emit("data", `${JSON.stringify(payload)}\n`),
    emitStderrLine: (line) => stderr.emit("data", `${line}\n`),
    emitClose: (code, signal) => proc.emit("close", code, signal),
    emitError: (err) => proc.emit("error", err),
    killCalls,
  };
  return fake;
}

function spawnImplReturning(fake: FakeImsgChild): ImsgSpawnImpl {
  return () => fake.child;
}

function lastRequest(fake: FakeImsgChild): { id: number; method: string; params: unknown } {
  const line = fake.writtenLines.at(-1);
  assert.ok(line, "expected a request line to have been written to stdin");
  return JSON.parse(line!) as { id: number; method: string; params: unknown };
}

// ---- ImsgRpcClient: request/response framing --------------------------

test("ImsgRpcClient: writes a JSON-RPC 2.0 request line and resolves on the matching response", async () => {
  const fake = makeFakeImsgChild();
  const client = new ImsgRpcClient({ cliPath: "imsg", spawnImpl: spawnImplReturning(fake) });
  await client.start();

  const pending = client.request<{ subscription: number }>("watch.subscribe", {
    attachments: false,
    include_reactions: true,
  });
  const req = lastRequest(fake);
  assert.equal(req.method, "watch.subscribe");
  assert.deepEqual(req.params, { attachments: false, include_reactions: true });

  fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, result: { subscription: 7 } });
  const result = await pending;
  assert.equal(result.subscription, 7);
  await client.stop();
});

test("ImsgRpcClient: rejects the pending request on an RPC error response", async () => {
  const fake = makeFakeImsgChild();
  const client = new ImsgRpcClient({ cliPath: "imsg", spawnImpl: spawnImplReturning(fake) });
  await client.start();

  const pending = client.request("chats.list", { limit: 1 });
  const req = lastRequest(fake);
  fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, error: { code: -32000, message: "not signed in" } });
  await assert.rejects(pending, /not signed in/);
  await client.stop();
});

test("ImsgRpcClient: dispatches a method-only line (no id) as a notification", async () => {
  const fake = makeFakeImsgChild();
  const notifications: unknown[] = [];
  const client = new ImsgRpcClient({
    cliPath: "imsg",
    spawnImpl: spawnImplReturning(fake),
    onNotification: (msg) => notifications.push(msg),
  });
  await client.start();

  fake.emitStdoutLine({
    method: "message",
    params: { id: 101, guid: "imsg-in-1", text: "hi", sender: "+15551234567", chat_guid: "iMessage;-;+15551234567" },
  });

  assert.equal(notifications.length, 1);
  assert.equal((notifications[0] as { method: string }).method, "message");
  await client.stop();
});

test("ImsgRpcClient: surfaces the Full Disk Access diagnostic instead of a generic close error", async () => {
  const fake = makeFakeImsgChild();
  const client = new ImsgRpcClient({ cliPath: "imsg", spawnImpl: spawnImplReturning(fake) });
  await client.start();
  const pending = client.request("chats.list", {});
  fake.emitStderrLine("Error: Full Disk Access is required to read chat.db");
  fake.emitClose(1, null);
  await assert.rejects(pending, /Full Disk Access/);
});

test("ImsgRpcClient: a request issued after the child errors out (e.g. ENOENT) fails fast, not by hanging", async () => {
  const fake = makeFakeImsgChild();
  const client = new ImsgRpcClient({ cliPath: "imsg", spawnImpl: spawnImplReturning(fake) });
  await client.start();
  fake.emitError(Object.assign(new Error("spawn imsg ENOENT"), { code: "ENOENT" }));
  await assert.rejects(client.request("chats.list", {}), /imsg rpc not running/);
});

// ---- Full Disk Access diagnostic normalization -------------------------

test("normalizeImsgFullDiskAccessError only matches lines naming both Full Disk Access and chat.db", () => {
  assert.ok(normalizeImsgFullDiskAccessError("Full Disk Access is required to read chat.db"));
  assert.equal(normalizeImsgFullDiskAccessError("some unrelated error"), undefined);
  assert.equal(normalizeImsgFullDiskAccessError("Full Disk Access required"), undefined);
});

// ---- Target parsing / send params ---------------------------------------

test("parseImsgTarget recognizes the chat_guid/chat_identifier/chat_id prefixes and falls back to a bare handle", () => {
  assert.deepEqual(parseImsgTarget("chat_guid:iMessage;-;+15551234567"), {
    kind: "chat_guid",
    chatGuid: "iMessage;-;+15551234567",
  });
  assert.deepEqual(parseImsgTarget("chat_identifier:+15551234567"), {
    kind: "chat_identifier",
    chatIdentifier: "+15551234567",
  });
  assert.deepEqual(parseImsgTarget("chat_id:42"), { kind: "chat_id", chatId: 42 });
  assert.deepEqual(parseImsgTarget("+15551234567"), { kind: "handle", to: "+15551234567" });
});

test("buildImsgSendParams builds the send RPC params for a chat_guid target with a reply", () => {
  const params = buildImsgSendParams("chat_guid:iMessage;-;+15551234567", "hello", { replyTo: "imsg-out-1" });
  assert.equal(params.text, "hello");
  assert.equal(params.service, "auto");
  assert.equal(params.chat_guid, "iMessage;-;+15551234567");
  assert.equal(params.reply_to, "imsg-out-1");
  assert.equal(params.chat_id, undefined);
  assert.equal(params.to, undefined);
});

test("buildImsgSendParams builds a bare-handle target for a fresh DM", () => {
  const params = buildImsgSendParams("+15551234567", "hi there");
  assert.equal(params.to, "+15551234567");
  assert.equal(params.chat_guid, undefined);
});

// ---- Inbound mapping: mapImsgMessageToBridgeEvent -----------------------

test("mapImsgMessageToBridgeEvent maps a DM into the shared BridgeEvent shape", () => {
  const event = mapImsgMessageToBridgeEvent({
    id: 501,
    guid: "imsg-in-1",
    text: "hello from imsg",
    sender: "+15551234567",
    chat_guid: "iMessage;-;+15551234567",
    is_from_me: false,
    is_group: false,
    created_at: "2026-07-20T12:00:00.000Z",
  });
  assert.ok(event);
  assert.equal(event?.external_message_id, "imsg-in-1");
  assert.equal(event?.remote_jid, "chat_guid:iMessage;-;+15551234567");
  assert.equal(event?.sender_jid, "+15551234567");
  assert.equal(event?.text, "hello from imsg");
  assert.equal(event?.from_me, false);
  assert.equal(event?.is_group, false);
  assert.equal(event?.is_mentioned, false);
});

test("mapImsgMessageToBridgeEvent falls back to chat_id, then chat_identifier, then sender for remote_jid", () => {
  assert.equal(
    mapImsgMessageToBridgeEvent({ guid: "g1", text: "hi", chat_id: 9 })?.remote_jid,
    "chat_id:9",
  );
  assert.equal(
    mapImsgMessageToBridgeEvent({ guid: "g2", text: "hi", chat_identifier: "+15550001111" })?.remote_jid,
    "chat_identifier:+15550001111",
  );
  assert.equal(
    mapImsgMessageToBridgeEvent({ guid: "g3", text: "hi", sender: "+15550002222" })?.remote_jid,
    "+15550002222",
  );
});

test("mapImsgMessageToBridgeEvent emits the shared <media:attachment> (N) placeholder for a caption-less attachment", () => {
  const event = mapImsgMessageToBridgeEvent({
    guid: "imsg-in-2",
    text: null,
    sender: "+15551234567",
    attachments: [
      { original_path: "/tmp/photo.heic", mime_type: "image/heic" },
      { original_path: "/tmp/missing.heic", missing: true },
    ],
  });
  assert.equal(event?.text, "<media:attachment> (1)");
});

test("mapImsgMessageToBridgeEvent drops a bare tapback/reaction row (no deliverable text)", () => {
  assert.equal(
    mapImsgMessageToBridgeEvent({ guid: "imsg-tapback-1", text: "￼", is_reaction: true, is_tapback: true }),
    null,
  );
});

test("mapImsgMessageToBridgeEvent drops a message with no text and no attachments", () => {
  assert.equal(mapImsgMessageToBridgeEvent({ guid: "imsg-empty-1", sender: "+15551234567" }), null);
});

test("mapImsgMessageToBridgeEvent group gate inputs: is_reply_to_sage is true only when reply_to_id matches a tracked sent id", () => {
  const sentMessageIds = new Set(["imsg-out-1"]);
  const replyEvent = mapImsgMessageToBridgeEvent(
    {
      guid: "imsg-group-in-1",
      text: "sounds good",
      sender: "+15551234567",
      chat_guid: "iMessage;+;chat-family",
      is_group: true,
      reply_to_id: "imsg-out-1",
    },
    { sentMessageIds },
  );
  assert.equal(replyEvent?.is_reply_to_sage, true);

  const unrelatedEvent = mapImsgMessageToBridgeEvent(
    {
      guid: "imsg-group-in-2",
      text: "what time",
      sender: "+15551234567",
      chat_guid: "iMessage;+;chat-family",
      is_group: true,
      reply_to_id: "imsg-out-999",
    },
    { sentMessageIds },
  );
  assert.equal(unrelatedEvent?.is_reply_to_sage, false);
});

// ---- Layered health probe ------------------------------------------------

function execImplReturning(
  byArgsPrefix: Record<string, { code: number; stdout: string; stderr: string }>,
): ImsgExecImpl {
  return async (_command, args) => {
    const key = args.join(" ");
    const match = Object.entries(byArgsPrefix).find(([prefix]) => key.startsWith(prefix));
    if (!match) {
      throw new Error(`unexpected exec args: ${key}`);
    }
    return match[1];
  };
}

test("probeImsgIMessage: binary missing (ENOENT) reports the binary stage", async () => {
  const execImpl: ImsgExecImpl = async () => {
    const err = Object.assign(new Error("spawn imsg ENOENT"), { code: "ENOENT" });
    throw err;
  };
  const result = await probeImsgIMessage("imsg", { execImpl });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "binary");
  assert.match(result.error || "", /imsg not found/);
});

test("probeImsgIMessage: rpc subcommand unsupported reports the rpc stage", async () => {
  const execImpl = execImplReturning({
    "rpc --help": { code: 1, stdout: "", stderr: "unknown command \"rpc\"" },
  });
  const result = await probeImsgIMessage("imsg", { execImpl });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "rpc");
});

test("probeImsgIMessage: rpc supported but chats.list fails (e.g. no Full Disk Access) reports the chats stage", async () => {
  const execImpl = execImplReturning({
    "rpc --help": { code: 0, stdout: "usage: imsg rpc", stderr: "" },
    "status --json": { code: 0, stdout: JSON.stringify({ advanced_features: false, v2_ready: false }), stderr: "" },
  });
  const fake = makeFakeImsgChild();
  const spawnImpl = spawnImplReturning(fake);
  const resultPromise = probeImsgIMessage("imsg", { execImpl, spawnImpl });
  // Let the probe's internal chats.list request land, then fail it.
  await new Promise((resolve) => setImmediate(resolve));
  const req = lastRequest(fake);
  assert.equal(req.method, "chats.list");
  fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, error: { message: "Full Disk Access is required (chat.db)" } });
  const result = await resultPromise;
  assert.equal(result.ok, false);
  assert.equal(result.stage, "chats");
  assert.equal(result.privateApiAvailable, false);
});

test("probeImsgIMessage: all stages pass reports ok with the private-API flag", async () => {
  const execImpl = execImplReturning({
    "rpc --help": { code: 0, stdout: "usage: imsg rpc", stderr: "" },
    "status --json": { code: 0, stdout: JSON.stringify({ advanced_features: true, v2_ready: true }), stderr: "" },
  });
  const fake = makeFakeImsgChild();
  const spawnImpl = spawnImplReturning(fake);
  const resultPromise = probeImsgIMessage("imsg", { execImpl, spawnImpl });
  await new Promise((resolve) => setImmediate(resolve));
  const req = lastRequest(fake);
  fake.emitStdoutLine({ jsonrpc: "2.0", id: req.id, result: { chats: [] } });
  const result = await resultPromise;
  assert.equal(result.ok, true);
  assert.equal(result.privateApiAvailable, true);
});
