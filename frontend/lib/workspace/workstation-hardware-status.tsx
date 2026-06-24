'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Cloud, Monitor, Server, Settings } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { joinClassNames } from '@/lib/ui/primitives';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import type { WorkspaceBootstrapRuntimeTarget } from '@/lib/workspace/workspace-bootstrap';

type HardwareNativeRuntime = {
  os: string;
  arch: string;
  release: string;
  hostname: string;
  desktopSession: string;
  systemServiceMode: boolean;
};

type HardwareAttachment = {
  attachmentId: string;
  attachmentKind: string;
  label: string;
  online: boolean;
  healthy: boolean;
  status: string;
  statusReason: string | null;
  heartbeatAt: string | null;
  capabilities: string[];
  capabilityReadiness: Record<string, unknown>;
  nativeRuntime: HardwareNativeRuntime | null;
};

type HardwareSummary = {
  label: string;
  popoverTitle: string;
  status: string;
  statusLabel: string;
  tone: 'ready' | 'warning' | 'danger' | 'muted';
  icon: LucideIcon;
  detail: string;
};

type LocalTrayStatus = {
  ok?: boolean;
  connected?: boolean;
  desired_connected?: boolean;
  device_name?: string;
  session_verified?: boolean;
  heartbeat_fresh?: boolean;
  session_status?: string;
  state?: string;
  supervisor_running?: boolean;
  gateway_running?: boolean;
  workspace_id?: string;
  error?: string | null;
};

const LOCAL_TRAY_STATUS_URL = 'http://127.0.0.1:7790/status';

function shouldPollLocalAgentComputer(): boolean {
  if (typeof window === 'undefined') return false;
  const host = window.location.hostname;
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
}

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readOptionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function readRecord(value: unknown): Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => readString(item)).filter(Boolean)
    : [];
}

function readField(record: Record<string, unknown>, camelKey: string, snakeKey: string): unknown {
  return record[camelKey] ?? record[snakeKey];
}

