'use client';

// CLOUD-SERVER PROVISIONING — the one place that knows "a server is being
// built right now", independent of whether the setup modal happens to be open.
//
// Why this exists: provisioning is a SERVER-side background task. The POST to
// /api/hardware/vps/provision writes a placeholder record, schedules
// run_vps_provisioning_lifecycle via asyncio.create_task, and returns
// immediately (see vps_provisioning_service.run_vps_provisioning_lifecycle's
// docstring). The browser's only job afterwards is to GET
// /hardware/vps/{vps_id}/status. Nothing the client does — closing a modal,
// navigating, reloading — can cancel it; the only thing that tears a server
// down is an explicit DELETE. So the progress modal was never more than a
// viewer, and trapping the user inside it for 2-10 minutes bought nothing.
//
// This module holds that viewer's state OUTSIDE the modal: one in-flight
// provision per workspace, polled here, mirrored to localStorage so a reload
// (or a hard navigation) resumes watching instead of losing the thread. It is
// deliberately NOT a general status/toast system — it knows about exactly one
// kind of work, and the two surfaces that read it (HardwareSection's pending
// row and CloudVpsSetupPanel's step list) render it with existing components.

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

/** The four steps the setup modal has always shown, now also the vocabulary
 *  the compact pending row speaks. Derived from the backend record's own
 *  status + install_phase — never guessed from elapsed time. */
export type VpsProvisionStage = 'creating' | 'installing' | 'connecting' | 'connected' | 'failed';

export type VpsProvisionWatch = {
  vpsId: string;
  workspaceId: string;
  /** Provider id ('digitalocean' | 'google' | 'aws') and its display label,
   *  captured at create time so the pending row can name the server without
   *  re-deriving anything from a record that may not exist yet. */
  provider: string;
  providerLabel: string;
  planLabel: string;
  regionLabel: string;
  startedAt: number;
  stage: VpsProvisionStage;
  /** Last install_phase the box itself beaconed (MAN-121) — 'preflight',
   *  'gateway_download', 'registration_wait', … See install-agent-computer.sh. */
  installPhase: string;
  providerResourceId: string;
  /** Human-readable failure reason, only set when stage === 'failed'. */
  error: string;
  /** True once we stopped polling without a terminal answer. NOT a failure —
   *  the record simply outlived our watch window. */
  pollStopped: boolean;
  /** The completion notification has been seen. A failed watch stays around
   *  after this (the user still has a half-built server to deal with); a
   *  connected one is dropped, because the new row in the list IS the result. */
  acknowledged: boolean;
  finishedAt: number | null;
};

type VpsProvisionStatusPayload = {
  status?: string;
  provider_resource_id?: string;
  error?: string;
  install_error?: string;
  install_phase?: string;
};

// 5s between polls — the same cadence the modal used, and comfortably inside
// what a multi-minute install needs.
const POLL_INTERVAL_MS = 5_000;
// The backend's own lifecycle gives up and writes a real 'failed' at ~20 min
// (run_vps_provisioning_lifecycle's timeout branch). Watch past that so the
// reason it writes is actually seen, then stop rather than poll forever.
const POLL_CEILING_MS = 30 * 60_000;
/** Past this, the copy stops promising and starts saying "longer than usual".
 *  Real observed durations are 2-10 minutes, so this is the top of normal,
 *  not the middle of it. */
export const VPS_PROVISION_SLOW_AFTER_MS = 10 * 60_000;
/** A finished-and-seen success older than this is stale on the next page
 *  load — don't re-announce a server that connected an hour ago. */
const SUCCESS_STALE_AFTER_MS = 5 * 60_000;

function storageKey(workspaceId: string): string {
  return `empyralis:vps-provision-watch:${workspaceId}`;
}

// ── store ──────────────────────────────────────────────────────────────────
// One record per workspace. Snapshots are cached by reference so
// useSyncExternalStore's getSnapshot is stable between real changes.

type Listener = () => void;

const snapshots = new Map<string, VpsProvisionWatch | null>();
const listeners = new Map<string, Set<Listener>>();
const hydrated = new Set<string>();
/** vps ids with a poll loop currently running, so re-subscribing (a second
 *  mount, a route change) never starts a second loop for the same server. */
const activePolls = new Set<string>();

