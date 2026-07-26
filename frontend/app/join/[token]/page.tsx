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

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

import { me } from '@/lib/auth/auth-client';
import { acceptWorkspaceInvite } from '@/lib/workspace/fleet/members-data';
import { AppButton } from '@/lib/ui/primitives';

type Status = 'checking-session' | 'accepting' | 'accepted' | 'signed-out' | 'error';

export default function JoinWorkspaceInvitePage() {
  const router = useRouter();
  const params = useParams();
  const token = typeof params?.token === 'string' ? params.token : '';

  const [status, setStatus] = useState<Status>('checking-session');
  const [error, setError] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setStatus('error');
      setError('This invite link is missing its token.');
      return;
    }
    let cancelled = false;
    void (async () => {
      const session = await me().catch(() => null);
      const signedIn = Boolean((session as { user?: { id?: string } } | null)?.user?.id);
      if (cancelled) return;
      if (!signedIn) {
        setStatus('signed-out');
        return;
      }
      setStatus('accepting');
      const result = await acceptWorkspaceInvite(token);
      if (cancelled) return;
      if (result.ok) {
        setWorkspaceId(result.workspace_id);
        setRole(result.role);
        setStatus('accepted');
      } else {
        setError(result.error);
        setStatus('error');
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  const nextParam = `?next=${encodeURIComponent(`/join/${token}`)}`;

  if (status === 'checking-session' || status === 'accepting') {
    return (
      <div className="invite-landing">
        <div className="invite-landing__card">
          <Loader2 className="invite-landing__spinner" size={32} aria-hidden="true" />
          <p>{status === 'accepting' ? 'Joining the workspace…' : 'Checking your invite…'}</p>
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
              onClick={() => router.push(workspaceId ? `/w/${encodeURIComponent(workspaceId)}/agents` : '/')}
            >
              Continue to workspace
            </AppButton>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="invite-landing">
      <div className="invite-landing__card invite-landing__card--invalid">
        <AlertCircle size={48} aria-hidden="true" />
        <h1>This invite couldn&apos;t be accepted</h1>
        <p className="invite-landing__subtitle">
          {error || 'This invite link is expired, already used, or does not exist.'}
        </p>
        <div className="invite-landing__actions">
          <AppButton tone="ghost" onClick={() => router.push('/')}>
            Go home
          </AppButton>
        </div>
      </div>
    </div>
  );
}
