/**
 * THE STEP SEQUENCE FOR CREATING AN AGENT — pure, framework-free, so a plain
 * `tsx` test drives the real rule (same discipline as agent-count-shape.ts /
 * channel-doors.ts / agent-create-model.ts).
 *
 * ── FOUR STEPS, and each boundary is a dependency rather than a chapter ──
 *
 * ```
 *  1 Identity & placement   name · what it does · WHERE IT RUNS
 *        │                  nothing committed
 *        ▼  placement decides what step 2 may honestly offer
 *  2 Brain                  who pays ▸ provider ▸ model
 *        └── "Create agent" ──▶ the agent becomes real here
 *  3 Channels               how people reach it        REQUIRED
 *        └── "Next" — blocked until one connects
 *  4 Apps                   what it can reach          optional
 *        └── "Finish" / "Do it later" ──▶ into the agent
 * ```
 *
 * **Placement is first because step 2 cannot be honest without it.** "Your
 * subscription" and "Run locally" both route the brain through a Gateway on
 * a real machine; on a cloud-only agent they are controls that cannot be
 * completed. Asking where first means the second question only ever offers
 * real answers. See agent-create-placement.ts / agent-create-brain.ts.
 *
 * ── CHANNELS AND APPS ARE TWO STEPS. THAT IS THE FOUNDER'S CALL ──────────
 * An earlier pass merged them into one "Reach" screen, on the argument that
 * both answer "what does this connect to" and neither is required — two
 * screens that can each ask for nothing read as ceremony. The founder
 * rejected that directly: *"channels and application must be separated, do
 * you understand?"* So they are two steps again.
 *
 * The merge's argument was not wrong about the COST, and the cost is paid
 * where it was already being paid — in the button's own word. Each step
 * moves in ONE press, and says which of the two things it is doing:
 * "Skip for now" when that step has nothing connected, "Next"/"Finish" when
 * it does. A second screen you can leave in one keystroke is not ceremony;
 * a second screen that traps you is.
 *
 * They are also genuinely different questions once you look at what they
 * cost to get wrong. A channel is the ONLY way a person talks to their agent
 * (chat left the platform), so a skipped Channels step leaves an agent
 * nobody can reach — a fact worth stating. A skipped Apps step leaves an
 * agent that simply cannot open Notion yet, which needs no sentence at all.
 * One screen could only ever state the harsher of the two facts, or neither.
 *
 * ── The commit boundary ──────────────────────────────────────────────────
 * Identity and the brain go in ONE request wherever the create path can
 * carry them, so no agent exists wearing a model nobody picked. The two
 * machine-bound brain modes cannot ride it (the create path accepts three
 * keys; those need gateway_binding + runtime + a real-box check that lives
 * in fleet_configure_agent) — so they patch immediately after, and
 * agent-create-brain.ts's header states the obligation that carries: the two
 * outcomes may never share one message.
 *
 * Steps 3 and 4 genuinely cannot precede the commit — a channel binds to an
 * agent id, a connector authorizes against one — so they sit after it, and
 * the surface stops pretending otherwise the moment it happens: no way back
 * into Identity/Brain (they are saved, and a Back that silently did nothing
 * would be a lie), and closing goes INTO the agent rather than discarding.
 *
 * Apps DOES offer a Back, and that is not an inconsistency: Channels is a
 * live, still-editable screen sitting one press away, so a Back there does
 * exactly what it says. The rule is not "no Back after the commit", it is
 * "no Back to a screen that can no longer change anything".
 *
 * ── SUPERSEDED 2026-08-26: CHANNELS ARE REQUIRED AGAIN ───────────────────
 *
 * Everything in the section below describes a design that no longer ships.
 * It is kept because it records WHY the block was removed, and that reason
 * still constrains the current one — but its conclusion is dead. The founder
 * overruled it, restating what he had already said on 2026-08-21: *"you
 * cannot have a fucking agent without channel… it's not optional."* Channels
 * now block the forward button; Apps say "Do it later".
 *
 * What survives from it is the constraint, not the verdict: the objection
 * was to a sequence that TRAPS you, and that is still honoured — `dismiss`
 * is never blocked, so leaving is always one press and lands you in the
 * agent. Required is not the same as caged.
 *
 * ── (historical) "SKIP" AND "FINISH" ARE DIFFERENT WORDS FOR A REASON ────
 *
 * An earlier pass BLOCKED the forward button until a channel connected, on
 * the founder's own words that day (*"channels cannot be skipped, because
 * it's something agents are going to speak"*). The later brief supersedes
 * that with an equally explicit instruction — both of these steps are
 * *"optional … Skipping is one action."*
 *
 * Both are honoured, because they were never about the same thing. What he
 * rejected was a sequence that TRAPS you; what he never asked for is a
 * product that calls an unreachable agent finished. So the button MOVES
 * either way, in one press, and it is NAMED for what it actually does:
 *
 * ```
 *   nothing connected yet   ─▶ "Skip for now"   one press, no block
 *   at least one connected  ─▶ "Next" (channels) / "Finish" (apps)
 *   not known yet           ─▶ "Skip for now", and NO reason line —
 *                              "nothing is connected" and "I have not
 *                              asked yet" are different facts (CLAUDE.md)
 * ```
 *
 * The unfinished channel step is not lost by skipping: the agent's own setup
 * band (agent-setup-steps.ts) carries it forward on the page you land on.
 *
 * ── NOTHING SAYS "CANCEL" ONCE THE AGENT EXISTS ──────────────────────────
 * The founder pressed the head's X on a post-commit step and found an agent
 * in his workspace afterwards. Rather than make X delete a real agent (a
 * destructive act behind a dismiss gesture, which is worse), the CONTROL
 * changes when the fact changes: `dismiss` is "Cancel" while nothing exists
 * and "Finish later" from the commit onward.
 *
 * ── THE FILL FOLLOWS THE MOVE THAT IS ACTUALLY AVAILABLE ─────────────────
 * `forward.accent` is the view's single accent fill (CLAUDE.md: "One accent
 * colour, spent on the single primary action in a view"), spent only on a
 * forward button that can actually be pressed.
 */

