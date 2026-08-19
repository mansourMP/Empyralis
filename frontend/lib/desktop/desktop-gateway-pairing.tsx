'use client';

import { useEffect, useRef } from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import { fleetAuthorizedFetch } from '@/lib/workspace/fleet/fleet-authorized-fetch';

/**
 * The webview-side half of "installing the desktop app IS pairing the
 * machine" (src-tauri/src/lib.rs's own top-of-file doc comment). Renders
 * nothing — its only job is, once per app session, to either resume an
 * already-paired gateway or mint a fresh pairing token using this page's
 * own authenticated session and hand it to the native shell, which spawns
 * and supervises the real `empyralis-gateway` process. Outside the desktop
 * app (`window.empyralisDesktop` absent, i.e. every ordinary browser tab)
 * this is a complete no-op.
 *
 * Deliberately a SEPARATE path from `GatewayPairPanel.tsx`
 * (Settings → Connections → "add your own computer"), not a refactor of
 * it: that panel's job is generating a `curl ... | bash` command for a
 * computer this webview cannot reach directly (a VPS, a different
 * machine) — the exact shell-quoting-hazard flow this desktop app exists
 * to make unnecessary for THIS machine specifically. Reusing its token-
 * mint call shape (`fleetAuthorizedFetch` + `buildCookieAuthHeaders`, the
 * same CSRF-correct pattern) avoids inventing a second, weaker way to hit
 * the same endpoint, without touching the working manual-pairing UI other
 * setups still need.
 *
 * Deliberately best-effort and silent on failure: a pairing attempt that
 * doesn't land must never block the workspace from rendering or surface a
 * scary error for something the person didn't initiate. The one thing this
 * component owns is trying once per mount; a real status surface (showing
 * whether THIS computer is currently a connected Agent Computer, with a
 * retry action) is a separate, larger UI piece — Settings → Connections
 * already renders hardware provider cards and would be the natural home
 * for it, but building that is out of scope here and is not attempted.
 */

type GatewayStatus = {
  running: boolean;
  everPaired: boolean;
  stateDir: string;
};

type GatewayStartResult = {
  started: boolean;
  alreadyRunning: boolean;
  pid?: number | null;
  stateDir: string;
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

async function mintPairingToken(workspaceId: string, displayName: string, platform: string): Promise<string | null> {
  const response = await fleetAuthorizedFetch('/api/gateway/pairings/intents', {
    method: 'POST',
    credentials: 'include',
    headers: buildCookieAuthHeaders('POST', { 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      workspace_id: workspaceId,
      display_name: displayName,
      platform,
    }),
  });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json().catch(() => null)) as PairingIntentResponse | null;
  const token = payload?.pairing_token;
  return typeof token === 'string' && token.trim() ? token : null;
}

export function DesktopGatewayPairing({ workspaceId }: { workspaceId: string }) {
  const attempted = useRef(false);

  useEffect(() => {
    if (attempted.current || typeof window === 'undefined') {
      return;
    }
    const bridge = window.empyralisDesktop;
    if (!bridge?.desktop || !bridge.getGatewayStatus || !bridge.pairAndStartGateway) {
      return;
    }
    attempted.current = true;

    const apiBaseUrl = `${window.location.origin}/api`;
    const displayName = 'This computer';

    (async () => {
      try {
        const status = await bridge.getGatewayStatus!();
        if (status?.running) {
          return;
        }

        if (status?.everPaired) {
          // Resume: no fresh token needed, the gateway has its own
          // persisted registration in its state directory.
          await bridge.pairAndStartGateway!({ apiBaseUrl, workspaceId, displayName });
          return;
        }

        const pairingToken = await mintPairingToken(workspaceId, displayName, bridge.platform);
        if (!pairingToken) {
          return;
        }
        await bridge.pairAndStartGateway!({ apiBaseUrl, pairingToken, workspaceId, displayName });
      } catch {
        // Best-effort — see module doc comment.
      }
    })();
  }, [workspaceId]);

  return null;
}
