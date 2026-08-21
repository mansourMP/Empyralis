/**
 * ONE COMPUTER, THREE QUESTIONS — the rule behind the Hardware detail page.
 *
 * Pure data + pure functions, its own module for the same reason
 * channel-doors.ts / agent-count-shape.ts / box-capability-state.ts are:
 * hardware-detail-shape.test.ts imports THE REAL RULE and runs under plain
 * `tsx`, so nothing here may import React, CSS or a component.
 *
 * ── The shape the founder approved ───────────────────────────────────────
 *   1. IS THIS COMPUTER WORKING?  one line — the state, plus the ONE fact
 *      that could break it. At rest that is all that shows.
 *   2. WHAT CAN IT DO?            one row per capability: name, honest
 *      state, at most ONE action.
 *   3. WHAT'S WRONG?              rendered only when something IS wrong.
 *      Absent entirely on a healthy box.
 *
 * ── WHY A RAW PROBE SUMMARY NEVER REACHES THE SCREEN ─────────────────────
 * The page used to print `service_inventory[].summary` verbatim as the
 * customer-facing reason line. Those summaries are diagnostic dumps: raw
 * `docker info` stderr, raw `openclaw channels list` stderr. Two things go
 * wrong with that, and both were live:
 *
 *   · it hands a customer a shell diagnostic as their only signal, against
 *     the standing rule that a professional tool labels rather than dumps;
 *   · the backend runs every visible string through
 *     secret_redaction_service, whose high-entropy sweep eats an ordinary
 *     long path — so the Channel plugins row rendered
 *     "~/[redacted-secret]" where a real file path belonged.
 *
 * The fix is HERE, not in the redactor: this module derives its own plain
 * sentence from the STATUS ENUM alone. The redactor is untouched and no
 * allowlist is widened — the leaking string simply stops being something
 * the product chose to display. `capabilityNote()` takes no free text
 * argument at all, so a summary cannot be threaded back in by accident.
 *
 * ── "COULD NOT CHECK" IS NOT "NOTHING SET UP" ────────────────────────────
 * Those two lines used to sit next to each other on this page reading as
 * one thought ("Unknown — Could not check whether channels are ready" above
 * "No channels yet"). They are different facts with different next actions:
 * one means go look at the box, the other means go connect a channel. They
 * are separate states here and the page renders exactly one of them.
 */

/** The connection tone this page already resolves from the registration
 *  (gateway-box-picker.tsx's connectionPresentation). Re-declared as a
 *  local union rather than imported so this module stays component-free;
 *  the caller passes the real value in. */
export type BoxTone = "online" | "degraded" | "offline" | "error" | "unknown" | "ready" | "working";

/** The gateway's own passive-probe vocabulary — mirrors
 *  box-capability-state.ts's ProbeStatus, which this module composes with
 *  rather than duplicates. */
export type ProbeStatus = "ready" | "degraded" | "offline" | "missing" | "unknown" | "blocked" | undefined;

/** One coding CLI's state, as gateway-box-picker.tsx's gatewayRuntimeState
 *  already reports it. */
export type CliRuntimeState = "ready" | "unauthenticated" | "missing";

// ── 1. IS THIS COMPUTER WORKING? ─────────────────────────────────────────

export interface BoxHealth {
  tone: BoxTone;
  /** The state, in one word or two. */
  headline: string;
  /** The ONE fact that could break it — null when nothing could, which is
   *  what makes a healthy box collapse to a single line. */
  caveat: string | null;
}

export interface BoxHealthFacts {
  /** connectionPresentation(gateway).tone */
  connectionTone: BoxTone;
  /** connectionPresentation(gateway).label — the word the pill already uses. */
  connectionLabel: string;
  /** The `docker` probe. Docker is what agents actually run commands
   *  inside, so a reachable box with no Docker is the single most common
   *  "connected but useless" shape and outranks every other caveat. */
  dockerStatus: ProbeStatus;
  /** True only when the server authorized full access AND the box has
   *  positively reported it is NOT enabled locally. `null` means the box
   *  has not said either way — which is not a caveat, it is an absence. */
  fullAccessAuthorizedButOff: boolean | null;
  /** True when the backend refused to advertise updates for this box —
   *  gateway_update_refusal_code is set and repair is a human action. */
  cannotReceiveUpdates: boolean;
  /** connection_status === "execution_blocked" — the box is reachable but
   *  its tools are not. Keyed on that STABLE CODE, never on the pill's
   *  prose: matching a reworded sentence is a failure this codebase has
   *  already paid for once. */
  executionBlocked: boolean;
}

