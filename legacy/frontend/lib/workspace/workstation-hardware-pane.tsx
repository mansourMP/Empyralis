'use client';

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Cloud, Copy, Monitor, Server, X } from 'lucide-react';
import { toDataURL } from 'qrcode';

import { ConfirmDialog } from '@/lib/ui/confirm-dialog';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import {
  CLOUD_VPS_PROVIDER_IDS,
  CLOUD_VPS_PROVIDERS,
  CloudVpsSetupPanel,
  type VpsProviderId,
} from '@/lib/workspace/cloud-vps-setup-panel';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

type HardwareKind = 'local_companion' | 'self_hosted_business_node' | 'cloud_computer';
type HardwareStatus = 'Connected' | 'Offline' | 'Needs approval' | 'Unavailable';
type HardwareTab = 'this_device' | 'other_computers' | 'ssh_server';
type ConnectOptionId = 'this_device' | 'other_computer' | 'ssh_server' | 'cloud_vps';
type ConnectModalContext = ConnectOptionId | 'all';

type HardwareAttachment = {
  kind: HardwareKind;
  name: string;
  status: string;
  online: boolean;
  healthy: boolean;
  trustStatus: string;
  reason: string;
  capabilities: string[];
};

type HardwareTarget = {
  kind: HardwareKind;
  name: string;
  status: string;
  online: boolean;
  healthy: boolean;
  reason: string;
  capabilities: string[];
};

type HardwareCard = {
  kind: HardwareKind;
  title: string;
  typeLabel: string;
  name: string;
  description: string;
  status: HardwareStatus;
  actionLabel: 'Connect' | 'Reconnect' | 'Approve' | 'Manage';
  reason: string;
  capabilities: string[];
  known: boolean;
};

type PairingIntentRecord = {
  pairing_token?: string | null;
  display_name?: string | null;
  platform?: string | null;
  expires_at?: string | null;
  metadata?: unknown;
};

type TrayLocalStatus = {
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

type HardwareCapabilityCheck = {
  name: 'screen_recording' | 'accessibility' | 'filesystem' | string;
  status: 'granted' | 'denied' | 'unknown' | 'unsupported' | string;
};

type HardwareCapabilityPayload = {
  available?: boolean;
  gateway_id?: string | null;
  checks?: HardwareCapabilityCheck[];
};

type HardwareActivityEvent = {
  id?: string | null;
  type?: string;
  gateway_id?: string | null;
  capability?: string | null;
  status?: 'completed' | 'failed' | string;
  duration_ms?: number;
  timestamp?: string | null;
};

type GatewayRegistrationRecord = Record<string, unknown> & {
  gateway_id?: string | null;
  display_name?: string | null;
  device_id?: string | null;
  platform?: string | null;
  status?: string | null;
  connection_status?: string | null;
  device_trust_state?: string | null;
  last_seen_at?: string | null;
};

type SageAgentComputerSelectionPayload = Record<string, unknown> & {
  selected_gateway_id?: string | null;
  selection?: Record<string, unknown> | null;
  gateway?: GatewayRegistrationRecord | null;
};

type SshAuthMode = 'password' | 'ssh_key';

const TRAY_STATUS_URL = 'http://127.0.0.1:7790/status';
const TRAY_CONNECT_URL = 'http://127.0.0.1:7790/connect';
const AGENT_DOWNLOAD_URL = 'https://empyralis.ai/downloads/empyralis-agent/macos/Empyralis-Agent.dmg';
const AGENT_COMPUTER_FULL_ACCESS_WARNING_VERSION = '2026-06-06';
const AGENT_COMPUTER_FULL_ACCESS_WARNING_COPY = 'Full Access lets Sage run commands, read, write, delete files, and access secrets, browser data, tokens, SSH keys, and connected accounts on this Agent Computer; dedicated hardware is recommended.';

const HARDWARE_KINDS: Array<{
  kind: HardwareKind;
  title: string;
  typeLabel: string;
  description: string;
}> = [
  {
    kind: 'local_companion',
    title: 'This device',
    typeLabel: 'Local computer',
    description: 'This computer is not connected yet.',
  },
  {
    kind: 'self_hosted_business_node',
    title: 'Server/VPS',
    typeLabel: 'Remote server',
    description: 'Connect a server or VPS with the Agent Computer gateway.',
  },
  {
    kind: 'cloud_computer',
    title: 'Cloud Computer',
    typeLabel: 'Managed cloud runtime',
    description: 'A hosted computer reserved for workspace execution when enabled.',
  },
];

const HARDWARE_TABS: Array<{ id: HardwareTab; label: string }> = [
  { id: 'this_device', label: 'This device' },
  { id: 'other_computers', label: 'Other computers' },
  { id: 'ssh_server', label: 'Servers' },
];

const KIND_ID_ALIASES: Record<HardwareKind, string[]> = {
  local_companion: ['local_companion', 'user_device_gateway', 'this_device'],
  self_hosted_business_node: ['self_hosted_business_node', 'self_hosted_node', 'self_host_runtime', 'server_vps'],
  cloud_computer: ['cloud_computer', 'sage_cloud_computer', 'empyralis_cloud_computer'],
};

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readString(record: Record<string, unknown> | null, ...keys: string[]): string {
  if (!record) {
    return '';
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
}

function readBoolean(record: Record<string, unknown> | null, ...keys: string[]): boolean {
  if (!record) {
    return false;
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }
  return false;
}

function readStringArray(record: Record<string, unknown> | null, ...keys: string[]): string[] {
  if (!record) {
    return [];
  }
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()));
    }
  }
  return [];
}

function normalizeKind(value: string): HardwareKind | null {
  const normalized = value.trim().toLowerCase();
  if (KIND_ID_ALIASES.local_companion.includes(normalized)) {
    return 'local_companion';
  }
  if (KIND_ID_ALIASES.self_hosted_business_node.includes(normalized)) {
    return 'self_hosted_business_node';
  }
  if (KIND_ID_ALIASES.cloud_computer.includes(normalized)) {
    return 'cloud_computer';
  }
  return null;
}

function normalizeAttachment(value: unknown): HardwareAttachment | null {
  const record = readRecord(value);
  const kind = normalizeKind(readString(record, 'kind', 'runtime_kind', 'target_kind', 'attachment_kind', 'attachmentKind'));
  if (!record || !kind) {
    return null;
  }
  return {
    kind,
    name: readString(record, 'display_name', 'name', 'label', 'hostname') || HARDWARE_KINDS.find((item) => item.kind === kind)?.title || 'Computer',
    status: readString(record, 'status', 'state', 'health_status'),
    online: readBoolean(record, 'online', 'is_online', 'connected'),
    healthy: readBoolean(record, 'healthy', 'is_healthy', 'gateway_healthy'),
    trustStatus: readString(record, 'trust_status', 'trustStatus', 'approval_status'),
    reason: readString(record, 'status_reason', 'statusReason', 'reason', 'message', 'last_error'),
    capabilities: readStringArray(record, 'capabilities', 'advertised_capabilities'),
  };
}

function normalizeAttachments(payload: unknown): HardwareAttachment[] {
  if (Array.isArray(payload)) {
    return payload.map(normalizeAttachment).filter((item): item is HardwareAttachment => Boolean(item));
  }
  const record = readRecord(payload);
  const candidates = record?.attachments ?? record?.items ?? record?.runtime_attachments ?? record?.results;
  return Array.isArray(candidates)
    ? candidates.map(normalizeAttachment).filter((item): item is HardwareAttachment => Boolean(item))
    : [];
}

function normalizeTarget(value: unknown): HardwareTarget | null {
  const record = readRecord(value);
  if (!record) {
    return null;
  }
  const rawKind = readString(record, 'kind', 'runtime_kind', 'target_kind');
  const rawId = readString(record, 'id', 'target_id', 'canonical_id', 'canonicalId');
  const kind =
    normalizeKind(rawKind) ??
    (Object.entries(KIND_ID_ALIASES).find(([, aliases]) => aliases.includes(rawId))?.[0] as HardwareKind | undefined) ??
    null;
  if (!kind) {
    return null;
  }
  const targetSelection = readRecord(record.target_selection ?? record.targetSelection);
  return {
    kind,
    name: readString(record, 'public_label', 'label', 'name', 'display_name') || HARDWARE_KINDS.find((item) => item.kind === kind)?.title || 'Computer',
    status: readString(record, 'status', 'state', 'health_status'),
    online: readBoolean(record, 'online', 'is_online', 'connected'),
    healthy: readBoolean(record, 'healthy', 'is_healthy', 'gateway_healthy'),
    reason: readString(record, 'status_reason', 'reason', 'message') || readString(targetSelection, 'reason', 'status_reason', 'message'),
    capabilities: readStringArray(record, 'capabilities', 'advertised_capabilities'),
  };
}

