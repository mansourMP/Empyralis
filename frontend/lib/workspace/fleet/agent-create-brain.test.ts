/**
 * Drives the REAL brain rules from agent-create-brain.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-brain.test.ts
 */

import {
  AGENT_CREATE_BRAIN_OPTIONS,
  AGENT_CREATE_DEFAULT_BRAIN_MODE,
  agentCreateBrainModeAvailable,
  agentCreateBrainOptionsFor,
  agentCreateBrainPlacementNote,
  planAgentCreateBrain,
  type AgentCreateBrainMode,
  type AgentCreateBrainState,
} from "./agent-create-brain";
import { runtimeForProvider } from "./fleet-provider-constants";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) passed++;
  else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function brain(patch: Partial<AgentCreateBrainState> = {}): AgentCreateBrainState {
  return {
    placement: "cloud",
    mode: "platform",
    provider: "deepseek",
    model: "deepseek-v4-pro",
    apiKey: "",
    providerHasCredential: false,
    nodeId: "",
    ...patch,
  };
}

const ALL_MODES: AgentCreateBrainMode[] = AGENT_CREATE_BRAIN_OPTIONS.map((o) => o.id);

// ── The four options ──────────────────────────────────────────────────────

assert(
  ALL_MODES.join(">") === "platform>byok>subscription>local",
  "the same four the deleted wizard asked, in the same order — platform first",
);
assert(AGENT_CREATE_DEFAULT_BRAIN_MODE === "platform", "platform credits is the default");
assert(
  AGENT_CREATE_BRAIN_OPTIONS.every((o) => o.label.trim() && o.body.trim()),
  "every option states what it is and who pays, on its own face",
);
assert(
  AGENT_CREATE_BRAIN_OPTIONS.filter((o) => o.needsMachine).map((o) => o.id).join(",") ===
    "subscription,local",
  "exactly the two machine-bound modes declare it — and the gate is DERIVED from that flag, never a name",
);

// ── The placement gate ────────────────────────────────────────────────────

{
  assert(
    agentCreateBrainOptionsFor("cloud").map((o) => o.id).join(",") === "platform,byok",
    "a cloud-only agent is offered exactly the two modes it can complete",
  );
  for (const placement of ["vps", "gateway"] as const) {
    assert(
      agentCreateBrainOptionsFor(placement).length === AGENT_CREATE_BRAIN_OPTIONS.length,
      `a ${placement} placement unlocks all four`,
    );
  }
  assert(
    !agentCreateBrainModeAvailable("subscription", "cloud"),
    "'Your subscription' is NOT RENDERED on cloud — a control that cannot be completed is not rendered disabled",
  );
  assert(agentCreateBrainModeAvailable("local", "gateway"), "…and is available the moment a machine exists");

  assert(
    agentCreateBrainPlacementNote("cloud").trim().length > 0,
    "a cloud agent is told ONCE where to go to unlock the missing two",
  );
  assert(/step 1/i.test(agentCreateBrainPlacementNote("cloud")), "and the note points at the decision that unlocks them");
  assert(
    agentCreateBrainPlacementNote("gateway") === "",
    "…and nothing is said when nothing is missing — a note about an absence nobody is experiencing is a lecture",
  );
}

// ── platform ──────────────────────────────────────────────────────────────

{
  const plan = planAgentCreateBrain(brain());
  assert(plan.ready, "a platform pick with a model is ready");
  assert(
    plan.modelChoice?.mode === "platform_credits" && plan.modelChoice.model === "deepseek-v4-pro",
    "and it rides the CREATE call, atomically",
  );
  assert(plan.modelConfigPatch === null, "nothing is patched afterwards — there is nothing the create cannot carry");
  assert(!plan.savesApiKey, "and no credential is written");

  assert(!planAgentCreateBrain(brain({ model: "" })).ready, "no model, no commit");
  assert(/model/i.test(planAgentCreateBrain(brain({ model: "" })).blockedReason), "and the reason names the model");
}

// ── byok ──────────────────────────────────────────────────────────────────

{
  const fresh = brain({ mode: "byok", provider: "anthropic", model: "claude-opus-4-6" });
  const noKey = planAgentCreateBrain(fresh);
  assert(!noKey.ready && /key/i.test(noKey.blockedReason), "a provider with no saved credential needs the key pasted");

  const pasted = planAgentCreateBrain({ ...fresh, apiKey: "sk-live-x" });
  assert(pasted.ready, "…and is ready once it is");
  assert(
    pasted.savesApiKey,
    "the credential is a PREREQUISITE — saved before the agent exists, so a failure has nothing to explain away",
  );
  assert(pasted.modelChoice?.mode === "byok_api", "the pick still rides the create call atomically");
  assert(pasted.modelConfigPatch === null, "byok never needs a follow-up patch");

  const saved = planAgentCreateBrain({ ...fresh, providerHasCredential: true });
  assert(saved.ready && !saved.savesApiKey, "an already-credentialed provider asks for no key and writes none");

  assert(!planAgentCreateBrain({ ...fresh, providerHasCredential: true, model: "" }).ready, "still needs a model");
  assert(!planAgentCreateBrain({ ...fresh, provider: "" }).ready, "and a provider");
}