/** ONE caveat, chosen by priority — never a list glued together with "and".
 *  A person acts on the first thing that is wrong; the rest live in the
 *  problems section below, which is where an enumeration belongs. */
export function planBoxHealth(facts: BoxHealthFacts): BoxHealth {
  const { connectionTone, connectionLabel } = facts;

  // Not reachable at all: the state IS the whole story, and naming a
  // second fact about a box nobody can talk to would be noise.
  if (connectionTone === "offline" || connectionTone === "error") {
    return {
      tone: connectionTone,
      headline: connectionLabel,
      caveat:
        connectionTone === "error"
          ? "Empyralis can't talk to this computer — it needs connecting again."
          : "Empyralis can't reach this computer right now.",
    };
  }

  if (connectionTone === "unknown") {
    return {
      tone: "unknown",
      headline: connectionLabel,
      // "we could not tell" is its own fact and must not read as "offline"
      // — they send a person to do two different things.
      caveat: "This computer hasn't reported in, so its state isn't known.",
    };
  }

  // Reachable. Now the one thing most likely to break it.
  if (facts.dockerStatus === "missing") {
    return {
      tone: "degraded",
      headline: connectionLabel,
      caveat: "Agents can't run commands or open files here — the sandbox they run inside isn't installed.",
    };
  }
  if (facts.dockerStatus === "offline" || facts.dockerStatus === "degraded" || facts.dockerStatus === "blocked") {
    return {
      tone: "degraded",
      headline: connectionLabel,
      caveat: "Agents can't run commands or open files here until Docker is running on this computer.",
    };
  }
  if (facts.cannotReceiveUpdates) {
    return {
      tone: "degraded",
      headline: connectionLabel,
      caveat: "This computer can't receive updates yet.",
    };
  }
  if (facts.fullAccessAuthorizedButOff === true) {
    return {
      tone: "degraded",
      headline: connectionLabel,
      caveat: "Full access is approved for this computer but hasn't been turned on there, so agents stay sandboxed.",
    };
  }
  // Reachable but not healthy, with nothing more specific found above. The
  // pill said "Degraded" while the line beneath it said nothing needed
  // attention — two facts contradicting each other on one screen, which is
  // worse than either alone. Last, so a real cause always outranks it.
  if (connectionTone === "degraded") {
    return {
      tone: "degraded",
      headline: connectionLabel,
      caveat: facts.executionBlocked
        ? "This computer is connected, but agents can't run anything on it right now."
        : "This computer is connected but hasn't checked in recently, so it may not answer straight away.",
    };
  }

  return { tone: connectionTone, headline: connectionLabel, caveat: null };
}

// ── 2. WHAT CAN IT DO? ───────────────────────────────────────────────────

/** What a row's single action does. `null` means there is genuinely nothing
 *  to press — an observation, not a broken control. Docker and Ollama are
 *  permanently `null`: the gateway exposes cli.install / cli.login.* /
 *  openclaw.provision and NOTHING ELSE (see
 *  empyralis-gateway/src/runtime/desktop-permissions.ts's
 *  DESKTOP_CAPABILITY_PERMISSIONS), so an "Install Docker" button here
 *  could only ever fail. Do not add one without a capability behind it. */
export type CapabilityAction = "install" | "sign_in" | "set_up" | null;

export type CapabilityTone = "ready" | "degraded" | "unknown" | "error";

export interface CapabilityRow {
  key: string;
  label: string;
  /** The honest state word shown in the row's pill. */
  stateLabel: string;
  tone: CapabilityTone;
  action: CapabilityAction;
  /** A short plain sentence, or null. NEVER a probe summary — see this
   *  module's own doc comment. */
  note: string | null;
  /** True when this row is fine and should collapse to one line with
   *  nothing else rendered. */
  healthy: boolean;
  /** True only when something is genuinely BROKEN — not merely unfinished.
   *  "Not installed", "not signed in", "no local model" and "channels not
   *  set up" are all ordinary states of a perfectly healthy computer that
   *  nobody has finished setting up; treating them as faults would put a
   *  "What's wrong" section on almost every box in the product and teach
   *  people to ignore it. Docker down is a real fault: agents cannot run
   *  anything. */
  problem: boolean;
}