function classifyStatus(attachment: HardwareAttachment | null, target: HardwareTarget | null, kind: HardwareKind): HardwareStatus {
  if (!attachment && !target) {
    return kind === 'local_companion' ? 'Offline' : 'Unavailable';
  }
  const statusText = `${attachment?.status ?? ''} ${attachment?.trustStatus ?? ''} ${attachment?.reason ?? ''} ${target?.status ?? ''} ${target?.reason ?? ''}`.toLowerCase();
  if (statusText.includes('approval') || statusText.includes('pending')) {
    return 'Needs approval';
  }
  if ((attachment?.online && attachment.healthy) || (target?.online && target.healthy)) {
    return 'Connected';
  }
  if (
    statusText.includes('revoked') ||
    statusText.includes('unavailable') ||
    statusText.includes('disabled') ||
    statusText.includes('unsupported') ||
    statusText.includes('missing') ||
    statusText.includes('not enabled')
  ) {
    return 'Unavailable';
  }
  return 'Offline';
}

function actionForStatus(status: HardwareStatus): HardwareCard['actionLabel'] {
  if (status === 'Connected') {
    return 'Manage';
  }
  if (status === 'Needs approval') {
    return 'Approve';
  }
  if (status === 'Offline') {
    return 'Reconnect';
  }
  return 'Connect';
}

function publicHardwareReason(rawReason: string, status: HardwareStatus, workspaceId: string, kind: HardwareKind): string {
  const normalized = rawReason.trim().toLowerCase();
  if (!normalized) {
    if (kind === 'local_companion' && status === 'Offline') {
      return 'This device is not connected yet.';
    }
    return '';
  }
  if (normalized.includes('workspace')) {
    return `This computer is paired to another workspace. Reconnect it to ${workspaceId}.`;
  }
  if (normalized.includes('gateway_offline') || normalized.includes('offline')) {
    return 'Empyralis cannot use this computer right now.';
  }
  if (normalized.includes('not_configured') || normalized.includes('missing') || normalized.includes('unavailable')) {
    return status === 'Unavailable'
      ? 'This computer option is not available for this workspace.'
      : 'This computer needs setup before Empyralis can use it.';
  }
  return rawReason.replace(/[_-]+/g, ' ');
}

function buildCard(
  metadata: (typeof HARDWARE_KINDS)[number],
  attachment: HardwareAttachment | null,
  target: HardwareTarget | null,
  workspaceId: string,
): HardwareCard {
  const status = classifyStatus(attachment, target, metadata.kind);
  const rawReason = attachment?.reason || target?.reason || '';
  const reason = publicHardwareReason(rawReason, status, workspaceId, metadata.kind);
  return {
    kind: metadata.kind,
    title: metadata.title,
    typeLabel: metadata.typeLabel,
    name: attachment?.name || target?.name || metadata.title,
    description: reason || metadata.description,
    status,
    actionLabel: actionForStatus(status),
    reason,
    capabilities: attachment?.capabilities.length ? attachment.capabilities : target?.capabilities ?? [],
    known: Boolean(attachment || target) || metadata.kind === 'local_companion',
  };
}

