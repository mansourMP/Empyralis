/**
 * THE STEP SEQUENCE FOR CREATING AN AGENT — pure, framework-free, so a plain
 * `tsx` test drives the real rule (same discipline as agent-count-shape.ts /
 * channel-doors.ts / agent-create-model.ts).
 *
 * ── Why there is a sequence again ────────────────────────────────────────
 * Agent creation was cut to one card with two fields, and everything the old
 * FleetCreateAgentWizard used to ask became a ROW OF OPTIONAL BUTTONS on the
 * agent's page afterwards ("Connect a channel" · "Give it a computer" ·
 * "Connect your tools"). The founder rejected that shape, twice:
 *
 *   *"it acts like a button, not step-by-step... if you want press this
 *    button and set up your hardware, if you want this if you want that —
 *    I don't want to have that."*
 *
 * A menu of optional links is not setup. So the sequence comes back — "as
 * before", his words — with ONE deletion he had already made separately:
 * there is no project/placement step, because an agent belongs to the
 * WORKSPACE now (CLAUDE.md, 2026-08-20). Four steps, not the old four:
 *
 * ```
 *  1 Identity   name + optional instructions      nothing committed yet
 *  2 Model      the live catalog                  nothing committed yet
 *      └── "Create agent" ──▶ ONE atomic POST carrying all three
 *  3 Channel    the real Channels grid            the agent is real from here
 *  4 Apps       the real Connectors picker
 *      └── "Finish" ──▶ into the agent
 * ```
 *
 * ── The commit boundary is the load-bearing part ─────────────────────────
 * The OLD wizard created the agent at the end of step 1 and PATCHed
 * everything after. That is the create-then-separate-step shape CLAUDE.md's
 * outcome-honesty law names directly, and it made the model a thing that
 * could half-apply. Here identity AND model go in one request, so no agent
 * can exist wearing a model nobody picked.
 *
 * Steps 3 and 4 genuinely cannot precede the commit — a channel binds to an
 * agent id and a connector authorizes against one — so they sit AFTER it, on
 * purpose, and the surface stops pretending otherwise the moment it happens:
 * there is no way back into Identity/Model once committed (they are saved,
 * and a Back button that silently did nothing would be a lie), and closing
 * from step 3 or 4 goes INTO the agent rather than discarding, because the
 * agent exists.
 *
 * ── THE CHANNEL STEP IS REQUIRED. It is DEFERRABLE, which is not the same
 *    thing — corrected 2026-08-21 after the founder reviewed the first
 *    version live. ─────────────────────────────────────────────────────────
 *
 * It used to say "Skip for now" and walk straight on to Finish. That was
 * wrong on the product's own terms: chat left the platform (founder,
 * 2026-08-19/20 — *"messaging would never be done inside this platform...
 * go to Telegram and speak with the agent inside that channel"*), so an
 * agent with no channel is unreachable by anybody. His words here:
 * *"channels cannot be skipped, because it's something agents are going to
 * speak."* A "Finish" that hands back an agent nobody can talk to is the
 * outcome-honesty law broken at the last screen of setup.
 *
 * ```
 *   channels not yet known  ─▶ forward BLOCKED, and SAYS NOTHING.
 *                              "no channel connected" and "I have not asked
 *                              yet" are different facts (CLAUDE.md) and a
 *                              reason line claiming the first while the
 *                              second is true is the lie, not the block.
 *   known, 0 connected      ─▶ forward BLOCKED, with the one fact that
 *                              explains it. The way out is DEFER, in the
 *                              head — never a forward button that calls
 *                              leaving "Skip" and then says "Finish".
 *   known, 1+ connected     ─▶ forward moves. The step is done.
 * ```
 *
 * Deferring is a real, first-class exit and always available: the agent is
 * already saved, closing takes you into it, and the setup band on its page
 * (agent-setup-steps.ts) carries the same unfinished channel step forward.
 * What is NOT available is calling that state finished.
 *
 * ── NOTHING SAYS "CANCEL" ONCE THE AGENT EXISTS ──────────────────────────
 *
 * The founder pressed the head's X on step 3 and found an agent named
 * Zephyr in his workspace afterwards. He was right to expect otherwise —
 * an X is a cancel gesture, and the commit had already happened two screens
 * earlier. Rather than make X delete a real agent (a destructive act behind
 * a dismiss gesture, which is worse), the CONTROL changes when the fact
 * changes: `dismiss` is "Cancel" while nothing exists and "Finish later"
 * from the commit onward. Same rule as `agentCreateCloseIntent` below, one
 * level up — the label and the behaviour move together, so neither can
 * claim something the other does not do.
 *
 * ── THE FILL FOLLOWS THE MOVE THAT IS ACTUALLY AVAILABLE ─────────────────
 *
 * `forward.accent` is the view's single accent fill (CLAUDE.md: "One accent
 * colour, spent on the single primary action in a view"), and it is spent
 * only on a forward button that can actually be pressed. A blocked step's
 * button is still rendered and still named — a disabled control that says
 * what it would do is not a dead control — it just stops being the loud
 * purple invitation to press it, which is exactly the thing it cannot
 * accept. Everything else in the surface (the step numbers, the identity
 * glyph) is neutral, so at most ONE accent-filled element is ever on
 * screen.
 */