function readStored(workspaceId: string): VpsProvisionWatch | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(storageKey(workspaceId));
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<VpsProvisionWatch> | null;
    const vpsId = String(parsed?.vpsId || '').trim();
    if (!parsed || !vpsId) {
      return null;
    }
    const watch: VpsProvisionWatch = {
      vpsId,
      workspaceId,
      provider: String(parsed.provider || ''),
      providerLabel: String(parsed.providerLabel || 'Cloud'),
      planLabel: String(parsed.planLabel || ''),
      regionLabel: String(parsed.regionLabel || ''),
      startedAt: Number(parsed.startedAt) || Date.now(),
      stage: (parsed.stage as VpsProvisionStage) || 'creating',
      installPhase: String(parsed.installPhase || ''),
      providerResourceId: String(parsed.providerResourceId || ''),
      error: String(parsed.error || ''),
      pollStopped: Boolean(parsed.pollStopped),
      acknowledged: Boolean(parsed.acknowledged),
      finishedAt: typeof parsed.finishedAt === 'number' ? parsed.finishedAt : null,
    };
    // A success that already ran its course shouldn't reappear later — the
    // connected box is in the hardware list by then, which is the real signal.
    if (watch.stage === 'connected' && watch.finishedAt && Date.now() - watch.finishedAt > SUCCESS_STALE_AFTER_MS) {
      window.localStorage.removeItem(storageKey(workspaceId));
      return null;
    }
    return watch;
  } catch {
    return null;
  }
}

function persist(workspaceId: string, watch: VpsProvisionWatch | null) {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    if (!watch) {
      window.localStorage.removeItem(storageKey(workspaceId));
      return;
    }
    window.localStorage.setItem(storageKey(workspaceId), JSON.stringify(watch));
  } catch {
    // Private mode / quota — the in-memory record still works for this tab.
  }
}

function ensureHydrated(workspaceId: string) {
  if (hydrated.has(workspaceId)) {
    return;
  }
  hydrated.add(workspaceId);
  snapshots.set(workspaceId, readStored(workspaceId));
}

function emit(workspaceId: string) {
  const set = listeners.get(workspaceId);
  if (!set) {
    return;
  }
  for (const listener of Array.from(set)) {
    listener();
  }
}

function setWatch(workspaceId: string, next: VpsProvisionWatch | null) {
  snapshots.set(workspaceId, next);
  persist(workspaceId, next);
  emit(workspaceId);
}

function patchWatch(workspaceId: string, vpsId: string, patch: Partial<VpsProvisionWatch>) {
  const current = snapshots.get(workspaceId) ?? null;
  // A newer provision replaced this one while its loop was still running —
  // let the old loop's late answer fall on the floor rather than overwrite.
  if (!current || current.vpsId !== vpsId) {
    return;
  }
  setWatch(workspaceId, { ...current, ...patch });
}

// ── status → stage ─────────────────────────────────────────────────────────

function stageFromStatus(status: string, providerResourceId: string, installPhase: string): VpsProvisionStage {
  if (status === 'connected') return 'connected';
  if (status === 'failed') return 'failed';
  if (status === 'registering') return 'connecting';
  if (installPhase === 'registration_wait') return 'connecting';
  // 'creating' is only honest until the provider actually hands back a
  // resource id (or the box starts beaconing its own install phases) — the
  // old code jumped straight to "Installing…" the moment the POST returned,
  // which was a lie for the first ~30-60s of every DigitalOcean build.
  if (providerResourceId || installPhase) return 'installing';
  return 'creating';
}

/** install-agent-computer.sh's INSTALL_PHASE values, in plain language. Used
 *  as the live sub-line under the step list and inside failure text, so a
 *  stuck or failed build says WHERE it is rather than just "installing". */
const INSTALL_PHASE_LABELS: Record<string, string> = {
  startup: 'Starting the installer',
  preflight: 'Checking the server',
  system_dependencies: 'Installing system packages',
  node_install: 'Installing Node.js',
  prepare_host: 'Preparing the host',
  docker_install: 'Installing Docker',
  gateway_download: 'Downloading Agent Computer',
  // The channel transport. Named for what it does FOR THE CUSTOMER, never for
  // the software it installs — they press one button, authorise their cloud
  // provider, and come back to a server that can carry messages. OpenClaw is
  // an implementation detail and must not appear on a screen anyone reads.
  channel_transport: 'Setting up messaging channels',
  service_setup: 'Setting up the service',
  service_start: 'Starting the service',
  registration_wait: 'Waiting for it to connect',
};

export function vpsInstallPhaseLabel(phase: string): string {
  const key = String(phase || '').trim().toLowerCase();
  if (!key) {
    return '';
  }
  return INSTALL_PHASE_LABELS[key] || key.replace(/_/g, ' ');
}