function detectHardwarePlatform(): string {
  if (typeof navigator === 'undefined') {
    return 'unknown';
  }
  const token = `${navigator.platform ?? ''} ${navigator.userAgent ?? ''}`.toLowerCase();
  if (token.includes('mac')) {
    return 'macos';
  }
  if (token.includes('win')) {
    return 'windows';
  }
  if (token.includes('linux')) {
    return 'linux';
  }
  return 'unknown';
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

function gatewayApiUrl(): string {
  const configured = process.env.NEXT_PUBLIC_ORION_API_URL || process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_PLATFORM_URL || '';
  if (configured.trim()) {
    return configured.replace(/\/+$/, '');
  }
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  return '';
}

function gatewayControlPlaneApiUrl(): string {
  const baseUrl = gatewayApiUrl().replace(/\/+$/, '');
  if (!baseUrl) {
    return '';
  }
  return baseUrl.endsWith('/api') ? baseUrl : `${baseUrl}/api`;
}

function readPairingIntent(payload: unknown): PairingIntentRecord | null {
  const record = readRecord(payload);
  const intent = readRecord(record?.intent) ?? record;
  if (!intent) {
    return null;
  }
  const pairingToken = readString(intent, 'pairing_token', 'token');
  if (!pairingToken) {
    return null;
  }
  return {
    pairing_token: pairingToken,
    display_name: readString(intent, 'display_name', 'displayName', 'name'),
    platform: readString(intent, 'platform'),
    expires_at: readString(intent, 'expires_at', 'expiresAt'),
    metadata: intent.metadata,
  };
}

function pairingCommand(intent: PairingIntentRecord, workspaceId: string, option: ConnectOptionId): string {
  const pairingToken = String(intent.pairing_token ?? '').trim();
  const displayName = String(intent.display_name ?? '').trim();
  const apiUrl = gatewayApiUrl();
  if (!pairingToken) {
    return 'Pairing token unavailable. Create a new hardware connection.';
  }
  if (!apiUrl) {
    return 'Computer connector API URL unavailable. Set NEXT_PUBLIC_ORION_API_URL or NEXT_PUBLIC_API_URL before pairing.';
  }
  const installCommand = option === 'ssh_server'
    ? 'scripts/agent_computer.sh service-install --system'
    : 'scripts/agent_computer.sh install';
  return [
    'cd "$(git rev-parse --show-toplevel)"',
    `export EMPYRALIS_GATEWAY_API_URL=${shellQuote(apiUrl)}`,
    `export EMPYRALIS_GATEWAY_PAIRING_TOKEN=${shellQuote(pairingToken)}`,
    displayName ? `export EMPYRALIS_GATEWAY_DISPLAY_NAME=${shellQuote(displayName)}` : '',
    workspaceId ? `export EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID=${shellQuote(workspaceId)}` : '',
    'scripts/agent_computer.sh stop',
    installCommand,
    'scripts/agent_computer.sh start',
  ].filter(Boolean).join('\n');
}

function setupLink(intent: PairingIntentRecord, workspaceId: string): string {
  const pairingToken = String(intent.pairing_token ?? '').trim();
  if (!pairingToken) {
    return '';
  }
  const params = new URLSearchParams({ token: pairingToken, ws: workspaceId });
  return `https://empyralis.ai/setup?${params.toString()}`;
}

function trayStatusConnected(status: TrayLocalStatus | null): boolean {
  return trayProcessConnected(status) && status?.session_verified === true;
}

function localTrayConnectionNote(status: TrayLocalStatus | null): string {
  if (!status) {
    return 'Connecting...';
  }
  if (status.gateway_running && !status.session_verified) {
    return 'Gateway is running; verifying workspace session.';
  }
  if (status.desired_connected && !status.gateway_running) {
    return 'Local runner is ready; gateway is stopped. Connect again to create a fresh pairing.';
  }
  if (status.supervisor_running && !status.gateway_running) {
    return 'Local runner is ready; gateway is stopped.';
  }
  return 'Connecting...';
}

function trayProcessConnected(status: TrayLocalStatus | null): boolean {
  return Boolean(
    status?.connected ||
    status?.gateway_running ||
    status?.supervisor_running ||
    status?.state === 'connected' ||
    status?.state === 'active',
  );
}

function normalizeCapabilityChecks(payload: HardwareCapabilityPayload | null): HardwareCapabilityCheck[] {
  if (!payload?.available || !Array.isArray(payload.checks)) {
    return [];
  }
  return payload.checks
    .map((item) => ({
      name: String(item?.name || '').trim(),
      status: String(item?.status || 'unknown').trim().toLowerCase(),
    }))
    .filter((item) => item.name && item.status !== 'unsupported');
}

function capabilityLabel(name: string): string {
  if (name === 'screen_recording') {
    return 'Screen recording';
  }
  if (name === 'accessibility') {
    return 'Accessibility';
  }
  if (name === 'filesystem') {
    return 'File access';
  }
  return name;
}

function capabilityFixTarget(name: string): string {
  if (name === 'screen_recording') {
    return 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture';
  }
  if (name === 'accessibility') {
    return 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility';
  }
  return '';
}

function normalizeHardwareTab(value: string | null): HardwareTab {
  if (
    value === 'this_device'
    || value === 'other_computers'
    || value === 'ssh_server'
  ) {
    return value;
  }
  return 'this_device';
}

function hardwareActivityLabel(capability: string | null | undefined): string {
  const normalized = String(capability || '').trim();
  if (!normalized) {
    return 'Hardware action';
  }
  return normalized
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timeAgo(timestamp: string | null | undefined): string {
  const parsed = timestamp ? Date.parse(timestamp) : Number.NaN;
  if (!Number.isFinite(parsed)) {
    return 'just now';
  }
  const deltaSeconds = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (deltaSeconds < 5) {
    return 'just now';
  }
  if (deltaSeconds < 60) {
    return `${deltaSeconds}s ago`;
  }
  const minutes = Math.floor(deltaSeconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  return `${Math.floor(minutes / 60)}h ago`;
}

function gatewayTimestampMs(gateway: GatewayRegistrationRecord, ...keys: string[]): number {
  const value = readString(gateway, ...keys);
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function gatewayHostIdentity(gateway: GatewayRegistrationRecord): string {
  const metadata = readRecord(gateway.metadata);
  const nativeRuntime = readRecord(metadata?.native_runtime);
  const deviceMetadata = readRecord(metadata?.device_metadata);
  const rawHost = (
    readString(nativeRuntime, 'hostname')
    || readString(deviceMetadata, 'hostname')
    || readString(metadata, 'hostname')
    || readString(gateway, 'display_name')
  );
  const normalized = rawHost
    .trim()
    .toLowerCase()
    .replace(/\.local$/i, '')
    .replace(/-\d+$/i, '');
  return normalized || readString(gateway, 'device_id', 'gateway_id').toLowerCase();
}

function gatewayIdentityKey(gateway: GatewayRegistrationRecord): string {
  const metadata = readRecord(gateway.metadata);
  const deviceMetadata = readRecord(metadata?.device_metadata);
  const platform = (
    readString(gateway, 'platform')
    || readString(deviceMetadata, 'platform')
    || readString(metadata, 'platform')
    || 'computer'
  ).toLowerCase();
  return `${platform}:${gatewayHostIdentity(gateway)}`;
}

function gatewayRecentlySeen(gateway: GatewayRegistrationRecord): boolean {
  const seenAt = gatewayTimestampMs(gateway, 'last_seen_at', 'updated_at');
  return seenAt > 0 && Date.now() - seenAt <= 120_000;
}

function gatewayConnectionStatus(gateway: GatewayRegistrationRecord): string {
  const metadata = readRecord(gateway.metadata);
  const rawStatus = (
    readString(gateway, 'connection_status')
    || readString(metadata, 'health_state')
    || readString(gateway, 'status')
    || 'offline'
  ).toLowerCase();
  if (rawStatus === 'active') {
    return 'offline';
  }
  if (rawStatus === 'offline' && readString(metadata, 'health_state').toLowerCase() === 'online' && gatewayRecentlySeen(gateway)) {
    return 'online';
  }
  return rawStatus;
}

function localCompanionCardFromLiveStatus(
  baseCard: HardwareCard | null,
  trayStatus: TrayLocalStatus | null,
  selectedGateway: GatewayRegistrationRecord | null,
): HardwareCard | null {
  if (!baseCard) {
    return null;
  }
  const trayConnected = trayStatusConnected(trayStatus);
  const selectedGatewayOnline = selectedGateway ? gatewayConnectionStatus(selectedGateway) === 'online' : false;
  const localProcessRunning = trayProcessConnected(trayStatus);
  if (!trayConnected && !selectedGatewayOnline && !localProcessRunning) {
    return baseCard;
  }
  if (!trayConnected && !selectedGatewayOnline) {
    return {
      ...baseCard,
      name: trayStatus?.device_name || baseCard.name,
      description: localTrayConnectionNote(trayStatus),
      status: 'Offline',
      actionLabel: 'Reconnect',
      known: true,
    };
  }
  return {
    ...baseCard,
    name: trayStatus?.device_name || readString(selectedGateway, 'display_name', 'device_id', 'gateway_id') || baseCard.name,
    description: '',
    status: 'Connected',
    actionLabel: 'Manage',
    reason: '',
    known: true,
  };
}

function gatewayStatusRank(gateway: GatewayRegistrationRecord): number {
  const status = gatewayConnectionStatus(gateway);
  if (status === 'online') {
    return 4;
  }
  if (status === 'reconnecting') {
    return 3;
  }
  if (status === 'degraded') {
    return 2;
  }
  if (status === 'offline') {
    return 1;
  }
  return 0;
}

function gatewayPreferenceScore(gateway: GatewayRegistrationRecord, selectedGatewayId: string): number {
  const metadata = readRecord(gateway.metadata);
  const gatewayId = readString(gateway, 'gateway_id');
  const seenAt = gatewayTimestampMs(gateway, 'last_seen_at', 'updated_at', 'created_at');
  return (
    (gatewayId && gatewayId === selectedGatewayId ? 1_000_000_000 : 0)
    + (readBoolean(metadata, 'sage_agent_computer_selected') ? 100_000_000 : 0)
    + gatewayStatusRank(gateway) * 1_000_000
    + Math.floor(seenAt / 1000)
  );
}

function dedupeGatewayRegistrations(
  registrations: GatewayRegistrationRecord[],
  selectedGatewayId: string,
): GatewayRegistrationRecord[] {
  const byIdentity = new Map<string, GatewayRegistrationRecord>();
  for (const gateway of registrations) {
    const key = gatewayIdentityKey(gateway);
    const existing = byIdentity.get(key);
    if (!existing || gatewayPreferenceScore(gateway, selectedGatewayId) > gatewayPreferenceScore(existing, selectedGatewayId)) {
      byIdentity.set(key, gateway);
    }
  }
  return Array.from(byIdentity.values()).sort((left, right) => {
    const scoreDelta = gatewayPreferenceScore(right, selectedGatewayId) - gatewayPreferenceScore(left, selectedGatewayId);
    if (scoreDelta !== 0) {
      return scoreDelta;
    }
    return readString(left, 'display_name', 'gateway_id').localeCompare(readString(right, 'display_name', 'gateway_id'));
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function fetchJsonWithTimeout<T>(url: string, init: RequestInit = {}, timeoutMs = 500): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Request failed with ${response.status}`);
    }
    return await response.json() as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function writeClipboardText(text: string): Promise<boolean> {
  if (!text) {
    return false;
  }
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy copy path.
  }
  if (typeof document === 'undefined') {
    return false;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.top = '0';
  textarea.style.left = '0';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } finally {
    document.body.removeChild(textarea);
  }
  return copied || Boolean(text);
}

export function WorkstationHardwarePane() {
  const { bootstrap } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const searchParams = useSearchParams();
  const requestedSection = searchParams.get('section');
  const requestedHardwareTab = normalizeHardwareTab(requestedSection);
  const [attachments, setAttachments] = useState<HardwareAttachment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedKind, setExpandedKind] = useState<HardwareKind | null>(null);
  const [activeTab, setActiveTab] = useState<HardwareTab>(requestedHardwareTab);
  const [connectOpen, setConnectOpen] = useState(false);
  const [connectContext, setConnectContext] = useState<ConnectModalContext>('all');
  const [selectedConnectOption, setSelectedConnectOption] = useState<ConnectOptionId>('this_device');
  const [pairingIntentState, setPairingIntentState] = useState<PairingIntentRecord | null>(null);
  const [otherSetupIntent, setOtherSetupIntent] = useState<PairingIntentRecord | null>(null);
  const [setupQrDataUrl, setSetupQrDataUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [localConnectBusy, setLocalConnectBusy] = useState(false);
  const [remoteConnectBusy, setRemoteConnectBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [copiedTarget, setCopiedTarget] = useState<'setup' | 'command' | null>(null);
  const [trayStatus, setTrayStatus] = useState<TrayLocalStatus | null>(null);
  const [capabilityChecks, setCapabilityChecks] = useState<HardwareCapabilityCheck[]>([]);
  const [hardwareActivity, setHardwareActivity] = useState<HardwareActivityEvent[]>([]);
  const [trayDetected, setTrayDetected] = useState<boolean | null>(null);
  const [trayChecking, setTrayChecking] = useState(false);
  const [localConnectError, setLocalConnectError] = useState<string | null>(null);
  const [gatewayRegistrations, setGatewayRegistrations] = useState<GatewayRegistrationRecord[]>([]);
  const [sageAgentComputerSelection, setSageAgentComputerSelection] = useState<SageAgentComputerSelectionPayload | null>(null);
  const [selectionBusyGatewayId, setSelectionBusyGatewayId] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [remoteExpanded, setRemoteExpanded] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [manualCommandOpen, setManualCommandOpen] = useState(false);
  const [pendingFullAccessPairingOption, setPendingFullAccessPairingOption] = useState<ConnectOptionId | null>(null);
  const [sshHost, setSshHost] = useState('');
  const [sshPort, setSshPort] = useState('22');
  const [sshUsername, setSshUsername] = useState('');
  const [sshAuthMode, setSshAuthMode] = useState<SshAuthMode>('password');
  const [sshPassword, setSshPassword] = useState('');
  const [sshKey, setSshKey] = useState('');
  const [cloudVpsOpen, setCloudVpsOpen] = useState(false);
  const [cloudVpsInitialProvider, setCloudVpsInitialProvider] = useState<VpsProviderId | null>(null);
  const workspaceRecord = readRecord(bootstrap.workspace);
  const workspaceId = readString(workspaceRecord, 'id', 'workspace_id') || 'ws-1';
  const workspaceLabel = workspaceId || readString(workspaceRecord, 'label', 'name') || 'this workspace';

  useEffect(() => {
    setActiveTab(requestedHardwareTab);
  }, [requestedHardwareTab]);

  async function refreshHardwareAttachments(): Promise<void> {
    try {
      const payload = await services.client.listRuntimeAttachments();
      setAttachments(normalizeAttachments(payload));
      setError(null);
    } catch {
      setAttachments([]);
      setError('Hardware status is unavailable right now.');
    }
  }

  async function refreshSageAgentComputerState(): Promise<void> {
    try {
      const [registrationsPayload, selectionPayload] = await Promise.all([
        services.client.requestJson<Record<string, unknown>>({
          path: `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
          allowStatuses: [404],
        }),
        services.client.getSageAgentComputerSelection() as Promise<SageAgentComputerSelectionPayload>,
      ]);
      const registrations = Array.isArray(registrationsPayload?.items)
        ? registrationsPayload.items.filter((item): item is GatewayRegistrationRecord => Boolean(item) && typeof item === 'object')
        : [];
      setGatewayRegistrations(registrations);
      setSageAgentComputerSelection(selectionPayload);
      setSelectionError(null);
    } catch (stateError) {
      setGatewayRegistrations([]);
      setSageAgentComputerSelection(null);
      setSelectionError(stateError instanceof Error ? stateError.message : 'Sage Agent Computer selection is unavailable.');
    }
  }

  async function refreshHardwareCapabilities(): Promise<void> {
    try {
      const payload = await fetchJsonWithTimeout<HardwareCapabilityPayload>(
        `/api/gateway/hardware/capabilities?workspace_id=${encodeURIComponent(workspaceId)}`,
        { method: 'GET' },
        5_000,
      );
      setCapabilityChecks(normalizeCapabilityChecks(payload));
    } catch {
      setCapabilityChecks([]);
    }
  }

  useEffect(() => {
    let active = true;
    setError(null);
    services.client
      .listRuntimeAttachments()
      .then((payload) => {
        if (!active) {
          return;
        }
        setAttachments(normalizeAttachments(payload));
      })
      .catch(() => {
        if (!active) {
          return;
        }
        setAttachments([]);
        setError('Hardware status is unavailable right now.');
      });
    return () => {
      active = false;
    };
  }, [services.client]);

  useEffect(() => {
    void refreshSageAgentComputerState();
  }, [services.client, workspaceId]);

  useEffect(() => {
    void detectTrayAgent();
  }, []);

  useEffect(() => {
    if (!connectOpen) {
      return;
    }
    void detectTrayAgent();
  }, [connectOpen]);

  useEffect(() => {
    if (!trayStatusConnected(trayStatus)) {
      setCapabilityChecks([]);
      return;
    }
    void refreshHardwareCapabilities();
  }, [trayStatus?.connected, trayStatus?.session_verified, workspaceId]);

  useEffect(() => {
    if (!trayStatusConnected(trayStatus) || typeof EventSource === 'undefined') {
      setHardwareActivity([]);
      return;
    }
    const source = new EventSource(`/api/gateway/hardware/activity/stream?workspace_id=${encodeURIComponent(workspaceId)}`);
    const handleHardwareAction = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as HardwareActivityEvent;
        const eventId = String(payload.id || `${payload.gateway_id || 'gateway'}:${payload.capability || 'action'}:${payload.timestamp || Date.now()}`);
        setHardwareActivity((current) => {
          const next = [
            { ...payload, id: eventId },
            ...current.filter((item) => String(item.id || '') !== eventId),
          ];
          return next.slice(0, 5);
        });
      } catch {
        // Ignore malformed stream frames.
      }
    };
    source.addEventListener('hardware_action', handleHardwareAction as EventListener);
    return () => {
      source.removeEventListener('hardware_action', handleHardwareAction as EventListener);
      source.close();
    };
  }, [trayStatus?.connected, trayStatus?.session_verified, workspaceId]);

  const targets = useMemo(
    () => (bootstrap.runtime.runtimeTargets ?? []).map(normalizeTarget).filter((item): item is HardwareTarget => Boolean(item)),
    [bootstrap.runtime.runtimeTargets],
  );

  const cards = useMemo(
    () =>
      HARDWARE_KINDS.map((metadata) => {
        const attachment = attachments.find((item) => item.kind === metadata.kind) ?? null;
        const target = targets.find((item) => item.kind === metadata.kind) ?? null;
        return buildCard(metadata, attachment, target, workspaceLabel);
      }),
    [attachments, targets, workspaceLabel],
  );

  const selectedSageGatewayId = readString(
    readRecord(sageAgentComputerSelection?.selection),
    'selected_gateway_id',
  ) || readString(readRecord(sageAgentComputerSelection), 'selected_gateway_id');
  const visibleGatewayRegistrations = useMemo(
    () => dedupeGatewayRegistrations(gatewayRegistrations, selectedSageGatewayId),
    [gatewayRegistrations, selectedSageGatewayId],
  );
  const selectedSageGateway = useMemo(() => {
    if (selectedSageGatewayId) {
      const registered = gatewayRegistrations.find((gateway) => readString(gateway, 'gateway_id') === selectedSageGatewayId);
      if (registered) {
        return registered;
      }
    }
    const selectedGateway = readRecord(sageAgentComputerSelection?.gateway);
    return selectedGateway ? selectedGateway as GatewayRegistrationRecord : null;
  }, [gatewayRegistrations, sageAgentComputerSelection, selectedSageGatewayId]);
  const baseLocalCard = cards.find((card) => card.kind === 'local_companion') ?? null;
  const localCard = useMemo(
    () => localCompanionCardFromLiveStatus(baseLocalCard, trayStatus, selectedSageGateway),
    [baseLocalCard, selectedSageGateway, trayStatus],
  );
  const displayCards = useMemo(
    () => cards.map((card) => (card.kind === 'local_companion' && localCard ? localCard : card)),
    [cards, localCard],
  );
  const serverCard = displayCards.find((card) => card.kind === 'self_hosted_business_node') ?? null;
  const cloudCard = displayCards.find((card) => card.kind === 'cloud_computer') ?? null;
  const visibleCards = useMemo(() => {
    if (activeTab === 'this_device') {
      return localCard ? [localCard] : [];
    }
    if (activeTab === 'ssh_server') {
      return serverCard && serverCard.known && serverCard.status !== 'Unavailable' ? [serverCard] : [];
    }
    return displayCards.filter((card) => (
      card.kind !== 'local_companion'
      && card.kind !== 'self_hosted_business_node'
      && card.known
      && card.status !== 'Unavailable'
    ));
  }, [activeTab, displayCards, localCard, serverCard]);
  const command = otherSetupIntent ? pairingCommand(otherSetupIntent, workspaceId, 'other_computer') : pairingIntentState ? pairingCommand(pairingIntentState, workspaceId, selectedConnectOption) : '';
  const generatedSetupLink = otherSetupIntent ? setupLink(otherSetupIntent, workspaceId) : '';
  const showGatewayRegistrationRows = activeTab === 'this_device' && visibleGatewayRegistrations.length > 0;

  useEffect(() => {
    let active = true;
    if (!generatedSetupLink) {
      setSetupQrDataUrl(null);
      return;
    }
    toDataURL(generatedSetupLink, {
      errorCorrectionLevel: 'M',
      margin: 1,
      width: 168,
      color: {
        dark: '#f4f4f5',
        light: '#00000000',
      },
    })
      .then((dataUrl) => {
        if (active) {
          setSetupQrDataUrl(dataUrl);
        }
      })
      .catch(() => {
        if (active) {
          setSetupQrDataUrl(null);
        }
      });
    return () => {
      active = false;
    };
  }, [generatedSetupLink]);

  function emptyCopy(): string {
    if (activeTab === 'this_device') {
      return 'This Mac is not connected yet.';
    }
    if (activeTab === 'ssh_server') {
      return 'No remote server is connected yet.';
    }
    return 'No other computers are connected yet.';
  }

  function optionPlatform(option: ConnectOptionId): string | undefined {
    if (option === 'ssh_server') {
      return 'linux';
    }
    if (option === 'this_device') {
      const detected = detectHardwarePlatform();
      return detected === 'unknown' ? undefined : detected;
    }
    return undefined;
  }

  async function createPairingIntent(
    optionOverride?: ConnectOptionId,
    options: { acknowledgedFullAccessWarning?: boolean } = {},
  ): Promise<PairingIntentRecord | null> {
    const option = optionOverride ?? selectedConnectOption;
    setSelectedConnectOption(option);
    setBusy(true);
    setError(null);
    setStatusMessage(null);
    setCopiedTarget(null);
    try {
      const payload = await services.client.requestJson<unknown>({
        path: '/api/gateway/pairings/intents',
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            display_name: option === 'ssh_server'
              ? 'Server/VPS'
              : option === 'other_computer'
                ? 'Other computer'
                : 'This device',
            platform: optionPlatform(option),
            runtime_access_mode: 'full_access',
            autonomous_agent_setup_warning_acknowledged: options.acknowledgedFullAccessWarning === true,
            metadata: options.acknowledgedFullAccessWarning === true ? {
              autonomous_agent_setup_warning_version: AGENT_COMPUTER_FULL_ACCESS_WARNING_VERSION,
            } : {},
          }),
        },
      });
      const intent = readPairingIntent(payload);
      if (!intent) {
        throw new Error('Pairing token was not returned.');
      }
      setPairingIntentState(intent);
      return intent;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not create a hardware pairing token.');
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function detectTrayAgent() {
    setTrayChecking(true);
    setLocalConnectError(null);
    try {
      const status = await fetchJsonWithTimeout<TrayLocalStatus>(TRAY_STATUS_URL, { method: 'GET' }, 500);
      setTrayStatus(status);
      setTrayDetected(Boolean(status?.ok ?? true));
    } catch {
      setTrayStatus(null);
      setTrayDetected(false);
    } finally {
      setTrayChecking(false);
    }
  }

  async function pollTrayConnection(): Promise<TrayLocalStatus | null> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      await wait(2_000);
      try {
        const status = await fetchJsonWithTimeout<TrayLocalStatus>(TRAY_STATUS_URL, { method: 'GET' }, 1_500);
        setTrayStatus(status);
        if (trayStatusConnected(status)) {
          return status;
        }
      } catch {
        setTrayStatus(null);
      }
    }
    return null;
  }

  async function connectThisMac(acknowledgedFullAccessWarning = false) {
    if (!acknowledgedFullAccessWarning) {
      setPendingFullAccessPairingOption('this_device');
      return;
    }
    setLocalConnectBusy(true);
    setLocalConnectError(null);
    setStatusMessage(null);
    try {
      const intent = await createPairingIntent('this_device', { acknowledgedFullAccessWarning: true });
      if (!intent?.pairing_token) {
        throw new Error('Could not create a hardware pairing token.');
      }
      const response = await fetchJsonWithTimeout<TrayLocalStatus & { error?: string }>(
        TRAY_CONNECT_URL,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            pairing_token: intent.pairing_token,
            workspace_id: workspaceId,
            gateway_api_url: gatewayControlPlaneApiUrl(),
          }),
        },
        30_000,
      );
      if (response.ok === false) {
        throw new Error(response.error || 'Could not connect this Mac.');
      }
      setTrayStatus(response);
      const connectedStatus = trayStatusConnected(response) ? response : await pollTrayConnection();
      if (!connectedStatus) {
        setLocalConnectError('Connection timed out');
        return;
      }
      closeConnect();
      await refreshHardwareAttachments();
      await refreshSageAgentComputerState();
      await refreshHardwareCapabilities();
    } catch (connectError) {
      setLocalConnectError(connectError instanceof Error ? connectError.message : 'Connection timed out');
    } finally {
      setLocalConnectBusy(false);
    }
  }

  async function connectRemoteServer(acknowledgedFullAccessWarning = false) {
    const trimmedHost = sshHost.trim();
    const trimmedUsername = sshUsername.trim();
    const parsedPort = Number.parseInt(sshPort, 10);
    if (!trimmedHost || !trimmedUsername) {
      setRemoteError('Enter host and username to connect a server.');
      return;
    }
    if (!Number.isFinite(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      setRemoteError('Enter a valid SSH port.');
      return;
    }
    if (sshAuthMode === 'password' && !sshPassword) {
      setRemoteError('Enter the SSH password or switch to SSH Key.');
      return;
    }
    if (sshAuthMode === 'ssh_key' && !sshKey.trim()) {
      setRemoteError('Paste an SSH key or switch to Password.');
      return;
    }
    if (!acknowledgedFullAccessWarning) {
      setPendingFullAccessPairingOption('ssh_server');
      return;
    }
    setRemoteConnectBusy(true);
    setRemoteError(null);
    setStatusMessage(null);
    const intent = await createPairingIntent('ssh_server', { acknowledgedFullAccessWarning: true });
    if (!intent) {
      setRemoteConnectBusy(false);
      return;
    }
    try {
      await services.client.requestJson<unknown>({
        path: '/api/gateway/pairings/ssh',
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            pairing_token: intent.pairing_token,
            host: trimmedHost,
            port: parsedPort,
            username: trimmedUsername,
            auth_mode: sshAuthMode,
            password: sshAuthMode === 'password' ? sshPassword : undefined,
            ssh_key: sshAuthMode === 'ssh_key' ? sshKey : undefined,
          }),
        },
      });
      setStatusMessage('Remote server connection started.');
      await refreshHardwareAttachments();
      await refreshSageAgentComputerState();
    } catch (sshError) {
      setRemoteError(sshError instanceof Error ? sshError.message : 'Remote server connection failed.');
    } finally {
      setRemoteConnectBusy(false);
    }
  }

  async function createOtherComputerSetupLink(acknowledgedFullAccessWarning = false) {
    if (!acknowledgedFullAccessWarning) {
      setPendingFullAccessPairingOption('other_computer');
      return;
    }
    setOtherSetupIntent(null);
    setManualCommandOpen(false);
    const intent = await createPairingIntent('other_computer', { acknowledgedFullAccessWarning: true });
    if (intent) {
      setOtherSetupIntent(intent);
      setStatusMessage('Setup link created.');
    }
  }

  function showCopied(target: 'setup' | 'command') {
    setCopiedTarget(target);
    window.setTimeout(() => {
      setCopiedTarget((current) => current === target ? null : current);
    }, 1_500);
  }

  async function copySetupLink() {
    if (await writeClipboardText(generatedSetupLink)) {
      showCopied('setup');
    }
  }

  async function copyCommand() {
    if (await writeClipboardText(command)) {
      showCopied('command');
    }
  }

  function openConnect(context: ConnectModalContext = 'all') {
    const option = context === 'all' ? 'this_device' : context;
    setConnectContext(context);
    setSelectedConnectOption(option);
    setConnectOpen(true);
    setPairingIntentState(null);
    setOtherSetupIntent(null);
    setStatusMessage(null);
    setLocalConnectError(null);
    setRemoteError(null);
    setCopiedTarget(null);
    setRemoteExpanded(option === 'ssh_server');
    setManualCommandOpen(false);
  }

  function closeConnect() {
    setConnectOpen(false);
    setPendingFullAccessPairingOption(null);
  }

  function openCloudVps(providerId: VpsProviderId | null = null) {
    setCloudVpsInitialProvider(providerId);
    setCloudVpsOpen(true);
  }

  function handleCardAction(card: HardwareCard) {
    if (card.status === 'Connected') {
      setExpandedKind(expandedKind === card.kind ? null : card.kind);
      return;
    }
    if (card.kind === 'self_hosted_business_node') {
      setActiveTab('ssh_server');
      setRemoteExpanded(true);
      return;
    }
    openConnect('this_device');
  }

  function confirmFullAccessPairing() {
    const option = pendingFullAccessPairingOption;
    setPendingFullAccessPairingOption(null);
    if (option === 'this_device') {
      void connectThisMac(true);
      return;
    }
    if (option === 'ssh_server') {
      void connectRemoteServer(true);
      return;
    }
    if (option === 'other_computer') {
      void createOtherComputerSetupLink(true);
    }
  }

  async function setAsSageAgentComputer(gatewayId: string) {
    const cleanGatewayId = gatewayId.trim();
    if (!cleanGatewayId) {
      return;
    }
    setSelectionBusyGatewayId(cleanGatewayId);
    setSelectionError(null);
    try {
      const payload = await services.client.setSageAgentComputerSelection({
        selectedGatewayId: cleanGatewayId,
        metadata: { source: 'hardware_page' },
      }) as SageAgentComputerSelectionPayload;
      setSageAgentComputerSelection(payload);
      await refreshSageAgentComputerState();
    } catch (selectionSetError) {
      setSelectionError(selectionSetError instanceof Error ? selectionSetError.message : 'Could not select Sage Agent Computer.');
    } finally {
      setSelectionBusyGatewayId(null);
    }
  }

  const connectModalTitle = trayDetected && connectContext === 'this_device'
    ? 'Connect via Empyralis Agent'
    : connectContext === 'this_device'
      ? 'Connect this Mac'
    : connectContext === 'ssh_server'
      ? 'Connect a remote server'
      : connectContext === 'cloud_vps'
        ? 'Create a cloud Agent Computer'
      : connectContext === 'other_computer'
        ? 'Connect another computer'
        : 'Connect a computer';
  const showThisMacCard = connectContext === 'all' || connectContext === 'this_device';
  const showCloudVpsCard = connectContext === 'all' || connectContext === 'cloud_vps';
  const showRemoteServerCard = connectContext === 'all' || connectContext === 'ssh_server';
  const showOtherComputerCard = connectContext === 'all' || connectContext === 'other_computer';

  return (
    <section className="workstation-hardware-pane" aria-label="Hardware">
      <div className="workstation-hardware-page">
        <nav className="workstation-hardware-tabs" aria-label="Hardware connection type">
          {HARDWARE_TABS.map((tab) => (
            <button
              key={tab.id}
              className={joinClassNames('workstation-hardware-tab-pill', activeTab === tab.id && 'is-active')}
              aria-current={activeTab === tab.id ? 'page' : undefined}
              type="button"
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <section className="workstation-hardware-section" aria-label={HARDWARE_TABS.find((tab) => tab.id === activeTab)?.label ?? 'Hardware'}>
          {selectionError ? <p className="workstation-hardware-section__notice">{selectionError}</p> : null}
          {error ? <p className="workstation-hardware-section__notice">{error}</p> : null}
          {statusMessage ? <p className="workstation-hardware-section__notice">{statusMessage}</p> : null}

          {activeTab === 'ssh_server' ? (
            <div className="workstation-hardware-connect-stack">
              <div className="workstation-hardware-provider-grid" aria-label="Cloud VPS providers">
                {CLOUD_VPS_PROVIDER_IDS.map((providerId) => {
                  const provider = CLOUD_VPS_PROVIDERS[providerId];
                  return (
                    <article key={providerId} className="workstation-hardware-provider-card">
                      <span className="workstation-hardware-provider-card__logo" aria-hidden="true">
                        <img src={provider.logoSrc} alt="" />
                      </span>
                      <div className="workstation-hardware-provider-card__copy">
                        <div>
                          <h3>{provider.label}</h3>
                          <p>{provider.tagline}</p>
                        </div>
                        <p className="workstation-hardware-provider-card__meta">{provider.features.join(' · ')}</p>
                      </div>
                      <div className="workstation-hardware-provider-card__side">
                        <strong>{provider.price}</strong>
                        <AppButton tone="secondary" type="button" onClick={() => openCloudVps(providerId)}>
                          Connect
                        </AppButton>
                      </div>
                    </article>
                  );
                })}
              </div>

              <article className={joinClassNames('workstation-hardware-connect-card', remoteExpanded && 'is-active')}>
                <div className="workstation-hardware-connect-card__main">
                  <div>
                    <h3>Remote server</h3>
                    <p>Connect an existing server with SSH.</p>
                  </div>
                  <AppButton tone="secondary" type="button" onClick={() => setRemoteExpanded((expanded) => !expanded)}>
                    Configure
                  </AppButton>
                </div>
                {remoteExpanded ? (
                  <div className="workstation-hardware-ssh-form">
                    <label className="app-form-field">
                      <span className="app-form-field__label">Host</span>
                      <input className="app-field" type="text" value={sshHost} onChange={(event) => setSshHost(event.target.value)} placeholder="server.example.com" />
                    </label>
                    <label className="app-form-field">
                      <span className="app-form-field__label">Port</span>
                      <input className="app-field" type="number" min="1" max="65535" value={sshPort} onChange={(event) => setSshPort(event.target.value)} />
                    </label>
                    <label className="app-form-field">
                      <span className="app-form-field__label">Username</span>
                      <input className="app-field" type="text" value={sshUsername} onChange={(event) => setSshUsername(event.target.value)} placeholder="ubuntu" />
                    </label>
                    <div className="workstation-hardware-auth-toggle" role="group" aria-label="SSH authentication">
                      <button className={joinClassNames(sshAuthMode === 'password' && 'is-active')} type="button" onClick={() => setSshAuthMode('password')}>
                        Password
                      </button>
                      <button className={joinClassNames(sshAuthMode === 'ssh_key' && 'is-active')} type="button" onClick={() => setSshAuthMode('ssh_key')}>
                        SSH Key
                      </button>
                    </div>
                    {sshAuthMode === 'password' ? (
                      <label className="app-form-field">
                        <span className="app-form-field__label">Password</span>
                        <input className="app-field" type="password" value={sshPassword} onChange={(event) => setSshPassword(event.target.value)} />
                      </label>
                    ) : (
                      <label className="app-form-field">
                        <span className="app-form-field__label">SSH key</span>
                        <textarea className="app-textarea" value={sshKey} onChange={(event) => setSshKey(event.target.value)} rows={5} />
                      </label>
                    )}
                    <AppButton tone="secondary" type="button" onClick={() => void connectRemoteServer()} disabled={remoteConnectBusy}>
                      {remoteConnectBusy ? <span className="workstation-hardware-spinner" aria-hidden="true" /> : null}
                      {remoteConnectBusy ? 'Connecting' : 'Connect'}
                    </AppButton>
                    {remoteError ? <p className="workstation-hardware-connect-card__error">{remoteError}</p> : null}
                  </div>
                ) : null}
              </article>
            </div>
          ) : null}

          {activeTab === 'other_computers' ? (
            <div className="workstation-hardware-connect-stack">
              <article className={joinClassNames('workstation-hardware-connect-card', otherSetupIntent && 'is-active')}>
                <div className="workstation-hardware-connect-card__main">
                  <div>
                    <h3>Another computer</h3>
                  </div>
                  <AppButton tone="secondary" type="button" onClick={() => void createOtherComputerSetupLink()} disabled={busy}>
                    {busy ? 'Creating…' : 'Get setup link'}
                  </AppButton>
                </div>
                {generatedSetupLink ? (
                  <div className="workstation-hardware-setup-link">
                    <div className="workstation-hardware-setup-link__row">
                      <code>{generatedSetupLink}</code>
                      <AppButton tone="secondary" type="button" onClick={() => void copySetupLink()}>
                        <Copy size={14} strokeWidth={2} aria-hidden="true" />
                        {copiedTarget === 'setup' ? 'Copied' : 'Copy'}
                      </AppButton>
                    </div>
                    <div className="workstation-hardware-qr-placeholder">
                      {setupQrDataUrl ? (
                        <img src={setupQrDataUrl} alt="Setup QR code" />
                      ) : (
                        <span>QR code unavailable</span>
                      )}
                    </div>
                    <button className="workstation-hardware-inline-link" type="button" onClick={() => setManualCommandOpen((open) => !open)}>
                      {manualCommandOpen ? 'Hide manual command ↑' : 'Show manual command ↓'}
                    </button>
                    {manualCommandOpen && command ? (
                      <div className="workstation-hardware-command">
                        <div className="workstation-hardware-command__header">
                          <span>Manual command</span>
                          <button type="button" onClick={() => void copyCommand()}>
                            <Copy size={14} strokeWidth={2} aria-hidden="true" />
                            {copiedTarget === 'command' ? 'Copied' : 'Copy'}
                          </button>
                        </div>
                        <pre><code>{command}</code></pre>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </article>
            </div>
          ) : null}

          <div className="workstation-hardware-list">
            {showGatewayRegistrationRows ? visibleGatewayRegistrations.map((gateway) => {
              const gatewayId = readString(gateway, 'gateway_id');
              const selected = Boolean(gatewayId && gatewayId === selectedSageGatewayId);
              const connectionStatus = selected && (trayStatusConnected(trayStatus) || localCard?.status === 'Connected')
                ? 'online'
                : gatewayConnectionStatus(gateway);
              const displayName = readString(gateway, 'display_name', 'device_id', 'gateway_id') || 'Agent Computer';
              const platform = readString(gateway, 'platform') || 'Computer';
              const busySelecting = selectionBusyGatewayId === gatewayId;
              return (
                <article key={gatewayId || displayName} className={joinClassNames('workstation-hardware-row', selected && 'is-selected')}>
                  <span className="workstation-hardware-row__icon" aria-hidden="true">
                    <Monitor size={18} strokeWidth={1.9} />
                  </span>
                  <div className="workstation-hardware-row__copy">
                    <h3>{displayName}</h3>
                    <p>{`${platform} · ${connectionStatus}${selected ? ' · Selected for Sage' : ''}`}</p>
                  </div>
                  <span className={joinClassNames('workstation-hardware-row__status', connectionStatus === 'online' && 'is-connected')}>
                    <span className={joinClassNames('workstation-hardware-row__status-dot', connectionStatus === 'online' ? 'is-connected' : 'is-offline')} />
                    <span className="workstation-hardware-row__status-text">{selected ? 'Selected' : connectionStatus}</span>
                  </span>
                  <AppButton
                    tone="secondary"
                    type="button"
                    disabled={selected || busySelecting}
                    onClick={() => void setAsSageAgentComputer(gatewayId)}
                  >
                    {selected ? 'Sage Agent Computer' : busySelecting ? 'Setting...' : 'Set as Sage Agent Computer'}
                  </AppButton>
                </article>
              );
            }) : visibleCards.length ? visibleCards.map((card) => {
              const Icon = card.kind === 'local_companion' ? Monitor : card.kind === 'cloud_computer' ? Cloud : Server;
              const expanded = expandedKind === card.kind;
              return (
                <article key={card.kind} className="workstation-hardware-row">
                  <span className="workstation-hardware-row__icon" aria-hidden="true">
                    <Icon size={18} strokeWidth={1.9} />
                  </span>
                  <div className="workstation-hardware-row__copy">
                    <h3>{card.name}</h3>
                    {card.description ? <p>{card.description}</p> : null}
                    {card.kind === 'local_companion' && trayStatusConnected(trayStatus) && capabilityChecks.length ? (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', color: 'var(--color-text-muted)', fontSize: '0.78rem' }}>
                        {capabilityChecks.map((check) => {
                          const fixTarget = capabilityFixTarget(check.name);
                          return (
                            <span key={check.name} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                              <span className={joinClassNames('workstation-hardware-row__status-dot', check.status === 'granted' && 'is-connected', check.status === 'denied' && 'is-offline')} />
                              <span>{capabilityLabel(check.name)}</span>
                              <span>{check.status === 'granted' ? '✓' : check.status === 'denied' ? '✗' : '—'}</span>
                              {check.status === 'denied' && fixTarget ? (
                                <button className="workstation-hardware-inline-link" type="button" onClick={() => window.open(fixTarget)}>
                                  Fix →
                                </button>
                              ) : null}
                            </span>
                          );
                        })}
                      </div>
                    ) : null}
                    {card.kind === 'local_companion' && trayStatusConnected(trayStatus) && hardwareActivity.length ? (
                      <div className="workstation-hardware-activity" aria-live="polite">
                        <div className="workstation-hardware-activity__list">
                          {hardwareActivity.map((item) => (
                            <div key={String(item.id || `${item.capability}:${item.timestamp}`)} className="workstation-hardware-activity__item">
                              <span>{hardwareActivityLabel(item.capability)}</span>
                              <span className={joinClassNames('workstation-hardware-activity__status', item.status === 'failed' && 'is-failed')}>
                                {item.status === 'failed' ? 'failed' : 'completed'}
                              </span>
                              <span>{timeAgo(item.timestamp)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {expanded ? (
                      <dl className="workstation-hardware-row__diagnostics">
                        <div>
                          <dt>Type</dt>
                          <dd>{card.typeLabel}</dd>
                        </div>
                        <div>
                          <dt>Capabilities</dt>
                          <dd>{card.capabilities.length ? card.capabilities.join(', ') : 'None advertised'}</dd>
                        </div>
                        <div>
                          <dt>Reason</dt>
                          <dd>{card.reason || 'No diagnostic reason reported.'}</dd>
                        </div>
                      </dl>
                    ) : null}
                  </div>
                  <span className={joinClassNames('workstation-hardware-row__status', `is-${card.status.toLowerCase().replace(/\s+/g, '-')}`)}>
                    <span className={joinClassNames('workstation-hardware-row__status-dot', `is-${card.status.toLowerCase().replace(/\s+/g, '-')}`)} />
                    <span className="workstation-hardware-row__status-text">{card.status}</span>
                  </span>
                  <AppButton tone="secondary" type="button" onClick={() => handleCardAction(card)}>
                    {card.actionLabel}
                  </AppButton>
                </article>
              );
	            }) : (
	              <div className="workstation-hardware-empty">
	                <p>{emptyCopy()}</p>
	              </div>
	            )}
          </div>
        </section>
      </div>

      {connectOpen ? (
        <div className="workstation-hardware-sheet" role="dialog" aria-modal="true" aria-label="Connect hardware">
          <button className="workstation-hardware-sheet__scrim" type="button" aria-label="Close connect hardware" onClick={closeConnect} />
          <section className="workstation-hardware-sheet__panel">
	            <header className="workstation-hardware-sheet__header">
	              <div>
	                <h2>{connectModalTitle}</h2>
	              </div>
              <button className="workstation-hardware-sheet__close" type="button" onClick={closeConnect} aria-label="Close">
                <X size={17} strokeWidth={2} />
              </button>
            </header>

	            <div className="workstation-hardware-connect-stack">
	              {showThisMacCard ? (
	              <article className={joinClassNames('workstation-hardware-connect-card', trayDetected && 'is-active')}>
	                <div className="workstation-hardware-connect-card__main">
	                  <div>
	                    <h3>This Mac</h3>
                  </div>
                  <div className="workstation-hardware-connect-card__actions">
                    {trayDetected ? (
                      <AppButton tone="primary" type="button" onClick={() => void connectThisMac()} disabled={localConnectBusy}>
                        {localConnectBusy ? <span className="workstation-hardware-spinner" aria-hidden="true" /> : null}
                        {localConnectBusy ? 'Connecting' : 'Connect'}
                      </AppButton>
                    ) : (
                      <>
                        <AppButton tone="secondary" type="button" onClick={() => window.open(AGENT_DOWNLOAD_URL, '_blank', 'noopener,noreferrer')}>
                          Download Agent
                        </AppButton>
                        <span className="workstation-hardware-connect-card__hint">Already installed? Open it and try again.</span>
                        <button
                          className="workstation-hardware-inline-link"
                          type="button"
                          onClick={() => void detectTrayAgent()}
                          disabled={trayChecking}
                        >
                          {trayChecking ? 'Checking…' : 'Retry detection'}
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {localConnectError ? <p className="workstation-hardware-connect-card__error">{localConnectError}</p> : null}
                {trayStatus?.error ? <p className="workstation-hardware-connect-card__error">{trayStatus.error}</p> : null}
	                {trayProcessConnected(trayStatus) && !trayStatusConnected(trayStatus) ? (
	                  <p className="workstation-hardware-connect-card__note">{localTrayConnectionNote(trayStatus)}</p>
	                ) : null}
	              </article>
	              ) : null}

	              {showCloudVpsCard ? (
	              <article className={joinClassNames('workstation-hardware-connect-card workstation-hardware-vps-card', selectedConnectOption === 'cloud_vps' && 'is-active')}>
	                <div className="workstation-hardware-connect-card__main">
	                  <div>
	                    <h3>Cloud VPS</h3>
                  </div>
                  <AppButton
                    tone="secondary"
                    type="button"
                    onClick={() => {
                      closeConnect();
                      openCloudVps();
                    }}
                  >
                    Choose
                  </AppButton>
                </div>
	              </article>
	              ) : null}

	              {showRemoteServerCard ? (
	              <article className={joinClassNames('workstation-hardware-connect-card', remoteExpanded && 'is-active')}>
	                <div className="workstation-hardware-connect-card__main">
	                  <div>
	                    <h3>Remote server</h3>
                  </div>
                  <AppButton tone="secondary" type="button" onClick={() => setRemoteExpanded((expanded) => !expanded)}>
                    Configure
                  </AppButton>
                </div>
                {remoteExpanded ? (
                  <div className="workstation-hardware-ssh-form">
                    <label className="app-form-field">
                      <span className="app-form-field__label">Host</span>
                      <input className="app-field" type="text" value={sshHost} onChange={(event) => setSshHost(event.target.value)} placeholder="server.example.com" />
                    </label>
                    <label className="app-form-field">
                      <span className="app-form-field__label">Port</span>
                      <input className="app-field" type="number" min="1" max="65535" value={sshPort} onChange={(event) => setSshPort(event.target.value)} />
                    </label>
                    <label className="app-form-field">
                      <span className="app-form-field__label">Username</span>
                      <input className="app-field" type="text" value={sshUsername} onChange={(event) => setSshUsername(event.target.value)} placeholder="ubuntu" />
                    </label>
                    <div className="workstation-hardware-auth-toggle" role="group" aria-label="SSH authentication">
                      <button className={joinClassNames(sshAuthMode === 'password' && 'is-active')} type="button" onClick={() => setSshAuthMode('password')}>
                        Password
                      </button>
                      <button className={joinClassNames(sshAuthMode === 'ssh_key' && 'is-active')} type="button" onClick={() => setSshAuthMode('ssh_key')}>
                        SSH Key
                      </button>
                    </div>
                    {sshAuthMode === 'password' ? (
                      <label className="app-form-field">
                        <span className="app-form-field__label">Password</span>
                        <input className="app-field" type="password" value={sshPassword} onChange={(event) => setSshPassword(event.target.value)} />
                      </label>
                    ) : (
                      <label className="app-form-field">
                        <span className="app-form-field__label">SSH key</span>
                        <textarea className="app-textarea" value={sshKey} onChange={(event) => setSshKey(event.target.value)} rows={5} />
                      </label>
                    )}
                    <AppButton tone="secondary" type="button" onClick={() => void connectRemoteServer()} disabled={remoteConnectBusy}>
                      {remoteConnectBusy ? <span className="workstation-hardware-spinner" aria-hidden="true" /> : null}
                      {remoteConnectBusy ? 'Connecting' : 'Connect'}
                    </AppButton>
                    {remoteError ? <p className="workstation-hardware-connect-card__error">{remoteError}</p> : null}
	                  </div>
	                ) : null}
	              </article>
	              ) : null}

	              {showOtherComputerCard ? (
	              <article className={joinClassNames('workstation-hardware-connect-card', otherSetupIntent && 'is-active')}>
	                <div className="workstation-hardware-connect-card__main">
	                  <div>
	                    <h3>Another computer</h3>
                  </div>
                  <AppButton tone="secondary" type="button" onClick={() => void createOtherComputerSetupLink()} disabled={busy}>
                    {busy ? 'Creating…' : 'Get setup link'}
                  </AppButton>
                </div>
                {generatedSetupLink ? (
                  <div className="workstation-hardware-setup-link">
                    <div className="workstation-hardware-setup-link__row">
                      <code>{generatedSetupLink}</code>
                      <AppButton tone="secondary" type="button" onClick={() => void copySetupLink()}>
                        <Copy size={14} strokeWidth={2} aria-hidden="true" />
                        {copiedTarget === 'setup' ? 'Copied' : 'Copy'}
                      </AppButton>
                    </div>
                    <div className="workstation-hardware-qr-placeholder">
                      {setupQrDataUrl ? (
                        <img src={setupQrDataUrl} alt="Setup QR code" />
                      ) : (
                        <span>QR code unavailable</span>
                      )}
                    </div>
                    <button className="workstation-hardware-inline-link" type="button" onClick={() => setManualCommandOpen((open) => !open)}>
                      {manualCommandOpen ? 'Hide manual command ↑' : 'Show manual command ↓'}
                    </button>
                    {manualCommandOpen && command ? (
                      <div className="workstation-hardware-command">
                        <div className="workstation-hardware-command__header">
                          <span>Manual command</span>
                          <button type="button" onClick={() => void copyCommand()}>
                            <Copy size={14} strokeWidth={2} aria-hidden="true" />
                            {copiedTarget === 'command' ? 'Copied' : 'Copy'}
                          </button>
                        </div>
                        <pre><code>{command}</code></pre>
                      </div>
                    ) : null}
	                  </div>
	                ) : null}
	              </article>
	              ) : null}
	            </div>
          </section>
        </div>
      ) : null}
      <CloudVpsSetupPanel
        open={cloudVpsOpen}
        workspaceId={workspaceId}
        initialProviderId={cloudVpsInitialProvider}
        onClose={() => {
          setCloudVpsOpen(false);
          setCloudVpsInitialProvider(null);
        }}
        onConnected={async () => {
          setCloudVpsOpen(false);
          setCloudVpsInitialProvider(null);
          await refreshHardwareAttachments();
          await refreshSageAgentComputerState();
        }}
      />
      <ConfirmDialog
        open={Boolean(pendingFullAccessPairingOption)}
        title="Full Access warning"
        body={AGENT_COMPUTER_FULL_ACCESS_WARNING_COPY}
        confirmLabel="Allow Full Access"
        cancelLabel="Cancel"
        confirmTone="danger"
        busy={localConnectBusy || remoteConnectBusy || busy}
        onConfirm={confirmFullAccessPairing}
        onCancel={() => setPendingFullAccessPairingOption(null)}
      />
    </section>
  );
}