/**
 * "apps", never "tools" — Configure carries a SEPARATE, genuinely different
 * "Tools" section (the built-in capability toggles), and the founder named
 * the right word for the connector picker: *"it's clearly applications, MCP
 * applications... name is clearly not tools."* It is a step id again now
 * that Channels and Apps are two steps, and the word it must never be is
 * still "tools".
 */
export type AgentCreateStepId = "identity" | "brain" | "channels" | "apps";

export type AgentCreateStep = {
  id: AgentCreateStepId;
  /** The word in the stepper. A noun, never an instruction. */
  label: string;
};

export const AGENT_CREATE_STEPS: readonly AgentCreateStep[] = [
  { id: "identity", label: "Identity" },
  { id: "brain", label: "Brain" },
  { id: "channels", label: "Channels" },
  { id: "apps", label: "Apps" },
];

/** The step whose forward button performs the one create call. Everything
 *  before it is uncommitted; everything after it operates on a real agent. */
export const AGENT_CREATE_COMMIT_STEP: AgentCreateStepId = "brain";

export function agentCreateStepIndex(id: AgentCreateStepId): number {
  return AGENT_CREATE_STEPS.findIndex((s) => s.id === id);
}

/** Whether reaching this step means the agent already exists. */
export function agentCreateStepIsPostCommit(id: AgentCreateStepId): boolean {
  return agentCreateStepIndex(id) > agentCreateStepIndex(AGENT_CREATE_COMMIT_STEP);
}

export type AgentCreateStepStatus = "done" | "current" | "todo";

export function agentCreateStepStatus(
  id: AgentCreateStepId,
  current: AgentCreateStepId,
): AgentCreateStepStatus {
  const a = agentCreateStepIndex(id);
  const b = agentCreateStepIndex(current);
  if (a === b) return "current";
  return a < b ? "done" : "todo";
}

