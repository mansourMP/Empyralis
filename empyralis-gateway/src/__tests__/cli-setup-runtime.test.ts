import test from "node:test";
import assert from "node:assert/strict";

import {
  GatewayCliSetupRuntime,
  CLI_INSTALL_CAPABILITY,
  CLI_LOGIN_START_CAPABILITY,
  CLI_LOGIN_INPUT_CAPABILITY,
} from "../llm/cli-setup-runtime";
import { CliInstallError, type CliInstallResult } from "../llm/cli-installer";
import { CliLoginSessionManager, CliLoginError } from "../llm/cli-login-session";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

function makeFrame(capabilityId: string, args: Record<string, unknown>, runId = "run-1"): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      run_id: runId,
      trace_id: "trace-1",
      workspace_id: "ws-1",
      arguments: args,
    },
  };
}

test("requestedCapabilities lists exactly the three cli_setup capabilities", () => {
  const runtime = new GatewayCliSetupRuntime();
  assert.deepEqual(
    [...runtime.requestedCapabilities()].sort(),
    [CLI_INSTALL_CAPABILITY, CLI_LOGIN_INPUT_CAPABILITY, CLI_LOGIN_START_CAPABILITY].sort(),
  );
});

test("supportsCapability recognizes only cli_setup's own capabilities", () => {
  const runtime = new GatewayCliSetupRuntime();
  assert.equal(runtime.supportsCapability(CLI_INSTALL_CAPABILITY), true);
  assert.equal(runtime.supportsCapability(CLI_LOGIN_START_CAPABILITY), true);
  assert.equal(runtime.supportsCapability(CLI_LOGIN_INPUT_CAPABILITY), true);
  assert.equal(runtime.supportsCapability("shell.execute"), false);
  assert.equal(runtime.supportsCapability("llm.generate"), false);
});

test("rejects an unsupported runtime before ever calling the installer", async () => {
  let called = false;
  const runtime = new GatewayCliSetupRuntime({
    installer: async () => {
      called = true;
      throw new Error("should not be reached");
    },
  });
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeFrame(CLI_INSTALL_CAPABILITY, { runtime: "gpt-5" })),
    /Unsupported cli_setup runtime/,
  );
  assert.equal(called, false);
});

test("cli.install dispatches to the injected installer and returns its result verbatim", async () => {
  const fakeResult: CliInstallResult = {
    runtime: "claude_code",
    package: "@anthropic-ai/claude-code",
    installed: true,
    os: "darwin",
    version: "2.1.205",
  };
  let receivedRuntime: string | null = null;
  const runtime = new GatewayCliSetupRuntime({
    installer: async (params) => {
      receivedRuntime = params.runtime;
      return fakeResult;
    },
  });
  const result = await runtime.handleCapabilityInvoke(makeFrame(CLI_INSTALL_CAPABILITY, { runtime: "claude_code" }));
  assert.equal(receivedRuntime, "claude_code");
  assert.deepEqual(result, fakeResult);
});

test("grok_build and cursor_cli are accepted runtimes, dispatched to the injected installer like claude_code/codex", async () => {
  for (const runtime of ["grok_build", "cursor_cli"]) {
    const fakeResult: CliInstallResult = {
      runtime: runtime as CliInstallResult["runtime"],
      package: runtime === "grok_build" ? "https://x.ai/cli/install.sh" : "https://cursor.com/install",
      installed: true,
      os: "linux",
    };
    let receivedRuntime: string | null = null;
    const setupRuntime = new GatewayCliSetupRuntime({
      installer: async (params) => {
        receivedRuntime = params.runtime;
        return fakeResult;
      },
    });
    const result = await setupRuntime.handleCapabilityInvoke(makeFrame(CLI_INSTALL_CAPABILITY, { runtime }));
    assert.equal(receivedRuntime, runtime);
    assert.deepEqual(result, fakeResult);
  }
});

