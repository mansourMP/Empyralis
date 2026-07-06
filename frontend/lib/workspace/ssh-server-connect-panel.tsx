'use client';

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

import { AppButton } from '@/lib/ui/primitives';
import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

// Same fleet/legacy-shell trade-off as cloud-vps-setup-panel.tsx: talk to the
// backend directly rather than through useWorkspaceServices(), which fleet
// routes never mount.
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = String(init.method || 'GET');
  const headers = buildCookieAuthHeaders(method, { accept: 'application/json', ...(init.headers as Record<string, string> | undefined) });
  const response = await fetch(path, { ...init, headers, credentials: 'include' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(data?.detail || data?.error || `Request failed with status ${response.status}.`));
  }
  return data as T;
}

const FULL_ACCESS_WARNING_VERSION = '2026-06-06';

type AuthMode = 'password' | 'ssh_key';

export function SshServerConnectPanel({
  open,
  workspaceId,
  onClose,
  onConnected,
}: {
  open: boolean;
  workspaceId: string;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [host, setHost] = useState('');
  const [port, setPort] = useState('22');
  const [username, setUsername] = useState('root');
  const [authMode, setAuthMode] = useState<AuthMode>('password');
  const [password, setPassword] = useState('');
  const [sshKey, setSshKey] = useState('');
  const [remoteRoot, setRemoteRoot] = useState('');
  const [warningAck, setWarningAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!open) return;
    setHost('');
    setPort('22');
    setUsername('root');
    setAuthMode('password');
    setPassword('');
    setSshKey('');
    setRemoteRoot('');
    setWarningAck(false);
    setBusy(false);
    setError(null);
    setConnected(false);
  }, [open]);

  if (!open) return null;

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      await requestJson('/api/gateway/pairings/ssh', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId,
          host: host.trim(),
          port: Number(port) || 22,
          username: username.trim(),
          auth_mode: authMode,
          password: authMode === 'password' ? password : undefined,
          ssh_key: authMode === 'ssh_key' ? sshKey : undefined,
          remote_root: remoteRoot.trim() || undefined,
          runtime_access_mode: 'full_access',
          autonomous_agent_setup_warning_acknowledged: true,
          metadata: { autonomous_agent_setup_warning_version: FULL_ACCESS_WARNING_VERSION },
        }),
      });
      setConnected(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not connect to that server.');
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    host.trim().length > 0 &&
    username.trim().length > 0 &&
    (authMode === 'password' ? password.trim().length > 0 : sshKey.trim().length > 0);

  return (
    <div className="cloud-vps-flow-modal" role="dialog" aria-modal="true" aria-label="Connect your own server">
      <button
        className="cloud-vps-flow-modal__scrim"
        type="button"
        aria-label="Close"
        onClick={onClose}
        disabled={busy}
      />
      <section className="cloud-vps-flow-modal__panel">
        <header className="cloud-vps-flow-modal__header">
          <span className="cloud-vps-flow-modal__icon-spacer" aria-hidden="true" />
          <button className="cloud-vps-flow-modal__icon-button" type="button" onClick={onClose} disabled={busy} aria-label="Close">
            <X size={18} strokeWidth={2} />
          </button>
        </header>

        <section className="cloud-vps-flow-modal__content">
          {connected ? (
            <>
              <div className="cloud-vps-panel__heading">
                <h2>Connected</h2>
                <p>Empyralis installed and started the agent runtime on {host}. It'll appear on the Hardware page once it reports in.</p>
              </div>
              <div className="cloud-vps-panel__footer">
                <AppButton tone="primary" type="button" onClick={onConnected}>Done</AppButton>
              </div>
            </>
          ) : !warningAck ? (
            <>
              <div className="cloud-vps-panel__heading">
                <h2>Connect your own server</h2>
                <p>Empyralis will SSH in once to install and start the agent runtime. It runs with full access to that machine — only connect a server you trust your agents to run on.</p>
              </div>
              <div className="cloud-vps-panel__footer">
                <AppButton tone="primary" type="button" onClick={() => setWarningAck(true)}>
                  I understand, continue
                </AppButton>
              </div>
            </>
          ) : (
            <>
              <div className="cloud-vps-panel__heading">
                <h2>Server details</h2>
              </div>
              <label className="app-form-field">
                <span className="app-form-field__label">Host</span>
                <input className="app-field" value={host} onChange={(e) => setHost(e.currentTarget.value)} placeholder="203.0.113.42 or my-server.example.com" />
              </label>
              <label className="app-form-field">
                <span className="app-form-field__label">Port</span>
                <input className="app-field" value={port} onChange={(e) => setPort(e.currentTarget.value)} placeholder="22" inputMode="numeric" />
              </label>
              <label className="app-form-field">
                <span className="app-form-field__label">Username</span>
                <input className="app-field" value={username} onChange={(e) => setUsername(e.currentTarget.value)} placeholder="root" />
              </label>
              <div className="cloud-vps-access-form">
                <div className="fleet-wizard-options">
                  <button type="button" className={`fleet-wizard-option${authMode === 'password' ? ' is-selected' : ''}`} onClick={() => setAuthMode('password')}>
                    <span className="fleet-wizard-option-label">Password</span>
                  </button>
                  <button type="button" className={`fleet-wizard-option${authMode === 'ssh_key' ? ' is-selected' : ''}`} onClick={() => setAuthMode('ssh_key')}>
                    <span className="fleet-wizard-option-label">SSH key</span>
                  </button>
                </div>
              </div>
              {authMode === 'password' ? (
                <label className="app-form-field">
                  <span className="app-form-field__label">Password</span>
                  <input className="app-field" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
                </label>
              ) : (
                <label className="app-form-field">
                  <span className="app-form-field__label">Private key</span>
                  <textarea
                    className="app-field"
                    style={{ minHeight: 96, fontFamily: 'var(--app-font-mono, ui-monospace, monospace)', fontSize: 12 }}
                    autoComplete="off"
                    spellCheck={false}
                    value={sshKey}
                    onChange={(e) => setSshKey(e.currentTarget.value)}
                    placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                  />
                </label>
              )}
              <label className="app-form-field">
                <span className="app-form-field__label">Install path (optional)</span>
                <input className="app-field" value={remoteRoot} onChange={(e) => setRemoteRoot(e.currentTarget.value)} placeholder="~/Multi_Agent_Orchestrator_Project" />
              </label>
              <div className="cloud-vps-panel__footer">
                <AppButton tone="primary" type="button" onClick={() => void connect()} disabled={busy || !canSubmit}>
                  {busy ? 'Connecting…' : 'Connect'}
                </AppButton>
              </div>
              {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
            </>
          )}
        </section>
      </section>
    </div>
  );
}
