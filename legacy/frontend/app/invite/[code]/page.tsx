'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

import { AppButton, AppEmptyState } from '@/lib/ui/primitives';

export default function InviteLandingPage() {
  const router = useRouter();
  const params = useParams();
  const code = typeof params?.code === 'string' ? params.code : '';

  const [status, setStatus] = useState<'loading' | 'valid' | 'invalid' | 'error'>('loading');
  const [invite, setInvite] = useState<{ code: string; email?: string; role?: string; plan_id?: string; uses_remaining?: number } | null>(null);

  useEffect(() => {
    if (!code) {
      setStatus('invalid');
      return;
    }

    fetch('/api/pilot/invites/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
      .then(async (res) => {
        if (res.ok) {
          const data = await res.json();
          if (data.ok && data.valid) {
            setInvite(data);
            setStatus('valid');
            return;
          }
        }
        setStatus('invalid');
      })
      .catch(() => setStatus('error'));
  }, [code]);

  if (status === 'loading') {
    return (
      <div className="invite-landing">
        <div className="invite-landing__card">
          <Loader2 className="invite-landing__spinner" size={32} aria-hidden="true" />
          <p>Checking your invite…</p>
        </div>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="invite-landing">
        <AppEmptyState
          title="Something went wrong"
          body="We could not verify your invite right now. Please try again later."
          actions={
            <AppButton tone="primary" onClick={() => window.location.reload()}>
              Retry
            </AppButton>
          }
        />
      </div>
    );
  }

  if (status === 'invalid') {
    return (
      <div className="invite-landing">
        <div className="invite-landing__card invite-landing__card--invalid">
          <AlertCircle size={48} aria-hidden="true" />
          <h1>Invite not valid</h1>
          <p>This invite code is expired, fully used, or does not exist.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="invite-landing">
      <div className="invite-landing__card invite-landing__card--valid">
        <CheckCircle size={48} aria-hidden="true" />
        <h1>You are invited to Empyralis</h1>
        <p className="invite-landing__subtitle">
          Welcome to the pilot program. You have been invited to create an account
          {invite?.email ? ` (${invite.email})` : ''}.
        </p>

        <div className="invite-landing__details">
          <div className="invite-landing__detail">
            <span className="invite-landing__detail-label">Plan</span>
            <span className="invite-landing__detail-value">{invite?.plan_id ?? 'Pilot'}</span>
          </div>
          <div className="invite-landing__detail">
            <span className="invite-landing__detail-label">Role</span>
            <span className="invite-landing__detail-value">{invite?.role ?? 'Owner'}</span>
          </div>
          {typeof invite?.uses_remaining === 'number' && (
            <div className="invite-landing__detail">
              <span className="invite-landing__detail-label">Uses remaining</span>
              <span className="invite-landing__detail-value">{invite.uses_remaining}</span>
            </div>
          )}
        </div>

        <div className="invite-landing__actions">
          <AppButton
            tone="primary"
            onClick={() => router.push(`/signup?pilot_code=${encodeURIComponent(code)}`)}
          >
            Create account
          </AppButton>
          <AppButton
            tone="ghost"
            onClick={() => router.push(`/login?pilot_code=${encodeURIComponent(code)}`)}
          >
            Already have an account? Log in
          </AppButton>
        </div>
      </div>
    </div>
  );
}
