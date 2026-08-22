/**
 * Honest box-capability reporting — MAN (the 2026-08-13 install-script
 * incident): empyralis.ai/install/agent-computer.sh served a stale,
 * Cloudflare-cached installer with zero Docker and zero OpenClaw to every
 * real customer box, and nothing in the product noticed, because the cloud
 * never read back what a box actually ended up with. A broken install and a
 * healthy one looked identical.
 *
 * This module is the state-MAPPING half of the fix: pure functions, no
 * React, no network, no CSS import (same reason channel-doors.ts and
 * agent-count-shape.ts are pure — box-capability-state.test.ts runs under
 * plain `npx tsx`, outside Next.js/webpack). It takes the raw
 * service_inventory statuses the gateway's passive probes already report
 * (empyralis-gateway/src/health/service-inventory.ts's probeDocker /
 * probeOpenClaw / probeOpenClawChannelPlugins, sanitized generically by
 * gateway_inventory_service.py with no id allowlist — no backend change was
 * needed to carry these two new facts) and collapses each into exactly ONE
 * displayed state a card face can show.
 *
 * THE CENTRAL RULE, STATED ONCE SO IT CANNOT DRIFT PER CALL SITE
 * ----------------------------------------------------------------
 * "A capability that cannot be PROBED is not the same as one that is
 * ABSENT" (this module's own charter). `undefined` (the gateway build
 * predates this probe, or has never heartbeated it) and `"unknown"` (the
 * probe ran and could not get a confident answer — a timeout, an
 * unparseable response) must both land on the SAME "unknown" displayed
 * state, and that state must never be reachable from a genuine negative
 * signal (`"missing"`) or vice versa. box-capability-state.test.ts proves
 * this exhaustively over the full cross product of possible inputs, not
 * with a handful of hand-picked cases, so a THIRD raw status this file
 * doesn't know about yet still degrades to "unknown" rather than silently
 * falling through to whatever the last `case` happened to be.
 */

/** The gateway's own passive-probe vocabulary
 *  (empyralis-gateway/src/health/service-inventory.ts's PassiveServiceStatus)
 *  plus `undefined` for "this item is absent from service_inventory
 *  altogether" — an older gateway build, or one that has never heartbeated
 *  since the probe shipped. Never treat that omission as a stronger signal
 *  than "unknown" would be. */
export type ProbeStatus = "ready" | "degraded" | "offline" | "missing" | "unknown" | "blocked" | undefined;

/** Every value ProbeStatus can take, `undefined` included — the fixture the
 *  table-driven test iterates over so a new probe status this file doesn't
 *  yet branch on is exercised automatically rather than requiring someone to
 *  remember to add a test case for it. */
export const ALL_PROBE_STATUSES: readonly ProbeStatus[] = [
  "ready",
  "degraded",
  "offline",
  "missing",
  "unknown",
  "blocked",
  undefined,
];

// ── Sandbox (the box's tool-execution capability) ──────────────────────────

export type SandboxCapabilityState = "working" | "not_working" | "absent" | "unknown";

/** Collapses the `docker` service_inventory item's status into the three
 *  facts a customer needs, never collapsed further: present-and-working,
 *  present-but-not-working, and genuinely absent are three different next
 *  actions (nothing to do / restart it / install it), and squashing any two
 *  together throws one of them away. */
export function planSandboxCapabilityState(dockerStatus: ProbeStatus): SandboxCapabilityState {
  switch (dockerStatus) {
    case "ready":
      return "working";
    case "degraded":
    case "offline":
    case "blocked":
      return "not_working";
    case "missing":
      return "absent";
    case "unknown":
    case undefined:
    default:
      return "unknown";
  }
}

// ── Channel transport ───────────────────────────────────────────────────────

export type ChannelTransportState = "ready" | "set_up" | "unavailable" | "unknown";

export interface ChannelTransportFacts {
  /** The `openclaw` service_inventory item's status — installed AND at the
   *  pinned version only when "ready". */
  transportStatus: ProbeStatus;
  /** The `openclaw_channel_plugins` item's status — "ready" only when at
   *  least one channel plugin is installed. Irrelevant once transportStatus
   *  already says the transport itself is missing or unconfirmed: there is
   *  nothing to enumerate plugins ON, so this input is ignored in both of
   *  those cases rather than allowed to override them. */
  pluginsStatus: ProbeStatus;
}

/** The one rule this whole module exists to enforce, applied in priority
 *  order — read top to bottom, first match wins, exactly like
 *  channel-doors.ts's planDoors():
 *
 *    1. transport confirmed ABSENT           -> "unavailable"
 *       (the strongest negative signal; nothing else can outrank it, and
 *        nothing else needs to be asked once this is known)
 *    2. transport status not confirmed       -> "unknown"
 *       (never probed, or the probe could not get a confident answer)
 *    3. transport confirmed present, but not
 *       at the required version              -> "set_up"
 *       (installed — the customer is not starting from zero — but this
 *        computer needs the setup action run again to reach the pinned
 *        build)
 *    4. transport ready, plugin count
 *       not confirmed                        -> "unknown"
 *    5. transport ready, zero plugins
 *       installed                            -> "set_up"
 *       (present, working, nothing connected yet — the honest "not
 *        finished" state, same word the channel-card grid already uses for
 *        this shape)
 *    6. transport ready, at least one
 *       plugin installed                     -> "ready"
 */
