import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";

import {
  CliLoginSessionManager,
  CliLoginError,
  type CliLoginChildProcessLike,
  type CliLoginSpawnImpl,
  type CliLoginEvent,
  type CliLoginOutputEvent,
} from "../llm/cli-login-session";

interface FakeChild {
  child: CliLoginChildProcessLike;
  emitStdout: (chunk: string) => void;
  emitStderr: (chunk: string) => void;
  emitClose: (code: number | null) => void;
  emitError: (err: NodeJS.ErrnoException) => void;
  killCalls: (NodeJS.Signals | undefined)[];
  stdinWrites: string[];
}

function makeFakeChild(): FakeChild {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const proc = new EventEmitter();
  const killCalls: (NodeJS.Signals | undefined)[] = [];
  const stdinWrites: string[] = [];
  return {
    child: {
      stdin: {
        write: (chunk: string, callback: (err?: Error | null) => void) => {
          stdinWrites.push(chunk);
          callback();
          return true;
        },
      },
      stdout: stdout as unknown as CliLoginChildProcessLike["stdout"],
      stderr: stderr as unknown as CliLoginChildProcessLike["stderr"],
      on: (event: string, listener: (...args: unknown[]) => void) => proc.on(event, listener),
      kill: (signal?: NodeJS.Signals) => {
        killCalls.push(signal);
        return true;
      },
    } as unknown as CliLoginChildProcessLike,
    emitStdout: (chunk) => stdout.emit("data", chunk),
    emitStderr: (chunk) => stderr.emit("data", chunk),
    emitClose: (code) => proc.emit("close", code),
    emitError: (err) => proc.emit("error", err),
    killCalls,
    stdinWrites,
  };
}

function spawnImplReturning(fake: FakeChild): CliLoginSpawnImpl {
  return () => fake.child;
}

function collectingPublisher() {
  const events: CliLoginEvent[] = [];
  return { events, publish: (event: CliLoginEvent) => { events.push(event); } };
}

// ---- The safety-critical property ----------------------------------------
// See cli-login-session.ts's module doc comment: `claude setup-token` prints
// the long-lived OAuth token to stdout as its own final success output.
// These tests are the enforcement check for "the Gateway never transmits the
// credential" — not a promise in a comment, a assertion that unmatched
// output (including what a real token line would look like) never reaches
// the event publisher.

test("forwards a URL line, drops an unrelated line, forwards a paste-code prompt", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-1", runtime: "claude_code" });
  fake.emitStdout("To authorize, visit: https://claude.ai/oauth/authorize?state=abc123\n");
  fake.emitStdout("Some unrelated banner line that should never be relayed\n");
  fake.emitStdout("Paste code here if prompted: \n");

  const outputEvents = events.filter((e) => e.event === "output");
  assert.equal(outputEvents.length, 2, "only the URL and the code-prompt lines should be forwarded");
  assert.equal(outputEvents[0].kind, "url");
  assert.equal(outputEvents[0].text, "https://claude.ai/oauth/authorize?state=abc123");
  assert.equal(outputEvents[1].kind, "code_prompt");
  assert.ok(!events.some((e) => e.event === "output" && /unrelated banner/.test(e.text)));
});

test("never forwards the final success line — the credential-shaped one", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-2", runtime: "claude_code" });
  fake.emitStdout("To authorize, visit: https://claude.ai/oauth/authorize?state=xyz\n");
  // Simulates claude setup-token's real final stdout: the long-lived token.
  // This must NEVER show up as an "output" event, under any kind.
  fake.emitStdout("sk-ant-oat01-thisisNOTarealtokenbutshapedlikeone1234567890\n");
  fake.emitClose(0);

  const secretLeak = events.find(
    (e) => e.event === "output" && e.text.includes("sk-ant-oat01-thisisNOTarealtokenbutshapedlikeone1234567890"),
  );
  assert.equal(secretLeak, undefined, "the token-shaped line must never reach the event publisher");
  const done = events.find((e) => e.event === "done");
  assert.ok(done);
  assert.equal((done as { ok: boolean }).ok, true);
});

test("start() resolves immediately without waiting for the process to finish", async () => {
  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/codex",
  });
  const result = await manager.start({ runId: "run-3", runtime: "codex" });
  assert.deepEqual(result, { run_id: "run-3", status: "started" });
  // Process hasn't closed — start() must not have blocked on it.
  assert.equal(fake.killCalls.length, 0);
});

test("input() writes the pasted-back code plus a newline to the session's stdin", async () => {
  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  await manager.start({ runId: "run-4", runtime: "claude_code" });
  const result = await manager.input({ runId: "run-4", code: "ABCD-1234" });
  assert.deepEqual(result, { ok: true });
  assert.deepEqual(fake.stdinWrites, ["ABCD-1234\n"]);
});

test("input() for an unknown run_id throws instead of silently no-op-ing", async () => {
  const manager = new CliLoginSessionManager();
  await assert.rejects(
    manager.input({ runId: "no-such-run", code: "1234" }),
    (err: unknown) => err instanceof CliLoginError,
  );
});

test("not_installed: start() throws before spawning when the binary isn't on PATH", async () => {
  let spawned = false;
  const manager = new CliLoginSessionManager({
    spawnImpl: () => {
      spawned = true;
      throw new Error("should not be called");
    },
    commandExists: () => null,
  });
  await assert.rejects(
    manager.start({ runId: "run-5", runtime: "codex" }),
    (err: unknown) => {
      assert.ok(err instanceof CliLoginError);
      assert.equal(err.kind, "not_installed");
      return true;
    },
  );
  assert.equal(spawned, false);
});

