'use client';

// Accept a workspace invite link — Multiplayer Projects Phase 1 (MAN-114).
//
// This is deliberately a NEW route, not a repurposing of
// frontend/app/invite/[code]/page.tsx. That page is a different, unrelated,
// already-shipped feature: a 12-character PILOT PROGRAM signup code
// (server_modules/pilot_invite_service.py, validated via
// POST /api/pilot/invites/validate), gating registration to a plan. It has
// nothing to do with joining a workspace. Workspace invites mint a signed
// JWT-shaped `workspace_invite_v1` token (control_plane_repository.py's
// create_workspace_invite / verify_workspace_invite_token) and are accepted
// via POST /workspaces/invites/accept — a completely separate contract with
// a completely separate frontend surface. Reusing /invite/[code] for both
// would have silently broken the pilot flow (different validate endpoint,
// different code shape, different redirect target).
//
// Flow: check whether the visitor is signed in (GET /api/auth/me). If not,
// send them to log in (or create an account) with a `next` back to this same
// URL so they land right back here once authenticated — accept_workspace_invite_route
// gates on the caller's authenticated email matching the invite's email
// exactly, so there is no meaningful "preview" to show before that check.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

import { me } from '@/lib/auth/auth-client';
import { acceptWorkspaceInvite, unverifiedWorkspaceIdFromInviteToken } from '@/lib/workspace/fleet/members-data';
import { loadAccountShellBootstrap } from '@/lib/account/account-workspaces-client';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import { AppButton } from '@/lib/ui/primitives';

type Status = 'checking-session' | 'accepting' | 'accepted' | 'signed-out' | 'error';

