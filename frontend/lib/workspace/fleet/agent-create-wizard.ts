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
 * ── NOTHING SAYS "CANCEL" ONCE THE AGENT EXISTS AND CAN BE REACHED ────────
 * The founder pressed the head's X on a post-commit step and found an agent
 * in his workspace afterwards. Rather than make X delete a real agent (a
 * destructive act behind a dismiss gesture, which is worse), the CONTROL
 * changes when the fact changes: `dismiss` is "Cancel" while nothing exists
 * and "Finish later" once the agent is real AND REACHABLE (see the next
 * section — that second half is new).
 *
 * ── PART B, 2026-08-27: LEAVING MAY NOT STRAND AN UNREACHABLE AGENT ───────
 * The founder saw the shape above ship and rejected it anyway: *"Halfway
 * through, the agent is already created... So channels cannot be skipped,
 * and BEFORE that step the agent cannot be created in this platform."*
 * Blocking the forward button (above) was not the whole fix — `dismiss` on
 * the Channels step still read "Finish later" and quietly opened a real,
 * permanently unreachable agent the moment nothing was connected yet.
 *
 * The commit could not simply move to the END of the sequence: every real
 * channel door (paste a token, an OAuth round trip, a pasted app credential,
 * a paired computer) binds to an AGENT ID that has to already exist —
 * `assign_byo_bot(agent_install_id=...)`, the OAuth callback's stored state,
 * `PUT .../channels/{key}/credential` — and ChannelsTab itself (the REAL
 * tab, reused rather than a fifth copy of a channel list) fetches by agent
 * id. Building a "pending agent" shim so every one of those write paths
 * could defer to a not-yet-real id would be a new concept threaded through
 * Telegram/Discord/Slack/WeChat plus ~21 transported OpenClaw doors, each
 * with their own per-agent binding writes — a large, separate body of work,
 * and it would not even remove the risk: an OAuth round trip leaves the
 * dialog and comes back, so a person who closes the tab mid-flow still
 * leaves an orphan behind, just a differently-shaped one (an authorized
 * connector wired to nothing, INSTEAD of an empty agent row).
 *
 * So the commit stays where it is (Brain), and what changes is what LEAVING
 * before a channel connects actually DOES:
 *
 * ```
 *   created, reachable       ─▶  dismiss defers, same as before — the
 *                                agent can already be messaged, so nothing
 *                                is stranded by leaving.
 *   created, NOT reachable   ─▶  dismiss reads Cancel (same head control as
 *                                before the commit), and PRESSING it asks —
 *                                never silently opens the agent, never
 *                                silently deletes it either. THREE named
 *                                choices, in order: "Go back", "Leave it for
 *                                now", "Delete agent" — see
 *                                AGENT_CREATE_UNREACHABLE_EXITS below, which
 *                                is where that order and the single
 *                                `.fleet-btn--danger` are held still.
 * ```
 *
 * **The middle option landed 2026-08-28 and it corrects this section's own
 * claim.** As shipped, PART B offered only the outer two, which is not "not
 * caged": Escape and the backdrop both resolve to "Go back", so somebody with
 * no credential to hand could leave the browser or delete their own work and
 * nothing else. The rule below is unchanged — Channels still blocks the
 * forward button — because whether the SEQUENCE may be completed without a
 * channel and whether a person may LEAVE keeping the agent are two different
 * questions, and only the first one is the founder's.
 *
 * REQUIRED IS STILL NOT CAGED: pressing dismiss is never blocked, in either
 * state — a confirmation is one more press, not a wall. What changed is
 * that the press can no longer end in an agent silently left behind with no
 * way to receive a message, which is the exact complaint. `reachable` is a
 * SEPARATE input from `connectedChannelCount`, deliberately: the live
 * channel list resets to empty the instant the caller stops asking for it
 * (fleet-data.ts's `useFleetAgentChannels` clears its array when handed a
 * null agent id, which AgentCreateCard does the moment the person leaves
 * the Channels step) — so "is this agent reachable" cannot be re-derived
 * from that count once the person has moved on. The caller tracks it as a
 * STICKY fact instead (true the moment a channel is ever observed connected
 * on the Channels step, never cleared afterward) and hands it in here,
 * exactly like `created`.
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
  /** STICKY — true once a channel has EVER connected this session, and never
   *  cleared afterward. See this file's header, "PART B, 2026-08-27": the
   *  live `connectedChannelCount` cannot answer this by itself once the
   *  person has moved past the Channels step (it resets to 0), so the caller
   *  tracks this separately and hands it in, exactly like `created`. Decides
   *  whether dismissing defers to the agent or must ask first. */
  reachable: boolean;
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

/** `reachable` is not asked for before the commit — nothing exists yet, so
 *  the question is meaningless and `created` alone decides. After the
 *  commit it is the whole decision: a real, unreachable agent gets the SAME
 *  Cancel-styled control it had before it existed, because pressing it must
 *  ask rather than silently open (or silently keep) an agent nobody can
 *  message. See this file's header, "PART B, 2026-08-27". */