export type AgentCreateWizardState = {
  step: AgentCreateStepId;
  /** The agent has been committed. Once true it never goes back to false —
   *  there is no un-creating. */
  created: boolean;
  busy: boolean;
  /** A name is resolved (typed, or the server's own suggestion). */
  hasName: boolean;
  /** agent-create-placement.ts's own verdict for step 1. */
  placementReady: boolean;
  placementBlockedReason: string;
  /** agent-create-brain.ts's own verdict for step 2. */
  brainReady: boolean;
  brainBlockedReason: string;
  /** False while the channels fetch is in flight — see this file's header
   *  for why that is not the same as zero. */
  channelsKnown: boolean;
  connectedChannelCount: number;
  /** Apps needs no `appsKnown` twin, and its absence is deliberate rather
   *  than an oversight: `channelsKnown` exists ONLY to keep the reason line
   *  silent while the answer is unknown, and the Apps step states no reason
   *  line at all (an agent with no apps is not a broken agent — it just
   *  cannot open Notion yet, which needs no sentence). Unknown and zero both
   *  resolve to "Skip for now", which is the honest word in both cases. */
  connectedAppCount: number;
};

export type AgentCreateFooterButton = {
  label: string;
  disabled: boolean;
};

/**
 * The head's dismiss control. Its KIND is the whole point: before the commit
 * there is something to cancel, and after it there is not.
 */
export type AgentCreateDismissPlan = {
  kind: "cancel" | "defer";
  label: string;
};

export type AgentCreateFooterPlan = {
  /** null means the step offers no backward move at all — never a disabled
   *  Back button, which is a dead control (CLAUDE.md: "if a control cannot
   *  be used in the current state, it is not rendered"). */
  back: (AgentCreateFooterButton & { action: "cancel" | "back" }) | null;
  forward: AgentCreateFooterButton & {
    action: "next" | "create" | "finish";
    /** Whether this button carries the view's ONE accent fill. */
    accent: boolean;
  };
  dismiss: AgentCreateDismissPlan;
  /** One short FACT explaining a blocked forward, or "" when there is none
   *  worth stating — including while the answer is still unknown. */
  blockedReason: string;
};

/** A blocked control is still a named control; it is just no longer the
 *  loud invitation to press it. One rule, so no branch below can forget. */
function forwardButton(
  label: string,
  action: "next" | "create" | "finish",
  disabled: boolean,
): AgentCreateFooterPlan["forward"] {
  return { label, action, disabled, accent: !disabled };
}

function dismissPlan(created: boolean): AgentCreateDismissPlan {
  return created ? { kind: "defer", label: "Finish later" } : { kind: "cancel", label: "Cancel" };
}

