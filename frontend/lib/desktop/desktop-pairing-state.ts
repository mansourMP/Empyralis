/**
 * What the desktop app is allowed to SAY about this computer's pairing —
 * pure data and pure functions, its own module for the same reason
 * channel-doors.ts and agent-count-shape.ts are: the test imports THE REAL
 * rule rather than a literal copied beside it.
 *
 * ── The bug this exists to close ─────────────────────────────────────────
 * desktop-gateway-pairing.tsx used to run the whole pairing sequence inside
 * one empty-bodied `catch` and render `null`. Its own doc comment said a
 * real status surface "is out of scope here and is not attempted." So a
 * customer who double-clicked the app got exactly the same
 * screen whether the machine paired perfectly, failed to mint a token, or
 * spawned a gateway that died on boot. That is CLAUDE.md's outcome-honesty
 * law violated at the one moment it matters most — the product's own front
 * door on a brand-new machine.
 *
 * ── "Connected" is PROVEN, never assumed ─────────────────────────────────
 * The native shell returning `started: true` means one thing only: a child
 * process was spawned and had not exited 1.5s later (lib.rs's
 * GATEWAY_BOOT_GRACE). It does NOT mean the control plane has seen this
 * machine. Registering, opening the WebSocket and being marked online all
 * happen afterwards, over the network, and any of them can fail silently.
 * So `started` is `starting`, never `connected` — the only thing that
 * promotes a machine to `connected` is `GET /gateway/registrations`
 * reporting THIS gateway online, which is what `matchesThisMachine` below
 * is for.
 *
 * ── Matching on THIS machine's gateway id is the whole correctness story ──
 * src-tauri/src/bin/gateway_pairing_proof.rs (the throwaway assessment
 * binary this promotes) polls `items.some(online)` — ANY online gateway in
 * the workspace. That is fine for a proof and WRONG for a status surface:
 * the founder's own workspace already holds a production VPS gateway that
 * is permanently online, so "any gateway online" renders a confident
 * "Connected" on a Mac that has never paired and never will. The gateway
 * writes its own identity to `<state_dir>/identity.json` at pairing time
 * (empyralis-gateway/src/pairing/device-identity.ts), the native shell
 * reads `gatewayId` back out of it, and this module refuses to promote
 * anything it cannot match by id — an unmatched poll is `couldNotConfirm`,
 * never `connected`.
 *
 * ── Five outcomes, never collapsed ───────────────────────────────────────
 * CLAUDE.md: "failed", "not connected yet" and "I lost track of it" are
 * different facts and must not share one message. They send a person to do
 * three different things — retry, wait, or go look at the network — so they
 * are three phases here, plus `connected` and a `degraded` that is
 * genuinely good news with one caveat attached.
 */

/** Read off `<state_dir>/identity.json`. Absent until the gateway has
 *  completed a first pairing, which is exactly why `everPaired` and this
 *  are two different signals rather than one. */
export type DesktopGatewayIdentity = { gatewayId: string | null };

export type GatewayRegistrationItem = {
  gateway_id?: string | null;
  connection_status?: string | null;
  latest_session_status?: string | null;
};

/**
 * `degraded` is deliberately NOT a failure and NOT a warning colour on its
 * own: the machine IS a working, connected Agent Computer right now. The
 * single thing that is untrue of it is that it will come back by itself
 * after a reboot. Collapsing it into `connected` hides a fact the owner
 * needs; collapsing it into `failed` tells them to retry something that
 * already worked.
 */
export type DesktopPairingPhase =
  | "inactive"
  | "checking"
  | "pairing"
  | "starting"
  | "connected"
  | "degraded"
  | "couldNotConfirm"
  | "failed";

export type DesktopPairingState = {
  phase: DesktopPairingPhase;
  /** One plain sentence. Never mechanism — no "gateway", "npm", "node",
   *  "process", "token". A customer who does not know what an agent is
   *  reads this. */
  title: string;
  detail: string;
  /** Whether a person can do anything about it right now. A retry control
   *  on a phase that is still working is a dead control. */
  canRetry: boolean;
  /** Whether this is a resting state the surface may fade out of. Only a
   *  clean `connected` qualifies: every other resting phase carries a fact
   *  the owner has not seen yet. */
  transient: boolean;
};

const STATES: Record<DesktopPairingPhase, Omit<DesktopPairingState, "phase" | "detail">> = {
  inactive: { title: "", canRetry: false, transient: true },
  checking: { title: "Checking this computer…", canRetry: false, transient: false },
  pairing: { title: "Connecting this computer…", canRetry: false, transient: false },
  starting: { title: "Connecting this computer…", canRetry: false, transient: false },
  connected: { title: "This computer is connected", canRetry: false, transient: true },
  degraded: { title: "Connected, with one thing left", canRetry: true, transient: false },
  couldNotConfirm: { title: "Couldn't confirm the connection", canRetry: true, transient: false },
  failed: { title: "Couldn't connect this computer", canRetry: true, transient: false },
};

