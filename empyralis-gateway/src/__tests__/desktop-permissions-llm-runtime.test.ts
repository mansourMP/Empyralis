import test from "node:test";
import assert from "node:assert/strict";

import {
  assertCapabilityPermissionReady,
  capabilityPermissionReady,
  capabilityPermissionStatus,
  desktopPermissionForCapability,
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