/**
 * "apps", never "tools". Step 4 embeds ConnectorsTab — the apps/MCP
 * connector picker — while Configure carries a SEPARATE, genuinely
 * different "Tools" section (the built-in capability toggles). Two
 * different things were called Tools; the founder named the right word for
 * this one: *"it's clearly applications, MCP applications... name is
 * clearly not tools."* The id moves with the label so the collision cannot
 * survive in the code either.
 */
export type AgentCreateStepId = "identity" | "model" | "channel" | "apps";

export type AgentCreateStep = {
  id: AgentCreateStepId;
  /** The word in the stepper. A noun, never an instruction. */
  label: string;
};

export const AGENT_CREATE_STEPS: readonly AgentCreateStep[] = [
  { id: "identity", label: "Identity" },
  { id: "model", label: "Model" },
  { id: "channel", label: "Channel" },
  { id: "apps", label: "Apps" },
];

/** The step whose forward button performs the one create call. Everything
 *  before it is uncommitted; everything after it operates on a real agent. */
export const AGENT_CREATE_COMMIT_STEP: AgentCreateStepId = "model";

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
  /** False while the channels fetch is in flight — see this file's header
   *  for why that is not the same as zero. */
  channelsKnown: boolean;
  connectedChannelCount: number;
};

export type AgentCreateFooterButton = {
  label: string;
  disabled: boolean;
};

/**
 * The head's dismiss control. Its KIND is the whole point: before the commit
 * there is something to cancel, and after it there is not. See this file's
 * header for the founder's own instance of the bug this closes.
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
  const { step, created, busy, hasName, channelsKnown, connectedChannelCount } = state;
  const dismiss = dismissPlan(created);

  if (step === "identity") {
    return {
      back: { label: "Cancel", action: "cancel", disabled: busy },
      forward: forwardButton("Next", "next", busy || !hasName),
      dismiss,
      blockedReason: "",
    };
  }

  if (step === "model") {
    return {
      back: { label: "Back", action: "back", disabled: busy },
      forward: forwardButton(busy ? "Creating…" : "Create agent", "create", busy || !hasName),
      dismiss,
      blockedReason: "",
    };
  }

  if (step === "channel") {
    // No way back: Identity and Model are committed by now, so a Back button
    // here could only ever return to a screen whose edits no longer go
    // anywhere. Better no control than one that lies.
    const reachable = connectedChannelCount > 0;
    return {
      back: null,
      forward: forwardButton("Next", "next", busy || !reachable),
      dismiss,
      // Stated ONLY once we actually know. While the fetch is in flight the
      // block is real and the explanation is not available yet, and naming
      // the wrong one of two facts is worse than naming neither.
      blockedReason:
        channelsKnown && !reachable
          ? "Nobody can reach this agent until a channel is connected."
          : "",
    };
  }

  return {
    back: { label: "Back", action: "back", disabled: busy },
    forward: forwardButton("Finish", "finish", busy),
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
 * never quietly drop them back on a list as if the last two screens had been
 * cancelled. That is the outcome-honesty law applied to a dismiss gesture:
 * "I stopped early" and "nothing happened" are different facts.
 */
export function agentCreateCloseIntent(created: boolean): "discard" | "open_agent" {
  return created ? "open_agent" : "discard";
}

/** The surface's own title. It names the thing once the thing exists —
 *  a person on step 3 must be able to see, without pressing anything, that
 *  their agent is already real. */
export function agentCreateSurfaceTitle(created: boolean, agentName: string): string {
  const name = agentName.trim();
  return created && name ? name : "New agent";
}
