import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "fs";
import os from "os";
import path from "path";

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
  // BYO-brain multi-method: start() now also reports the resolved method
  // (defaulting to device_auth for codex) and whether the flow expects a
  // stdin secret. See LOGIN_METHODS / LOGIN_COMMAND in cli-login-session.
  assert.deepEqual(result, { run_id: "run-3", status: "started", method: "device_auth", awaits_secret: false });
  // Process hasn't closed — start() must not have blocked on it.
  assert.equal(fake.killCalls.length, 0);
});

// ---- claude_code's reliable subscription default (claudeai) --------------
// `claudeai` (`claude auth login --claudeai`) is the fully-automatic,
// Codex-device_auth-equivalent subscription flow: the CLI itself persists
// the resulting credential (Keychain / credentials file), so this is now
// claude_code's default method — see the module doc comment for why
// `claude setup-token` is deliberately NOT one of the spawnable methods.

test("claude_code defaults to claudeai (subscription) and spawns the right argv", async () => {
  const fake = makeFakeChild();
  let capturedCommand = "";
  let capturedArgs: string[] = [];
  const manager = new CliLoginSessionManager({
    spawnImpl: (command, args) => {
      capturedCommand = command;
      capturedArgs = args;
      return fake.child;
    },
    // Precise (not blanket-true) so resolveLineBufferedSpawn's own
    // `commandExists("stdbuf", env)` probe correctly resolves to "absent" —
    // keeps this assertion about LOGIN_COMMAND's argv, not the unrelated
    // stdbuf line-buffering prefix (covered by its own tests elsewhere).
    commandExists: (command) => (command === "claude" ? "/usr/bin/claude" : null),
  });
  const result = await manager.start({ runId: "run-claudeai", runtime: "claude_code" });
  assert.equal(result.method, "claudeai", "claudeai must be claude_code's default method, mirroring Codex's device_auth default");
  assert.equal(result.awaits_secret, false);
  assert.equal(capturedCommand, "/usr/bin/claude");
  assert.deepEqual(capturedArgs, ["auth", "login", "--claudeai"]);
});

test("claude_code:subscription (the old gateway-spawned `claude setup-token`) is no longer a supported method", async () => {
  // Confirms the removal is deliberate, not an accidental regression: the
  // gateway must never spawn `claude setup-token` itself, because the
  // resulting token would only ever reach this process's own memory (never
  // the owner) and would be correctly-but-uselessly discarded — see the
  // module doc comment. Requesting it explicitly must fail loudly with
  // "unsupported_method", not silently fall back to some other flow.
  const manager = new CliLoginSessionManager({
    spawnImpl: () => {
      throw new Error("must not spawn for an unsupported method");
    },
    commandExists: () => "/usr/bin/claude",
  });
  await assert.rejects(
    manager.start({ runId: "run-no-subscription", runtime: "claude_code", method: "subscription" as never }),
    (err: unknown) => {
      assert.ok(err instanceof CliLoginError);
      assert.equal(err.kind, "unsupported_method");
      return true;
    },
  );
});