export default function JoinWorkspaceInvitePage() {
  const router = useRouter();
  const params = useParams();
  const token = typeof params?.token === 'string' ? params.token : '';
  const { actions } = useAccountShell();

  const [status, setStatus] = useState<Status>('checking-session');
  const [error, setError] = useState<string | null>(null);
  // Whether the accept genuinely failed (the server said no — expired,
  // already used, wrong email) versus the request never producing a
  // response at all (offline, timeout, a dropped connection right after the
  // server committed it). These are different facts and must not share a
  // message: see acceptWorkspaceInvite's `ambiguous` flag and CLAUDE.md's
  // "reporting failure on success" law. Defaults false so the one
  // early-return path with no real attempt — a missing token — reads as a
  // plain dead link rather than implying a retry might do something.
  const [ambiguous, setAmbiguous] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);

  // Cancellation token rather than a boolean `cancelled` closure flag: this
  // flow can now run more than once per mount (the manual "Try again" below
  // as well as the original effect), and each run must be able to tell a
  // stale, superseded run's late-arriving result from its own.
  const attemptRef = useRef(0);

  const runAcceptFlow = useCallback(async () => {
    const attemptId = ++attemptRef.current;
    const stale = () => attemptRef.current !== attemptId;

    if (!token) {
      setStatus('error');
      setAmbiguous(false);
      setError('This invite link is missing its token.');
      return;
    }

    const session = await me().catch(() => null);
    if (stale()) return;
    const signedIn = Boolean((session as { user?: { id?: string } } | null)?.user?.id);
    if (!signedIn) {
      setStatus('signed-out');
      return;
    }

    setStatus('accepting');
    let result = await acceptWorkspaceInvite(token);
    if (stale()) return;

    if (!result.ok && result.ambiguous) {
      // The accept request itself never produced a response. Before ever
      // telling the person it failed, check whether it actually went
      // through: decode (unverified — a UI hint only, the server re-checks
      // the signature on every real call) the workspace this token targets
      // out of the token itself, and see if that workspace now shows up in
      // the caller's own real membership list.
      const targetWorkspaceId = unverifiedWorkspaceIdFromInviteToken(token);
      const bootstrap = targetWorkspaceId ? await loadAccountShellBootstrap().catch(() => null) : null;
      if (stale()) return;
      const alreadyMember = Boolean(
        bootstrap?.workspaceMemberships?.some((m) => m.workspace.id === targetWorkspaceId),
      );
      if (alreadyMember && targetWorkspaceId) {
        const membership = bootstrap!.workspaceMemberships.find((m) => m.workspace.id === targetWorkspaceId)!;
        result = { ok: true, workspace_id: targetWorkspaceId, role: membership.role };
      }
    }

    if (!result.ok) {
      setError(result.error);
      setAmbiguous(result.ambiguous);
      setStatus('error');
      return;
    }

    setWorkspaceId(result.workspace_id);
    setRole(result.role);
    setStatus('accepted');

    // The account shell's workspaceMemberships list (root layout, loaded
    // once per full page load) predates this accept call, so it doesn't
    // know the new membership exists yet — refresh it from the same
    // bootstrap endpoint the rest of the shell hydrates from before
    // navigating, so the workspace switcher and any workspace-scoped route
    // guard both see the membership immediately, on the first render,
    // rather than only after a later full reload picks it up.
    //
    // Then land the user directly in the workspace they just joined,
    // rather than leaving them on this confirmation screen or wherever
    // they happened to be before (MAN-108 Phase 1 bug 4) — this becomes
    // the current workspace for the rest of the session the same way any
    // other workspace navigation does (PrimaryRail's route-sync effect
    // stamps it as the last-visited workspace on the very next render).
    const bootstrap = await loadAccountShellBootstrap().catch(() => null);
    if (stale()) return;
    if (bootstrap) {
      actions.replaceSession(bootstrap);
    }
    // Bare workspace route, not /agents (project-as-spine nav, 2026-08-13
    // — Agents is no longer a top-level, linked destination): this is
    // FleetHome, the genuine landing page, and it works at any agent
    // count, including the zero a person just-invited-in almost always
    // has.
    router.replace(`/w/${encodeURIComponent(result.workspace_id)}`);
    // `actions` is intentionally excluded: it's a new object identity on
    // every account-shell state change (see account-shell-context.tsx), and
    // this function itself calls actions.replaceSession, which would
    // otherwise need to be re-created on every call. `router` is stable
    // across renders (Next.js), included only for lint-completeness.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, router]);

  useEffect(() => {
    void runAcceptFlow();
    return () => { attemptRef.current += 1; };
  }, [runAcceptFlow]);

  const nextParam = `?next=${encodeURIComponent(`/join/${token}`)}`;

  if (status === 'checking-session' || status === 'accepting') {
    // `.invite-landing` re-centers its child, so a small spinner-only card
    // growing into any of the three real resolved cards (signed-out /
    // accepted / invalid — each a heading + subtitle + one-or-two actions)
    // visibly resized and re-centered. Matches the signed-out shape (no
    // icon) as the more neutral of the two non-error outcomes.
    return (
      <div className="invite-landing">
        <div className="invite-landing__card" aria-busy="true" aria-label={status === 'accepting' ? 'Joining the workspace' : 'Checking your invite'}>
          <Loader2 className="invite-landing__spinner" size={32} aria-hidden="true" />
          <h1>{status === 'accepting' ? 'Joining the workspace…' : 'Checking your invite…'}</h1>
          <p className="invite-landing__subtitle">This only takes a moment.</p>
          <div className="invite-landing__actions">
            <AppButton tone="primary" disabled>Continue</AppButton>
          </div>
        </div>
      </div>
    );
  }

  if (status === 'signed-out') {
    return (
      <div className="invite-landing">
        <div className="invite-landing__card">
          <h1>You&apos;ve been invited to a workspace</h1>
          <p className="invite-landing__subtitle">
            Sign in or create an account with the email this invite was sent to, then come back to this link to
            finish joining.
          </p>
          <div className="invite-landing__actions">
            <AppButton tone="primary" onClick={() => router.push(`/login${nextParam}`)}>
              Log in
            </AppButton>
            <AppButton tone="ghost" onClick={() => router.push(`/signup${nextParam}`)}>
              Create an account
            </AppButton>
          </div>
        </div>
      </div>
    );
  }

  if (status === 'accepted') {
    return (
      <div className="invite-landing">
        <div className="invite-landing__card invite-landing__card--valid">
          <CheckCircle size={48} aria-hidden="true" />
          <h1>You&apos;re in</h1>
          <p className="invite-landing__subtitle">
            {role ? `You've joined this workspace as ${role}.` : "You've joined this workspace."}
          </p>
          <div className="invite-landing__actions">
            <AppButton
              tone="primary"
              onClick={() => router.push(workspaceId ? `/w/${encodeURIComponent(workspaceId)}` : '/')}
            >
              Continue to workspace
            </AppButton>
          </div>
        </div>
      </div>
    );
  }

  // `ambiguous` (a request that never got a response, already checked above
  // against the caller's real membership list and found not-yet-a-member)
  // is a genuinely different fact from a definitive server rejection — say
  // so, and let a retry resolve it rather than asserting failure.
  return (
    <div className="invite-landing">
      <div className="invite-landing__card invite-landing__card--invalid">
        <AlertCircle size={48} aria-hidden="true" />
        <h1>{ambiguous ? "We couldn't confirm this invite" : "This invite couldn't be accepted"}</h1>
        <p className="invite-landing__subtitle">
          {ambiguous
            ? (error
              ? `${error} We couldn't tell whether it went through — it's safe to try again.`
              : "The request didn't complete. It's safe to try again — if it already went through, retrying will pick that up.")
            : (error || 'This invite link is expired, already used, or does not exist.')}
        </p>
        <div className="invite-landing__actions">
          <AppButton tone="primary" onClick={() => void runAcceptFlow()}>
            Try again
          </AppButton>
          <AppButton tone="ghost" onClick={() => router.push('/')}>
            Go home
          </AppButton>
        </div>
      </div>
    </div>
  );
}