/** Same label, folded into the middle of a sentence ("failed while …").
 *  Only the first character drops case, so "Downloading Agent Computer" and
 *  "Installing Node.js" keep their proper nouns intact. */
function phraseCase(label: string): string {
  return label ? label.charAt(0).toLowerCase() + label.slice(1) : '';
}

/** The failure text shown in the modal, the pending row and the notification.
 *  Order of preference is most-specific-first:
 *    1. what the box itself beaconed (install_error + install_phase, MAN-121)
 *    2. the reason the backend recorded on the record (error)
 *    3. an honest statement of how far it got, when there is no reason at all
 *  Never "something went wrong". */
export function describeVpsProvisionFailure(payload: {
  error?: string;
  installError?: string;
  installPhase?: string;
  providerResourceId?: string;
}): string {
  const installError = String(payload.installError || '').trim();
  const backendError = String(payload.error || '').trim();
  const phaseLabel = vpsInstallPhaseLabel(String(payload.installPhase || ''));
  const hasResource = Boolean(String(payload.providerResourceId || '').trim());
  const detail = installError || backendError;

  // DigitalOcean's most common self-inflicted failure: a token scoped without
  // tag permissions. The raw API text is unreadable; the fix is specific.
  if (/tag:create|tag:read|tag:delete/i.test(detail)) {
    return 'The connected DigitalOcean account is missing a permission (tagging). Disconnect and reconnect DigitalOcean, then try again.';
  }
  if (installError) {
    return phaseLabel
      ? `The server was created, but setup failed while ${phraseCase(phaseLabel)}: ${installError}`
      : `The server was created, but setup failed: ${installError}`;
  }
  if (backendError) {
    return hasResource
      ? `The server was created but could not connect: ${backendError}`
      : `Setup failed before the server was created: ${backendError}`;
  }
  return hasResource
    ? 'The server was created but never connected.'
    : 'Setup failed before the server was created.';
}

/** One-line summary of what is happening right now, for the compact row. */
export function vpsProvisionStageLabel(watch: VpsProvisionWatch): string {
  if (watch.stage === 'connected') return 'Connected';
  if (watch.stage === 'failed') return 'Setup failed';
  const phase = vpsInstallPhaseLabel(watch.installPhase);
  if (watch.stage === 'creating') return 'Creating the server';
  if (watch.stage === 'connecting') return phase || 'Connecting';
  return phase || 'Installing Agent Computer';
}

// ── polling ────────────────────────────────────────────────────────────────

