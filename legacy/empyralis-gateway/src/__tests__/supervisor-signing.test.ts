import test from "node:test";
import assert from "node:assert/strict";

import { signSupervisorExecuteRequest } from "../supervisor/signing";

const baseInput = {
  requestId: "req-1",
  capabilityId: "shell.execute",
  runId: "run-1",
  traceId: "trace-1",
  workspaceId: "ws-1",
  arguments: { command: "pwd" },
  runtimeAccessMode: "default",
  empyralisApproved: false,
  agentScope: "sage",
  policy: { mode: "default", agent_scope: "sage", allowed_paths: ["/tmp/project"] },
  nonce: "nonce-1",
  expiresAt: "2026-06-06T12:00:00.000Z",
};

test("supervisor execute signature covers arguments and policy envelope", () => {
  const secret = "test-secret";
  const original = signSupervisorExecuteRequest(secret, baseInput);
  const tamperedArguments = signSupervisorExecuteRequest(secret, {
    ...baseInput,
    arguments: { command: "cat .env" },
  });
  const tamperedPolicy = signSupervisorExecuteRequest(secret, {
    ...baseInput,
    policy: { mode: "full_access", agent_scope: "sage", full_access_warning_acknowledged: true },
  });

  assert.notEqual(tamperedArguments, original);
  assert.notEqual(tamperedPolicy, original);
});

test("supervisor execute signature is canonical for object key order", () => {
  const secret = "test-secret";
  const first = signSupervisorExecuteRequest(secret, baseInput);
  const second = signSupervisorExecuteRequest(secret, {
    ...baseInput,
    arguments: { z: true, command: "pwd", a: 1 },
    policy: { z: true, mode: "default", allowed_paths: ["/tmp/project"], agent_scope: "sage" },
  });
  const third = signSupervisorExecuteRequest(secret, {
    ...baseInput,
    arguments: { a: 1, command: "pwd", z: true },
    policy: { agent_scope: "sage", allowed_paths: ["/tmp/project"], mode: "default", z: true },
  });

  assert.notEqual(first, second);
  assert.equal(second, third);
});
