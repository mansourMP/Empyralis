/**
 * Drives the REAL step rules from agent-create-wizard.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-wizard.test.ts
 *
 * ── ASSERTIONS IN HERE ARE INVERTED, NEVER DELETED ───────────────────────
 * Each was correct for the shape it was written against. They are kept,
 * pointed the other way, with the reason in the label — so nobody reads the
 * current shape as something that merely slipped past a weaker test.
 *
 * 1. "NO project or placement step". The PROJECT half still holds and is
 *    still asserted (an agent belongs to the workspace, CLAUDE.md
 *    2026-08-20). The PLACEMENT half is reversed: placement is step 1's
 *    second question, because step 2 cannot honestly offer "Your
 *    subscription" / "Run locally" without knowing whether a machine
 *    exists.
 * 2. "the channel step is REQUIRED — an unreachable agent may not walk on
 *    to Finish". Both post-commit steps are skippable in one press, per the
 *    brief. The honesty moved from a BLOCK to the LABEL: the button reads
 *    "Skip for now" rather than "Next"/"Finish" whenever nothing is
 *    connected. Asserted below in that form, so the guarantee is not lost —
 *    only relocated.
 * 3. "THREE steps — Channel and Apps were two screens asking the same
 *    question", and its sibling "the order is identity > brain > reach".
 *    REVERSED, 2026-08-21, on the founder's explicit instruction:
 *    *"channels and application must be separated, do you understand?"*
 *    The merge's own argument (two optional screens read as ceremony) is
 *    answered by the one-press skip rather than by fusing them, and both
 *    halves of that are asserted below: FOUR steps, and each of the two
 *    post-commit ones movable in a single press.
 * 4. "no Back once the agent is committed". Narrowed rather than dropped:
 *    it still holds for Channels, whose only predecessor (Brain) is
 *    committed and saved. Apps DOES offer a Back, because Channels is a
 *    live, still-editable screen one press away — the rule is "no Back to a
 *    screen that can no longer change anything", not "no Back after the
 *    commit". Both halves asserted separately.
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
    placementReady: true,
    placementBlockedReason: "",
    brainReady: true,
    brainBlockedReason: "",
    channelsKnown: false,
    connectedChannelCount: 0,
    connectedAppCount: 0,
    ...patch,
  };
}

const ALL_STEPS = AGENT_CREATE_STEPS.map((s) => s.id);

// ── The sequence itself ───────────────────────────────────────────────────

// INVERTED, see this file's header (3). Was: THREE steps, identity>brain>reach.
assert(
  AGENT_CREATE_STEPS.length === 4,
  'FOUR steps — the founder: "channels and application must be separated"',
);
assert(
  ALL_STEPS.join(">") === "identity>brain>channels>apps",
  "the order is identity > brain > channels > apps",
);
assert(
  AGENT_CREATE_STEPS.every((s) => s.label.trim().split(/\s+/).length === 1),
  "every step label is ONE noun — the strip is where you are, not a sentence",
);
assert(
  !AGENT_CREATE_STEPS.some((s) => /^tools$/i.test(s.label.trim())),
  'NO step is called "Tools" — Configure has a genuinely different Tools section',
);
// INVERTED, half. See this file's header.
assert(
  !AGENT_CREATE_STEPS.some((s) => /project/i.test(s.id) || /project/i.test(s.label)),
  "NO project step, still — an agent belongs to the workspace (CLAUDE.md 2026-08-20)",
);

// ── The commit boundary ───────────────────────────────────────────────────

assert(AGENT_CREATE_COMMIT_STEP === "brain", "the create happens when Brain is committed, carrying identity with it");
assert(!agentCreateStepIsPostCommit("identity"), "identity precedes the commit");
assert(!agentCreateStepIsPostCommit("brain"), "brain IS the commit, not after it");
assert(agentCreateStepIsPostCommit("channels"), "channels operates on a real agent");
assert(agentCreateStepIsPostCommit("apps"), "apps operates on a real agent");

{
  // Exactly ONE step's forward button may perform a create — a second one
  // would be a second agent.
  const creators = ALL_STEPS.filter(
    (step) =>
      planAgentCreateFooter(state({ step, created: agentCreateStepIsPostCommit(step) })).forward.action === "create",
  );
  assert(creators.length === 1 && creators[0] === "brain", "exactly one step creates, and it is Brain");

  // …and exactly ONE step ends the sequence. Splitting Reach into two is
  // where a second "finish" would slip in unnoticed: both new steps are
  // optional and post-commit, and only the last of them may leave.
  const finishers = ALL_STEPS.filter(
    (step) =>
      planAgentCreateFooter(state({ step, created: agentCreateStepIsPostCommit(step) })).forward.action === "finish",
  );
  assert(finishers.length === 1 && finishers[0] === "apps", "exactly one step finishes, and it is the last one");
}

// ── Footer: identity, which now also carries placement ────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "identity" }));
  assert(plan.back?.action === "cancel", "the first step's backward move is Cancel, not Back");
  assert(plan.forward.label === "Next", "the first step moves forward, it does not create");
  assert(planAgentCreateFooter(state({ step: "identity", hasName: false })).forward.disabled, "no name, no forward");
  assert(planAgentCreateFooter(state({ step: "identity", busy: true })).forward.disabled, "busy blocks forward");

  const noBox = planAgentCreateFooter(
    state({ step: "identity", placementReady: false, placementBlockedReason: "No computers paired yet." }),
  );
  assert(noBox.forward.disabled, "a placement that names no machine cannot move forward");
  assert(
    noBox.blockedReason === "No computers paired yet.",
    "and the placement module's OWN sentence is what is shown — never a second copy of it here",
  );
  assert(
    planAgentCreateFooter(state({ step: "identity", placementReady: false, placementBlockedReason: "" }))
      .blockedReason === "",
    "a block whose reason is not known yet says nothing — 'no machines' and 'not looked yet' are different facts",
  );
  assert(
    planAgentCreateFooter(state({ step: "identity", hasName: false, placementBlockedReason: "x" })).blockedReason ===
      "Give this agent a name.",
    "the missing NAME is named first — it is the field the eye is already on",
  );
}

// ── Footer: brain, the commit ─────────────────────────────────────────────

{
  const plan = planAgentCreateFooter(state({ step: "brain" }));
  assert(plan.back?.action === "back", "brain can go back to identity — nothing is committed yet");
  assert(plan.forward.label === "Create agent", "the commit button says what it does");
  const busy = planAgentCreateFooter(state({ step: "brain", busy: true }));
  assert(busy.forward.label === "Creating…" && busy.forward.disabled, "an in-flight create can't be fired twice");
  assert(busy.back?.disabled === true, "and can't be walked away from mid-flight");

  const noKey = planAgentCreateFooter(
    state({ step: "brain", brainReady: false, brainBlockedReason: "Paste your API key." }),
  );
  assert(noKey.forward.disabled, "an incomplete brain cannot commit");
  assert(noKey.blockedReason === "Paste your API key.", "and the brain module's own sentence is what is shown");
}

// ── Footer: channels — skippable in ONE press, honest in the LABEL ────────
//
// INVERTED from "the channel step is REQUIRED". See this file's header (2):
// the brief makes this step skippable, so the guarantee that an unreachable
// agent is never called finished moved into the button's word.

{
  const unknown = planAgentCreateFooter(state({ step: "channels", created: true, channelsKnown: false }));
  assert(
    unknown.back === null,
    "no Back from Channels — the step behind it is the committed, saved Brain",
  );
  assert(!unknown.forward.disabled, "channels always moves in one press — skipping is one action, not a fight");
  assert(
    unknown.blockedReason === "",
    "and it SAYS NOTHING while unknown — 'not asked yet' may never be reported as 'nothing connected'",
  );

  const none = planAgentCreateFooter(
    state({ step: "channels", created: true, channelsKnown: true, connectedChannelCount: 0 }),
  );
  assert(!none.forward.disabled, "nothing is connected, and the way on is still one press");
  assert(
    /skip/i.test(none.forward.label) && !/next|finish/i.test(none.forward.label),
    'a step that connected nothing is never "Next"ed past — the button says Skip, which is what it does',
  );
  assert(none.blockedReason.trim().length > 0, "and the one fact about what stays undone is stated");
  assert(
    none.blockedReason.split(/[.!?]\s/).filter((part) => part.trim()).length <= 2,
    "at most two short sentences — a professional tool labels, it does not lecture",
  );
  assert(
    none.dismiss.kind === "defer",
    "the head's exit defers rather than cancels — the agent is saved and its page carries the same step forward",
  );

  const some = planAgentCreateFooter(
    state({ step: "channels", created: true, channelsKnown: true, connectedChannelCount: 1 }),
  );
  assert(some.forward.label === "Next", "one real connected channel earns the ordinary forward word");
  assert(some.blockedReason === "", "and nothing is left to explain");
  assert(some.forward.action === "next", "channels is not the last step — it advances, it does not finish");
}

// ── Footer: apps — the same one-press rule, and NOTHING to explain ────────
//
// ADDED with the split. The asymmetry with Channels above is the point: an
// agent with no channel is unreachable, which is a fact worth a sentence; an
// agent with no apps just cannot open Notion yet, which is not.

{
  const none = planAgentCreateFooter(state({ step: "apps", created: true, connectedAppCount: 0 }));
  assert(!none.forward.disabled, "apps moves in one press too");
  assert(
    /skip/i.test(none.forward.label) && !/finish/i.test(none.forward.label),
    "nothing connected on this step, so the last button says Skip rather than claiming completion",
  );
  assert(
    none.blockedReason === "",
    "and it states NO reason — an agent with no apps is not a broken agent",
  );
  assert(none.forward.action === "finish", "either way the last step finishes");

  // INVERTED half of "no Back once committed" — see this file's header (4).
  assert(
    none.back?.action === "back",
    "apps CAN go back to channels — that screen is live and still editable, so the control is not a lie",
  );
  assert(
    none.back?.label === "Back" && !/cancel/i.test(none.back?.label ?? ""),
    "and it is a Back, never a Cancel — there is nothing left to cancel",
  );

  const some = planAgentCreateFooter(state({ step: "apps", created: true, connectedAppCount: 1 }));
  assert(some.forward.label === "Finish", "one real connected app earns the word Finish");

  const busy = planAgentCreateFooter(state({ step: "apps", created: true, busy: true }));
  assert(busy.back?.disabled === true, "and the Back is not walkable mid-flight");
}

// ── Nothing claims to cancel something that already happened ──────────────

{
  for (const step of ALL_STEPS) {
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

{
  for (const step of ALL_STEPS) {
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

// ── No dead controls anywhere in the sequence ─────────────────────────────

{
  for (const step of ALL_STEPS) {
    const plan = planAgentCreateFooter(state({ step, created: agentCreateStepIsPostCommit(step) }));
    assert(plan.back === null || !plan.back.disabled, `step "${step}" never renders a permanently disabled Back`);
    assert(plan.forward.label.trim().length > 0, `step "${step}" always names its forward action`);
  }
}

// ── Navigation ────────────────────────────────────────────────────────────

assert(agentCreateNextStep("identity") === "brain", "identity advances to brain");
assert(agentCreateNextStep("brain") === "channels", "the commit lands on channels");
assert(agentCreateNextStep("channels") === "apps", "channels advances to apps");
assert(agentCreateNextStep("apps") === null, "the last step has nowhere further to go");
assert(agentCreatePreviousStep("identity") === null, "the first step has nowhere back to go");
assert(agentCreatePreviousStep("brain") === "identity", "brain goes back to identity");
assert(agentCreatePreviousStep("apps") === "channels", "apps goes back to channels");

// ── Step status ───────────────────────────────────────────────────────────

assert(agentCreateStepStatus("identity", "apps") === "done", "passed steps read as done");
assert(agentCreateStepStatus("apps", "apps") === "current", "the current step reads as current");
assert(agentCreateStepStatus("apps", "brain") === "todo", "steps ahead read as todo");
assert(agentCreateStepStatus("channels", "apps") === "done", "and the newly split step reads as done once passed");

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
// The Apps step embeds ConnectorsTab; Configure carries a separate,
// genuinely different "Tools" section (the built-in capability toggles).
// Both were once labelled Tools. The step id carries "apps" and never
// "tools" (asserted above), so what is guarded here is the OTHER half: that
// the three places naming the connector section still agree with each other,
// and that none of them calls it Tools. Canaries below, because a scan that
// silently stops matching reports green forever.

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
  assert(new Set(labels).size === labels.length, `no two sections share a label — ${labels.join(", ")}`);

  const connectorsLabel = rows.find((r) => r.id === "connectors")?.label ?? "";
  assert(
    connectorsLabel.toLowerCase() !== "tools",
    'the connector picker is never called "Tools" — that name belongs to the capability toggles',
  );

  // The connector section is NAMED in two more places (this codebase's own
  // "a list copied into a third place" defect, one surface over).
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