/** A coding CLI. Three states, three different next steps — installed and
 *  signed in (nothing to do), installed but not signed in (Sign in), and
 *  not installed (Install). Signing in is what actually lets an agent use
 *  it, which is why "installed" alone is never `healthy`. */
export function planCliCapabilityRow(key: string, label: string, state: CliRuntimeState): CapabilityRow {
  if (state === "ready") {
    return { key, label, stateLabel: "Ready", tone: "ready", action: null, note: null, healthy: true, problem: false };
  }
  if (state === "unauthenticated") {
    return {
      key,
      label,
      stateLabel: "Not signed in",
      tone: "degraded",
      action: "sign_in",
      note: null,
      healthy: false,
      problem: false,
    };
  }
  return {
    key,
    label,
    stateLabel: "Not installed",
    tone: "unknown",
    action: "install",
    note: null,
    healthy: false,
    problem: false,
  };
}

/** Docker, named for what it DOES rather than what it is called. The row
 *  has no action by design (see CapabilityAction) — a sentence, not a
 *  button that cannot work. */
export function planSandboxCapabilityRow(status: ProbeStatus): CapabilityRow {
  const key = "docker";
  const label = "Running commands and opening files";
  switch (status) {
    case "ready":
      return { key, label, stateLabel: "Ready", tone: "ready", action: null, note: null, healthy: true, problem: false };
    case "missing":
      return {
        key,
        label,
        stateLabel: "Not installed",
        tone: "unknown",
        action: null,
        note: "Docker isn't on this computer. Agents can't run commands or open files here until it is.",
        healthy: false,
        problem: true,
      };
    case "offline":
    case "degraded":
    case "blocked":
      return {
        key,
        label,
        stateLabel: "Not running",
        tone: "degraded",
        action: null,
        note: "Docker is installed but isn't running. Start it on that computer, then check again.",
        healthy: false,
        problem: true,
      };
    default:
      return {
        key,
        label,
        stateLabel: "Unknown",
        tone: "unknown",
        action: null,
        // "could not check" is not "absent" — box-capability-state.ts's own
        // central rule, applied to the copy as well as to the state.
        note: "This computer hasn't reported whether Docker is ready.",
        healthy: false,
        problem: false,
      };
  }
}

/** Ollama's own documented floor is ~8 GB of RAM for even a 7B-class model.
 *  Below that the box physically cannot serve local inference, which is a
 *  different fact from "not installed" and must not read as one. */
export const OLLAMA_MIN_MEMORY_GB = 8;

export function ollamaMemoryShortfallGB(memoryTotalBytes: number | null | undefined): number | null {
  if (typeof memoryTotalBytes !== "number" || !Number.isFinite(memoryTotalBytes) || memoryTotalBytes <= 0) return null;
  const totalGB = memoryTotalBytes / 1_000_000_000;
  return totalGB >= OLLAMA_MIN_MEMORY_GB ? null : totalGB;
}

export function planLocalModelCapabilityRow(
  status: ProbeStatus,
  memoryTotalBytes: number | null | undefined,
): CapabilityRow {
  const key = "ollama";
  const label = "Running a model on this computer";
  if (status === "ready") {
    return { key, label, stateLabel: "Ready", tone: "ready", action: null, note: null, healthy: true, problem: false };
  }
  const shortfall = ollamaMemoryShortfallGB(memoryTotalBytes);
  if (shortfall !== null) {
    const have = shortfall < 1 ? `${shortfall.toFixed(1)} GB` : `${Math.round(shortfall)} GB`;
    return {
      key,
      label,
      stateLabel: "Too small",
      tone: "unknown",
      action: null,
      note: `This computer has ${have} of memory. Running a model here needs about ${OLLAMA_MIN_MEMORY_GB} GB.`,
      healthy: false,
      problem: false,
    };
  }
  if (status === undefined || status === "unknown") {
    return {
      key,
      label,
      stateLabel: "Unknown",
      tone: "unknown",
      action: null,
      note: "This computer hasn't reported whether it can run a model locally.",
      healthy: false,
      problem: false,
    };
  }
  return {
    key,
    label,
    stateLabel: "Not set up",
    tone: "unknown",
    action: null,
    note: "No local model is running on this computer. Agents here use a hosted model instead.",
    healthy: false,
    problem: false,
  };
}