test("claude_code:console remains available as the non-subscription (API billing) alternative", async () => {
  const fake = makeFakeChild();
  let capturedArgs: string[] = [];
  const manager = new CliLoginSessionManager({
    spawnImpl: (_command, args) => {
      capturedArgs = args;
      return fake.child;
    },
    commandExists: (command) => (command === "claude" ? "/usr/bin/claude" : null),
  });
  const result = await manager.start({ runId: "run-console", runtime: "claude_code", method: "console" });
  assert.equal(result.method, "console");
  assert.deepEqual(capturedArgs, ["auth", "login", "--console"]);
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

test("real detection: start() finds the binary in ~/.local/bin even when it is not on PATH", async (t) => {
  // commandExists is deliberately NOT overridden below — this exercises the
  // real, production defaultCommandExists()/resolveCommandPath() fallback-dir
  // logic (shared with health/service-inventory.ts's passive detection), not
  // a test double of it. Without this fix, a box could show "Claude Code:
  // installed" on the health dashboard while clicking "Sign in" still threw
  // not_installed — the same PATH gap hit a second time in a second lookup.
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-cli-login-detect-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const localBin = path.join(home, ".local", "bin");
  fs.mkdirSync(localBin, { recursive: true });
  fs.writeFileSync(path.join(localBin, "claude"), "#!/bin/sh\necho fake-claude\n", { mode: 0o755 });

  const fake = makeFakeChild();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    // Deliberately narrow — does NOT include localBin — reproducing the
    // real Gateway-process-PATH gap, not a full interactive shell PATH.
    env: { HOME: home, PATH: "/usr/bin:/bin" },
  });

  const result = await manager.start({ runId: "run-local-bin", runtime: "claude_code" });
  assert.equal(result.status, "started", "the binary in ~/.local/bin must resolve instead of throwing not_installed");
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

test("real Codex 0.144.1 output: ANSI-wrapped URL directly followed by an ANSI-wrapped code line, with NO instruction sentence, still forwards both", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/codex",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-codex-0-144-1", runtime: "codex" });
  // Exact real captured output from `codex login --device-auth` on 0.144.1:
  // the URL and the device code, each on their own ANSI-colored line, with
  // no "Enter this one-time code" (or any other) instruction line between
  // them. Before the fix, CODE_PROMPT_PATTERN never matched anything here,
  // so awaitingCodexCodeValue never got set, and the bare code line matched
  // neither URL_PATTERN nor CODE_PROMPT_PATTERN — it was silently dropped,
  // leaving the sign-in UI with a link but no code to enter (an effective
  // hang: "waiting for the sign-in link" never resolves into something the
  // user can finish).
  fake.emitStdout("\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n");
  fake.emitStdout("\x1b[94mCSXL-XXXXX\x1b[0m\n");

  const outputEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output");
  const urlEvents = outputEvents.filter((e) => e.kind === "url");
  const codePromptEvents = outputEvents.filter((e) => e.kind === "code_prompt");
  assert.equal(urlEvents.length, 1, "the URL line must be extracted and forwarded");
  assert.equal(
    urlEvents[0].text,
    "https://auth.openai.com/codex/device",
    "the URL must be forwarded with ANSI codes stripped, not embedded in escape bytes",
  );
  assert.equal(
    codePromptEvents.length,
    1,
    "the bare device code, with no preceding instruction line, must still be extracted and forwarded",
  );
  assert.equal(
    codePromptEvents[0].text,
    "CSXL-XXXXX",
    "the device code must be forwarded stripped of ANSI codes, verbatim",
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

// ---- Real Claude Code output: the client_id-truncation lead, checked ------
// A reported bug: the browser showed Anthropic's own "Invalid OAuth Request
// — Missing client_id parameter" error page after opening the URL the
// Gateway relayed. The leading hypothesis was that `claude`'s own stdout
// wraps the long authorize URL across two terminal lines, and because
// extraction ran per-line, only the first fragment matched — truncating
// right before client_id.
//
// That hypothesis was checked against REAL ground truth, not assumed: live
// `claude auth login --claudeai` (v2.1.220) was spawned exactly the way this
// module spawns it (stdbuf -oL -eL prefix, piped/non-TTY stdio, via Node's
// child_process.spawn) and its raw stdout was captured byte-for-byte, both
// against an already-authenticated HOME and a from-scratch empty HOME (no
// existing credentials) — same result both times. The CLI does NOT wrap the
// URL: "If the browser didn't open, visit: <url>" prints as one single,
// uncolored, un-decorated line, arriving in one single stdout `data` chunk,
// terminated by one `\n`. The client_id parameter is present, verified
// end-to-end by feeding this exact captured line through the real (not
// mocked) extraction path.
//
// This fixture is that real capture (the OAuth `code`/`state`/
// `code_challenge` values were single-use, already expired by the time this
// test was written, and are not a live credential). It's here so a FUTURE
// regression in URL_PATTERN, the ANSI-stripping, or the line-buffering logic
// that clips anything from this specific, realistic shape gets caught — not
// because line-wrapping was the actual root cause here (it wasn't).
test("real Claude Code output (claude auth login --claudeai, v2.1.220, piped/non-TTY): the full authorize URL including client_id is captured intact from one single-line chunk", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-claude-real-url", runtime: "claude_code" });
  // Byte-for-byte real captured stdout chunk (single `data` event, single
  // line) from `claude auth login --claudeai` under stdbuf -oL -eL, piped,
  // non-TTY stdio — the exact conditions this module spawns under.
  fake.emitStdout(
    "Opening browser to sign in…\n" +
      "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload&code_challenge=KSABa9o7wSJ36-_SRnqjWpMfZEs5fL2woZmqATA-R98&code_challenge_method=S256&state=Z_BGje3CVadCKYjt31FG83plFQb7nzXEagdpsLg29e4\n" +
      "Paste code here if prompted > ",
  );

  const urlEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output" && e.kind === "url");
  assert.equal(urlEvents.length, 1, "exactly one url event — the banner line above it must not spuriously match");
  assert.equal(
    urlEvents[0].text,
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload&code_challenge=KSABa9o7wSJ36-_SRnqjWpMfZEs5fL2woZmqATA-R98&code_challenge_method=S256&state=Z_BGje3CVadCKYjt31FG83plFQb7nzXEagdpsLg29e4",
    "the full URL, byte-for-byte, must reach the event publisher",
  );
  assert.ok(urlEvents[0].text.includes("client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e"), "client_id must not be dropped");
});

// A second, adversarial variant of the same fixture: what the ORIGINAL
// line-wrap hypothesis described, even though live evidence shows the real
// CLI doesn't actually do this. Kept as a belt-and-suspenders regression: IF
// a future `claude` version (or a differently-configured terminal) ever DOES
// split this line across two stdout `data` chunks — mid-parameter, before
// client_id is fully written — extractSafeLines' persistent `state.
// lineBuffer` must still reassemble it before matching, per its own doc
// comment ("Chunk-boundary handling").
test("a hypothetical mid-URL chunk split (before client_id is fully written) is still reassembled and captured intact", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/claude",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-claude-split-chunk", runtime: "claude_code" });
  // Split mid-way through "client_id=..." itself, across two separate
  // stdout `data` events, with NO newline in the first fragment.
  fake.emitStdout(
    "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c25",
  );
  fake.emitStdout(
    "0a-e61b-44d9-88ed-5944d1962f5e&response_type=code&state=Z_BGje3CVadCKYjt31FG83plFQb7nzXEagdpsLg29e4\n",
  );

  const urlEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output" && e.kind === "url");
  assert.equal(urlEvents.length, 1);
  assert.equal(
    urlEvents[0].text,
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&state=Z_BGje3CVadCKYjt31FG83plFQb7nzXEagdpsLg29e4",
    "the URL split across two chunks, mid-client_id, must still be reassembled whole before matching",
  );
});

