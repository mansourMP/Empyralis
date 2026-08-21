/**
 * Drives the REAL step rules from agent-create-wizard.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-wizard.test.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

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
  AGENT_CREATE_STEPS.map((s) => s.id).join(">") === "identity>model>channel>apps",
  "the order is identity > model > channel > apps",
);
assert(
  !AGENT_CREATE_STEPS.some((s) => /^tools$/i.test(s.label.trim())),
  'NO step is called "Tools" — Configure already has a genuinely different Tools section, and step 4 is the apps/MCP picker',
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
assert(agentCreateStepIsPostCommit("apps"), "apps operates on a real agent");

{
  // Exactly ONE step's forward button may perform a create — a second one
  // would be a second agent.
  const creators = (["identity", "model", "channel", "apps"] as AgentCreateStepId[]).filter(
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
  assert(unknown.forward.disabled, "nothing moves forward off a channel state we have not been told yet");
  assert(
    unknown.blockedReason === "",
    "and it SAYS NOTHING while unknown — 'not asked yet' may never be reported as 'nothing connected'",
  );

  const none = planAgentCreateFooter(state({ step: "channel", created: true, channelsKnown: true, connectedChannelCount: 0 }));
  assert(none.forward.disabled, "the channel step is REQUIRED — an unreachable agent may not walk on to Finish");
  assert(
    !/skip/i.test(none.forward.label),
    'nothing calls leaving a required step "Skip" — the founder: "channels cannot be skipped"',
  );
  assert(none.blockedReason.trim().length > 0, "a blocked forward states the one fact that explains it");
  assert(
    none.blockedReason.split(/[.!?]\s/).filter((part) => part.trim()).length === 1,
    "one sentence, not policy prose — a professional tool labels, it does not lecture",
  );
  assert(
    none.dismiss.kind === "defer",
    "and the way out is DEFERRING, which is a real exit — the agent is saved and its page carries the same step forward",
  );

  const some = planAgentCreateFooter(
    state({ step: "channel", created: true, channelsKnown: true, connectedChannelCount: 1 }),
  );
  assert(!some.forward.disabled, "one real connected channel completes the step");
  assert(some.forward.label === "Next" && some.blockedReason === "", "and nothing is left to explain");
}

// ── Nothing claims to cancel something that already happened ──────────────
//
// The founder pressed the head's X on step 3 and found a created agent
// afterwards. The commit sits after step 2, so from step 3 on there is
// nothing left to cancel — and a dismiss gesture must not quietly delete a
// real agent either. The CONTROL changes instead.

{
  for (const step of AGENT_CREATE_STEPS.map((s) => s.id)) {
    const created = agentCreateStepIsPostCommit(step);
    const plan = planAgentCreateFooter(state({ step, created }));
    if (created) {
      assert(plan.dismiss.kind === "defer", `step "${step}" is post-commit, so dismissing defers rather than cancels`);
      assert(
        !/cancel|discard|close/i.test(plan.dismiss.label),
        `step "${step}" never labels its dismiss control as a cancel — the agent exists`,
      );
      assert(
        plan.back === null || plan.back.action !== "cancel",
        `step "${step}" offers no Cancel in the footer either`,
      );
    } else {
      assert(plan.dismiss.kind === "cancel", `step "${step}" precedes the commit, so dismissing genuinely discards`);
    }
    assert(plan.dismiss.label.trim().length > 0, `step "${step}" always names its dismiss control`);
  }
}

// ── The one accent fill ───────────────────────────────────────────────────
//
// CLAUDE.md: "One accent colour, spent on the single primary action in a
// view." A blocked forward is not that action, so it keeps its label and
// loses the fill — never a saturated purple button that refuses to be
// pressed. Everything else in this surface is neutral by CSS, so this is
// the only accent-bearing element the rules produce.

{
  for (const step of AGENT_CREATE_STEPS.map((s) => s.id)) {
    const created = agentCreateStepIsPostCommit(step);
    const open = planAgentCreateFooter(state({ step, created, channelsKnown: true, connectedChannelCount: 1 }));
    assert(open.forward.accent, `step "${step}" spends the accent on the move that is actually available`);
    const blocked = planAgentCreateFooter(
      state({ step, created, busy: true, channelsKnown: true, connectedChannelCount: 0 }),
    );
    assert(!blocked.forward.accent, `step "${step}" drops the fill the moment the move is unavailable`);
    assert(blocked.forward.label.trim().length > 0, `step "${step}" still names it — losing the fill is not going dark`);
  }
}

// ── Footer: apps ──────────────────────────────────────────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "apps", created: true }));
  assert(plan.forward.action === "finish" && plan.forward.label === "Finish", "the last step finishes");
  assert(plan.back?.action === "back", "apps can go back to channel — both act on the same real agent");
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
assert(agentCreateNextStep("apps") === null, "the last step has nowhere further to go");
assert(agentCreatePreviousStep("identity") === null, "the first step has nowhere back to go");
assert(agentCreatePreviousStep("apps") === "channel", "apps goes back to channel");

// ── Step status ───────────────────────────────────────────────────────────

assert(agentCreateStepStatus("identity", "channel") === "done", "passed steps read as done");
assert(agentCreateStepStatus("channel", "channel") === "current", "the current step reads as current");
assert(agentCreateStepStatus("apps", "channel") === "todo", "steps ahead read as todo");

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

// ── "Tools" meant two different things, and only a SOURCE scan can see it ─
//
// The wizard's step 4 embeds ConnectorsTab; Configure carries a separate,
// genuinely different "Tools" section (the built-in capability toggles).
// Both were labelled Tools. The collision lives half in this pure module and
// half in a React component `tsx` cannot import, so the labels are read out
// of FleetAgentDetail.tsx's own source — the expected set and the actual set
// from different places, per this codebase's standing rule. Canaries below,
// because a scan that silently stops matching reports green forever.

{
  const source = readFileSync(join(__dirname, "FleetAgentDetail.tsx"), "utf8");
  const tabsBlock = source.match(
    /const TABS: \{ id: TabId; label: string; icon: LucideIcon \}\[\] = \[([\s\S]*?)\n\];/,
  );
  assert(Boolean(tabsBlock), "CANARY: the TABS declaration is still findable in FleetAgentDetail.tsx");

  const rows = [...(tabsBlock?.[1] ?? "").matchAll(/\{\s*id:\s*"([a-z]+)",\s*label:\s*"([^"]+)"/g)].map(
    (m) => ({ id: m[1], label: m[2] }),
  );
  assert(rows.length >= 10, `CANARY: the tab rows still parse (found ${rows.length})`);
  assert(
    rows.some((r) => r.id === "connectors") && rows.some((r) => r.id === "tools"),
    "CANARY: both halves of the collision still exist as separate tabs",
  );

  const labels = rows.map((r) => r.label.toLowerCase());
  assert(
    new Set(labels).size === labels.length,
    `no two sections share a label — ${labels.join(", ")}`,
  );

  const connectorsLabel = rows.find((r) => r.id === "connectors")?.label ?? "";
  const step4 = AGENT_CREATE_STEPS[3];
  assert(
    step4.label.toLowerCase() === connectorsLabel.toLowerCase(),
    `step 4 ("${step4.label}") is named for what it embeds, which Configure calls "${connectorsLabel}"`,
  );
  assert(
    connectorsLabel.toLowerCase() !== "tools",
    'the connector picker is never called "Tools" — that name belongs to the capability toggles',
  );

  // The same section is NAMED in three more places (this codebase's own
  // "a channel list copied into a third place" defect, one surface over).
  // They are hand-kept literals in React files, so the only thing that can
  // hold them together is a scan that reads all of them.
  const others: Array<[string, RegExp]> = [
    ["Breadcrumbs.tsx", /\n\s*connectors:\s*"([^"]+)"/],
    ["FleetCommandPalette.tsx", /\{\s*id:\s*"connectors",\s*label:\s*"([^"]+)"/],
  ];
  for (const [file, re] of others) {
    const m = readFileSync(join(__dirname, file), "utf8").match(re);
    assert(Boolean(m), `CANARY: ${file} still names the connectors section`);
    assert(
      (m?.[1] ?? "").toLowerCase() === connectorsLabel.toLowerCase(),
      `${file} calls it "${m?.[1]}" while Configure calls it "${connectorsLabel}"`,
    );
  }
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