async function fetchStatus(vpsId: string): Promise<VpsProvisionStatusPayload | 'gone' | null> {
  try {
    const response = await fetch(`/api/hardware/vps/${encodeURIComponent(vpsId)}/status`, {
      headers: buildCookieAuthHeaders('GET', { accept: 'application/json' }),
      credentials: 'include',
    });
    // 404 means the record is genuinely gone (deleted elsewhere, or never
    // existed) — that's the one status worth forgetting the watch over. Any
    // other error is transient by assumption and simply retried.
    if (response.status === 404) {
      return 'gone';
    }
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as VpsProvisionStatusPayload;
  } catch {
    return null;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function runPollLoop(workspaceId: string, vpsId: string) {
  if (activePolls.has(vpsId)) {
    return;
  }
  activePolls.add(vpsId);
  try {
    const deadline = Date.now() + POLL_CEILING_MS;
    // First poll immediately: the placeholder record exists from the instant
    // the POST returns, so there is real state to read right away.
    for (let first = true; Date.now() < deadline; first = false) {
      if (!first) {
        await sleep(POLL_INTERVAL_MS);
      }
      const current = snapshots.get(workspaceId) ?? null;
      if (!current || current.vpsId !== vpsId) {
        return; // replaced or cleared underneath us
      }
      const payload = await fetchStatus(vpsId);
      if (payload === 'gone') {
        const stillOurs = snapshots.get(workspaceId);
        if (stillOurs && stillOurs.vpsId === vpsId) {
          setWatch(workspaceId, null);
        }
        return;
      }
      if (!payload) {
        continue; // transient; try again next tick
      }
      const status = String(payload.status || '').trim().toLowerCase();
      const providerResourceId = String(payload.provider_resource_id || '').trim();
      const installPhase = String(payload.install_phase || '').trim();
      if (status === 'deleted') {
        setWatch(workspaceId, null);
        return;
      }
      const stage = stageFromStatus(status, providerResourceId, installPhase);
      if (stage === 'failed') {
        patchWatch(workspaceId, vpsId, {
          stage: 'failed',
          providerResourceId,
          installPhase,
          error: describeVpsProvisionFailure({
            error: payload.error,
            installError: payload.install_error,
            installPhase,
            providerResourceId,
          }),
          finishedAt: Date.now(),
        });
        return;
      }
      if (stage === 'connected') {
        patchWatch(workspaceId, vpsId, {
          stage: 'connected',
          providerResourceId,
          installPhase,
          error: '',
          finishedAt: Date.now(),
        });
        return;
      }
      patchWatch(workspaceId, vpsId, { stage, providerResourceId, installPhase });
    }
    // Out of watch window with no terminal answer. Say exactly that — do not
    // paint a red failure over a build the backend may still be finishing.
    patchWatch(workspaceId, vpsId, { pollStopped: true });
  } finally {
    activePolls.delete(vpsId);
  }
}

function resumeIfUnfinished(workspaceId: string) {
  const watch = snapshots.get(workspaceId);
  if (!watch || watch.stage === 'connected' || watch.stage === 'failed' || watch.pollStopped) {
    return;
  }
  void runPollLoop(workspaceId, watch.vpsId);
}

// ── public API ─────────────────────────────────────────────────────────────

export function startVpsProvisionWatch(init: {
  vpsId: string;
  workspaceId: string;
  provider: string;
  providerLabel: string;
  planLabel: string;
  regionLabel: string;
  providerResourceId?: string;
}): void {
  ensureHydrated(init.workspaceId);
  setWatch(init.workspaceId, {
    vpsId: init.vpsId,
    workspaceId: init.workspaceId,
    provider: init.provider,
    providerLabel: init.providerLabel,
    planLabel: init.planLabel,
    regionLabel: init.regionLabel,
    startedAt: Date.now(),
    stage: 'creating',
    installPhase: '',
    providerResourceId: String(init.providerResourceId || '').trim(),
    error: '',
    pollStopped: false,
    acknowledged: false,
    finishedAt: null,
  });
  void runPollLoop(init.workspaceId, init.vpsId);
}

/** The completion notification was seen. A success is then forgotten (the new
 *  row in the hardware list is the lasting result); a failure is kept, still
 *  visible as a row, because there is usually a half-built server to delete. */
export function acknowledgeVpsProvisionWatch(workspaceId: string): void {
  const current = snapshots.get(workspaceId) ?? null;
  if (!current) {
    return;
  }
  if (current.stage === 'connected') {
    setWatch(workspaceId, null);
    return;
  }
  setWatch(workspaceId, { ...current, acknowledged: true });
}

/** Forget the watch entirely — used after the failed server is deleted, and
 *  by the failed row's own dismiss. Never cancels anything server-side. */
export function clearVpsProvisionWatch(workspaceId: string): void {
  setWatch(workspaceId, null);
}

export function getVpsProvisionWatch(workspaceId: string): VpsProvisionWatch | null {
  ensureHydrated(workspaceId);
  return snapshots.get(workspaceId) ?? null;
}

export function useVpsProvisionWatch(workspaceId: string): VpsProvisionWatch | null {
  const subscribe = (listener: Listener) => {
    ensureHydrated(workspaceId);
    let set = listeners.get(workspaceId);
    if (!set) {
      set = new Set();
      listeners.set(workspaceId, set);
    }
    set.add(listener);
    // A reload lands here with a half-finished record in localStorage and no
    // loop running — pick it back up.
    resumeIfUnfinished(workspaceId);
    return () => {
      set?.delete(listener);
    };
  };
  const getSnapshot = () => {
    ensureHydrated(workspaceId);
    return snapshots.get(workspaceId) ?? null;
  };
  // Nothing to show during SSR — this state only exists in the browser.
  return useSyncExternalStore(subscribe, getSnapshot, () => null);
}

/** Seconds since `startedAt`, ticking once a second while `running`. Lives
 *  here so the modal's elapsed line and the pending row's share one
 *  implementation instead of each running their own interval logic. */
export function useElapsedSeconds(startedAt: number | null, running: boolean): number {
  const [elapsed, setElapsed] = useState(() =>
    startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0,
  );
  const startedRef = useRef(startedAt);
  startedRef.current = startedAt;

  useEffect(() => {
    const tick = () => {
      const started = startedRef.current;
      setElapsed(started ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0);
    };
    tick();
    if (!running || startedAt == null) {
      return;
    }
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [running, startedAt]);

  return elapsed;
}

/** m:ss, tabular — the same shape the progress screen already printed. */
export function formatElapsed(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}