test("cli.install failure is wrapped into a precise, honest error message", async () => {
  const runtime = new GatewayCliSetupRuntime({
    installer: async () => {
      throw new CliInstallError("npm_missing", "npm was not found on PATH.");
    },
  });
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeFrame(CLI_INSTALL_CAPABILITY, { runtime: "codex" })),
    /Codex install failed on this Gateway \(npm_missing\): npm was not found on PATH\./,
  );
});

test("cli.login.start requires run_id", async () => {
  const runtime = new GatewayCliSetupRuntime();
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeFrame(CLI_LOGIN_START_CAPABILITY, { runtime: "claude_code" }, "")),
    /requires run_id/,
  );
});

test("cli.login.start dispatches to the login session manager with the frame's run_id", async () => {
  let startedWith: { runId: string; runtime: string; method?: string } | null = null;
  const fakeSessions = {
    start: async (params: { runId: string; runtime: "claude_code" | "codex"; method?: string }) => {
      startedWith = params;
      return { run_id: params.runId, status: "started" as const, method: "device_auth", awaits_secret: false };
    },
  } as unknown as CliLoginSessionManager;
  const runtime = new GatewayCliSetupRuntime({ loginSessions: fakeSessions });
  const result = await runtime.handleCapabilityInvoke(makeFrame(CLI_LOGIN_START_CAPABILITY, { runtime: "codex" }, "run-42"));
  assert.deepEqual(result, { run_id: "run-42", status: "started", method: "device_auth", awaits_secret: false });
  // BYO-brain multi-method: `method` is threaded through (undefined when
  // omitted, letting the session manager resolve the runtime's default).
  assert.deepEqual(startedWith, { runId: "run-42", runtime: "codex", method: undefined });
});

test("cli.login.start wraps a not_installed failure honestly", async () => {
  const fakeSessions = {
    start: async () => {
      throw new CliLoginError("not_installed", '"claude" was not found on PATH.');
    },
  } as unknown as CliLoginSessionManager;
  const runtime = new GatewayCliSetupRuntime({ loginSessions: fakeSessions });
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeFrame(CLI_LOGIN_START_CAPABILITY, { runtime: "claude_code" }, "run-1")),
    /Claude Code sign-in failed on this Gateway \(not_installed\)/,
  );
});

test("cli.login.input dispatches run_id + code to the login session manager", async () => {
  let inputWith: { runId: string; value: string; kind?: string } | null = null;
  const fakeSessions = {
    input: async (params: { runId: string; value: string; kind?: string }) => {
      inputWith = params;
      return { ok: true };
    },
  } as unknown as CliLoginSessionManager;
  const runtime = new GatewayCliSetupRuntime({ loginSessions: fakeSessions });
  const result = await runtime.handleCapabilityInvoke(
    // Legacy shape: caller sends `code` only — the router auto-fills
    // `kind: "code"` and passes the code as `value` to the session
    // manager. Both new and old backend shapes hit this same path.
    makeFrame(CLI_LOGIN_INPUT_CAPABILITY, { code: "WXYZ-9876" }, "run-9"),
  );
  assert.deepEqual(result, { ok: true });
  assert.deepEqual(inputWith, { runId: "run-9", value: "WXYZ-9876", kind: "code" });
});

test("interruptRun delegates to the login session manager's cancel()", async () => {
  let cancelledRunId: string | null = null;
  const fakeSessions = {
    cancel: async (runId: string) => {
      cancelledRunId = runId;
      return { ok: true };
    },
  } as unknown as CliLoginSessionManager;
  const runtime = new GatewayCliSetupRuntime({ loginSessions: fakeSessions });
  const result = await runtime.interruptRun("run-7");
  assert.deepEqual(result, { interrupted: true, run_id: "run-7" });
  assert.equal(cancelledRunId, "run-7");
});

test("an unrecognized capability_id throws", async () => {
  const runtime = new GatewayCliSetupRuntime();
  await assert.rejects(
    runtime.handleCapabilityInvoke(makeFrame("cli.uninstall", {})),
    /Unsupported cli_setup capability/,
  );
});
