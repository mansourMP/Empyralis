/**
 * Drives the REAL step rules from agent-create-wizard.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-wizard.test.ts
 */

import {
  AGENT_CREATE_COMMIT_STEP,
  AGENT_CREATE_STEPS,
  agentCreateCloseIntent,
  agentCreateNextStep,
  agentCreatePreviousStep,
  agentCreateStepIsPostCommit,
  agentCreateStepStatus,
  agentCreateSurfaceTitle,
  planAgentCreateFooter,
  type AgentCreateStepId,
  type AgentCreateWizardState,
} from "./agent-create-wizard";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function state(patch: Partial<AgentCreateWizardState> = {}): AgentCreateWizardState {
  return {
    step: "identity",
    created: false,
    busy: false,
    hasName: true,
    channelsKnown: false,
    connectedChannelCount: 0,
    ...patch,
  };
}

// ── The sequence itself ───────────────────────────────────────────────────

assert(AGENT_CREATE_STEPS.length === 4, "four steps, not the old wizard's four-with-a-project-screen");
assert(
  AGENT_CREATE_STEPS.map((s) => s.id).join(">") === "identity>model>channel>tools",
  "the order is identity > model > channel > tools",
);
assert(
  !AGENT_CREATE_STEPS.some((s) => /project|placement/i.test(s.id) || /project|placement/i.test(s.label)),
  "NO project or placement step — an agent belongs to the workspace",
);

// ── The commit boundary ───────────────────────────────────────────────────

assert(AGENT_CREATE_COMMIT_STEP === "model", "the create happens when Model is committed, carrying identity with it");
assert(!agentCreateStepIsPostCommit("identity"), "identity precedes the commit");
assert(!agentCreateStepIsPostCommit("model"), "model IS the commit, not after it");
assert(agentCreateStepIsPostCommit("channel"), "channel operates on a real agent");
assert(agentCreateStepIsPostCommit("tools"), "tools operates on a real agent");

{
  // Exactly ONE step's forward button may perform a create — a second one
  // would be a second agent.
  const creators = (["identity", "model", "channel", "tools"] as AgentCreateStepId[]).filter(
    (step) => planAgentCreateFooter(state({ step, created: step !== "identity" && step !== "model" })).forward.action === "create",
  );
  assert(creators.length === 1 && creators[0] === "model", "exactly one step creates, and it is Model");
}

// ── Footer: identity ──────────────────────────────────────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "identity" }));
  assert(plan.back?.action === "cancel", "the first step's backward move is Cancel, not Back");
  assert(plan.forward.label === "Next", "the first step moves forward, it does not create");
  assert(planAgentCreateFooter(state({ step: "identity", hasName: false })).forward.disabled, "no name, no forward");
  assert(planAgentCreateFooter(state({ step: "identity", busy: true })).forward.disabled, "busy blocks forward");
}

// ── Footer: model, the commit ─────────────────────────────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "model" }));
  assert(plan.back?.action === "back", "model can go back to identity — nothing is committed yet");
  assert(plan.forward.label === "Create agent", "the commit button says what it does");
  const busy = planAgentCreateFooter(state({ step: "model", busy: true }));
  assert(busy.forward.label === "Creating…" && busy.forward.disabled, "an in-flight create can't be fired twice");
  assert(busy.back?.disabled === true, "and can't be walked away from mid-flight");
}

// ── Footer: channel — skippable, and honest about why ─────────────────────

{
  const unknown = planAgentCreateFooter(state({ step: "channel", created: true, channelsKnown: false }));
  assert(unknown.back === null, "no Back once the agent is committed — it would return to saved screens");
  assert(
    unknown.forward.label === "Next",
    "while the channel list is still loading the button stays NEUTRAL — 'not asked yet' may not read as 'nothing connected'",
  );

  const none = planAgentCreateFooter(state({ step: "channel", created: true, channelsKnown: true, connectedChannelCount: 0 }));
  assert(none.forward.label === "Skip for now", "a step that can't be completed right now is skippable, never a dead end");
  assert(!none.forward.disabled, "and skipping is never blocked");

  const some = planAgentCreateFooter(
    state({ step: "channel", created: true, channelsKnown: true, connectedChannelCount: 1 }),
  );
  assert(some.forward.label === "Next", "once a channel is really connected the button stops calling it a skip");
}

// ── Footer: tools ─────────────────────────────────────────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "tools", created: true }));
  assert(plan.forward.action === "finish" && plan.forward.label === "Finish", "the last step finishes");
  assert(plan.back?.action === "back", "tools can go back to channel — both act on the same real agent");
}

// ── No dead controls anywhere in the sequence ─────────────────────────────

{
  for (const step of AGENT_CREATE_STEPS.map((s) => s.id)) {
    const plan = planAgentCreateFooter(state({ step, created: agentCreateStepIsPostCommit(step) }));
    assert(plan.back === null || !plan.back.disabled, `step "${step}" never renders a permanently disabled Back`);
    assert(plan.forward.label.trim().length > 0, `step "${step}" always names its forward action`);
  }
}

// ── Navigation ────────────────────────────────────────────────────────────

assert(agentCreateNextStep("identity") === "model", "identity advances to model");
assert(agentCreateNextStep("tools") === null, "the last step has nowhere further to go");
assert(agentCreatePreviousStep("identity") === null, "the first step has nowhere back to go");
assert(agentCreatePreviousStep("tools") === "channel", "tools goes back to channel");

// ── Step status ───────────────────────────────────────────────────────────

assert(agentCreateStepStatus("identity", "channel") === "done", "passed steps read as done");
assert(agentCreateStepStatus("channel", "channel") === "current", "the current step reads as current");
assert(agentCreateStepStatus("tools", "channel") === "todo", "steps ahead read as todo");

// ── Closing is honest about what already happened ─────────────────────────

assert(agentCreateCloseIntent(false) === "discard", "closing before the commit discards — nothing exists");
assert(
  agentCreateCloseIntent(true) === "open_agent",
  "closing AFTER the commit opens the agent — 'I stopped early' is not 'nothing happened'",
);

assert(agentCreateSurfaceTitle(false, "Ridge") === "New agent", "before the commit the surface is still 'New agent'");
assert(
  agentCreateSurfaceTitle(true, "Ridge") === "Ridge",
  "after it the surface names the agent, so its existence is visible without pressing anything",
);
assert(agentCreateSurfaceTitle(true, "   ") === "New agent", "a blank name never renders as an empty title");

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
