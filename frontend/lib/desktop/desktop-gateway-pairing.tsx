'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import { fleetAuthorizedFetch } from '@/lib/workspace/fleet/fleet-authorized-fetch';
import {
  describePairing,
  findThisMachine,
  resolveStartOutcome,
  type DesktopPairingState,
  type GatewayRegistrationItem,
  type ThisMachineState,
} from '@/lib/desktop/desktop-pairing-state';

import './desktop-gateway-pairing.css';

/**
 * The webview-side half of "installing the desktop app IS pairing the
 * machine" (src-tauri/src/lib.rs's own top-of-file doc comment). Once per
 * app session it either resumes an already-paired computer or connects a
 * fresh one using this page's own authenticated session, hands the result
 * to the native shell, and — this is the part that used to be missing —
 * SAYS WHAT HAPPENED. Outside the desktop app (`window.empyralisDesktop`
 * absent, i.e. every ordinary browser tab) this is a complete no-op and
 * renders nothing.
 *
 * ── What changed, and why it is not scope creep ──────────────────────────
 * This component used to wrap the entire sequence in one empty-bodied
 * `catch` and return `null`, with its own doc comment stating that a real
 * status surface "is out of scope here and is not attempted." The result:
 * a customer who double-clicked the app saw an identical screen whether the
 * machine paired perfectly, could not mint a token, or spawned a child that
 * died on boot. That is CLAUDE.md's outcome-honesty law broken at the
 * product's own front door, and it is the specific thing the founder asked
 * for ("show honest status — Connecting… / Connected / a real error, never
 * a spinner that lasts forever").
 *
 * ── "Connected" is PROVEN by the control plane, never by the spawn ───────
 * The native shell's `started: true` means only that a child process was
 * spawned and had not exited 1.5s later. Registering, opening the socket
 * and being marked online all happen afterwards and can each fail silently.
 * So a successful start puts this in `starting`, and ONLY a poll of
 * `GET /gateway/registrations` reporting THIS machine's own gateway id
 * online promotes it to `connected` — see desktop-pairing-state.ts for why
 * matching on the id (rather than "any online gateway in the workspace")
 * is the whole correctness story.
 *
 * ── The poll is bounded and its expiry is an HONEST phase ────────────────
 * If the window elapses with no match, this reports `couldNotConfirm` — not
 * `failed`. The child is still running and may come online a moment later;
 * telling the owner it failed would make them retry something that already
 * worked. The poll stopping is what guarantees the founder's "never a
 * spinner that lasts forever": every path out of `pairing`/`starting`
 * terminates in a resting phase that says something.
 *
 * Deliberately still a SEPARATE path from `GatewayPairPanel.tsx`
 * (Settings → Connections → "add your own computer"), not a refactor of it:
 * that panel's job is generating a `curl … | bash` command for a computer
 * this webview cannot reach directly (a VPS, a different machine) — the
 * exact flow this desktop app exists to make unnecessary for THIS machine.
 */

type GatewayStatus = {
  running: boolean;
  everPaired: boolean;
  stateDir: string;
  /** This machine's own identity, read by the native shell out of
   *  `<state_dir>/identity.json`. Absent until a first pairing completes.
   *  Older shells that predate this field simply omit it, which resolves to
   *  `couldNotConfirm` rather than a wrong "Connected" — see
   *  desktop-pairing-state.ts's `findThisMachine`. */
  gatewayId?: string | null;
};

type GatewayStartResult = {
  started: boolean;
  alreadyRunning: boolean;
  pid?: number | null;
  stateDir: string;
  gatewayId?: string | null;
  supervisor: { attempted: boolean; ok: boolean; detail: string };
};

type EmpyralisDesktopBridge = {
  desktop: boolean;
  platform: string;
  getGatewayStatus?: () => Promise<GatewayStatus | null>;
  pairAndStartGateway?: (request: {
    apiBaseUrl: string;
    pairingToken?: string;
    workspaceId: string;
    displayName?: string;
    /** The workspace's HUMAN name. Recorded natively so the menu bar can say
     *  which workspace this computer serves while no window exists — only a
     *  signed-in webview knows it, and a raw `ws_9f3c…` in a menu is not an
     *  answer to "what is this computer doing". */
    workspaceLabel?: string;
  }) => Promise<GatewayStartResult>;
  /** Present only on shells that ship the menu bar app. Optional so an older
   *  shell keeps its window rather than throwing — the window staying up is a
   *  strictly safer degradation than a pairing that ends in an error. */
  hideWindow?: () => Promise<boolean>;
};

declare global {
  interface Window {
    empyralisDesktop?: EmpyralisDesktopBridge;
  }
}

type PairingIntentResponse = { pairing_token?: string | null };

