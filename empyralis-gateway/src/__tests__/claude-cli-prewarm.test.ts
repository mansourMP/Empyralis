import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import type { spawn as nodeSpawn } from "node:child_process";

import { ClaudeCliPrewarmPool, type ClaudePrewarmParams } from "../llm/claude-cli-prewarm";

// This suite exists to prove ONE property before this pool is ever enabled in
// production: a "warm" process is NEVER handed a second turn. Claude's
// stream-json protocol has no in-process context reset — a second stdin
// message continues the SAME conversation — so if the pool ever reused a
// process across two logical turns, one caller's turn could land on a
// process still holding a DIFFERENT conversation's content. See
// claude-cli-prewarm.ts's file header and docs/PLATFORM-MAP.md §26.5.

interface FakeChild {
  stdinWrites: string[];
  killed: (NodeJS.Signals | undefined)[];
  handle: unknown;
}

interface CreatedProcess {
  fake: FakeChild;
  stdout: PassThrough;
  args: string[];
}

function makeSpawnFake(): { spawnImpl: typeof nodeSpawn; created: CreatedProcess[] } {
  const created: CreatedProcess[] = [];
  const spawnImpl = ((command: string, args: string[]) => {
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const proc = new EventEmitter();
    const stdinWrites: string[] = [];
    const killed: (NodeJS.Signals | undefined)[] = [];
    const handle = {
      stdout,
      stderr,
      stdin: {
        write: (chunk: string) => {
          stdinWrites.push(chunk);
          return true;
        },
      },
      on: (event: string, listener: (...unknownArgs: unknown[]) => void) => {
        proc.on(event, listener);
        return handle;
      },
      kill: (signal?: NodeJS.Signals) => {
        killed.push(signal);
        proc.emit("exit", null, signal);
        return true;
      },
    };
    created.push({ fake: { stdinWrites, killed, handle }, stdout, args });
    return handle as unknown as ReturnType<typeof nodeSpawn>;
  }) as typeof nodeSpawn;
  return { spawnImpl, created };
}

function writeLine(stdout: PassThrough, obj: Record<string, unknown>): void {
  stdout.write(`${JSON.stringify(obj)}\n`);
}

function resultLine(text: string, opts: { isError?: boolean } = {}): Record<string, unknown> {
  return {
    type: "result",
    is_error: opts.isError ?? false,
    result: text,
    usage: { input_tokens: 5, output_tokens: 3 },
  };
}

function baseParams(overrides: Partial<ClaudePrewarmParams> = {}): ClaudePrewarmParams {
  return { prompt: "hi", model: "opus", systemPrompt: "you are terse", timeoutMs: 5_000, ...overrides };
}

test("two sequential turns for the SAME agent (model+systemPrompt) use two DIFFERENT child processes", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const p1 = pool.generate(baseParams({ prompt: "what is 2+2" }));
  assert.equal(created.length, 1, "first turn should spawn exactly one process");
  writeLine(created[0].stdout, resultLine("4"));
  const r1 = await p1;
  assert.equal(r1.text, "4");

  // The pool refills a spare for this key in the background after a turn
  // settles — that spawn is what call #2 below should land on.
  assert.equal(created.length, 2, "a background replacement should have been spawned after turn 1 settled");

  const p2 = pool.generate(baseParams({ prompt: "what did I just ask you" }));
  // Crucially: turn 2 must NOT have spawned a THIRD process reusing entry #1 —
  // it should have claimed the pre-spawned spare (entry #2).
  writeLine(created[1].stdout, resultLine("I don't have access to a previous message."));
  const r2 = await p2;
  assert.equal(r2.text, "I don't have access to a previous message.");

  assert.notEqual(created[0].fake.handle, created[1].fake.handle, "turn 1 and turn 2 must run on different child processes");

  // The critical isolation check: turn 1's prompt content must appear ONLY in
  // process #1's stdin, and turn 2's prompt content must appear ONLY in
  // process #2's stdin — never crossed.
  const p1Stdin = created[0].fake.stdinWrites.join("");
  const p2Stdin = created[1].fake.stdinWrites.join("");
  assert.ok(p1Stdin.includes("what is 2+2"), "process 1 must have received turn 1's prompt");
  assert.ok(!p1Stdin.includes("what did I just ask"), "process 1 must NEVER see turn 2's prompt");
  assert.ok(p2Stdin.includes("what did I just ask"), "process 2 must have received turn 2's prompt");
  assert.ok(!p2Stdin.includes("what is 2+2"), "process 2 must NEVER see turn 1's prompt (no conversation bleed)");

  // And process #1 must have been torn down after serving its one turn —
  // never left running to (mis)serve a later, unrelated turn.
  assert.ok(created[0].fake.killed.length > 0, "process 1 must be retired (killed) after its single turn");
});