export function planChannelTransportState(facts: ChannelTransportFacts): ChannelTransportState {
  const { transportStatus, pluginsStatus } = facts;
  if (transportStatus === "missing") {
    return "unavailable";
  }
  if (transportStatus !== "ready") {
    // Covers "degraded" (installed, wrong version or unreadable) as
    // "set_up" — genuinely different from "unknown"/"unavailable" and
    // handled below — and folds every other non-"ready" status (unknown,
    // undefined, and any future/blocked/offline value this probe does not
    // actually emit today) into "unknown" rather than guessing.
    return transportStatus === "degraded" ? "set_up" : "unknown";
  }
  if (pluginsStatus !== "ready" && pluginsStatus !== "degraded" && pluginsStatus !== "missing") {
    return "unknown";
  }
  if (pluginsStatus === "ready") {
    return "ready";
  }
  // "degraded" (zero installed) and the defensive "missing" case (should not
  // occur once the transport itself is ready — the gateway's own probe
  // short-circuits plugins to "missing" only when the transport is absent —
  // but treated the same way here rather than assumed unreachable) both mean
  // "the transport works; nothing is connected through it yet".
  return "set_up";
}

// ── Card-face copy — mechanism-free, matching openclaw-channel-copy.ts's
//    channelCardPill tone vocabulary so this reads as one colour system
//    with the rest of the fleet UI rather than a second one. ─────────────

export type CapabilityCardPill = {
  label: string;
  tone: "connected" | "gateway" | "locked" | "setup";
};

export function sandboxCapabilityPill(state: SandboxCapabilityState): CapabilityCardPill {
  switch (state) {
    case "working":
      return { label: "Ready", tone: "connected" };
    case "not_working":
      return { label: "Not ready", tone: "setup" };
    case "absent":
      return { label: "Not installed", tone: "setup" };
    case "unknown":
    default:
      return { label: "Unknown", tone: "locked" };
  }
}

export function channelTransportPill(state: ChannelTransportState): CapabilityCardPill {
  switch (state) {
    case "ready":
      return { label: "Ready", tone: "connected" };
    case "set_up":
      return { label: "Set up", tone: "setup" };
    case "unavailable":
      return { label: "Unavailable", tone: "locked" };
    case "unknown":
    default:
      return { label: "Unknown", tone: "locked" };
  }
}

/** One line, no mechanism named — the control does the work, per
 *  openclaw-channel-copy.ts's own Remediation doc comment ("the customer is
 *  never told to install things"). `hasSetupAction` decides whether a "Set
 *  up" button renders at all: "unknown" has nothing a button could fix yet
 *  (retrying the probe, not re-running setup, is the honest next step), and
 *  a control that cannot do anything is not rendered — CLAUDE.md's "no dead
 *  controls". */
export function channelTransportSummary(state: ChannelTransportState): { headline: string; hasSetupAction: boolean } {
  switch (state) {
    case "ready":
      return { headline: "Channels are ready.", hasSetupAction: false };
    case "set_up":
      return { headline: "Channels need one more step to finish connecting.", hasSetupAction: true };
    case "unavailable":
      return { headline: "Channels unavailable.", hasSetupAction: true };
    case "unknown":
    default:
      return { headline: "Could not check whether channels are ready.", hasSetupAction: false };
  }
}

// ── WHICH ISOLATION IS ACTUALLY IN EFFECT ON THIS COMPUTER ────────────────
//
// Founder's ruling, 2026-08-22: "Docker is not something that is going to
// degrade what we do. It's just something that is fundamentally going to be
// safer... not like 'this is impossible to run here' or 'because you don't
// have Docker I don't have any permission to run it'." And on saying so:
// "we should be honest with the customer... most people already know what
// Docker is."
//
// So Docker's presence chooses the isolation LEVEL, and the product states
// which one is in effect. It is not a prerequisite the owner has to satisfy,
// not a mode they must pick before anything runs, and not a gate. The
// honesty lives in the LABEL.
//
// TWO TRUE STATEMENTS, NEITHER AN ERROR AND NEITHER A WARNING. Do not render
// either of these with a warning tone, an alert icon, or a "fix this"
// affordance — that would put the wall back in the customer's head after it
// was taken out of the code.
//
// The strings below are a deliberate, pinned MIRROR of
// empyralis-gateway/src/shell/execution-isolation.ts's SANDBOX_STATEMENT /
// HOST_STATEMENT / FULL_ACCESS_STATEMENT — a genuine cross-language
// duplicate, the same shape (and the same justification) as the channel-map
// duplicate CLAUDE.md already documents between the gateway and the backend.
// The gateway reports what actually happened on each call; this page has to
// say what WILL happen before any call is made, and cannot import
// TypeScript that ships in a different package. box-capability-state.test.ts
// pins them so a change on one side is visible on the other.