function humanizeToken(value: string, fallback = ''): string {
  const text = readString(value, fallback);
  if (!text) {
    return fallback;
  }
  return text
    .split(/[_\s.-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function normalizeNativeRuntime(value: unknown): HardwareNativeRuntime | null {
  const record = readRecord(value);
  const os = readString(record.os);
  const arch = readString(record.arch);
  const desktopSession = readString(record.desktop_session);
  const systemServiceMode = readBoolean(record.system_service_mode);
  if (!os && !arch && !desktopSession && !systemServiceMode) {
    return null;
  }
  return {
    os,
    arch,
    release: readString(record.release),
    hostname: readString(record.hostname),
    desktopSession,
    systemServiceMode,
  };
}

function normalizeHardwareAttachments(payload: unknown): HardwareAttachment[] {
  const record = readRecord(payload);
  const attachments = Array.isArray(record.attachments) ? record.attachments : [];
  const agentComputerKinds = new Set(['local_companion', 'cloud_computer', 'self_hosted_business_node']);

  return attachments.flatMap((entry) => {
    const attachment = readRecord(entry);
    const attachmentKind = readString(readField(attachment, 'attachmentKind', 'attachment_kind'));
    if (!agentComputerKinds.has(attachmentKind)) {
      return [];
    }
    const attachmentId = readString(readField(attachment, 'attachmentId', 'attachment_id'), attachmentKind);
    return [{
      attachmentId,
      attachmentKind,
      label: readString(attachment.label, humanizeToken(attachmentKind)),
      online: readBoolean(attachment.online),
      healthy: readBoolean(attachment.healthy),
      status: readString(attachment.status, 'offline').toLowerCase(),
      statusReason: readOptionalString(readField(attachment, 'statusReason', 'status_reason')),
      heartbeatAt: readOptionalString(readField(attachment, 'heartbeatAt', 'heartbeat_at')),
      capabilities: readStringList(attachment.capabilities),
      capabilityReadiness: readRecord(readField(attachment, 'capabilityReadiness', 'capability_readiness')),
      nativeRuntime: normalizeNativeRuntime(readField(attachment, 'nativeRuntime', 'native_runtime')),
    }];
  });
}

function toneForStatus(status: string, online = false, healthy = false): HardwareSummary['tone'] {
  const normalized = readString(status).toLowerCase();
  if ((online && healthy) || ['ready', 'online', 'healthy', 'ok', 'available'].includes(normalized)) {
    return 'ready';
  }
  if (['busy', 'connecting', 'degraded', 'stale', 'warning', 'warn', 'reconnecting', 'registered', 'promptable'].includes(normalized)) {
    return 'warning';
  }
  if (['blocked', 'revoked', 'error', 'failed', 'fail', 'unhealthy', 'denied', 'restricted', 'offline', 'missing', 'unavailable'].includes(normalized)) {
    return 'danger';
  }
  return 'muted';
}

function offlineAttachmentStatus(rawStatus: string): string {
  const normalized = readString(rawStatus, 'offline').toLowerCase();
  if (['blocked', 'revoked', 'error', 'failed', 'fail', 'unhealthy', 'denied', 'restricted'].includes(normalized)) {
    return normalized;
  }
  if (['connecting', 'reconnecting', 'stale', 'registered', 'promptable'].includes(normalized)) {
    return normalized;
  }
  return 'offline';
}

function inferredOsForAttachment(attachment: HardwareAttachment): string {
  const nativeOs = attachment.nativeRuntime?.os.toLowerCase() ?? '';
  if (nativeOs) {
    return nativeOs;
  }
  const label = attachment.label.toLowerCase();
  if (/\b(darwin|mac|macbook|imac)\b/.test(label) || label.includes('mac mini')) {
    return 'darwin';
  }
  if (/\b(win32|windows|windows pc)\b/.test(label)) {
    return 'windows';
  }
  if (/\b(linux|ubuntu|debian|fedora)\b/.test(label)) {
    return 'linux';
  }
  return '';
}

function friendlyOsName(os: string): string {
  const normalized = os.toLowerCase();
  if (normalized === 'darwin') {
    return 'Mac';
  }
  if (normalized === 'win32' || normalized.includes('windows')) {
    return 'Windows';
  }
  if (normalized.includes('linux')) {
    return 'Linux';
  }
  return humanizeToken(os);
}

function hardwareLabelForAttachment(attachment: HardwareAttachment): string {
  if (attachment.attachmentKind === 'cloud_computer') {
    return 'Cloud Computer';
  }
  if (attachment.attachmentKind === 'self_hosted_business_node') {
    return 'Server/VPS';
  }

  const os = inferredOsForAttachment(attachment);
  if (os === 'darwin') {
    return 'Agent Computer';
  }
  if (os === 'win32' || os.includes('windows')) {
    return 'Agent Computer';
  }
  if (os.includes('linux')) {
    return 'Agent Computer';
  }
  return 'Agent Computer';
}

function iconForAttachment(attachment: HardwareAttachment | null, target: WorkspaceBootstrapRuntimeTarget | null): LucideIcon {
  const kind = attachment?.attachmentKind ?? target?.id ?? target?.canonicalId ?? '';
  if (kind === 'cloud_computer' || kind === 'sage_cloud_computer' || kind === 'empyralis_cloud_computer') {
    return Cloud;
  }
  if (kind === 'self_hosted_business_node' || kind === 'self_host_runtime' || kind === 'self_hosted_node') {
    return Server;
  }
  return Monitor;
}

function selectHardwareAttachment(attachments: HardwareAttachment[]): HardwareAttachment | null {
  const byKind = (kind: string) => attachments.filter((item) => item.attachmentKind === kind);
  return byKind('local_companion').find((item) => item.online)
    ?? byKind('local_companion')[0]
    ?? byKind('self_hosted_business_node').find((item) => item.online)
    ?? byKind('self_hosted_business_node')[0]
    ?? byKind('cloud_computer').find((item) => item.online)
    ?? byKind('cloud_computer')[0]
    ?? null;
}

function selectHardwareTarget(targets: WorkspaceBootstrapRuntimeTarget[]): WorkspaceBootstrapRuntimeTarget | null {
  const hardwareTargets = targets.filter((target) => target.id !== 'cloud_default');
  return hardwareTargets.find((target) => target.preferred)
    ?? hardwareTargets.find((target) => target.online)
    ?? hardwareTargets[0]
    ?? targets.find((target) => target.preferred)
    ?? targets[0]
    ?? null;
}

function trayStatusToAttachment(status: LocalTrayStatus | null): HardwareAttachment | null {
  if (!status || status.ok === false) {
    return null;
  }
  const statusToken = status.error
    ? 'error'
    : status.session_verified && status.gateway_running && status.heartbeat_fresh
      ? 'online'
      : status.session_verified && status.gateway_running
        ? 'degraded'
        : status.desired_connected || status.gateway_running
          ? 'connecting'
          : 'offline';
  return {
    attachmentId: 'local-tray',
    attachmentKind: 'local_companion',
    label: readString(status.device_name, 'Agent Computer'),
    online: statusToken === 'online',
    healthy: statusToken === 'online',
    status: statusToken,
    statusReason: status.error || (statusToken === 'connecting' ? 'Gateway is not verified yet.' : null),
    heartbeatAt: null,
    capabilities: [],
    capabilityReadiness: {},
    nativeRuntime: null,
  };
}

function buildSummary(
  attachment: HardwareAttachment | null,
  target: WorkspaceBootstrapRuntimeTarget | null,
  error: string | null,
): HardwareSummary {
  if (attachment) {
    const status = attachment.online
      ? attachment.healthy
        ? 'online'
        : 'degraded'
      : offlineAttachmentStatus(attachment.status);
    const runtimeBits = attachment.nativeRuntime
      ? [friendlyOsName(attachment.nativeRuntime.os), attachment.nativeRuntime.arch].filter(Boolean).join(' ')
      : friendlyOsName(inferredOsForAttachment(attachment)) || readString(target?.kind, 'Agent Computer');
    return {
      label: hardwareLabelForAttachment(attachment),
      popoverTitle: attachment.label || hardwareLabelForAttachment(attachment),
      status,
      statusLabel: humanizeToken(status),
      tone: toneForStatus(status, attachment.online, attachment.healthy),
      icon: iconForAttachment(attachment, target),
      detail: attachment.statusReason || runtimeBits,
    };
  }

  if (target) {
    const status = target.online
      ? target.healthy
        ? 'online'
        : 'degraded'
      : target.status;
    return {
      label: target.id === 'cloud_default' ? 'Cloud' : target.publicLabel || target.label,
      popoverTitle: target.publicLabel || target.label,
      status,
      statusLabel: target.statusLabel || humanizeToken(status, target.available ? 'Available' : 'Offline'),
      tone: toneForStatus(status, target.online, target.healthy),
      icon: iconForAttachment(null, target),
      detail: target.statusReason || target.description || target.kind,
    };
  }

  return {
    label: 'Computer',
    popoverTitle: 'Agent Computer',
    status: error ? 'unavailable' : 'unknown',
    statusLabel: error ? 'Unavailable' : 'Unknown',
    tone: error ? 'warning' : 'muted',
    icon: Monitor,
    detail: error || 'No runtime target reported.',
  };
}

function topbarStateLabel(summary: HardwareSummary): string {
  if (summary.status === 'online') {
    return 'In use';
  }
  if (summary.status === 'degraded') {
    return 'Needs attention';
  }
  return summary.statusLabel;
}

function lowerHardwareLabel(label: string): string {
  if (label === 'Agent Computer') {
    return 'Agent Computer';
  }
  if (label === 'This Mac') {
    return 'this Mac';
  }
  if (label === 'This Device') {
    return 'this device';
  }
  return label;
}

function hardwareUsageMessage(summary: HardwareSummary): string {
  if (summary.status === 'online') {
    return `Empyralis is using ${lowerHardwareLabel(summary.label)} through Agent Computer connection and local execution.`;
  }
  if (summary.status === 'degraded') {
    return `${summary.label} is connected, but its connection or local execution needs attention.`;
  }
  if (summary.status === 'offline') {
    return `${summary.label} is connected to this workspace, but its connection and local execution are not available right now.`;
  }
  return summary.detail || 'Computer status is managed by Agent Computer connection and local execution.';
}

export function WorkstationHardwareStatus({
  runtimeTargets,
  hardwareHref,
}: {
  runtimeTargets: WorkspaceBootstrapRuntimeTarget[];
  hardwareHref: string;
}) {
  const services = useWorkspaceServices();
  const [attachments, setAttachments] = useState<HardwareAttachment[]>([]);
  const [localTrayStatus, setLocalTrayStatus] = useState<LocalTrayStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loadAttachments = async () => {
      try {
        const payload = await services.client.listRuntimeAttachments();
        if (!cancelled) {
          setAttachments(normalizeHardwareAttachments(payload));
          setError(null);
        }
      } catch {
        if (!cancelled) {
          setError('Agent Computer details are unavailable.');
        }
      }
    };

    void loadAttachments();
    const interval = window.setInterval(() => {
      void loadAttachments();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [services.client]);

  useEffect(() => {
    let cancelled = false;
    const loadTrayStatus = async () => {
      if (!shouldPollLocalAgentComputer()) {
        setLocalTrayStatus(null);
        return;
      }
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 800);
      try {
        const response = await fetch(LOCAL_TRAY_STATUS_URL, {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error('Local tray status unavailable.');
        }
        const payload = await response.json() as LocalTrayStatus;
        if (!cancelled) {
          setLocalTrayStatus(payload);
        }
      } catch {
        if (!cancelled) {
          setLocalTrayStatus(null);
        }
      } finally {
        window.clearTimeout(timeout);
      }
    };

    void loadTrayStatus();
    const interval = window.setInterval(() => {
      void loadTrayStatus();
    }, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && event.target instanceof Node && !rootRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const selectedAttachment = useMemo(() => {
    const backendAttachment = selectHardwareAttachment(attachments);
    const localAttachment = trayStatusToAttachment(localTrayStatus);
    if (localAttachment && (!backendAttachment || backendAttachment.attachmentKind === 'local_companion')) {
      return localAttachment;
    }
    return backendAttachment;
  }, [attachments, localTrayStatus]);
  const selectedTarget = useMemo(() => selectHardwareTarget(runtimeTargets), [runtimeTargets]);
  const summary = useMemo(
    () => buildSummary(selectedAttachment, selectedTarget, error),
    [error, selectedAttachment, selectedTarget],
  );
  const StatusIcon = summary.icon;
  const stateLabel = topbarStateLabel(summary);

  return (
    <div
      ref={rootRef}
      className={joinClassNames('workstation-hardware-status', open && 'workstation-hardware-status--open')}
    >
      <button
        type="button"
        className={joinClassNames(
          'workstation-hardware-status__trigger',
          `workstation-hardware-status__trigger--${summary.tone}`,
        )}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`${summary.label} · ${stateLabel}`}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="workstation-hardware-status__dot" aria-hidden="true" />
        <StatusIcon size={14} aria-hidden="true" />
      </button>

      {open ? (
        <section
          className="workstation-hardware-status__popover"
          role="dialog"
          aria-label="Agent Computer status"
        >
          <header className="workstation-hardware-status__header">
            <div className="workstation-hardware-status__header-copy">
              <strong>Empyralis computer use</strong>
              <span>{hardwareUsageMessage(summary)}</span>
            </div>
            <span className={joinClassNames('workstation-hardware-status__badge', `workstation-hardware-status__badge--${summary.tone}`)}>
              {stateLabel}
            </span>
          </header>

          <div className="workstation-hardware-status__summary">
            <span>
              <strong>{summary.label}</strong>
              <small>{summary.detail || 'Governed runtime'}</small>
            </span>
            <span className={joinClassNames('workstation-hardware-status__row-state', `workstation-hardware-status__row-state--${summary.tone}`)}>
              {stateLabel}
            </span>
          </div>

          {error ? <p className="workstation-hardware-status__notice">{error}</p> : null}

          <Link
            href={hardwareHref}
            className="workstation-hardware-status__settings"
            onClick={() => setOpen(false)}
          >
            <Settings size={14} aria-hidden="true" />
            <span>Manage hardware</span>
          </Link>
        </section>
      ) : null}
    </div>
  );
}
