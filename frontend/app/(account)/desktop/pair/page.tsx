/**
 * "Connect this Mac", the half that runs in the customer's OWN browser.
 *
 * The desktop app never hosts sign-in. It opens this page in the real
 * browser — where there is an address bar to check, a password manager and
 * existing sessions — and waits on a loopback listener for the answer. Same
 * shape as Ollama, Docker Desktop, Tailscale and the GitHub CLI, and the same
 * reason: a password typed into a window the app owns is a password the app
 * can read, and Google refuses OAuth in embedded webviews outright.
 *
 * ── Living in `(account)` is the auth story, and it is not incidental ─────
 * That layout already redirects a signed-out visitor through
 * `loginHrefForPath`, and `proxy.ts` puts `pathname + search` in
 * REQUEST_PATHNAME_HEADER — so the callback and the nonce survive the sign-in
 * and the customer lands back here with the request intact. This is the
 * MAN-358 machinery, reused rather than a fourth copy of it: the very first
 * customer to install this app has no session in that browser, so a flow that
 * lost its query through login would be broken for everybody's first run.
 *
 * ── The refusal happens on the SERVER, before anything is rendered ────────
 * A `callback` this page will not redirect to is refused here, with nothing
 * minted and no approve control rendered at all. `checkPairCallback` is the
 * gate — see its own module doc comment for what accepting an arbitrary one
 * would cost.
 */

import { loadAccountShellSession } from '@/lib/server/load-account-shell-session';
import {
  checkPairCallback,
  isValidPairState,
  pairCallbackRefusalDetail,
} from '@/lib/desktop/desktop-pair-callback';

import { DesktopPairApproval, type PairWorkspaceOption } from './DesktopPairApproval';
import './desktop-pair.css';

export const dynamic = 'force-dynamic';

function Refusal({ detail }: { detail: string }) {
  return (
    <main className="desktop-pair-page">
      <section className="desktop-pair-card">
        <h1 className="desktop-pair-title">Nothing was connected</h1>
        <p className="desktop-pair-detail">{detail}</p>
      </section>
    </main>
  );
}

export default async function DesktopPairPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const first = (key: string): string => {
    const raw = params[key];
    return (Array.isArray(raw) ? raw[0] : raw) ?? '';
  };

  const callbackCheck = checkPairCallback(first('callback'));
  if (!callbackCheck.ok) {
    return <Refusal detail={pairCallbackRefusalDetail(callbackCheck.reason)} />;
  }

  const state = first('state').trim();
  if (!isValidPairState(state)) {
    // A missing or malformed nonce means this request cannot be proven to
    // have come from the app, and the app would refuse the callback anyway.
    // Saying so here is better than minting a token that can only be thrown
    // away one hop later.
    return <Refusal detail={pairCallbackRefusalDetail('state')} />;
  }

  const session = await loadAccountShellSession();
  // The layout above has already redirected a signed-out visitor and rendered
  // its own recovery screen for a degraded one. Reaching here with no
  // memberships means the account has no workspace to connect a computer to —
  // a real, different fact from "you are not signed in".
  const memberships = session && 'workspaceMemberships' in session ? session.workspaceMemberships : [];
  const workspaces: PairWorkspaceOption[] = memberships.map((membership) => ({
    id: membership.workspace.id,
    label: membership.workspace.label,
    role: membership.role,
  }));

  const machineName = first('name').trim().slice(0, 80);

  return (
    <DesktopPairApproval
      callback={callbackCheck.callback}
      state={state}
      machineName={machineName}
      workspaces={workspaces}
    />
  );
}