export type ExecutionIsolation = "sandbox" | "host" | "full_access" | "unknown";

export const ISOLATION_SANDBOX_STATEMENT = "Commands run isolated in a container on this computer.";
export const ISOLATION_HOST_STATEMENT =
  "Commands run directly on this computer, because Docker isn't running here.";
export const ISOLATION_FULL_ACCESS_STATEMENT =
  "Commands run directly on this computer, which has full access turned on.";

export interface ExecutionIsolationFacts {
  /** The `docker` probe status, from service_readiness.docker.status or the
   *  raw service_inventory item. */
  dockerStatus: ProbeStatus;
  /** The SERVER's half of the full_access opt-in — what this box was
   *  authorized for at pairing time (gateway_registry_service's
   *  runtime_access_mode). */
  runtimeAccessMode?: string | null;
  /** The BOX's own half, reported live on every heartbeat. `null`/`undefined`
   *  means this gateway has not reported it (an older build) — which is
   *  "don't know", never "false". */
  shellFullAccessLocallyEnabled?: boolean | null;
}

/**
 * First match wins, top to bottom — same shape as planChannelTransportState
 * above and channel-doors.ts's planDoors().
 *
 *   1. full_access authorized server-side AND enabled on the box  -> full_access
 *      (BOTH halves, because that is the actual two-part opt-in the gateway
 *       enforces; one half alone changes nothing about what runs, so claiming
 *       it here would be a label describing a state the box is not in)
 *   2. full_access authorized but the box half is UNREPORTED      -> unknown
 *      (an older gateway build: we genuinely cannot tell which of two very
 *       different things this computer will do, and guessing either way is
 *       the dishonesty this module exists to prevent)
 *   3. Docker confirmed ready                                      -> sandbox
 *   4. Docker confirmed NOT ready (offline/degraded/blocked/missing) -> host
 *   5. Docker unprobed or unconfirmed                              -> unknown
 *
 * Note step 4 folds "absent" and "not_working" together ON PURPOSE, which is
 * the one place this module deliberately collapses two of
 * planSandboxCapabilityState's states: they lead to different ADMIN actions
 * (install it vs. start it) but to the identical fact about what happens to
 * a command right now, and this function answers only that question.
 */
export function planExecutionIsolation(facts: ExecutionIsolationFacts): ExecutionIsolation {
  const authorizedFullAccess = String(facts.runtimeAccessMode || "").trim().toLowerCase() === "full_access";
  if (authorizedFullAccess) {
    if (facts.shellFullAccessLocallyEnabled === true) {
      return "full_access";
    }
    if (facts.shellFullAccessLocallyEnabled !== false) {
      return "unknown";
    }
    // Explicitly false: the box will NOT run full access, so what actually
    // happens is decided by Docker exactly as it is for any other computer.
  }
  const sandboxState = planSandboxCapabilityState(facts.dockerStatus);
  if (sandboxState === "working") {
    return "sandbox";
  }
  if (sandboxState === "unknown") {
    return "unknown";
  }
  return "host";
}

/** The one sentence to render, or null when there is nothing true to say
 *  yet. `null` is a REAL answer and must render NOTHING — "this computer
 *  runs commands on the host" and "I have not found out yet" are different
 *  facts, and the second one has no sentence. */
export function executionIsolationStatement(isolation: ExecutionIsolation): string | null {
  switch (isolation) {
    case "sandbox":
      return ISOLATION_SANDBOX_STATEMENT;
    case "host":
      return ISOLATION_HOST_STATEMENT;
    case "full_access":
      return ISOLATION_FULL_ACCESS_STATEMENT;
    case "unknown":
    default:
      return null;
  }
}

/** Pulls the `docker` status out of whichever shape a caller has. Prefers
 *  the backend's already-distilled service_readiness (gateway_registry_
 *  service._hardware_execution_readiness_summary) and falls back to the raw
 *  service_inventory list, so this works against an older payload too.
 *  Returns undefined — "not reported" — rather than guessing. */
export function dockerStatusFromGatewayPayload(payload: {
  service_readiness?: { docker?: { status?: string | null } | null } | null;
  metadata?: { service_inventory?: { id?: string; status?: string }[] } | null;
} | null | undefined): ProbeStatus {
  const distilled = String(payload?.service_readiness?.docker?.status || "").trim().toLowerCase();
  if (distilled && distilled !== "unknown") {
    return distilled as ProbeStatus;
  }
  const inventory = payload?.metadata?.service_inventory;
  if (Array.isArray(inventory)) {
    const item = inventory.find((entry) => String(entry?.id || "").trim() === "docker");
    const raw = String(item?.status || "").trim().toLowerCase();
    if (raw) {
      return raw as ProbeStatus;
    }
  }
  return distilled ? (distilled as ProbeStatus) : undefined;
}