// ── subscription ──────────────────────────────────────────────────────────

{
  const base = brain({
    placement: "gateway",
    mode: "subscription",
    provider: "openai-codex",
    model: "gpt-5.6-terra",
    nodeId: "gw_1",
  });
  const plan = planAgentCreateBrain(base);
  assert(plan.ready, "a subscription bound to a machine is ready");
  assert(plan.modelChoice === null, "it does NOT ride the create call — three keys cannot carry a gateway binding");
  assert(
    plan.modelConfigPatch?.mode === "cli_subscription" &&
      plan.modelConfigPatch.gateway_binding === "gw_1" &&
      plan.modelConfigPatch.runtime === runtimeForProvider("openai-codex"),
    "…it patches, carrying the binding and the runtime DERIVED from the provider rather than typed here",
  );
  assert(
    Object.prototype.hasOwnProperty.call(plan.modelConfigPatch ?? {}, "model"),
    "the model key is always present — model_config is replaced WHOLE, so omitting it would keep the seed's provider id",
  );
  assert(
    planAgentCreateBrain({ ...base, model: "" }).ready,
    "a blank model is legitimate for a CLI — it means the CLI's own default wins",
  );
  assert(!planAgentCreateBrain({ ...base, provider: "" }).ready, "but a subscription with no CLI named is not");
}

// ── local ─────────────────────────────────────────────────────────────────

{
  const base = brain({
    placement: "vps",
    mode: "local",
    provider: "ollama",
    model: "llama3.2",
    nodeId: "v1",
  });
  const plan = planAgentCreateBrain(base);
  assert(plan.ready, "a local model bound to a machine is ready");
  assert(
    plan.modelConfigPatch?.mode === "local" && plan.modelConfigPatch.runtime === "ollama",
    "and patches a local config",
  );
  assert(
    !planAgentCreateBrain({ ...base, model: "" }).ready,
    "…but a local model MUST be named — unlike a CLI, there is no default to fall back to",
  );
}

// ── A mode the placement no longer offers is REFUSED, never reinterpreted ─
//
// Pick "Run locally" on a paired computer, walk back to step 1 and choose
// Cloud only. Silently resolving that to platform credits would hand the
// person a different agent than the one they asked for.

{
  const stale = planAgentCreateBrain(
    brain({ placement: "cloud", mode: "local", provider: "ollama", model: "llama3.2", nodeId: "v1" }),
  );
  assert(!stale.ready, "a mode the current placement does not offer cannot commit");
  assert(/computer|step 1/i.test(stale.blockedReason), "and the reason points at the decision that would allow it");
  assert(stale.modelChoice === null && stale.modelConfigPatch === null, "and it emits nothing at all");
}

// ── EXACTLY ONE of the two carriers is ever non-null ──────────────────────
//
// A pick that both rides the create AND patches would write the model twice,
// with the second write able to disagree with the first.

{
  const cases: AgentCreateBrainState[] = [
    brain(),
    brain({ mode: "byok", provider: "anthropic", model: "claude-opus-4-6", apiKey: "k" }),
    brain({ placement: "gateway", mode: "subscription", provider: "openai-codex", model: "m", nodeId: "g" }),
    brain({ placement: "gateway", mode: "local", provider: "ollama", model: "llama3.2", nodeId: "g" }),
  ];
  for (const state of cases) {
    const plan = planAgentCreateBrain(state);
    assert(plan.ready, `the ${state.mode} case under test is actually ready`);
    const carriers = [plan.modelChoice, plan.modelConfigPatch].filter(Boolean).length;
    assert(carriers === 1, `mode "${state.mode}" writes the model through exactly one carrier, not two`);
  }
}

// ── A machine mode can never emit a binding-less config ───────────────────

{
  for (const mode of ["subscription", "local"] as AgentCreateBrainMode[]) {
    const plan = planAgentCreateBrain(
      brain({ placement: "gateway", mode, provider: mode === "local" ? "ollama" : "openai-codex", model: "m", nodeId: "" }),
    );
    assert(!plan.ready, `mode "${mode}" with no machine named is refused`);
    assert(plan.modelConfigPatch === null, `…and emits no config for mode "${mode}"`);
  }
}

// ── Every refusal states one short fact ───────────────────────────────────

{
  const refusals = [
    planAgentCreateBrain(brain({ model: "" })),
    planAgentCreateBrain(brain({ mode: "byok", provider: "anthropic", model: "m" })),
    planAgentCreateBrain(brain({ placement: "cloud", mode: "subscription" })),
  ];
  for (const plan of refusals) {
    assert(!plan.ready, "the refusal under test really is one");
    assert(plan.blockedReason.trim().length > 0, "a blocked commit always states its one fact");
    assert(
      plan.blockedReason.split(/[.!?]\s/).filter((s) => s.trim()).length === 1,
      `one sentence, not policy prose — got "${plan.blockedReason}"`,
    );
  }
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
