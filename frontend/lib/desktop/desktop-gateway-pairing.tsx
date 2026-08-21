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
  }) => Promise<GatewayStartResult>;
};

declare global {
  interface Window {
    empyralisDesktop?: EmpyralisDesktopBridge;
  }
}

type PairingIntentResponse = { pairing_token?: string | null };

/** Bounded on purpose — see the doc comment. Roughly the same order as
 *  gateway_pairing_proof.rs's own 30s poll window, which is the only
 *  measurement anyone has of how long a real machine takes to come online. */
const CONFIRM_POLL_INTERVAL_MS = 1_500;
const CONFIRM_POLL_TIMEOUT_MS = 45_000;
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
  gatewayId: string | null,
  signal: { cancelled: boolean },
): Promise<ThisMachineState> {
  let best: ThisMachineState = 'absent';
  const deadline = Date.now() + CONFIRM_POLL_TIMEOUT_MS;
  while (Date.now() < deadline && !signal.cancelled) {
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

export function DesktopGatewayPairing({ workspaceId }: { workspaceId: string }) {
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
      const machine = await pollForThisMachine(workspaceId, status.gatewayId ?? null, signal);
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

    // Re-read the identity from the shell rather than trusting the id the
    // start call returned: on a FIRST pair the gateway writes identity.json
    // during registration, i.e. after that call already returned, so the id
    // is usually absent there and present a moment later.
    let gatewayId = result.gatewayId ?? status?.gatewayId ?? null;
    if (!gatewayId) {
      try {
        gatewayId = (await bridge.getGatewayStatus())?.gatewayId ?? null;
      } catch {
        // Keep the null and let the poll report couldNotConfirm honestly.
      }
    }
    const machine = await pollForThisMachine(workspaceId, gatewayId, signal);
    if (signal.cancelled) return;
    setState(
      resolveStartOutcome({
        machine,
        supervisorOk: result.supervisor?.ok !== false,
        supervisorDetail: result.supervisor?.detail,
      }),
    );
  }, [workspaceId]);

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

  // Only a clean success fades. Every other resting phase carries a fact the
  // owner has not acted on, so it stays until they dismiss it.
  useEffect(() => {
    if (state.phase !== 'connected') {
      return;
    }
    const timer = setTimeout(() => setDismissed(true), CONNECTED_VISIBLE_MS);
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