/**
 * Bounded on purpose — see the doc comment. Sized from a MEASURED first
 * pairing rather than a guess: on a real machine against a real backend the
 * gateway took roughly 18s from spawn to writing its identity and appearing
 * in the workspace. 60s is ~3x that headroom while still being a window a
 * person will sit through, which is the trade the founder's "never a spinner
 * that lasts forever" actually asks for — the point is that it ENDS, in a
 * phase that says something, not that it ends quickly.
 *
 * examples/gateway_pairing_proof.rs uses 30s for the same wait. That is fine
 * for a proof run by someone watching a terminal and too tight here, where
 * expiry shows a customer a caveat about a machine that was merely slow.
 */
const CONFIRM_POLL_INTERVAL_MS = 1_500;
const CONFIRM_POLL_TIMEOUT_MS = 60_000;
/** How long a clean success stays on screen before fading. Long enough to
 *  read, short enough that the app does not carry a permanent banner about
 *  something that is simply working. */
const CONNECTED_VISIBLE_MS = 6_000;

async function mintPairingToken(
  workspaceId: string,
  displayName: string,
  platform: string,
): Promise<{ token: string } | { error: string }> {
  let response: Response;
  try {
    response = await fleetAuthorizedFetch('/api/gateway/pairings/intents', {
      method: 'POST',
      credentials: 'include',
      headers: buildCookieAuthHeaders('POST', { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ workspace_id: workspaceId, display_name: displayName, platform }),
    });
  } catch {
    // No response at all — the request never landed, or the reply was lost.
    // Distinct from a refusal: retrying this is safe and often works.
    return { error: "Couldn't reach your workspace. Check your internet connection and try again." };
  }
  if (!response.ok) {
    return {
      error:
        response.status === 403
          ? "You don't have permission to connect a computer to this workspace."
          : "Your workspace turned down the request to connect this computer. Try again in a moment.",
    };
  }
  const payload = (await response.json().catch(() => null)) as PairingIntentResponse | null;
  const token = payload?.pairing_token;
  if (typeof token !== 'string' || !token.trim()) {
    return { error: "Your workspace didn't send back what this computer needs to connect." };
  }
  return { token: token.trim() };
}

/**
 * Polls until THIS machine shows up, or the window closes.
 *
 * Returns the machine's state rather than a boolean because `limited`
 * (connected, but Docker not running so it cannot run commands yet) is a
 * real third answer -- and, on a Mac nobody has set up, the usual one. See
 * desktop-pairing-state.ts for why collapsing it into either neighbour
 * reports a lie.
 *
 * A `limited` reading does NOT stop the poll early: a machine whose Docker
 * is still starting will promote itself to `connected` within the window,
 * and settling for the weaker answer while a better one is still coming
 * would leave a caveat on screen that is about to stop being true.
 */