test("exit code 0 reports done/ok true; nonzero reports done/ok false with error_kind", async () => {
  const okChild = makeFakeChild();
  const failChild = makeFakeChild();
  const okManager = new CliLoginSessionManager({ spawnImpl: spawnImplReturning(okChild), commandExists: () => "/x" });
  const failManager = new CliLoginSessionManager({ spawnImpl: spawnImplReturning(failChild), commandExists: () => "/x" });
  const ok = collectingPublisher();
  const fail = collectingPublisher();
  okManager.setEventPublisher(ok.publish);
  failManager.setEventPublisher(fail.publish);

  await okManager.start({ runId: "ok-run", runtime: "codex" });
  okChild.emitClose(0);
  await failManager.start({ runId: "fail-run", runtime: "codex" });
  failChild.emitClose(1);

  const okDone = ok.events.find((e) => e.event === "done") as { ok: boolean };
  const failDone = fail.events.find((e) => e.event === "done") as { ok: boolean; error_kind?: string };
  assert.equal(okDone.ok, true);
  assert.equal(failDone.ok, false);
  assert.equal(failDone.error_kind, "crash");
});

test("timeout kills the process and reports a timeout done event", async () => {
  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
    sessionTimeoutMs: 10,
  });
  const { events, publish } = collectingPublisher();
  manager.setEventPublisher(publish);
  await manager.start({ runId: "run-timeout", runtime: "claude_code" });

  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.ok(fake.killCalls.includes("SIGTERM"), "timeout must SIGTERM the held-open process");
  const done = events.find((e) => e.event === "done") as { ok: boolean; error_kind?: string };
  assert.ok(done);
  assert.equal(done.ok, false);
  assert.equal(done.error_kind, "timeout");
});

test("cancel() kills the process and reports a cancelled done event, mirroring tool.interrupt", async () => {
  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/codex",
  });
  const { events, publish } = collectingPublisher();
  manager.setEventPublisher(publish);
  await manager.start({ runId: "run-cancel", runtime: "codex" });

  const result = await manager.cancel("run-cancel");
  assert.deepEqual(result, { ok: true });
  assert.ok(fake.killCalls.includes("SIGTERM"));
  const done = events.find((e) => e.event === "done") as { ok: boolean; error_kind?: string };
  assert.equal(done.ok, false);
  assert.equal(done.error_kind, "cancelled");
  assert.equal(manager.hasSession("run-cancel"), false, "session should be cleaned up after cancel");
});

test("cancel() on an unknown run_id returns ok:false without throwing", async () => {
  const manager = new CliLoginSessionManager();
  const result = await manager.cancel("never-started");
  assert.deepEqual(result, { ok: false });
});

test("starting a second session for the same run_id is rejected", async () => {
  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  await manager.start({ runId: "dup", runtime: "claude_code" });
  await assert.rejects(manager.start({ runId: "dup", runtime: "claude_code" }));
});

// ---- Real-output regression tests -----------------------------------------
// Fixtures below are byte-for-byte what `codex login --device-auth` actually
// printed in a live test (session of 2026-07-11/12): confirmed via direct
// `codex login --device-auth` runs, both to a TTY and piped, and via `cat -v`
// to see every control byte. The device code itself was cancelled
// immediately after capture and was never a valid credential by the time
// this test was written.

test("real Codex output: ANSI-wrapped URL is captured without the trailing escape code", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/codex",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-ansi-url", runtime: "codex" });
  // Real captured line: "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m"
  fake.emitStdout("   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n");

  const urlEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output" && e.kind === "url");
  assert.equal(urlEvents.length, 1);
  assert.equal(
    urlEvents[0].text,
    "https://auth.openai.com/codex/device",
    "the trailing \\x1b[0m reset code must not be appended to the captured URL",
  );
});

test("real Codex output: the device code on the line AFTER the prompt is captured, not dropped", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/codex",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-code-value", runtime: "codex" });
  // Real captured two-line sequence from `codex login --device-auth`:
  fake.emitStdout("2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n");
  fake.emitStdout("   \x1b[94m158R-XDWM5\x1b[0m\n");
  // A trailing unrelated line must not be mistaken for a second code value.
  fake.emitStdout("\x1b[90mContinue only if you started this login in Codex.\x1b[0m\n");

  const codePromptEvents = events.filter(
    (e): e is CliLoginOutputEvent => e.event === "output" && e.kind === "code_prompt",
  );
  assert.equal(codePromptEvents.length, 2, "both the instruction line and the code value line should be forwarded");
  assert.match(codePromptEvents[0].text, /Enter this one-time code/);
  assert.equal(
    codePromptEvents[1].text,
    "158R-XDWM5",
    "the bare code value, stripped of ANSI codes, must be forwarded as its own event",
  );
});

test("Claude Code's paste-back prompt is unaffected: no follow-up line is mis-captured as a code value", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-claude-no-capture", runtime: "claude_code" });
  fake.emitStdout("Paste code here if prompted: \n");
  // Unlike Codex, nothing meaningful follows on stdout — the process blocks
  // on stdin at this point. Whatever prints next (e.g. a stray banner after
  // the user's paste-back completes) must NOT be captured as if it were a
  // device code the way Codex's follow-up line is.
  fake.emitStdout("Some later unrelated line\n");

  const codePromptEvents = events.filter(
    (e): e is CliLoginOutputEvent => e.event === "output" && e.kind === "code_prompt",
  );
  assert.equal(codePromptEvents.length, 1, "claude_code must not gain the codex-only follow-up-line capture");
  assert.match(codePromptEvents[0].text, /Paste code here/);
});
