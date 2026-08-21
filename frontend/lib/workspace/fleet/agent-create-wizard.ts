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
 *  4 Tools      the real Connectors picker
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
 * ── Skippable, but still a step ──────────────────────────────────────────
 * A person with no channel credential to hand must be able to move on. That
 * makes the forward button say "Skip for now" — but only when we actually
 * KNOW nothing is connected. While the channels fetch is still in flight,
 * "nothing is connected" and "I have not asked yet" are different facts and
 * may not share one label, so the neutral "Next" is used instead.
 */

export type AgentCreateStepId = "identity" | "model" | "channel" | "tools";

export type AgentCreateStep = {
  id: AgentCreateStepId;
  /** The word in the stepper. A noun, never an instruction. */
  label: string;
};

export const AGENT_CREATE_STEPS: readonly AgentCreateStep[] = [
  { id: "identity", label: "Identity" },
  { id: "model", label: "Model" },
  { id: "channel", label: "Channel" },
  { id: "tools", label: "Tools" },
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

export type AgentCreateFooterPlan = {
  /** null means the step offers no backward move at all — never a disabled
   *  Back button, which is a dead control (CLAUDE.md: "if a control cannot
   *  be used in the current state, it is not rendered"). */
  back: (AgentCreateFooterButton & { action: "cancel" | "back" }) | null;
  forward: AgentCreateFooterButton & { action: "next" | "create" | "finish" };
};

export function planAgentCreateFooter(state: AgentCreateWizardState): AgentCreateFooterPlan {
  const { step, created, busy, hasName, channelsKnown, connectedChannelCount } = state;

  if (step === "identity") {
    return {
      back: { label: "Cancel", action: "cancel", disabled: busy },
      forward: { label: "Next", action: "next", disabled: busy || !hasName },
    };
  }

  if (step === "model") {
    return {
      back: { label: "Back", action: "back", disabled: busy },
      forward: {
        label: busy ? "Creating…" : "Create agent",
        action: "create",
        disabled: busy || !hasName,
      },
    };
  }

  if (step === "channel") {
    // No way back: Identity and Model are committed by now, so a Back button
    // here could only ever return to a screen whose edits no longer go
    // anywhere. Better no control than one that lies.
    return {
      back: null,
      forward: {
        label: channelsKnown && connectedChannelCount === 0 ? "Skip for now" : "Next",
        action: "next",
        disabled: busy,
      },
    };
  }

  return {
    back: { label: "Back", action: "back", disabled: busy },
    forward: { label: "Finish", action: "finish", disabled: busy },
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