async function pollForThisMachine(
  workspaceId: string,
  resolveGatewayId: () => Promise<string | null>,
  signal: { cancelled: boolean },
): Promise<ThisMachineState> {
  let best: ThisMachineState = 'absent';
  let gatewayId: string | null = null;
  const deadline = Date.now() + CONFIRM_POLL_TIMEOUT_MS;
  while (Date.now() < deadline && !signal.cancelled) {
    // Re-resolved every tick until found, never once up front. MEASURED on a
    // real first pairing: the child process starts, the native shell returns
    // after its 1.5s boot grace, and identity.json does not appear for
    // another ~16s -- the gateway writes it partway through registering. A
    // single read before the loop therefore finds nothing on exactly the run
    // that matters (a brand-new machine), leaves gatewayId null forever, and
    // reports "couldn't confirm" on a pairing that in fact succeeded.
    if (!gatewayId) {
      try {
        gatewayId = await resolveGatewayId();
      } catch {
        // Keep waiting; an unreadable identity this tick is not an answer.
      }
    }
    try {
      const response = await fleetAuthorizedFetch(
        `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: 'include' },
      );
      if (response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { items?: GatewayRegistrationItem[] }
          | null;
        const state = findThisMachine(payload?.items ?? [], gatewayId);
        if (state === 'connected') {
          return 'connected';
        }
        if (state === 'limited') {
          best = 'limited';
        }
      }
    } catch {
      // A single failed poll is not an answer — keep asking until the
      // window closes, then report couldNotConfirm honestly.
    }
    await new Promise((resolve) => setTimeout(resolve, CONFIRM_POLL_INTERVAL_MS));
  }
  return best;
}

export function DesktopGatewayPairing({
  workspaceId,
  workspaceLabel,
}: {
  workspaceId: string;
  /** Passed straight through to the native shell, which records it so the
   *  menu bar can name this workspace with no window open. */
  workspaceLabel?: string;
}) {
  const [state, setState] = useState<DesktopPairingState>(() => describePairing('inactive'));
  const [dismissed, setDismissed] = useState(false);
  const attempted = useRef(false);
  const cancelSignal = useRef<{ cancelled: boolean }>({ cancelled: false });

  const run = useCallback(async () => {
    const bridge = typeof window === 'undefined' ? undefined : window.empyralisDesktop;
    if (!bridge?.desktop || !bridge.getGatewayStatus || !bridge.pairAndStartGateway) {
      return;
    }
    const signal = cancelSignal.current;
    const apiBaseUrl = `${window.location.origin}/api`;
    const displayName = 'This computer';

    setDismissed(false);
    setState(describePairing('checking'));

    let status: GatewayStatus | null = null;
    try {
      status = await bridge.getGatewayStatus();
    } catch {
      setState(
        describePairing('failed', "This computer couldn't be checked. Try again, or restart the app."),
      );
      return;
    }
    if (signal.cancelled) return;

    // Already running from a previous launch or started at login. Still not
    // proof the control plane can see it — confirm before saying connected.
    if (status?.running) {
      setState(describePairing('starting'));
      const machine = await pollForThisMachine(
        workspaceId,
        async () => status?.gatewayId ?? (await bridge.getGatewayStatus!())?.gatewayId ?? null,
        signal,
      );
      if (signal.cancelled) return;
      setState(resolveStartOutcome({ machine, supervisorOk: true }));
      return;
    }

    let pairingToken: string | undefined;
    if (!status?.everPaired) {
      setState(describePairing('pairing'));
      const minted = await mintPairingToken(workspaceId, displayName, bridge.platform);
      if (signal.cancelled) return;
      if ('error' in minted) {
        setState(describePairing('failed', minted.error));
        return;
      }
      pairingToken = minted.token;
    }

    setState(describePairing('starting'));
    let result: GatewayStartResult;
    try {
      result = await bridge.pairAndStartGateway({
        apiBaseUrl,
        ...(pairingToken ? { pairingToken } : {}),
        workspaceId,
        displayName,
        ...(workspaceLabel?.trim() ? { workspaceLabel: workspaceLabel.trim() } : {}),
      });
    } catch (error) {
      // The native shell's own message is already customer-shaped prose
      // (see lib.rs) — relay it rather than replacing it with something
      // vaguer, but never let a raw object stringify to "[object Object]".
      const message = error instanceof Error ? error.message : '';
      setState(
        describePairing(
          'failed',
          message.trim() || "This computer couldn't be started up. Try again, or restart the app.",
        ),
      );
      return;
    }
    if (signal.cancelled) return;

    const machine = await pollForThisMachine(
      workspaceId,
      async () => result.gatewayId ?? (await bridge.getGatewayStatus!())?.gatewayId ?? null,
      signal,
    );
    if (signal.cancelled) return;
    setState(
      resolveStartOutcome({
        machine,
        supervisorOk: result.supervisor?.ok !== false,
        supervisorDetail: result.supervisor?.detail,
      }),
    );
  }, [workspaceId, workspaceLabel]);

  useEffect(() => {
    if (attempted.current) {
      return;
    }
    attempted.current = true;
    const signal = cancelSignal.current;
    void run();
    return () => {
      signal.cancelled = true;
    };
  }, [run]);

  // Only a clean success fades — and in the menu bar app, the whole WINDOW
  // goes with it.
  //
  // This is the founder's "the window appears exactly once" rule reaching its
  // one exit: pairing has succeeded, there is nothing left for a person to do
  // here, and from now on the menu bar item is the surface. `hideWindow` is
  // hide-not-close on purpose (see its own comment in lib.rs) — the process
  // has to outlive the window or the machine stops being an Agent Computer
  // the moment it disappears.
  //
  // Every OTHER resting phase keeps the window, because each carries a fact
  // the owner has not acted on. Hiding the window over one of those would be
  // the outcome-honesty law broken by disappearance rather than by wording:
  // the customer would be left with a menu bar icon and no idea that anything
  // needed them.
  useEffect(() => {
    if (state.phase !== 'connected') {
      return;
    }
    const timer = setTimeout(() => {
      setDismissed(true);
      // Failing to hide is not worth reporting and not worth retrying: the
      // window simply stays, showing a banner that says the machine is
      // connected — which is true. A shell without this bridge method (an
      // older build) lands here too, and gets exactly that.
      void window.empyralisDesktop?.hideWindow?.().catch(() => undefined);
    }, CONNECTED_VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [state.phase]);

  const retry = useCallback(() => {
    cancelSignal.current.cancelled = true;
    cancelSignal.current = { cancelled: false };
    void run();
  }, [run]);

  if (state.phase === 'inactive' || dismissed) {
    return null;
  }

  const working = state.phase === 'checking' || state.phase === 'pairing' || state.phase === 'starting';

  return (
    <div className="desktop-pairing" role="status" aria-live="polite" data-phase={state.phase}>
      <span className={`desktop-pairing-dot${working ? ' desktop-pairing-dot--working' : ''}`} aria-hidden="true" />
      <div className="desktop-pairing-body">
        <p className="desktop-pairing-title">{state.title}</p>
        {state.detail ? <p className="desktop-pairing-detail">{state.detail}</p> : null}
      </div>
      {state.canRetry ? (
        <button type="button" className="desktop-pairing-action" onClick={retry}>
          Try again
        </button>
      ) : null}
      {!working ? (
        <button
          type="button"
          className="desktop-pairing-dismiss"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