test("a process is retired even when its turn fails — never reused after an error", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const p1 = pool.generate(baseParams({ prompt: "trigger a failure" }));
  writeLine(created[0].stdout, resultLine("Not logged in · Please run /login", { isError: true }));
  await assert.rejects(p1);
  assert.ok(created[0].fake.killed.length > 0, "a failed turn's process must still be retired");

  const p2 = pool.generate(baseParams({ prompt: "second turn after a failure" }));
  writeLine(created[1].stdout, resultLine("ok"));
  await p2;
  assert.notEqual(created[0].fake.handle, created[1].fake.handle, "the turn after a failure must use a fresh process, not the failed one");
  assert.ok(!created[1].fake.stdinWrites.join("").includes("trigger a failure"), "the failed turn's content must not leak into the next process");
});

test("different agents (different system prompts) never share a pool key or a process", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const pA = pool.generate(baseParams({ systemPrompt: "You are Agent A, a pirate.", prompt: "hello" }));
  writeLine(created[0].stdout, resultLine("Arr!"));
  await pA;

  const pB = pool.generate(baseParams({ systemPrompt: "You are Agent B, a butler.", prompt: "hello" }));
  // Agent A's refill (a background spawn keyed to Agent A) must NOT be what
  // Agent B's turn lands on — different key, so there is no spare to claim,
  // and Agent B's process must be spawned with Agent B's own system prompt.
  const bIndex = created.length - 1;
  writeLine(created[bIndex].stdout, resultLine("Very good, sir."));
  const rB = await pB;
  assert.equal(rB.text, "Very good, sir.");
  assert.ok(
    !created[bIndex].args.some((a) => a.includes("pirate")),
    "Agent B's process must never be spawned with Agent A's system prompt",
  );
  assert.ok(
    created[bIndex].args.some((a) => a.includes("butler")),
    "Agent B's process must be spawned with Agent B's own system prompt",
  );
});

// ── Reasoning effort (Phase 1: reasoning-effort control) — this pool's own
// header comment promises it mirrors cli-runner.ts's buildInvocation
// exactly, so --effort must appear here too, AND reasoningEffort must be
// part of the spawn-time argv it's keyed by (like model/systemPrompt) —
// otherwise a spare pre-spawned for one effort level could be silently
// claimed by a turn that asked for a different one.

test("reasoningEffort appends --effort <level> to the spawned argv", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const p1 = pool.generate(baseParams({ reasoningEffort: "xhigh" }));
  writeLine(created[0].stdout, resultLine("ok"));
  await p1;

  assert.deepEqual(
    created[0].args.slice(created[0].args.indexOf("--effort")),
    ["--effort", "xhigh"],
    "--effort <level> must be the trailing argv pair when reasoningEffort is set",
  );
});

test("an unset reasoningEffort never appends --effort", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const p1 = pool.generate(baseParams());
  writeLine(created[0].stdout, resultLine("ok"));
  await p1;

  assert.ok(!created[0].args.includes("--effort"), "--effort must not appear when reasoningEffort is unset");
});

test("two turns for the SAME agent but DIFFERENT reasoningEffort never share a spare process", async () => {
  const { spawnImpl, created } = makeSpawnFake();
  const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

  const p1 = pool.generate(baseParams({ reasoningEffort: "low", prompt: "first" }));
  assert.equal(created.length, 1);
  writeLine(created[0].stdout, resultLine("ok-1"));
  await p1;
  // The background refill after turn 1 settles is keyed to "low" — a
  // DIFFERENT effort level's turn must not be able to claim it.
  assert.equal(created.length, 2, "background refill spawned for the (model, systemPrompt, low) key");

  const p2 = pool.generate(baseParams({ reasoningEffort: "high", prompt: "second" }));
  // A THIRD process must be spawned fresh for the "high" key — turn 2 must
  // NOT claim entry #2 (spawned for "low").
  assert.equal(created.length, 3, "a different reasoningEffort must spawn its own process, not reuse the other level's spare");
  writeLine(created[2].stdout, resultLine("ok-2"));
  await p2;

  // entry #2 (index 1) is the background refill spawned for turn 1's "low"
  // key; entry #3 (index 2) is what turn 2's "high" request actually spawned
  // fresh — each keeps its OWN effort level, never the other's.
  assert.equal(created[1].args[created[1].args.indexOf("--effort") + 1], "low");
  assert.equal(created[2].args[created[2].args.indexOf("--effort") + 1], "high");
});