// ---- xAI Grok Build (device-code flow) ------------------------------------
// docs.x.ai/build's authentication guide (fetched live 2026-07-24): `grok
// login --device-auth` "prints a URL and code to the terminal... Grok polls
// until the login is confirmed" — same URL-then-bare-code shape as Codex's
// device_auth, so it reuses the identical follow-up-code capture path.

test("grok_build defaults to device_auth and spawns the right argv", async () => {
  const fake = makeFakeChild();
  let capturedCommand = "";
  let capturedArgs: string[] = [];
  const manager = new CliLoginSessionManager({
    spawnImpl: (command, args) => {
      capturedCommand = command;
      capturedArgs = args;
      return fake.child;
    },
    commandExists: (command) => (command === "grok" ? "/usr/bin/grok" : null),
  });
  const result = await manager.start({ runId: "run-grok-device", runtime: "grok_build" });
  assert.equal(result.method, "device_auth");
  assert.equal(result.awaits_secret, false);
  assert.equal(capturedCommand, "/usr/bin/grok");
  assert.deepEqual(capturedArgs, ["login", "--device-auth"]);
});

test("grok_build: a device-code login surfaces BOTH the URL and the bare device code to the owner — this is the actual 'surfaces the code to the owner' behavior the cli_setup rail exists for", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/grok",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-grok-code-value", runtime: "grok_build" });
  // Shape per docs.x.ai/build: a URL, then an instruction line, then the bare
  // code on its own line — modeled on the same two-line-after-prompt shape
  // Codex's real captured output uses (see the codex device-code tests above).
  fake.emitStdout("Open this URL to sign in: https://accounts.x.ai/device\n");
  fake.emitStdout("Enter this device code: \n");
  fake.emitStdout("GX7K-QP2M\n");

  const outputEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output");
  const urlEvents = outputEvents.filter((e) => e.kind === "url");
  const codePromptEvents = outputEvents.filter((e) => e.kind === "code_prompt");
  assert.equal(urlEvents.length, 1, "the sign-in URL must reach the owner");
  assert.equal(urlEvents[0].text, "https://accounts.x.ai/device");
  assert.equal(codePromptEvents.length, 2, "both the instruction line and the bare code value must reach the owner");
  assert.equal(codePromptEvents[1].text, "GX7K-QP2M", "the bare device code itself must be forwarded verbatim");
});