/** Channels, composed from box-capability-state.ts's own
 *  planChannelTransportState rather than re-deriving it — the transport
 *  state and this row can never disagree because there is one derivation.
 *
 *  `hasSetupTarget` is what stops the "Set up" button being a dead control:
 *  the channel transport is provisioned against a specific agent's channel
 *  policy, so with no agent pinned to this box there is nothing the button
 *  could act on and it is not rendered. */
export function planChannelCapabilityRow(
  transportState: "ready" | "set_up" | "unavailable" | "unknown",
  hasSetupTarget: boolean,
): CapabilityRow {
  const key = "channels";
  const label = "Channels";
  switch (transportState) {
    case "ready":
      return { key, label, stateLabel: "Ready", tone: "ready", action: null, note: null, healthy: true, problem: false };
    case "set_up":
      return {
        key,
        label,
        stateLabel: "Needs setting up",
        tone: "degraded",
        action: hasSetupTarget ? "set_up" : null,
        note: hasSetupTarget
          ? null
          : "Pin an agent to this computer first — channels are set up for a particular agent.",
        healthy: false,
        problem: false,
      };
    case "unavailable":
      return {
        key,
        label,
        stateLabel: "Not set up",
        tone: "unknown",
        action: hasSetupTarget ? "set_up" : null,
        note: hasSetupTarget
          ? null
          : "Pin an agent to this computer first — channels are set up for a particular agent.",
        healthy: false,
        problem: false,
      };
    default:
      return {
        key,
        label,
        stateLabel: "Unknown",
        tone: "unknown",
        // Nothing a setup run would fix — the honest next step is to check
        // again, not to re-run setup against an unknown.
        action: null,
        note: "This computer hasn't reported whether channels are ready.",
        healthy: false,
        problem: false,
      };
  }
}

// ── 3. WHAT'S WRONG? ─────────────────────────────────────────────────────

export interface BoxProblem {
  key: string;
  /** What is wrong, in one line. */
  title: string;
  /** What to do about it, in one line — or null when there is nothing the
   *  reader can do and waiting is the honest answer. */
  detail: string | null;
}

export interface BoxProblemFacts extends BoxHealthFacts {
  /** The backend's own refusal sentence, when it refused. */
  updateRefusalReason: string | null;
}

/** SECTION 3 CARRIES ONLY WHAT SECTION 2 CANNOT SAY.
 *
 *  This deliberately does NOT enumerate capability problems. Driven live,
 *  the version that did printed the identical three sentences twice on one
 *  screen — once beside the capability they belong to, once again below it —
 *  which made the page longer without making it truer. A capability states
 *  its own problem in its own row, where its own state and any control
 *  already are. What is left for this section is the facts that have no row
 *  of their own: the connection itself, updates, and the full-access
 *  mismatch. Repair and diagnostics live here too — see boxHasProblem. */
export function planBoxProblems(facts: BoxProblemFacts): BoxProblem[] {
  const problems: BoxProblem[] = [];

  if (facts.connectionTone === "offline" || facts.connectionTone === "error") {
    problems.push({
      key: "connection",
      title:
        facts.connectionTone === "error"
          ? "This computer needs connecting to Empyralis again."
          : "Empyralis can't reach this computer.",
      detail:
        facts.connectionTone === "error"
          ? "Connect it again from the Hardware page."
          : "Check the computer is switched on and online. It reconnects on its own once it is.",
    });
  }

  if (facts.cannotReceiveUpdates) {
    problems.push({
      key: "updates",
      title: "This computer can't receive updates.",
      detail: facts.updateRefusalReason,
    });
  }

  if (facts.fullAccessAuthorizedButOff === true) {
    problems.push({
      key: "full-access",
      title: "Full access is approved for this computer but isn't turned on there.",
      detail: "Agents keep running sandboxed until it is turned on on the computer itself.",
    });
  }

  return problems;
}

/** Whether section 3 renders at all — which is a WIDER question than
 *  "are there problem entries": a box whose only fault is Docker being down
 *  has no entry here (the Docker row said it), but still deserves the
 *  diagnostics and repair this section holds.
 *
 *  A row that is merely unfinished never counts. See CapabilityRow.problem
 *  for why: "not signed in" and "no local model" are ordinary states of a
 *  healthy computer, and letting them raise this section would put
 *  "What's wrong" on nearly every box in the product. */
export function boxHasProblem(
  problems: readonly BoxProblem[],
  capabilities: readonly CapabilityRow[],
): boolean {
  return problems.length > 0 || capabilities.some((capability) => capability.problem);
}