export function planAgentCreateFooter(state: AgentCreateWizardState): AgentCreateFooterPlan {
  const {
    step,
    created,
    busy,
    hasName,
    placementReady,
    placementBlockedReason,
    brainReady,
    brainBlockedReason,
    channelsKnown,
    connectedChannelCount,
    connectedAppCount,
  } = state;
  const dismiss = dismissPlan(created);

  if (step === "identity") {
    const ok = hasName && placementReady;
    return {
      back: { label: "Cancel", action: "cancel", disabled: busy },
      forward: forwardButton("Next", "next", busy || !ok),
      dismiss,
      blockedReason: !hasName ? "Give this agent a name." : placementBlockedReason,
    };
  }

  if (step === "brain") {
    const ok = hasName && brainReady;
    return {
      back: { label: "Back", action: "back", disabled: busy },
      forward: forwardButton(busy ? "Creating…" : "Create agent", "create", busy || !ok),
      dismiss,
      blockedReason: !hasName ? "Give this agent a name." : brainBlockedReason,
    };
  }

  if (step === "channels") {
    // A CHANNEL IS REQUIRED. Founder, 2026-08-26, restating what he had
    // already said on 2026-08-21: *"you cannot have a fucking agent without
    // channel — how do you expect customers to speak with their agents
    // without channel? So it's not optional."*
    //
    // This REVERSES the "Skip for now" behaviour documented in this file's
    // header, and the reversal is deliberate rather than a regression: the
    // header's compromise ("the button moves either way, the LABEL carries
    // the honesty") was reached from a later brief that called both steps
    // optional. He has now overruled that twice, and the reasoning is the
    // product's own: chat left the platform, so a channel is the ONLY way a
    // person reaches an agent. An agent with none is not a partly-configured
    // agent, it is an unreachable one.
    //
    // WHY THIS DOES NOT TRAP ANYONE, which was the real objection behind the
    // earlier removal: the FORWARD button is blocked, but `dismiss` is not.
    // The agent already exists by this point (Brain committed it, because
    // ChannelsTab needs a real agent id to attach to), so leaving is always
    // possible in one press and lands you IN the agent, where its own setup
    // band carries the unfinished channel forward. Blocking the forward path
    // makes the sequence say "this is required"; leaving the exit open means
    // it never becomes a cage.
    const reachable = connectedChannelCount > 0;
    return {
      // No Back: the only step behind this one is Brain, which is already
      // committed and saved. A Back that silently changed nothing is a lie.
      back: null,
      forward: forwardButton("Next", "next", busy || !reachable),
      dismiss,
      // Only once we actually know. "Nothing is connected" and "I have not
      // asked yet" are different facts, so an unknown channel list says
      // nothing rather than accusing the person of skipping something.
      blockedReason:
        channelsKnown && !reachable
          ? "Pick how people will reach this agent. Telegram needs no computer."
          : "",
    };
  }

  // Apps — the last step, and the one that genuinely IS optional. Founder,
  // 2026-08-26, drawing the line himself: *"this application connector is
  // also something that could be skipped… 'do it later' is much better."*
  //
  // So the two steps are no longer treated alike, and that asymmetry is the
  // point: an agent with no CHANNEL cannot be reached at all, while an agent
  // with no APPS is simply an agent that has not been given extra reach yet.
  // One is broken, the other is unfinished — and they should not share a
  // word.
  //
  // "Do it later" rather than "Skip for now": skipping sounds like the step
  // is being thrown away, when in fact the agent's own Apps tab is sitting
  // one click away afterwards and the setup band carries it forward. NO
  // reason line either — an agent with no apps is not broken, so there is no
  // fact worth a sentence here. A professional tool labels; it does not
  // lecture.
  return {
    // Channels is live and still editable one press back, so this Back does
    // exactly what it says — see this file's header.
    back: { label: "Back", action: "back", disabled: busy },
    forward: forwardButton(connectedAppCount > 0 ? "Finish" : "Do it later", "finish", busy),
    dismiss,
    blockedReason: "",
  };
}

/** Where the forward button goes. Null on the last step — the caller
 *  finishes instead. */
export function agentCreateNextStep(step: AgentCreateStepId): AgentCreateStepId | null {
  const next = AGENT_CREATE_STEPS[agentCreateStepIndex(step) + 1];
  return next ? next.id : null;
}

export function agentCreatePreviousStep(step: AgentCreateStepId): AgentCreateStepId | null {
  const prev = AGENT_CREATE_STEPS[agentCreateStepIndex(step) - 1];
  return prev ? prev.id : null;
}

/**
 * What closing the surface should DO.
 *
 * Before the commit there is nothing to keep, so closing discards. After it,
 * the agent is real and already saved — closing must take the person to it,
 * never quietly drop them back on a list as if the last screens had been
 * cancelled. That is the outcome-honesty law applied to a dismiss gesture:
 * "I stopped early" and "nothing happened" are different facts.
 */
export function agentCreateCloseIntent(created: boolean): "discard" | "open_agent" {
  return created ? "open_agent" : "discard";
}

/** The surface's own title. It names the thing once the thing exists —
 *  a person on the last step must be able to see, without pressing anything,
 *  that their agent is already real. */
export function agentCreateSurfaceTitle(created: boolean, agentName: string): string {
  const name = agentName.trim();
  return created && name ? name : "New agent";
}
