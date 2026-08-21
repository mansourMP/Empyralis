'use client';

/**
 * The approve/refuse surface for "connect this Mac", rendered in the
 * customer's own browser. See page.tsx for why the flow works this way.
 *
 * ── Approving is what mints. Nothing before it does ──────────────────────
 * Landing on this page mints nothing, and a refused callback never gets this
 * far (page.tsx checks on the server). A pairing token only exists after a
 * deliberate press, and the moment it exists it is handed to the loopback
 * address that was checked — one hop, no storage, nothing left behind if the
 * customer walks away.
 *
 * ── Four outcomes, four sentences ────────────────────────────────────────
 * "You aren't allowed to", "we couldn't reach your workspace", "your
 * workspace turned it down" and "your workspace didn't send back what the app
 * needs" send a person to do four different things. Collapsing them into
 * "something went wrong" is the outcome-honesty failure CLAUDE.md names, and
 * this is a first-run screen — the worst possible place to be vague.
 */

import { useCallback, useMemo, useState } from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';
import { fleetAuthorizedFetch } from '@/lib/workspace/fleet/fleet-authorized-fetch';
import { buildPairCallbackUrl, buildPairDenialUrl } from '@/lib/desktop/desktop-pair-callback';

export type PairWorkspaceOption = {
  id: string;
  label: string;
  role: string;
};

type MintResult = { token: string } | { error: string };

async function mintPairingToken(
  workspaceId: string,
  displayName: string,
): Promise<MintResult> {
  let response: Response;
  try {
    response = await fleetAuthorizedFetch('/api/gateway/pairings/intents', {
      method: 'POST',
      credentials: 'include',
      headers: buildCookieAuthHeaders('POST', { 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        workspace_id: workspaceId,
        display_name: displayName,
        platform: 'macos',
        // Sandbox is the floor, stated rather than inferred. The full-access
        // opt-in is a deliberate, separately-labelled choice that lives in
        // Settings -> Connections (GatewayPairPanel), never a default and
        // never something a first-run screen decides on somebody's behalf.
        runtime_access_mode: 'default_guarded',
      }),
    });
  } catch {
    // No response at all — the request never landed, or the reply was lost.
    // A different fact from a refusal: retrying this is safe.
    return { error: "Couldn't reach your workspace. Check your internet connection and try again." };
  }
  if (!response.ok) {
    return {
      error:
        response.status === 403
          ? "You don't have permission to connect a computer to this workspace."
          : 'Your workspace turned down the request to connect this computer. Try again in a moment.',
    };
  }
  const payload = (await response.json().catch(() => null)) as { pairing_token?: string | null } | null;
  const token = payload?.pairing_token;
  if (typeof token !== 'string' || !token.trim()) {
    return { error: "Your workspace didn't send back what this computer needs to connect." };
  }
  return { token: token.trim() };
}

export function DesktopPairApproval({
  callback,
  state,
  machineName,
  workspaces,
}: {
  /** Already checked server-side by `checkPairCallback`. Re-checked again
   *  inside the builders, so a bug here cannot launder a hostile address. */
  callback: string;
  state: string;
  machineName: string;
  workspaces: PairWorkspaceOption[];
}) {
  const connectable = useMemo(
    // A viewer cannot mint a pairing intent, so offering them a workspace
    // they will be refused for is a control that cannot act.
    () => workspaces.filter((workspace) => workspace.role === 'owner' || workspace.role === 'member'),
    [workspaces],
  );
  const [workspaceId, setWorkspaceId] = useState(() => connectable[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const label = machineName.trim() || 'This computer';

  const connect = useCallback(async () => {
    const chosen = connectable.find((workspace) => workspace.id === workspaceId);
    if (!chosen) {
      setError('Pick a workspace first.');
      return;
    }
    setBusy(true);
    setError(null);
    const minted = await mintPairingToken(chosen.id, label);
    if ('error' in minted) {
      setBusy(false);
      setError(minted.error);
      return;
    }
    // Straight to the loopback listener. Deliberately `replace`, not
    // `assign`: the URL carries a single-use token, and there is no reason to
    // leave a back-button entry pointing at it.
    window.location.replace(
      buildPairCallbackUrl(callback, {
        state,
        pairingToken: minted.token,
        workspaceId: chosen.id,
        workspaceLabel: chosen.label,
      }),
    );
  }, [callback, connectable, label, state, workspaceId]);

  const cancel = useCallback(() => {
    // Told, never left to time out. "They said no" and "the browser never
    // came back" are different facts, and the app cannot tell them apart on
    // its own.
    window.location.replace(buildPairDenialUrl(callback, state));
  }, [callback, state]);

  if (connectable.length === 0) {
    return (
      <main className="desktop-pair-page">
        <section className="desktop-pair-card">
          <h1 className="desktop-pair-title">Nothing to connect this computer to</h1>
          <p className="desktop-pair-detail">
            {workspaces.length === 0
              ? "This account isn't in a workspace yet. Create one, then connect this computer."
              : 'You can view these workspaces but not add a computer to them. Ask an owner to invite you as a member.'}
          </p>
          <div className="desktop-pair-actions">
            <button type="button" className="desktop-pair-btn" onClick={cancel}>
              Go back to the app
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="desktop-pair-page">
      <section className="desktop-pair-card">
        <h1 className="desktop-pair-title">Connect “{label}”?</h1>
        <p className="desktop-pair-detail">
          Your agents will be able to run work on this computer.
        </p>

        {connectable.length === 1 ? (
          // One workspace is not a choice, so it is not rendered as one — but
          // it is still NAMED, because "connect it" without saying where is a
          // decision made on somebody's behalf.
          <p className="desktop-pair-workspace">
            It will join <strong>{connectable[0].label}</strong>.
          </p>
        ) : (
          <label className="desktop-pair-field">
            <span>Workspace</span>
            <select
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.currentTarget.value)}
              disabled={busy}
            >
              {connectable.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.label}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="desktop-pair-actions">
          <button
            type="button"
            className="desktop-pair-btn desktop-pair-btn--primary"
            onClick={() => void connect()}
            disabled={busy}
          >
            {busy ? 'Connecting…' : 'Connect'}
          </button>
          <button type="button" className="desktop-pair-btn" onClick={cancel} disabled={busy}>
            Cancel
          </button>
        </div>

        {error ? <p className="desktop-pair-error">{error}</p> : null}
      </section>
    </main>
  );
}
