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
 * Whether a registrations item is THIS machine, online, right now.
 *
 * Both conditions are required and they are different facts:
 * `connection_status` is the registration's own view, `latest_session_status`
 * is whether a live WebSocket session is actually up. A registration can sit
 * at `online` with a dead session after an ungraceful disconnect — reporting
 * that as connected is the "a gateway-published snapshot goes stale the
 * moment the box goes offline" trap CLAUDE.md already records for channel
 * health pills. Mirrors gateway_pairing_proof.rs's own predicate exactly, so
 * the proof and the product agree on what "online" means.
 */
export function matchesThisMachine(item: GatewayRegistrationItem, gatewayId: string): boolean {
  const id = String(item.gateway_id ?? "").trim();
  if (!id || id !== gatewayId.trim()) {
    return false;
  }
  return (
    String(item.connection_status ?? "").trim() === "online" &&
    String(item.latest_session_status ?? "").trim() === "connected"
  );
}

export function findThisMachine(
  items: readonly GatewayRegistrationItem[],
  gatewayId: string | null,
): boolean {
  if (!gatewayId || !gatewayId.trim()) {
    // No identity to match against. NEVER fall back to "any gateway online"
    // — see this module's own doc comment; that is the exact shape that
    // would report a never-paired Mac as connected.
    return false;
  }
  return items.some((item) => matchesThisMachine(item, gatewayId));
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
  confirmed: boolean;
  supervisorOk: boolean;
  supervisorDetail?: string;
}): DesktopPairingState {
  if (!input.confirmed) {
    return describePairing(
      "couldNotConfirm",
      "This computer started up but hasn't shown up in your workspace yet. It may still connect on its own. Check your internet connection, or try again.",
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
