import test from "node:test";
import assert from "node:assert/strict";

import {
  assertCapabilityPermissionReady,
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
  setLlmRuntimeClaudeCodeReady,
  setLlmRuntimeCodexReady,
  setLlmRuntimeOllamaReady,
} from "../runtime/desktop-permissions";

test("llm.generate maps to the llm_runtime permission", () => {
  assert.equal(desktopPermissionForCapability("llm.generate"), "llm_runtime");
});

test("llm_runtime has no granted-by-default fallback — restricted until Ollama is confirmed ready", () => {
  setLlmRuntimeOllamaReady(false);
  const status = capabilityPermissionStatus("llm.generate", {});
  assert.equal(status.state, "restricted");
  assert.equal(capabilityPermissionReady("llm.generate", {}), false);
  assert.throws(() => assertCapabilityPermissionReady("llm.generate", {}), /blocked\/local_permission_denied/);
});

test("llm_runtime is granted once Ollama is confirmed ready", () => {
  setLlmRuntimeOllamaReady(true);
  const status = capabilityPermissionStatus("llm.generate", {});
  assert.equal(status.state, "granted");
  assert.equal(capabilityPermissionReady("llm.generate", {}), true);
  assert.doesNotThrow(() => assertCapabilityPermissionReady("llm.generate", {}));
  setLlmRuntimeOllamaReady(false); // restore default for any other tests sharing this process
});

test("an explicit env override wins over the live Ollama-readiness flag either direction", () => {
  setLlmRuntimeOllamaReady(true);
  assert.equal(
    capabilityPermissionStatus("llm.generate", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_LLM_RUNTIME: "denied" }).state,
    "denied",
  );
  setLlmRuntimeOllamaReady(false);
  assert.equal(
    capabilityPermissionStatus("llm.generate", { EMPYRALIS_AGENT_COMPUTER_PERMISSION_LLM_RUNTIME: "granted" }).state,
    "granted",
  );
});

// cli_subscription (Phase 3): llm_runtime must ALSO open up for a box with
// only Claude Code or only Codex ready — a subscription-only box (no Ollama
// installed at all) must still get llm.generate advertised, otherwise the
// capability never reaches the router and the control plane sees a
// misleading "capability missing" instead of "claude_code isn't ready".

test("llm_runtime is granted when ONLY Claude Code is confirmed ready (no Ollama, no Codex)", () => {
  setLlmRuntimeOllamaReady(false);
  setLlmRuntimeCodexReady(false);
  setLlmRuntimeClaudeCodeReady(true);
  assert.equal(capabilityPermissionStatus("llm.generate", {}).state, "granted");
  assert.equal(capabilityPermissionReady("llm.generate", {}), true);
  setLlmRuntimeClaudeCodeReady(false); // restore default for any other tests sharing this process
});

test("llm_runtime is granted when ONLY Codex is confirmed ready (no Ollama, no Claude Code)", () => {
  setLlmRuntimeOllamaReady(false);
  setLlmRuntimeClaudeCodeReady(false);
  setLlmRuntimeCodexReady(true);
  assert.equal(capabilityPermissionStatus("llm.generate", {}).state, "granted");
  assert.equal(capabilityPermissionReady("llm.generate", {}), true);
  setLlmRuntimeCodexReady(false); // restore default for any other tests sharing this process
});

test("llm_runtime stays restricted when none of the three backends (Ollama, Claude Code, Codex) are ready", () => {
  setLlmRuntimeOllamaReady(false);
  setLlmRuntimeClaudeCodeReady(false);
  setLlmRuntimeCodexReady(false);
  assert.equal(capabilityPermissionStatus("llm.generate", {}).state, "restricted");
  assert.equal(capabilityPermissionReady("llm.generate", {}), false);
});