function dismissPlan(created: boolean, reachable: boolean): AgentCreateDismissPlan {
  if (!created) return { kind: "cancel", label: "Cancel" };
  return reachable ? { kind: "defer", label: "Finish later" } : { kind: "cancel", label: "Cancel" };
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
    reachable,
  } = state;
  const dismiss = dismissPlan(created, reachable);

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
    // earlier removal: the FORWARD button is blocked, but `dismiss` is not —
    // pressing it is always exactly one press. Blocking the forward path
    // makes the sequence say "this is required"; leaving the exit open means
    // it never becomes a cage. What PRESSING it now does is `dismiss`'s own
    // decision (see dismissPlan / agentCreateCloseIntent, PART B): while
    // nothing is connected yet it asks rather than silently opening a real,
    // unreachable agent — asking is still one press, so this is not the cage
    // that was rejected once already.
    //
    // `connectedNow` is deliberately a SEPARATE, live-only value from
    // `state.reachable` above — it decides only whether THIS STEP's forward
    // button may move, off the count this step is actually looking at right
    // now. `reachable` (already folded into `dismiss`) is the caller's
    // STICKY memory of whether a channel has EVER connected, because that
    // live count resets to 0 the moment the person leaves this step.
    const connectedNow = connectedChannelCount > 0;
    return {
      // No Back: the only step behind this one is Brain, which is already
      // committed and saved. A Back that silently changed nothing is a lie.
      back: null,
      forward: forwardButton("Next", "next", busy || !connectedNow),
      dismiss,
      // Only once we actually know. "Nothing is connected" and "I have not
      // asked yet" are different facts, so an unknown channel list says
      // nothing rather than accusing the person of skipping something.
      blockedReason:
        channelsKnown && !connectedNow
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

export type AgentCreateCloseIntent = "discard" | "open_agent" | "confirm_delete";

/**
 * What closing the surface should DO.
 *
 * Before the commit there is nothing to keep, so closing discards. After it
 * AND once the agent is reachable, closing must take the person to it, never
 * quietly drop them back on a list as if the last screens had been
 * cancelled — the outcome-honesty law applied to a dismiss gesture: "I
 * stopped early" and "nothing happened" are different facts.
 *
 * After it but BEFORE it is reachable, closing may do neither silently — see
 * this file's header, "PART B, 2026-08-27". `confirm_delete` means: ask.
 * Never open an agent nobody can message, and never delete one without
 * saying so.
 */
export function agentCreateCloseIntent(created: boolean, reachable: boolean): AgentCreateCloseIntent {
  if (!created) return "discard";
  return reachable ? "open_agent" : "confirm_delete";
}

/**
 * THE THREE WAYS OUT OF `confirm_delete`, in the order they are rendered.
 *
 * PART B shipped this confirmation with TWO buttons — "Go back" and "Delete
 * agent" — and its own header claims *"REQUIRED still never becomes CAGED."*
 * That claim was false as written. Escape and the backdrop both resolve to
 * "Go back", the backdrop covers the rail, and Channels blocks the forward
 * button until a real credential connects. So a customer who does not have a
 * bot token in front of them had exactly two exits: leave the browser, or
 * destroy work they had just done. That is a cage with a delete key in it.
 *
 * The founder's rule is UNCHANGED and is not weakened here: *"channels cannot
 * be skipped… you cannot have a fucking agent without channel."* Channels
 * still BLOCKS advancing to Apps and to finish — `planAgentCreateFooter`'s
 * forward button is untouched. What this adds is a way to LEAVE while keeping
 * the agent, which is a different question from whether the sequence may be
 * completed without one.
 *
 * "Leave it for now" is the `open_agent` behaviour made EXPLICIT rather than
 * reinstated silently. PART B was right that closing must never resolve to it
 * on its own — that is how an unreachable agent got abandoned with nothing
 * said. It was wrong to conclude the option should not exist: the agent's own
 * page renders the "Finish setting up ▸ Connect a channel" band whenever zero
 * channels are connected (agent-setup-steps.ts), so the state is recoverable
 * AND visibly labelled as unfinished. A named choice that lands somewhere
 * saying what is missing is honest; a silent one was not.
 *
 * ```
 * stay    Go back            the DEFAULT — what Escape and the backdrop do.
 *                            Unchanged, and still first.
 * leave   Leave it for now   keeps the agent, opens it. Its page flags it.
 * delete  Delete agent       destroys it. --danger. Still last.
 * ```
 *
 * Ordered data rather than three literals in the JSX so the three properties
 * that matter can be ASSERTED: that leaving is never first (i.e. never what a
 * stray Escape or a mis-aimed click resolves to), that exactly one option is
 * destructive and it is last, and that nothing here calls itself "skip" — the
 * word the founder rejected, and the one that would make this read as the
 * channel requirement being waived rather than deferred.
 */
export type AgentCreateUnreachableExitId = "stay" | "leave" | "delete";

export type AgentCreateUnreachableExit = {
  id: AgentCreateUnreachableExitId;
  label: string;
  /** Renders `.fleet-btn--danger`, and it is the only one that may. */
  destructive: boolean;
};

export const AGENT_CREATE_UNREACHABLE_EXITS: readonly AgentCreateUnreachableExit[] = [
  { id: "stay", label: "Go back", destructive: false },
  { id: "leave", label: "Leave it for now", destructive: false },
  { id: "delete", label: "Delete agent", destructive: true },
];

/** The surface's own title. It names the thing once the thing exists —
 *  a person on the last step must be able to see, without pressing anything,
 *  that their agent is already real. */
export function agentCreateSurfaceTitle(created: boolean, agentName: string): string {
  const name = agentName.trim();
  return created && name ? name : "New agent";
}