test("grok_build:api_key is not a supported method (XAI_API_KEY is an env-var fallback, not a persisting login-session subcommand)", async () => {
  const manager = new CliLoginSessionManager({
    spawnImpl: () => {
      throw new Error("must not spawn for an unsupported method");
    },
    commandExists: () => "/usr/bin/grok",
  });
  await assert.rejects(
    manager.start({ runId: "run-grok-no-api-key", runtime: "grok_build", method: "api_key" as never }),
    (err: unknown) => {
      assert.ok(err instanceof CliLoginError);
      assert.equal(err.kind, "unsupported_method");
      return true;
    },
  );
});

// ---- Cursor CLI (browser OAuth, URL-only) ----------------------------------
// cursor.com/docs/cli/reference/authentication (fetched live 2026-07-24):
// `agent login` opens a browser by default; NO_OPEN_BROWSER=1 makes it print
// the URL instead. Unlike Codex/Grok, there is no documented bare device
// code to capture — this is a URL-only flow, same shape as Claude Code's
// claudeai/console methods.

test("cursor_cli defaults to login and spawns the right argv with NO_OPEN_BROWSER=1 merged into env", async () => {
  const fake = makeFakeChild();
  let capturedCommand = "";
  let capturedArgs: string[] = [];
  let capturedEnv: NodeJS.ProcessEnv = {};
  const manager = new CliLoginSessionManager({
    env: { PATH: "/usr/bin", SOME_OTHER_VAR: "kept" },
    spawnImpl: (command, args, options) => {
      capturedCommand = command;
      capturedArgs = args;
      capturedEnv = options.env;
      return fake.child;
    },
    commandExists: (command) => (command === "cursor-agent" ? "/usr/bin/cursor-agent" : null),
  });
  const result = await manager.start({ runId: "run-cursor-login", runtime: "cursor_cli" });
  assert.equal(result.method, "login");
  assert.equal(result.awaits_secret, false);
  assert.equal(capturedCommand, "/usr/bin/cursor-agent");
  assert.deepEqual(capturedArgs, ["login"]);
  assert.equal(capturedEnv.NO_OPEN_BROWSER, "1", "NO_OPEN_BROWSER=1 must be merged in so `agent login` prints the URL instead of trying to open a browser on the headless Gateway");
  assert.equal(capturedEnv.SOME_OTHER_VAR, "kept", "extraEnv must be MERGED on top of the session's own env, not replace it");
});

test("cursor_cli: the sign-in URL reaches the owner; no bare device code is expected or mis-captured", async () => {
  const fake = makeFakeChild();
  const { events, publish } = collectingPublisher();
  const manager = new CliLoginSessionManager({
    spawnImpl: spawnImplReturning(fake),
    commandExists: () => "/usr/bin/cursor-agent",
  });
  manager.setEventPublisher(publish);

  await manager.start({ runId: "run-cursor-url", runtime: "cursor_cli" });
  fake.emitStdout("Sign in to Cursor: https://cursor.com/auth/device?code=ABC123\n");
  // A bare short code on its own line must NOT be mis-captured for cursor_cli
  // the way it correctly IS for codex/grok_build — cursor_cli is deliberately
  // excluded from URL_CODE_FOLLOWUP_RUNTIMES.
  fake.emitStdout("ABCD-1234\n");

  const outputEvents = events.filter((e): e is CliLoginOutputEvent => e.event === "output");
  const urlEvents = outputEvents.filter((e) => e.kind === "url");
  const codePromptEvents = outputEvents.filter((e) => e.kind === "code_prompt");
  assert.equal(urlEvents.length, 1, "the sign-in URL must reach the owner");
  assert.equal(urlEvents[0].text, "https://cursor.com/auth/device?code=ABC123");
  assert.equal(codePromptEvents.length, 0, "a bare code line must not be mis-captured as a device code for cursor_cli");
});

test("cursor_cli:api_key is not a supported method (CURSOR_API_KEY is an env-var fallback, not a persisting login-session subcommand)", async () => {
  const manager = new CliLoginSessionManager({
    spawnImpl: () => {
      throw new Error("must not spawn for an unsupported method");
    },
    commandExists: () => "/usr/bin/cursor-agent",
  });
  await assert.rejects(
    manager.start({ runId: "run-cursor-no-api-key", runtime: "cursor_cli", method: "api_key" as never }),
    (err: unknown) => {
      assert.ok(err instanceof CliLoginError);
      assert.equal(err.kind, "unsupported_method");
      return true;
    },
  );
});