export function describePairing(phase: DesktopPairingPhase, detail = ""): DesktopPairingState {
  const base = STATES[phase];
  return { phase, detail, ...base };
}

/**
 * What the control plane currently says about THIS machine.
 *
 * `limited` is not a smaller version of `connected` — it is its own fact,
 * and it is the one a fresh Mac almost always lands on first.
 */
export type ThisMachineState = "connected" | "limited" | "absent";

/**
 * `execution_blocked` is a DEMOTION FROM `online`, not a failure — and
 * getting this wrong would have shipped a lie on the most common machine
 * there is.
 *
 * Observed live, on a real first pairing against a real backend: a freshly
 * paired Mac reports `connection_status: "execution_blocked"` with
 * `latest_session_status: "connected"`. gateway_registry_service.py only
 * ever assigns it to a box that was ALREADY computed as `online` (live
 * session, fresh heartbeat) and then demotes it because Docker is not ready,
 * so `shell.execute`/`filesystem.read_write` are blocked. Docker not running
 * is the DEFAULT state of a Mac nobody has set up yet.
 *
 * So the predicate this module started with — `connection_status ===
 * "online"`, copied from examples/gateway_pairing_proof.rs, which is correct
 * for its own narrower purpose — would have told a customer whose machine
 * had just paired perfectly that we "couldn't confirm the connection". That
 * is the outcome-honesty law broken in the worst direction: reporting
 * failure on success, which makes a person retry something that already
 * worked.
 *
 * Connectivity and execution-readiness are two different facts and the
 * backend deliberately keeps them apart (its own comment says so). This
 * mirrors that split rather than flattening it back out.
 */
export function classifyThisMachine(
  item: GatewayRegistrationItem,
  gatewayId: string,
): ThisMachineState {
  const id = String(item.gateway_id ?? "").trim();
  if (!id || id !== gatewayId.trim()) {
    return "absent";
  }
  // A live session is required either way. A registration can sit at
  // `online` with a dead session after an ungraceful disconnect — reporting
  // that as connected is the "a published snapshot goes stale the moment the
  // box goes offline" trap CLAUDE.md already records for channel pills.
  if (String(item.latest_session_status ?? "").trim() !== "connected") {
    return "absent";
  }
  const connection = String(item.connection_status ?? "").trim();
  if (connection === "online") {
    return "connected";
  }
  if (connection === "execution_blocked") {
    return "limited";
  }
  // degraded / reconnecting / offline / revoked each carry their own more
  // urgent reason and are NOT a connected machine. Anything unrecognised
  // fails closed here too — a status vocabulary that grows must not
  // silently start reading as connected.
  return "absent";
}

export function findThisMachine(
  items: readonly GatewayRegistrationItem[],
  gatewayId: string | null,
): ThisMachineState {
  if (!gatewayId || !gatewayId.trim()) {
    // No identity to match against. NEVER fall back to "any gateway online"
    // — see this module's own doc comment; that is the exact shape that
    // would report a never-paired Mac as connected.
    return "absent";
  }
  let best: ThisMachineState = "absent";
  for (const item of items) {
    const state = classifyThisMachine(item, gatewayId);
    if (state === "connected") {
      return "connected";
    }
    if (state === "limited") {
      best = "limited";
    }
  }
  return best;
}

/**
 * Turns a finished start attempt plus the result of polling into the one
 * phase the surface renders.
 *
 * `confirmed === false` after the poll window is `couldNotConfirm`, NOT
 * `failed`: the child process is still running and may well come online a
 * moment later. Telling the owner it failed would be the "reporting failure
 * ON SUCCESS is the worst case" half of the outcome-honesty law — they would
 * retry something that already worked, or conclude the product is broken
 * while it is fine.
 */
export function resolveStartOutcome(input: {
  machine: ThisMachineState;
  supervisorOk: boolean;
  supervisorDetail?: string;
}): DesktopPairingState {
  if (input.machine === "absent") {
    return describePairing(
      "couldNotConfirm",
      "This computer started up but hasn't shown up in your workspace yet. It may still connect on its own. Check your internet connection, or try again.",
    );
  }
  // Connected but unable to run commands outranks the restart caveat: it is
  // about whether the machine can do work AT ALL right now, where the other
  // is only about what happens after a reboot.
  if (input.machine === "limited") {
    return describePairing(
      "degraded",
      "This computer is connected, but it can't run commands yet because Docker isn't running. Start Docker Desktop and it'll pick it up on its own.",
    );
  }
  if (!input.supervisorOk) {
    return describePairing(
      "degraded",
      "This computer is connected and ready to use. It won't come back on its own after you restart it, though — try again to fix that.",
    );
  }
  return describePairing("connected", "It's ready to use, and it'll come back on its own after you restart.");
}
