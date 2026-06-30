'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';

import { CommandSheet } from '@/lib/ui/command-sheet';
import { DataBadge } from '@/lib/ui/data-table';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { AppButton } from '@/lib/ui/primitives';
import {
  FormField,
  FormGrid,
  FormInput,
  FormReadout,
  FormSelect,
  FormSection,
  FormTokenListEditor,
} from '@/lib/ui/form-controls';
import {
  WorkstationActionButton,
  WorkstationSurfaceCard,
  WorkstationSurfaceList,
  WorkstationSurfaceListItem,
  WorkstationSurfaceNotice,
} from '@/lib/workspace/workstation-surface-primitives';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

type GatewayRegistrationRecord = Record<string, unknown> & {
  gateway_id?: string | null;
  display_name?: string | null;
  platform?: string | null;
  status?: string | null;
  connection_status?: string | null;
  device_trust_state?: string | null;
  capabilities?: unknown;
  runtime_access_mode?: string | null;
  runtime_access_label?: string | null;
  runtime_access_setup_warning?: string | null;
  autonomous_agent_setup_warning_acknowledged?: boolean | null;
  agent_computer_policy_id?: string | null;
  agent_computer_emergency_stop?: Record<string, unknown> | null;
  last_seen_at?: string | null;
};

type AgentComputerPolicyRecord = Record<string, unknown> & {
  policy_id?: string | null;
  autonomy_mode?: string | null;
  allowed_capabilities?: unknown;
  approval_required_capabilities?: unknown;
  blocked_capabilities?: unknown;
  filesystem_scope?: unknown;
  blocked_filesystem_scope?: unknown;
  domain_allowlist?: unknown;
  terminal_policy?: string | null;
  network_policy?: string | null;
  browser_access_policy?: string | null;
  app_access_policy?: string | null;
  approval_ttl_seconds?: number | null;
  max_runtime_seconds?: number | null;
  max_budget_cents?: number | null;
  emergency_stop_enabled?: boolean | null;
};

type AgentComputerPolicyPayload = Record<string, unknown> & {
  computer_id?: string | null;
  policy_id?: string | null;
  saved?: boolean | null;
  runtime_access_mode?: string | null;
  custom_policy_ready?: boolean | null;
  policy?: AgentComputerPolicyRecord | null;
  effective_policy?: AgentComputerPolicyRecord | null;
  emergency_stop?: Record<string, unknown> | null;
};

type AgentComputerPolicyDraft = {
  allowedFolders: string[];
  blockedFolders: string[];
  domains: string[];
  terminalPolicy: string;
  networkPolicy: string;
  browserAccessPolicy: string;
  appAccessPolicy: string;
  approvalTtlSeconds: string;
  maxRuntimeSeconds: string;
  maxBudgetDollars: string;
};

type GatewayPairingIntentRecord = Record<string, unknown> & {
  pairing_token?: string | null;
  expires_at?: string | null;
  display_name?: string | null;
  platform?: string | null;
};

type GatewayDoctorCheckRecord = Record<string, unknown> & {
  id?: string | null;
  status?: string | null;
  summary?: string | null;
};

type GatewayDoctorPayload = Record<string, unknown> & {
  status?: string | null;
  checks?: GatewayDoctorCheckRecord[];
  approvals?: Record<string, unknown> | null;
  checkpoint?: Record<string, unknown> | null;
  browser?: Record<string, unknown> | null;
  specialists?: Record<string, unknown> | null;
  providers?: Record<string, unknown> | null;
  quota?: Record<string, unknown> | null;
};

type PersonalChannelStateRecord = Record<string, unknown> & {
  status?: string | null;
  qr_code?: string | null;
  login_hint?: string | null;
  linked_name?: string | null;
  linked_jid?: string | null;
  linked_phone?: string | null;
  linked_username?: string | null;
  connected_at?: string | null;
};

type PersonalChannelViewPayload = Record<string, unknown> & {
  state?: PersonalChannelStateRecord | null;
  recent_messages?: Array<Record<string, unknown>>;
};

type GatewayApprovalRecord = Record<string, unknown> & {
  approval_id?: string | null;
  capability_id?: string | null;
  status?: string | null;
  run_id?: string | null;
  note?: string | null;
};

type GatewayApprovalsPayload = Record<string, unknown> & {
  pending_count?: number | null;
  retryable_count?: number | null;
  items?: GatewayApprovalRecord[];
};

type GatewayBrowserSessionRecord = Record<string, unknown> & {
  browser_session_id?: string | null;
  status?: string | null;
  session_profile?: string | null;
  current_url?: string | null;
  resume_supported?: boolean | null;
  manual_takeover?: boolean | null;
  reviewed_approval_required?: boolean | null;
  reviewed_approved?: boolean | null;
  last_activity_at?: string | null;
  metadata?: Record<string, unknown> | null;
};

type GatewayBrowserSessionsPayload = Record<string, unknown> & {
  items?: GatewayBrowserSessionRecord[];
};

type GatewayEventRecord = Record<string, unknown> & {
  gateway_id?: string | null;
  session_id?: string | null;
  direction?: string | null;
  frame_kind?: string | null;
  message_type?: string | null;
  payload?: Record<string, unknown> | null;
  created_at?: string | null;
};

type GatewayEventsPayload = Record<string, unknown> & {
  items?: GatewayEventRecord[];
};

type WorkspaceRuntimeSessionRecord = Record<string, unknown> & {
  session_id?: string | null;
  thread_id?: string | null;
  status?: string | null;
  channel?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  deployed_agent_id?: string | null;
  runtime_session_id?: string | null;
  runtime_binding?: string | null;
  runtime_choice?: string | null;
  runtime_provider_id?: string | null;
  runtime_provider_kind?: string | null;
  runtime_attachment_id?: string | null;
  runtime_profile_id?: string | null;
  runtime_node_id?: string | null;
  runtime_access_mode?: string | null;
  gateway_id?: string | null;
  owner_email?: string | null;
  owner_user_id?: string | null;
  runtime_lane?: string | null;
  permission_mode?: string | null;
  approval_mode?: string | null;
  approval_reviewer?: string | null;
  interactive_approvals?: boolean | null;
};

type WorkspaceRuntimeSessionsPayload = Record<string, unknown> & {
  items?: WorkspaceRuntimeSessionRecord[];
};

type RuntimeAccessMode = 'default_guarded' | 'custom' | 'full_access';

type PairingDraft = {
  displayName: string;
  platform: string;
  runtimeAccessMode: RuntimeAccessMode;
  autonomousAgentWarningAccepted: boolean;
};

type ChannelDraft = {
  phoneNumber: string;
  apiId: string;
  apiHash: string;
  loginCode: string;
  password: string;
  remoteJid: string;
  text: string;
};

type ChannelKind = 'whatsapp' | 'telegram';

type GatewayOperatorSection = 'all' | 'status' | 'channels' | 'approvals' | 'activity';
type MyComputerStatusLabel = 'Not connected' | 'Pairing' | 'Online' | 'Reconnecting' | 'Needs approval' | 'Offline' | 'Revoked';
type CapabilitySurfaceItem = {
  id: 'gateway' | 'supervisor';
  label: string;
  description: string;
  available: boolean;
};

type CertificationLaneItem = {
  id: 'phone_web' | 'gateway_truth' | 'approvals' | 'degraded_states' | 'channel_recovery';
  title: string;
  subtitle: string;
  description: string;
  tone: 'neutral' | 'success' | 'warning' | 'danger' | 'accent';
};

type GatewayPrimaryAction = {
  label: 'Connect a computer' | 'Reconnect' | 'Approve action' | 'Revoke access';
  tone: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  action:
    | { kind: 'connect' }
    | { kind: 'reconnect' }
    | { kind: 'open_approvals' }
    | { kind: 'revoke' };
};

type GatewayOperatorState = {
  id: 'not_connected' | 'pairing' | 'online' | 'reconnecting' | 'needs_approval' | 'offline' | 'revoked';
  label: MyComputerStatusLabel;
  tone: 'neutral' | 'success' | 'warning' | 'danger';
  detail: string;
  primaryAction: GatewayPrimaryAction;
};

function buildQueryString(params: Record<string, string | number | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : '';
}

function readString(value: unknown, fallback = 'Unknown'): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item ?? '').trim()).filter(Boolean)
    : [];
}

function readNumberString(value: unknown, fallback: string): string {
  const parsed = typeof value === 'number' ? value : Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? String(parsed) : fallback;
}

function defaultAgentComputerPolicyDraft(): AgentComputerPolicyDraft {
  return {
    allowedFolders: ['/'],
    blockedFolders: [],
    domains: [],
    terminalPolicy: 'allow',
    networkPolicy: 'allow',
    browserAccessPolicy: 'allow',
    appAccessPolicy: 'allow',
    approvalTtlSeconds: '900',
    maxRuntimeSeconds: '0',
    maxBudgetDollars: '0',
  };
}

function policyDraftFromPayload(payload: AgentComputerPolicyPayload | null): AgentComputerPolicyDraft {
  const policy = readRecord(payload?.policy ?? payload?.effective_policy);
  return {
    allowedFolders: readStringList(policy.filesystem_scope),
    blockedFolders: readStringList(policy.blocked_filesystem_scope),
    domains: readStringList(policy.domain_allowlist),
    terminalPolicy: readString(policy.terminal_policy, 'review_required'),
    networkPolicy: readString(policy.network_policy, 'approval_required'),
    browserAccessPolicy: readString(policy.browser_access_policy, 'approval_required'),
    appAccessPolicy: readString(policy.app_access_policy, 'approval_required'),
    approvalTtlSeconds: readNumberString(policy.approval_ttl_seconds, '900'),
    maxRuntimeSeconds: readNumberString(policy.max_runtime_seconds, '0'),
    maxBudgetDollars: (() => {
      const cents = Number.parseInt(String(policy.max_budget_cents ?? '0'), 10);
      return Number.isFinite(cents) && cents > 0 ? String(Math.round(cents) / 100) : '0';
    })(),
  };
}

function policyPayloadFromDraft(draft: AgentComputerPolicyDraft, existing: AgentComputerPolicyPayload | null): Record<string, unknown> {
  const maxBudget = Math.max(0, Math.round(Number.parseFloat(draft.maxBudgetDollars || '0') * 100));
  return {
    policy_id: readString(existing?.policy_id, readString(existing?.policy?.policy_id, '')),
    autonomy_mode: 'trusted_workstation',
    filesystem_scope: draft.allowedFolders,
    blocked_filesystem_scope: draft.blockedFolders,
    domain_allowlist: draft.domains,
    terminal_policy: draft.terminalPolicy,
    network_policy: draft.networkPolicy,
    browser_access_policy: draft.browserAccessPolicy,
    app_access_policy: draft.appAccessPolicy,
    approval_ttl_seconds: Math.max(60, Number.parseInt(draft.approvalTtlSeconds || '900', 10) || 900),
    max_runtime_seconds: Math.max(0, Number.parseInt(draft.maxRuntimeSeconds || '0', 10) || 0),
    max_budget_cents: Number.isFinite(maxBudget) ? maxBudget : 0,
    emergency_stop_enabled: true,
  };
}

function normalizeWorkspaceRuntimeSessions(payload: unknown): WorkspaceRuntimeSessionRecord[] {
  const record = readRecord(payload);
  return Array.isArray(record.items)
    ? record.items.filter((item): item is WorkspaceRuntimeSessionRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function humanizeToken(value: unknown, fallback = 'Unknown'): string {
  const token = String(value ?? '').trim();
  if (!token) {
    return fallback;
  }
  return token
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function runtimeBindingLabel(value: unknown): string {
  switch (String(value ?? '').trim().toLowerCase()) {
    case 'cloud_computer_agent':
      return 'Cloud Computer';
    case 'my_computer_agent':
      return 'This Device';
    case 'self_hosted_agent':
      return 'Server/VPS';
    default:
      return humanizeToken(value, 'Computer session');
  }
}

function gatewayEventItems(payload: GatewayEventsPayload | null): GatewayEventRecord[] {
  return Array.isArray(payload?.items)
    ? payload.items.filter((item): item is GatewayEventRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function eventTimestamp(event: GatewayEventRecord | null): number {
  if (!event) {
    return 0;
  }
  const parsed = Date.parse(readString(event.created_at, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function newestGatewayEvent(events: GatewayEventRecord[], predicate?: (event: GatewayEventRecord) => boolean): GatewayEventRecord | null {
  const filtered = predicate ? events.filter(predicate) : events;
  return filtered.reduce<GatewayEventRecord | null>((latest, event) => {
    if (!latest || eventTimestamp(event) >= eventTimestamp(latest)) {
      return event;
    }
    return latest;
  }, null);
}

function gatewayEventSearchText(event: GatewayEventRecord): string {
  const payload = readRecord(event.payload);
  return [
    event.message_type,
    event.frame_kind,
    payload.action,
    payload.capability_id,
    payload.tool_name,
    payload.status,
    payload.summary,
    payload.title,
    payload.current_url,
    payload.artifact_id,
    payload.artifact_ids,
    payload.screenshot_id,
  ].map((value) => String(value ?? '').toLowerCase()).join(' ');
}

function isProofGatewayEvent(event: GatewayEventRecord): boolean {
  const text = gatewayEventSearchText(event);
  return ['proof', 'screenshot', 'artifact', 'browser', 'screen', 'capture'].some((token) => text.includes(token));
}

function isActionGatewayEvent(event: GatewayEventRecord): boolean {
  const text = gatewayEventSearchText(event);
  if (text.includes('heartbeat')) {
    return false;
  }
  return ['tool', 'browser', 'channel', 'approval', 'execute', 'takeover', 'resume', 'interrupt', 'risk'].some((token) => text.includes(token));
}

function summarizeGatewayEvent(event: GatewayEventRecord | null, fallback: string): string {
  if (!event) {
    return fallback;
  }
  const payload = readRecord(event.payload);
  const detail = readString(
    payload.summary
      ?? payload.title
      ?? payload.note
      ?? payload.action
      ?? payload.capability_id
      ?? payload.tool_name
      ?? payload.current_url
      ?? payload.status,
    '',
  );
  const label = detail || humanizeToken(event.message_type, 'Gateway event');
  return `${label} · ${formatRelativeTimestamp(event.created_at)}`;
}

function runtimeSessionOwnerLabel(session: WorkspaceRuntimeSessionRecord): string {
  return readString(
    session.owner_email
      ?? session.owner_user_id
      ?? session.actor
      ?? session.user_id
      ?? session.workspace_id,
    'Workspace owner',
  );
}

function runtimeSessionLaneLabel(session: WorkspaceRuntimeSessionRecord): string {
  const explicitLane = readString(session.runtime_lane, '');
  if (explicitLane) {
    return humanizeToken(explicitLane);
  }
  const parts = [
    runtimeBindingLabel(session.runtime_binding),
    humanizeToken(session.runtime_choice, ''),
    readString(session.runtime_provider_id, ''),
  ].filter(Boolean);
  return parts.join(' · ') || 'Computer runtime';
}

function runtimeSessionApprovalModeLabel(session: WorkspaceRuntimeSessionRecord, pendingCount = 0): string {
  const accessMode = readString(session.runtime_access_mode, '').toLowerCase().replace(/[\s-]+/g, '_');
  if (accessMode === 'full_access') {
    return 'Full Access';
  }
  if (accessMode === 'custom') {
    return 'Custom';
  }
  if (accessMode === 'default_guarded') {
    return pendingCount > 0 ? 'Needs approval' : 'Default';
  }
  const explicitMode = readString(session.permission_mode ?? session.approval_mode, '');
  if (explicitMode) {
    return humanizeToken(explicitMode);
  }
  if (pendingCount > 0) {
    return 'Needs approval';
  }
  if (session.interactive_approvals === false) {
    return 'Runs inside runtime';
  }
  if (readString(session.approval_reviewer, '')) {
    return `Reviewed by ${humanizeToken(session.approval_reviewer)}`;
  }
  switch (String(session.runtime_binding ?? '').trim().toLowerCase()) {
    case 'my_computer_agent':
      return 'Default';
    case 'cloud_computer_agent':
      return 'Runtime capable';
    default:
      return 'Policy guarded';
  }
}

function runtimeAccessModeToken(value: unknown): RuntimeAccessMode {
  const token = readString(value, '').toLowerCase().replace(/[\s-]+/g, '_');
  if (token === 'full_access') {
    return 'full_access';
  }
  if (token === 'custom') {
    return 'custom';
  }
  return 'default_guarded';
}

function gatewayRuntimeAccessLabel(gateway: GatewayRegistrationRecord | null): string {
  const explicit = readString(gateway?.runtime_access_label, '');
  if (explicit) {
    return explicit;
  }
  const mode = runtimeAccessModeToken(gateway?.runtime_access_mode);
  if (mode === 'full_access') {
    return 'Full Access';
  }
  if (mode === 'custom') {
    return 'Custom';
  }
  return 'Default';
}

function browserTakeoverStatus(session: GatewayBrowserSessionRecord): string {
  if (session.manual_takeover) {
    return 'Takeover active';
  }
  if (session.reviewed_approval_required && !session.reviewed_approved) {
    return 'Waiting for approval';
  }
  return Boolean(session.resume_supported ?? true) ? 'Resume ready' : 'Resume unavailable';
}

function formatTimestamp(value: unknown): string {
  const token = String(value ?? '').trim();
  if (!token) {
    return 'Not available';
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(token));
  } catch {
    return token;
  }
}

function formatRelativeTimestamp(value: unknown): string {
  const token = String(value ?? '').trim();
  if (!token) {
    return 'Not available';
  }
  const parsed = Date.parse(token);
  if (!Number.isFinite(parsed)) {
    return token;
  }
  const deltaMs = Date.now() - parsed;
  const future = deltaMs < 0;
  const deltaMinutes = Math.round(Math.abs(deltaMs) / 60000);
  if (deltaMinutes < 1) {
    return future ? 'In under a minute' : 'Just now';
  }
  if (deltaMinutes < 60) {
    return future ? `In ${deltaMinutes} min` : `${deltaMinutes} min ago`;
  }
  const deltaHours = Math.round(deltaMinutes / 60);
  if (deltaHours < 24) {
    return future ? `In ${deltaHours} hr` : `${deltaHours} hr ago`;
  }
  const deltaDays = Math.round(deltaHours / 24);
  return future ? `In ${deltaDays} day${deltaDays === 1 ? '' : 's'}` : `${deltaDays} day${deltaDays === 1 ? '' : 's'} ago`;
}

function statusTone(value: unknown): 'neutral' | 'success' | 'warning' | 'danger' | 'accent' {
  const status = String(value ?? '').trim().toLowerCase();
  if (['healthy', 'active', 'connected', 'attached', 'executed', 'approved', 'pass'].includes(status)) {
    return 'success';
  }
  if (['approval_required', 'pending', 'requested', 'warn', 'waiting_for_input', 'attach_required', 'degraded'].includes(status)) {
    return 'warning';
  }
  if (['blocked', 'revoked', 'rejected', 'fail', 'attach_failed', 'offline', 'not_attached'].includes(status)) {
    return 'danger';
  }
  if (['fallback_ready'].includes(status)) {
    return 'accent';
  }
  return 'neutral';
}

function sortGatewayApprovals(items: GatewayApprovalRecord[]): GatewayApprovalRecord[] {
  return [...items].sort((left, right) => {
    const leftPending = String(left.status ?? '').trim().toLowerCase() === 'pending';
    const rightPending = String(right.status ?? '').trim().toLowerCase() === 'pending';
    if (leftPending !== rightPending) {
      return leftPending ? -1 : 1;
    }
    return String(left.approval_id ?? '').localeCompare(String(right.approval_id ?? ''));
  });
}

function sortBrowserSessions(items: GatewayBrowserSessionRecord[]): GatewayBrowserSessionRecord[] {
  return [...items].sort((left, right) => {
    const leftStatus = String(left.status ?? '').trim().toLowerCase();
    const rightStatus = String(right.status ?? '').trim().toLowerCase();
    if (leftStatus !== rightStatus) {
      if (leftStatus === 'active' || leftStatus === 'attached') {
        return -1;
      }
      if (rightStatus === 'active' || rightStatus === 'attached') {
        return 1;
      }
    }
    return String(left.browser_session_id ?? '').localeCompare(String(right.browser_session_id ?? ''));
  });
}

function summarizeWhatsappState(state: PersonalChannelStateRecord | null | undefined): string {
  if (!state) {
    return 'The selected computer has not reported WhatsApp status yet.';
  }
  if (state.qr_code) {
    return 'A WhatsApp pairing QR is ready on the selected computer.';
  }
  if (String(state.status ?? '').trim().toLowerCase() === 'connected') {
    const linkedName = readString(state.linked_name, '');
    const linkedJid = readString(state.linked_jid, '');
    const linkedLabel = linkedName || linkedJid || 'Linked account';
    return `${linkedLabel} is connected on the paired computer.`;
  }
  return `WhatsApp personal is ${humanizeToken(state.status, 'Idle')}.`;
}

function summarizeTelegramState(state: PersonalChannelStateRecord | null | undefined): string {
  if (!state) {
    return 'The selected computer has not reported Telegram status yet.';
  }
  if (state.login_hint) {
    return 'Telegram is waiting for a login code or confirmation.';
  }
  if (String(state.status ?? '').trim().toLowerCase() === 'connected') {
    const linkedName = readString(state.linked_name, '');
    const linkedUsername = readString(state.linked_username, '');
    const linkedPhone = readString(state.linked_phone, '');
    const linkedLabel = linkedName || linkedUsername || linkedPhone || 'Linked account';
    return `${linkedLabel} is connected on the paired computer.`;
  }
  return `Telegram personal is ${humanizeToken(state.status, 'Idle')}.`;
}

function summarizeDoctorFacet(value: unknown): {
  status: string;
  summary: string;
} {
  const record = readRecord(value);
  return {
    status: humanizeToken(readString(record.status, 'unknown'), 'Unknown'),
    summary: readString(record.summary, 'No detail reported yet.'),
  };
}

function gatewayPairingCommand(token: unknown, displayName?: unknown, workspaceId?: unknown): string {
  const pairingToken = readString(token, '');
  if (!pairingToken) {
    return 'Pairing token unavailable';
  }
  const deviceLabel = readString(displayName, 'Agent Computer');
  const expectedWorkspaceId = readString(workspaceId, '');
  const shellQuote = (value: string): string => `'${String(value).replace(/'/g, "'\\''")}'`;
  const normalizeGatewayApiBase = (value: string): string => {
    const trimmed = String(value || '').trim().replace(/\/+$/, '');
    if (!trimmed) {
      return 'http://127.0.0.1:8001/api';
    }
    return trimmed.endsWith('/api') ? trimmed : `${trimmed}/api`;
  };
  const isProductionLike = ['production', 'prod', 'staging'].includes(
    readString(process.env.NEXT_PUBLIC_EMPYRALIS_DEPLOY_ENV, '').toLowerCase(),
  ) || process.env.NODE_ENV === 'production';
  const apiUrl = (() => {
    const explicitRuntimeBase =
      process.env.NEXT_PUBLIC_ORION_API_URL
      ?? process.env.NEXT_PUBLIC_API_URL
      ?? '';
    if (explicitRuntimeBase.trim()) {
      return normalizeGatewayApiBase(explicitRuntimeBase);
    }
    if (typeof window === 'undefined') {
      return isProductionLike ? '' : 'http://127.0.0.1:8001/api';
    }
    const hostname = String(window.location.hostname || '').trim().toLowerCase();
    if (hostname === '127.0.0.1' || hostname === 'localhost') {
      return 'http://127.0.0.1:8001/api';
    }
    return normalizeGatewayApiBase(window.location.origin);
  })();
  if (!apiUrl) {
    return 'Computer connector API URL unavailable. Set NEXT_PUBLIC_ORION_API_URL or NEXT_PUBLIC_API_URL before pairing.';
  }
  return [
    'cd "$(git rev-parse --show-toplevel)"',
    `export EMPYRALIS_GATEWAY_API_URL=${shellQuote(apiUrl)}`,
    `export EMPYRALIS_GATEWAY_PAIRING_TOKEN=${shellQuote(pairingToken)}`,
    `export EMPYRALIS_GATEWAY_DISPLAY_NAME=${shellQuote(deviceLabel)}`,
    ...(expectedWorkspaceId ? [`export EMPYRALIS_GATEWAY_EXPECTED_WORKSPACE_ID=${shellQuote(expectedWorkspaceId)}`] : []),
    'scripts/agent_computer.sh stop',
    'scripts/agent_computer.sh install',
    'scripts/agent_computer.sh start',
  ].join('\n');
}

function detectGatewayPlatform(): string {
  if (typeof navigator === 'undefined') {
    return 'macos';
  }
  const source = `${navigator.platform || ''} ${navigator.userAgent || ''}`.toLowerCase();
  if (source.includes('win')) {
    return 'windows';
  }
  if (source.includes('linux')) {
    return 'linux';
  }
  return 'macos';
}

const GATEWAY_SETUP_STEPS = [
    {
      title: 'Install the connector',
    description: 'The connector is a small app that lets Sage use the selected Agent Computer after setup.',
    },
  {
    title: 'Pair your machine',
    description: 'Pairing creates a secure connection for this workspace. You can revoke it at any time.',
  },
  {
    title: 'Start Sage control',
    description: 'Full Access starts only after the explicit Sage warning is accepted.',
  },
] as const;

const GATEWAY_PERMISSION_CHECKLIST = [
  'Files',
  'Browser',
  'Screenshots',
  'Clipboard',
  'Terminal',
] as const;

const GATEWAY_MODE_SUMMARIES = [
  {
    title: 'Full Access',
    subtitle: 'Explicit Sage warning required',
    description: 'Lets Sage run commands, read, write, delete files, and access secrets, browser data, tokens, SSH keys, and connected accounts on this Agent Computer.',
  },
  {
    title: 'Default',
    subtitle: 'Guarded access',
    description: 'Keeps risky local actions behind policy decisions instead of granting all machine access.',
  },
  {
    title: 'Custom',
    subtitle: 'Editable guarded access',
    description: 'Uses the same guarded model as Default, with exact grants edited by the user.',
  },
] as const;

function gatewayTrustSummary(selectedGateway: GatewayRegistrationRecord | null): {
  deviceLabel: string;
  platformLabel: string;
  lastSeenLabel: string;
  lastConnectedLabel: string;
  trustLabel: string;
} {
  return {
    deviceLabel: selectedGateway
      ? readString(selectedGateway.display_name, 'This Device')
      : 'This Device',
    platformLabel: selectedGateway
      ? humanizeToken(selectedGateway.platform, 'Unknown platform')
      : 'Unknown platform',
    lastSeenLabel: formatRelativeTimestamp(selectedGateway?.last_seen_at),
    lastConnectedLabel: formatTimestamp(selectedGateway?.last_seen_at),
    trustLabel: humanizeToken(selectedGateway?.device_trust_state, selectedGateway ? 'Trusted device' : 'Not paired'),
  };
}

function summarizeMyComputerCapabilities(
  selectedGatewayCapabilities: string[],
): CapabilitySurfaceItem[] {
  const supervisorReady = selectedGatewayCapabilities.length > 0;
  return [
    {
      id: 'gateway',
      label: 'Gateway',
      description: 'Keeps the selected computer connected to Empyralis.',
      available: supervisorReady,
    },
    {
      id: 'supervisor',
      label: 'Local execution',
      description: 'Executes governed hardware actions for the selected computer.',
      available: supervisorReady,
    },
  ];
}

function summarizeMyComputerStatus(params: {
  gateways: GatewayRegistrationRecord[];
  selectedGateway: GatewayRegistrationRecord | null;
  doctorStatus: string;
  approvalsPendingCount: number;
  busyActionKey: string | null;
  pairingIntentActive: boolean;
}): GatewayOperatorState {
  const {
    gateways,
    selectedGateway,
    doctorStatus,
    approvalsPendingCount,
    busyActionKey,
    pairingIntentActive,
  } = params;
  if (gateways.length === 0) {
    if (pairingIntentActive || busyActionKey === 'pairing') {
      return {
        id: 'pairing',
        label: 'Pairing',
        tone: 'warning',
        detail: 'A short-lived connection request is ready. Open the desktop app on the computer you want to connect.',
        primaryAction: {
          label: 'Connect a computer',
          tone: 'primary',
          disabled: true,
          action: { kind: 'connect' },
        },
      };
    }
    return {
      id: 'not_connected',
      label: 'Not connected',
      tone: 'neutral',
      detail: 'Connect a computer when Empyralis needs to run on this hardware.',
      primaryAction: {
        label: 'Connect a computer',
        tone: 'primary',
        action: { kind: 'connect' },
      },
    };
  }

  const selected = selectedGateway ?? gateways[0] ?? null;
  const connection = String(selected?.connection_status ?? selected?.status ?? '').trim().toLowerCase();
  const trust = String(selected?.device_trust_state ?? '').trim().toLowerCase();
  const doctor = String(doctorStatus ?? '').trim().toLowerCase();
  const revoked = ['revoked'].includes(connection) || ['revoked'].includes(trust);
  if (revoked) {
    return {
      id: 'revoked',
      label: 'Revoked',
      tone: 'danger',
      detail: 'Access from the selected computer was revoked. Pair it again before this workspace can use local tools.',
      primaryAction: {
        label: 'Reconnect',
        tone: 'primary',
        action: { kind: 'reconnect' },
      },
    };
  }
  if (approvalsPendingCount > 0) {
    return {
      id: 'needs_approval',
      label: 'Needs approval',
      tone: 'warning',
      detail: 'Sage is waiting for an explicit approval before continuing local actions.',
      primaryAction: {
        label: 'Approve action',
        tone: 'primary',
        action: { kind: 'open_approvals' },
      },
    };
  }
  if (
    busyActionKey?.startsWith('pairing')
    || ['connecting', 'reconnecting', 'resuming', 'attach_required', 'pending'].includes(connection)
    || ['connecting', 'reconnecting', 'resuming', 'degraded', 'warning'].includes(doctor)
  ) {
    return {
      id: 'reconnecting',
      label: 'Reconnecting',
      tone: 'warning',
      detail: 'The selected computer is reconnecting or waiting for the desktop app to finish attaching.',
      primaryAction: {
        label: 'Reconnect',
        tone: 'primary',
        disabled: busyActionKey === 'pairing',
        action: { kind: 'reconnect' },
      },
    };
  }
  const online = ['online', 'connected', 'attached', 'healthy', 'active', 'pass'].includes(connection)
    || ['online', 'healthy', 'pass'].includes(doctor);
  if (online) {
    return {
      id: 'online',
      label: 'Online',
      tone: 'success',
      detail: 'Local access is on by default for ordinary Sage tasks.',
      primaryAction: {
        label: 'Revoke access',
        tone: 'danger',
        disabled: busyActionKey === `revoke:${String(selected?.gateway_id ?? '').trim()}`,
        action: { kind: 'revoke' },
      },
    };
  }
  return {
    id: 'offline',
    label: 'Offline',
    tone: 'danger',
    detail: 'The selected computer is not ready for local tasks yet.',
    primaryAction: {
      label: 'Reconnect',
      tone: 'primary',
      action: { kind: 'reconnect' },
    },
  };
}

function doctorDisplayStatus(status: unknown): { label: string; tone: 'neutral' | 'success' | 'warning' | 'danger' | 'accent' } {
  const normalized = String(status ?? '').trim().toLowerCase();
  if (['healthy', 'pass', 'online'].includes(normalized)) {
    return { label: 'Healthy', tone: 'success' };
  }
  if (['warn', 'warning', 'degraded', 'issues_found', 'offline', 'fail', 'failed', 'blocked'].includes(normalized)) {
    return { label: 'Issues found', tone: normalized === 'offline' || normalized === 'fail' || normalized === 'failed' || normalized === 'blocked' ? 'danger' : 'warning' };
  }
  return { label: 'Unknown', tone: 'neutral' };
}

function channelRecoveryLane(params: {
  whatsappState: string;
  telegramState: string;
}): CertificationLaneItem {
  const whatsapp = params.whatsappState;
  const telegram = params.telegramState;
  const states = [whatsapp, telegram].filter(Boolean);
  const configured = states.some((state) => !['', 'idle', 'unknown'].includes(state));
  const reconnecting = states.some((state) => ['connecting', 'reconnecting', 'resuming', 'pending', 'code_required', 'pairing_code_required'].includes(state));
  const failed = states.some((state) => ['offline', 'failed', 'fail', 'revoked', 'error', 'blocked'].includes(state));
  const connected = states.some((state) => ['connected', 'active', 'ready'].includes(state));
  if (failed) {
    return {
      id: 'channel_recovery',
      title: 'Channel recovery',
      subtitle: 'Needs attention',
      description: 'A personal channel is offline or blocked. Recover it here without opening developer tools.',
      tone: 'danger',
    };
  }
  if (reconnecting) {
    return {
      id: 'channel_recovery',
      title: 'Channel recovery',
      subtitle: 'Recovery in progress',
      description: 'A personal channel is reconnecting or waiting for login confirmation on the paired computer.',
      tone: 'warning',
    };
  }
  if (connected) {
    return {
      id: 'channel_recovery',
      title: 'Channel recovery',
      subtitle: 'Ready',
      description: 'Telegram and WhatsApp recovery stays available from phone and web when the selected computer is online.',
      tone: 'success',
    };
  }
  return {
    id: 'channel_recovery',
    title: 'Channel recovery',
    subtitle: configured ? 'Standby' : 'Not configured',
    description: 'Personal Channels are optional. Configure them here when Sage should use This Device to deliver messages.',
    tone: configured ? 'neutral' : 'accent',
  };
}

function buildCertificationLanes(params: {
  myComputerStatus: { label: MyComputerStatusLabel; tone: 'neutral' | 'success' | 'warning' | 'danger'; detail: string };
  doctorStatus: string;
  doctorChecks: GatewayDoctorCheckRecord[];
  approvalsPendingCount: number;
  whatsappState: string;
  telegramState: string;
}): CertificationLaneItem[] {
  const degradedChecks = params.doctorChecks.filter((check) => {
    const status = String(check.status ?? '').trim().toLowerCase();
    return ['warn', 'warning', 'degraded', 'issues_found', 'offline', 'fail', 'failed', 'blocked'].includes(status);
  });
  const degradedTone = degradedChecks.some((check) => ['offline', 'fail', 'failed', 'blocked'].includes(String(check.status ?? '').trim().toLowerCase()))
    ? 'danger'
    : degradedChecks.length > 0 || ['warn', 'warning', 'degraded', 'issues_found'].includes(params.doctorStatus)
      ? 'warning'
      : 'success';
  return [
    {
      id: 'phone_web',
      title: 'Phone/web command center',
      subtitle: 'Ready',
      description: 'Approvals, recovery, and computer status stay in the product so users do not need a terminal.',
      tone: 'success',
    },
    {
      id: 'gateway_truth',
      title: 'Computer online/offline status',
      subtitle: params.myComputerStatus.label,
      description: params.myComputerStatus.detail,
      tone: params.myComputerStatus.tone,
    },
    {
      id: 'approvals',
      title: 'Approval boundary',
      subtitle: params.approvalsPendingCount > 0 ? `${params.approvalsPendingCount} waiting` : 'Clear',
      description: params.approvalsPendingCount > 0
        ? 'Risky local or external actions are paused until the owner approves them from the product shell.'
        : 'No risky local actions are waiting right now.',
      tone: params.approvalsPendingCount > 0 ? 'warning' : 'success',
    },
    {
      id: 'degraded_states',
      title: 'Degraded-state clarity',
      subtitle: degradedChecks.length > 0 ? `${degradedChecks.length} issue${degradedChecks.length === 1 ? '' : 's'} surfaced` : 'Clear',
      description: degradedChecks.length > 0
        ? degradedChecks.map((check) => readString(check.summary, humanizeToken(check.id, 'Computer check'))).slice(0, 2).join(' · ')
        : 'Computer health, browser attach, provider reachability, and quota checks are currently readable and quiet.',
      tone: degradedTone,
    },
    channelRecoveryLane({
      whatsappState: params.whatsappState,
      telegramState: params.telegramState,
    }),
  ];
}

function gatewayCapabilityLabels(gateway: GatewayRegistrationRecord | null): string[] {
  const values = Array.isArray(gateway?.capabilities) ? gateway?.capabilities : [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const label = readString(value, '');
    if (!label || seen.has(label)) {
      continue;
    }
    seen.add(label);
    out.push(label);
  }
  return out.sort((left, right) => left.localeCompare(right));
}

function defaultChannelDraft(text: string): ChannelDraft {
  return {
    phoneNumber: '',
    apiId: '',
    apiHash: '',
    loginCode: '',
    password: '',
    remoteJid: '',
    text,
  };
}

export function WorkstationGatewayOperatorPane({
  initialSection = 'all',
}: {
  initialSection?: GatewayOperatorSection;
}) {
  const router = useRouter();
  const services = useWorkspaceServices();
  const { bootstrap } = useWorkspaceBoundary();
  const workspaceId = bootstrap.workspace.id;

  const [gateways, setGateways] = useState<GatewayRegistrationRecord[]>([]);
  const [selectedGatewayId, setSelectedGatewayId] = useState<string | null>(null);
  const [doctor, setDoctor] = useState<GatewayDoctorPayload | null>(null);
  const [whatsapp, setWhatsapp] = useState<PersonalChannelViewPayload | null>(null);
  const [telegram, setTelegram] = useState<PersonalChannelViewPayload | null>(null);
  const [approvals, setApprovals] = useState<GatewayApprovalsPayload | null>(null);
  const [browserSessions, setBrowserSessions] = useState<GatewayBrowserSessionsPayload | null>(null);
  const [gatewayEvents, setGatewayEvents] = useState<GatewayEventsPayload | null>(null);
  const [agentComputerPolicy, setAgentComputerPolicy] = useState<AgentComputerPolicyPayload | null>(null);
  const [policyDraft, setPolicyDraft] = useState<AgentComputerPolicyDraft>(() => defaultAgentComputerPolicyDraft());
  const [activeRuntimeSessions, setActiveRuntimeSessions] = useState<WorkspaceRuntimeSessionRecord[]>([]);
  const [pairingDraft, setPairingDraft] = useState<PairingDraft>({
    displayName: 'My device',
    platform: 'macos',
    runtimeAccessMode: 'full_access',
    autonomousAgentWarningAccepted: true,
  });
  const [pairingIntent, setPairingIntent] = useState<GatewayPairingIntentRecord | null>(null);
  const [loadingRegistrations, setLoadingRegistrations] = useState(true);
  const [registrationsTimedOut, setRegistrationsTimedOut] = useState(false);
  const [loadingGatewayDetail, setLoadingGatewayDetail] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [busyActionKey, setBusyActionKey] = useState<string | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [manualSetupVisible, setManualSetupVisible] = useState(false);
  const [diagnosticsVisible, setDiagnosticsVisible] = useState(false);
  const [channelDrafts, setChannelDrafts] = useState<Record<ChannelKind, ChannelDraft>>({
    whatsapp: defaultChannelDraft('Empyralis connected-computer test.'),
    telegram: defaultChannelDraft('Empyralis connected-computer test.'),
  });

  const selectedGateway = useMemo(
    () =>
      gateways.find((gateway) => String(gateway.gateway_id ?? '').trim() === String(selectedGatewayId ?? '').trim()) ?? null,
    [gateways, selectedGatewayId],
  );
  const gatewayRuntimeTarget = useMemo(
    () => bootstrap.runtime.runtimeTargets.find((target) => target.id === 'local_companion') ?? null,
    [bootstrap.runtime.runtimeTargets],
  );
  const gatewayExecutionModes = useMemo(
    () => (gatewayRuntimeTarget?.executionModes ?? []).filter((mode) => mode.available),
    [gatewayRuntimeTarget?.executionModes],
  );
  const autonomousAgentMode = useMemo(
    () => gatewayExecutionModes.find((mode) =>
      mode.id === 'full_access' || runtimeAccessModeToken(mode.runtimeAccessMode) === 'full_access') ?? null,
    [gatewayExecutionModes],
  );
  const customAccessMode = useMemo(
    () => gatewayExecutionModes.find((mode) =>
      mode.id === 'custom' || runtimeAccessModeToken(mode.runtimeAccessMode) === 'custom') ?? null,
    [gatewayExecutionModes],
  );
  const defaultAccessMode = useMemo(
    () => gatewayExecutionModes.find((mode) =>
      mode.id === 'default' || runtimeAccessModeToken(mode.runtimeAccessMode) === 'default_guarded') ?? null,
    [gatewayExecutionModes],
  );
  const autonomousAgentAvailable = Boolean(autonomousAgentMode || gatewayRuntimeTarget?.supportsFullAccess);
  const activeGatewayRuntimeAccessLabel = selectedGateway ? gatewayRuntimeAccessLabel(selectedGateway) : 'Not paired';

  const selectedGatewayCapabilities = useMemo(
    () => gatewayCapabilityLabels(selectedGateway),
    [selectedGateway],
  );
  const selectedCapabilitySet = useMemo(
    () => new Set(selectedGatewayCapabilities),
    [selectedGatewayCapabilities],
  );
  const whatsappConfigureAvailable = selectedCapabilitySet.has('channel.whatsapp.personal.configure');
  const whatsappOutboundAvailable = selectedCapabilitySet.has('channel.whatsapp.personal.outbound');
  const telegramConfigureAvailable = selectedCapabilitySet.has('channel.telegram.personal.configure');
  const telegramOutboundAvailable = selectedCapabilitySet.has('channel.telegram.personal.outbound');

  const pendingApprovals = useMemo(
    () => sortGatewayApprovals(Array.isArray(approvals?.items) ? approvals?.items : []),
    [approvals],
  );

  const browserItems = useMemo(
    () => sortBrowserSessions(Array.isArray(browserSessions?.items) ? browserSessions?.items : []),
    [browserSessions],
  );

  const gatewayEventList = useMemo(
    () => gatewayEventItems(gatewayEvents),
    [gatewayEvents],
  );

  const latestGatewayProof = useMemo(
    () => newestGatewayEvent(gatewayEventList, isProofGatewayEvent),
    [gatewayEventList],
  );

  const latestGatewayAction = useMemo(
    () => newestGatewayEvent(gatewayEventList, isActionGatewayEvent),
    [gatewayEventList],
  );

  const activeRuntimeItems = useMemo(
    () => [...activeRuntimeSessions].sort((left, right) =>
      readString(right.created_at, '').localeCompare(readString(left.created_at, '')),
    ),
    [activeRuntimeSessions],
  );

  useEffect(() => {
    setPairingDraft((current) => ({
      ...current,
      platform: detectGatewayPlatform(),
    }));
  }, []);

  useEffect(() => {
    if (!loadingRegistrations) {
      setRegistrationsTimedOut(false);
      return () => {};
    }
    const timeoutId = window.setTimeout(() => {
      setRegistrationsTimedOut(true);
      setLoadingRegistrations(false);
    }, 5_000);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [loadingRegistrations]);

  async function requestPayload<T>(
    path: string,
    init?: RequestInit,
    allowStatuses: number[] = [],
  ): Promise<T | null> {
    return services.client.requestJson<T>({
      path,
      init,
      allowStatuses,
    });
  }

  async function requestOptionalPayload<T>(path: string): Promise<T | null> {
    try {
      return await requestPayload<T>(path, undefined, [403, 404]);
    } catch {
      return null;
    }
  }

  async function copyToClipboard(label: string, value: string): Promise<void> {
    const text = readString(value, '');
    if (!text) {
      setErrorMessage(`${label} is not available yet.`);
      return;
    }
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', 'true');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setErrorMessage(null);
      setStatusMessage(`${label} copied. Paste it into Terminal and press Return.`);
    } catch {
      setErrorMessage(`Could not copy ${label.toLowerCase()}.`);
    }
  }

  async function refreshRegistrations(showLoading = false): Promise<void> {
    if (showLoading) {
      setRegistrationsTimedOut(false);
      setLoadingRegistrations(true);
    }
    const payload = await requestPayload<Record<string, unknown>>(
      `/api/gateway/registrations${buildQueryString({ workspace_id: workspaceId })}`,
    );
    const items = Array.isArray(payload?.items)
      ? payload.items.filter((item): item is GatewayRegistrationRecord => Boolean(item) && typeof item === 'object')
      : [];
    setGateways(items);
    setLoadingRegistrations(false);
    setSelectedGatewayId((current) => {
      if (current && items.some((item) => String(item.gateway_id ?? '').trim() === current)) {
        return current;
      }
      return String(items[0]?.gateway_id ?? '').trim() || null;
    });
  }

  async function refreshRuntimeSessions(): Promise<void> {
    const payload = await services.client.listWorkspaceRuntimeSessions({ limit: 100 }) as WorkspaceRuntimeSessionsPayload;
    setActiveRuntimeSessions(normalizeWorkspaceRuntimeSessions(payload));
  }

  async function refreshGatewayDetail(gatewayId: string, showLoading = false, forceProviderProbe = false): Promise<void> {
    if (!gatewayId) {
      setDoctor(null);
      setWhatsapp(null);
      setTelegram(null);
      setApprovals(null);
      setBrowserSessions(null);
      setGatewayEvents(null);
      setAgentComputerPolicy(null);
      setPolicyDraft(defaultAgentComputerPolicyDraft());
      return;
    }
    if (showLoading) {
      setLoadingGatewayDetail(true);
    }
    const nextDoctor = await requestPayload<GatewayDoctorPayload>(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/doctor${forceProviderProbe ? '?force_provider_probe=1' : ''}`,
    );
    const [
      nextWhatsapp,
      nextTelegram,
      nextApprovals,
      nextBrowserSessions,
      nextGatewayEvents,
      nextAgentComputerPolicy,
    ] = await Promise.all([
      requestOptionalPayload<PersonalChannelViewPayload>(
        `/api/personal-channels/whatsapp/gateways/${encodeURIComponent(gatewayId)}`,
      ),
      requestOptionalPayload<PersonalChannelViewPayload>(
        `/api/personal-channels/telegram/gateways/${encodeURIComponent(gatewayId)}`,
      ),
      requestOptionalPayload<GatewayApprovalsPayload>(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/approvals`,
      ),
      requestOptionalPayload<GatewayBrowserSessionsPayload>(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/browser/sessions`,
      ),
      requestOptionalPayload<GatewayEventsPayload>(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/events?limit=500`,
      ),
      requestOptionalPayload<AgentComputerPolicyPayload>(
        `/api/agent-computers/${encodeURIComponent(gatewayId)}/policy${buildQueryString({ workspace_id: workspaceId })}`,
      ),
    ]);
    setDoctor(nextDoctor);
    setWhatsapp(nextWhatsapp);
    setTelegram(nextTelegram);
    setApprovals(nextApprovals);
    setBrowserSessions(nextBrowserSessions);
    setGatewayEvents(nextGatewayEvents);
    setAgentComputerPolicy(nextAgentComputerPolicy);
    setPolicyDraft(policyDraftFromPayload(nextAgentComputerPolicy));
    setErrorMessage(null);
    setLoadingGatewayDetail(false);
  }

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      refreshRegistrations(true),
      refreshRuntimeSessions(),
    ]).catch((error) => {
      if (cancelled) {
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : 'Device runtimes are unavailable right now.');
      setLoadingRegistrations(false);
    });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    const intervalId = window.setInterval(() => {
      void refreshRuntimeSessions().catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : 'Computer runtime sessions are unavailable right now.');
        }
      });
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [workspaceId]);

  useEffect(() => {
    if (!selectedGatewayId) {
      setDoctor(null);
      setWhatsapp(null);
      setTelegram(null);
      setApprovals(null);
      setBrowserSessions(null);
      setGatewayEvents(null);
      setAgentComputerPolicy(null);
      setPolicyDraft(defaultAgentComputerPolicyDraft());
      return;
    }
    let cancelled = false;
    void refreshGatewayDetail(selectedGatewayId, true).catch((error) => {
      if (cancelled) {
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : 'Device connection state is unavailable right now.');
      setLoadingGatewayDetail(false);
    });
    const intervalId = window.setInterval(() => {
      void refreshGatewayDetail(selectedGatewayId, false).catch((error) => {
        if (cancelled) {
          return;
        }
        setErrorMessage(error instanceof Error ? error.message : 'Device connection state is unavailable right now.');
      });
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [selectedGatewayId]);

  async function handleCreatePairingIntent() {
    if (pairingDraft.runtimeAccessMode === 'full_access' && !pairingDraft.autonomousAgentWarningAccepted) {
      setErrorMessage('Confirm Full Access before creating this computer connection.');
      return;
    }
    setBusyActionKey('pairing');
    setErrorMessage(null);
    try {
      const payload = await requestPayload<GatewayPairingIntentRecord>(
        '/api/gateway/pairings/intents',
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            display_name: pairingDraft.displayName.trim() || undefined,
            platform: pairingDraft.platform || undefined,
            runtime_access_mode: pairingDraft.runtimeAccessMode,
            autonomous_agent_setup_warning_acknowledged: pairingDraft.runtimeAccessMode === 'full_access'
              ? pairingDraft.autonomousAgentWarningAccepted
              : false,
          }),
        },
      );
      setPairingIntent(payload);
      setStatusMessage('Computer pairing is ready. Run the command below on the computer you want to connect to this workspace.');
      await refreshRegistrations(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not create a computer pairing token.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleResolveApproval(approvalId: string, decision: 'approved' | 'rejected') {
    if (!selectedGatewayId) {
      return;
    }
    setBusyActionKey(`approval:${approvalId}:${decision}`);
    setErrorMessage(null);
    try {
      await requestPayload<Record<string, unknown>>(
        `/api/gateway/registrations/${encodeURIComponent(selectedGatewayId)}/approvals/${encodeURIComponent(approvalId)}/resolve`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            decision,
            note: decision === 'approved'
              ? 'Approved from the device connection page.'
              : 'Rejected from the device connection page.',
          }),
        },
      );
      setStatusMessage(
        decision === 'approved'
          ? 'Permission accepted and the blocked work resumed.'
          : 'Permission rejected and the blocked work stopped.',
      );
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not resolve the local permission request.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleRevokeGateway() {
    if (!selectedGatewayId || !selectedGateway) {
      return;
    }
    const deviceLabel = readString(selectedGateway.display_name, 'the selected computer');
    const confirmed = window.confirm(
      `Revoke ${deviceLabel}? This workspace will stop using that computer until it is paired again.`,
    );
    if (!confirmed) {
      return;
    }
    setBusyActionKey(`revoke:${selectedGatewayId}`);
    setErrorMessage(null);
    try {
      await requestPayload<Record<string, unknown>>(
        `/api/gateway/registrations/${encodeURIComponent(selectedGatewayId)}/revoke`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            reason: 'Revoked from the device connection page.',
          }),
        },
      );
      setStatusMessage(`${deviceLabel} revoked. Local tools from that computer are now unavailable.`);
      setSelectedGatewayId(null);
      setDoctor(null);
      setWhatsapp(null);
      setTelegram(null);
      setApprovals(null);
      setBrowserSessions(null);
      setGatewayEvents(null);
      setAgentComputerPolicy(null);
      setPolicyDraft(defaultAgentComputerPolicyDraft());
      await refreshRegistrations(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not revoke the selected computer.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleSaveAgentComputerPolicy() {
    if (!selectedGatewayId) {
      setErrorMessage('Select an Agent Computer before saving a custom policy.');
      return;
    }
    setBusyActionKey('policy:save');
    setErrorMessage(null);
    try {
      const payload = await requestPayload<AgentComputerPolicyPayload>(
        `/api/agent-computers/${encodeURIComponent(selectedGatewayId)}/policy`,
        {
          method: 'PUT',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            policy: policyPayloadFromDraft(policyDraft, agentComputerPolicy),
          }),
        },
      );
      setAgentComputerPolicy(payload);
      setPolicyDraft(policyDraftFromPayload(payload));
      setStatusMessage('Custom Agent Computer policy saved.');
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not save the Agent Computer policy.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleAgentComputerEmergencyStop(clear = false) {
    if (!selectedGatewayId) {
      return;
    }
    const action = clear ? 'clear-emergency-stop' : 'emergency-stop';
    const confirmed = clear || window.confirm('Stop this Agent Computer now? Running local work will be interrupted.');
    if (!confirmed) {
      return;
    }
    setBusyActionKey(clear ? 'policy:clear-stop' : 'policy:stop');
    setErrorMessage(null);
    try {
      const payload = await requestPayload<AgentComputerPolicyPayload>(
        `/api/agent-computers/${encodeURIComponent(selectedGatewayId)}/${action}`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            reason: clear ? 'Cleared from Agent Computer settings.' : 'Stopped from Agent Computer settings.',
          }),
        },
      );
      setAgentComputerPolicy(payload);
      setPolicyDraft(policyDraftFromPayload(payload));
      setStatusMessage(clear ? 'Emergency stop cleared.' : 'Agent Computer stopped.');
      await refreshRegistrations(false);
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not update the emergency stop state.');
    } finally {
      setBusyActionKey(null);
    }
  }

  function updateChannelDraft(channel: ChannelKind, patch: Partial<ChannelDraft>) {
    setChannelDrafts((current) => ({
      ...current,
      [channel]: {
        ...current[channel],
        ...patch,
      },
    }));
  }

  async function handleConfigureWhatsApp() {
    if (!selectedGatewayId) {
      return;
    }
    const draft = channelDrafts.whatsapp;
    if (!draft.phoneNumber.trim()) {
      setErrorMessage('Enter the WhatsApp phone number before requesting pairing.');
      return;
    }
    setBusyActionKey('channel:whatsapp:setup');
    setErrorMessage(null);
    try {
      await requestPayload<Record<string, unknown>>(
        `/api/personal-channels/whatsapp/gateways/${encodeURIComponent(selectedGatewayId)}/setup`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            phone_number: draft.phoneNumber.trim(),
          }),
        },
      );
      setStatusMessage('WhatsApp pairing requested. Check the channel state for QR or pairing-code instructions.');
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'WhatsApp pairing could not be requested.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleConfigureTelegram() {
    if (!selectedGatewayId) {
      return;
    }
    const draft = channelDrafts.telegram;
    const apiId = Number.parseInt(draft.apiId.trim(), 10);
    const payload: Record<string, unknown> = {};
    if (Number.isFinite(apiId)) {
      payload.api_id = apiId;
    }
    if (draft.apiHash.trim()) {
      payload.api_hash = draft.apiHash.trim();
    }
    if (draft.phoneNumber.trim()) {
      payload.phone_number = draft.phoneNumber.trim();
    }
    if (draft.loginCode.trim()) {
      payload.login_code = draft.loginCode.trim();
    }
    if (draft.password.trim()) {
      payload.password = draft.password.trim();
    }
    if (Object.keys(payload).length === 0) {
      setErrorMessage('Enter Telegram API details, phone number, or login code before requesting setup.');
      return;
    }
    setBusyActionKey('channel:telegram:setup');
    setErrorMessage(null);
    try {
      await requestPayload<Record<string, unknown>>(
        `/api/personal-channels/telegram/gateways/${encodeURIComponent(selectedGatewayId)}/setup`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify(payload),
        },
      );
      setStatusMessage('Telegram setup requested. Check the channel state for login-code or confirmation instructions.');
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Telegram setup could not be requested.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleSendChannelTest(channel: ChannelKind) {
    if (!selectedGatewayId) {
      return;
    }
    const draft = channelDrafts[channel];
    if (!draft.remoteJid.trim() || !draft.text.trim()) {
      setErrorMessage(`Enter a ${channel === 'whatsapp' ? 'WhatsApp' : 'Telegram'} recipient and test message first.`);
      return;
    }
    const endpointChannel = channel === 'whatsapp' ? 'whatsapp' : 'telegram';
    const actionKey = `channel:${channel}:send-test`;
    setBusyActionKey(actionKey);
    setErrorMessage(null);
    try {
      await requestPayload<Record<string, unknown>>(
        `/api/personal-channels/${endpointChannel}/gateways/${encodeURIComponent(selectedGatewayId)}/messages`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            remote_jid: draft.remoteJid.trim(),
            text: draft.text.trim(),
            idempotency_key: `gateway-operator-${channel}-${Date.now().toString(36)}`,
          }),
        },
      );
      setStatusMessage(`${channel === 'whatsapp' ? 'WhatsApp' : 'Telegram'} test message dispatched through the selected computer.`);
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'The channel test message could not be sent.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleBrowserControl(
    browserSessionId: string,
    action: 'takeover' | 'resume' | 'interrupt',
  ) {
    if (!selectedGatewayId) {
      return;
    }
    const actionKey = `browser:${browserSessionId}:${action}`;
    setBusyActionKey(actionKey);
    setErrorMessage(null);
    try {
      const payload = await requestPayload<Record<string, unknown>>(
        `/api/gateway/registrations/${encodeURIComponent(selectedGatewayId)}/browser/sessions/${encodeURIComponent(browserSessionId)}/${action}`,
        {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            run_id: `gateway-operator-${action}-${Date.now().toString(36)}`,
            trace_id: `gateway-operator-${action}-${Date.now().toString(36)}`,
            note: action === 'interrupt'
              ? 'Interrupted from the device connection page.'
              : action === 'takeover'
                ? 'Takeover requested from the device connection page.'
                : 'Resume requested from the device connection page.',
          }),
        },
      );
      const resultStatus = readString((payload as Record<string, unknown> | null)?.status, humanizeToken(action));
      setStatusMessage(`${humanizeToken(action)} request sent. Browser status: ${resultStatus}.`);
      await refreshGatewayDetail(selectedGatewayId, false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not control the local browser session.');
    } finally {
      setBusyActionKey(null);
    }
  }

  async function handleStopRuntimeSession(session: WorkspaceRuntimeSessionRecord) {
    const sessionId = readString(session.session_id, '');
    const deployedAgentId = readString(session.deployed_agent_id, '');
    if (!sessionId || !deployedAgentId) {
      setErrorMessage('This runtime session cannot be stopped because it is missing its deployed agent binding.');
      return;
    }
    const actionKey = `runtime:${sessionId}:stop`;
    setBusyActionKey(actionKey);
    setErrorMessage(null);
    try {
      await services.client.killDeployedAgentRuntimeSession({
        deployedAgentId,
        sessionId,
      });
      setStatusMessage('Computer runtime session stopped.');
      await refreshRuntimeSessions();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not stop the computer runtime session.');
    } finally {
      setBusyActionKey(null);
    }
  }

  const doctorChecks = Array.isArray(doctor?.checks)
    ? doctor.checks.filter((item): item is GatewayDoctorCheckRecord => Boolean(item) && typeof item === 'object')
    : [];

  const doctorStatus = readString(doctor?.status, selectedGateway ? readString(selectedGateway.status, 'unknown') : 'unknown');
  const doctorStatusDisplay = doctorDisplayStatus(doctorStatus);
  const checkpointStatus = summarizeDoctorFacet(doctor?.checkpoint);
  const browserLaneStatus = summarizeDoctorFacet(doctor?.browser);
  const specialistStatus = summarizeDoctorFacet(doctor?.specialists);
  const providerStatus = summarizeDoctorFacet(doctor?.providers);
  const quotaStatus = summarizeDoctorFacet(doctor?.quota);
  const approvalsPendingCount = Number(approvals?.pending_count ?? 0);
  const browserSessionCount = browserItems.length;
  const latestProofSummary = summarizeGatewayEvent(latestGatewayProof, 'No proof reported yet');
  const latestActionSummary = summarizeGatewayEvent(latestGatewayAction, 'No action reported yet');
  const selectedRuntimeSession = activeRuntimeItems.find((session) => {
    const sessionGatewayId = readString(session.gateway_id, '');
    return sessionGatewayId && sessionGatewayId === readString(selectedGatewayId, '');
  }) ?? activeRuntimeItems[0] ?? null;
  const selectedRuntimeOwner = selectedRuntimeSession
    ? runtimeSessionOwnerLabel(selectedRuntimeSession)
    : 'Workspace owner';
  const selectedRuntimeLane = selectedRuntimeSession
    ? runtimeSessionLaneLabel(selectedRuntimeSession)
    : runtimeBindingLabel(selectedGateway ? 'my_computer_agent' : '');
  const selectedApprovalMode = selectedRuntimeSession
    ? runtimeSessionApprovalModeLabel(selectedRuntimeSession, approvalsPendingCount)
    : approvalsPendingCount > 0
      ? 'Needs approval'
      : selectedGateway
        ? activeGatewayRuntimeAccessLabel
        : 'No active computer session';
  const selectedEmergencyStop = readRecord(agentComputerPolicy?.emergency_stop);
  const emergencyStopActive = Boolean(selectedEmergencyStop.active);
  const customPolicySaved = Boolean(agentComputerPolicy?.saved);
  const customPolicyStateLabel = customPolicySaved ? 'Custom saved' : 'Guarded until saved';
  const selectedTakeoverStatus = browserItems[0]
    ? browserTakeoverStatus(browserItems[0])
    : 'No browser session';
  const whatsappRecentCount = Array.isArray(whatsapp?.recent_messages) ? whatsapp.recent_messages.length : 0;
  const telegramRecentCount = Array.isArray(telegram?.recent_messages) ? telegram.recent_messages.length : 0;
  const specialistItems = Array.isArray(readRecord(doctor?.specialists).items)
    ? (readRecord(doctor?.specialists).items as Record<string, unknown>[])
    : [];
  const providerItems = Array.isArray(readRecord(doctor?.providers).items)
    ? (readRecord(doctor?.providers).items as Record<string, unknown>[])
    : [];
  const myComputerStatus = summarizeMyComputerStatus({
    gateways,
    selectedGateway,
    doctorStatus,
    approvalsPendingCount,
    busyActionKey,
    pairingIntentActive: Boolean(pairingIntent),
  });
  const trustSummary = gatewayTrustSummary(selectedGateway);
  const platformToken = String(selectedGateway?.platform ?? pairingDraft.platform ?? '').trim().toLowerCase();
  const normalDeviceTitle = selectedGateway
    ? trustSummary.deviceLabel
    : platformToken.includes('mac') || platformToken.includes('darwin')
      ? 'Connect a Mac'
      : platformToken.includes('windows')
        ? 'Connect a Windows PC'
        : platformToken.includes('linux')
          ? 'Connect a Linux computer'
          : 'Connect a computer';
  const normalTrustLabel = myComputerStatus.id === 'online'
    ? (['Trusted', 'Verified'].includes(trustSummary.trustLabel) ? 'Verified' : trustSummary.trustLabel)
    : trustSummary.trustLabel;
  const normalDeviceLine = `${myComputerStatus.label} · ${normalTrustLabel} · Last seen ${trustSummary.lastSeenLabel}`;
  const certificationLanes = buildCertificationLanes({
    myComputerStatus,
    doctorStatus: String(doctorStatus ?? '').trim().toLowerCase(),
    doctorChecks,
    approvalsPendingCount,
    whatsappState: String(whatsapp?.state?.status ?? (whatsapp?.state?.qr_code ? 'pending' : 'idle')).trim().toLowerCase(),
    telegramState: String(telegram?.state?.status ?? (telegram?.state?.login_hint ? 'pending' : 'idle')).trim().toLowerCase(),
  });
  const myComputerCapabilities = summarizeMyComputerCapabilities(selectedGatewayCapabilities);
  const showStatusSection = initialSection === 'all' || initialSection === 'status';
  const showChannelsSection = initialSection === 'all' || initialSection === 'channels';
  const showApprovalsSection = initialSection === 'all' || initialSection === 'approvals';
  const showActivitySection = initialSection === 'all' || initialSection === 'activity';
  const expandedDetailVisible = diagnosticsVisible || initialSection !== 'all';
  const diagnosticsSectionVisible = diagnosticsVisible;
  const pairingDeviceLabel = pairingDraft.platform === 'macos'
    ? 'Mac'
    : pairingDraft.platform === 'windows'
      ? 'Windows PC'
      : pairingDraft.platform === 'linux'
        ? 'Linux computer'
        : 'computer';

  const handlePrimaryAction = () => {
    switch (myComputerStatus.primaryAction.action.kind) {
      case 'connect':
      case 'reconnect':
        void handleCreatePairingIntent();
        return;
      case 'open_approvals':
        if (showApprovalsSection) {
          const firstPendingId = String(pendingApprovals[0]?.approval_id ?? '').trim();
          if (firstPendingId && typeof document !== 'undefined') {
            document.getElementById(`gateway-approval-${firstPendingId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          return;
        }
        router.push(`/w/${encodeURIComponent(workspaceId)}/gateway-approvals`);
        return;
      case 'revoke':
        void handleRevokeGateway();
        return;
      default:
    }
  };

  return (
    <div className="gateway-operator-pane app-stack-3">
      <section className="gateway-computer-sheet sage-computer-connect-modal">
        <div className="gateway-computer-sheet__header">
          <div>
            <h2>Connect a computer</h2>
            <p>Agent Computer connects this hardware to Empyralis for governed local execution.</p>
          </div>
          <AppButton
            type="button"
            tone="ghost"
            onClick={() => {
              router.push(`/w/${encodeURIComponent(workspaceId)}/integrations`);
            }}
          >
            Close
          </AppButton>
        </div>
        <div className="sage-computer-connect">
          <div className="sage-computer-connect__hero">
            <div className="sage-computer-connect__orb" aria-hidden="true">
              <span>Connected Computer</span>
            </div>
            <div className="sage-computer-connect__copy">
              <span className={`sage-computer-connect__status${myComputerStatus.id === 'online' ? ' sage-computer-connect__status--online' : ''}`}>
                {myComputerStatus.id === 'online' ? 'Online and ready' : myComputerStatus.label}
              </span>
              <strong>Selected hardware runtime.</strong>
              <p>
                The connected computer is treated as full hardware. Agent Computer keeps it connected and carries out governed local work.
              </p>
            </div>
          </div>
          <div className="sage-computer-connect__capabilities" aria-label="Agent Computer components">
            {['Connection', 'Local execution'].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <div className="sage-computer-connect__state">
            <div>
              <span>Status</span>
              <strong>{myComputerStatus.label}</strong>
            </div>
            <div>
              <span>Trust</span>
              <strong>{trustSummary.trustLabel}</strong>
            </div>
            <div>
              <span>Mode</span>
              <strong>{activeGatewayRuntimeAccessLabel}</strong>
            </div>
            <div>
              <span>Last seen</span>
              <strong>{trustSummary.lastSeenLabel}</strong>
            </div>
            <div>
              <span>Policy</span>
              <strong>{customPolicyStateLabel}</strong>
            </div>
            <div>
              <span>Stop</span>
              <strong>{emergencyStopActive ? 'Stopped' : 'Ready'}</strong>
            </div>
          </div>
          <details className="sage-computer-connect__config">
            <summary>Connection details</summary>
            <p>
              {selectedGateway
                ? `${normalDeviceTitle} · ${normalDeviceLine}. Use Manage only for reconnecting, revoking access, manual setup, or diagnostics.`
                : 'Connection details are only needed for setup or troubleshooting. Normal use starts from the connect button.'}
            </p>
          </details>
        </div>

        {!diagnosticsSectionVisible ? (
          <div className="gateway-computer-sheet__footer">
            <AppButton
              type="button"
              tone="ghost"
              onClick={() => {
                router.push(`/w/${encodeURIComponent(workspaceId)}/sage`);
              }}
            >
              Not now
            </AppButton>
            <AppButton
              type="button"
              onClick={() => {
                setManageOpen(true);
              }}
            >
              {selectedGateway ? 'Manage computer' : 'Connect a computer'}
            </AppButton>
          </div>
        ) : null}

        {diagnosticsSectionVisible ? (
          <FormSection
          title="Agent Computer components"
          description="Implementation status for the selected computer."
        >
          {statusMessage ? <WorkstationSurfaceNotice tone="success">{statusMessage}</WorkstationSurfaceNotice> : null}
          {errorMessage ? <WorkstationSurfaceNotice tone="danger">{errorMessage}</WorkstationSurfaceNotice> : null}
          <WorkstationSurfaceList>
            {myComputerCapabilities.map((capability) => (
              <WorkstationSurfaceListItem
                key={capability.id}
                title={capability.label}
                description={capability.description}
                actions={(
                  <DataBadge tone={capability.available ? 'success' : 'neutral'}>
                    {capability.available ? 'Available' : 'Not available'}
                  </DataBadge>
                )}
              />
            ))}
          </WorkstationSurfaceList>
          </FormSection>
        ) : null}

        {manualSetupVisible ? (
          <FormSection
          title={`Connect or reconnect a ${pairingDeviceLabel}`}
          description="Choose how much autonomy this computer has before creating the short-lived connection request."
        >
          <FormGrid columns="repeat(2, minmax(0, 1fr))">
            <FormField label="Device label" hint="Human-readable name shown in operator surfaces.">
              <FormInput
                value={pairingDraft.displayName}
                onChange={(event) => {
                  setPairingDraft((current) => ({
                    ...current,
                    displayName: event.currentTarget.value,
                  }));
                }}
                placeholder="My MacBook"
              />
            </FormField>
            <FormReadout
              label="Platform"
              value={`${humanizeToken(pairingDraft.platform, 'Detected device')} · auto-detected`}
            />
            <FormField
              label="Runtime mode"
              hint={pairingDraft.runtimeAccessMode === 'full_access'
                ? 'For dedicated agent hardware. Empyralis will not ask for each allowed action.'
                : pairingDraft.runtimeAccessMode === 'custom'
                  ? 'Policy-defined. Starts approval-gated until a custom policy is saved.'
                  : 'For personal computers. Risky actions can still ask for approval.'}
            >
              <FormSelect
                value={pairingDraft.runtimeAccessMode}
                onChange={(event) => {
                  const nextMode = runtimeAccessModeToken(event.currentTarget.value);
                  setPairingDraft((current) => ({
                    ...current,
                    runtimeAccessMode: nextMode,
                    autonomousAgentWarningAccepted: nextMode === 'full_access'
                      ? current.autonomousAgentWarningAccepted
                      : false,
                  }));
                  setPairingIntent(null);
                }}
              >
                <option value="full_access" disabled={!autonomousAgentAvailable}>
                  {autonomousAgentMode?.label || 'Full Access'}
                </option>
                <option value="default_guarded">{defaultAccessMode?.label || 'Default'}</option>
                <option value="custom">
                  {customAccessMode?.label || 'Custom'}
                </option>
              </FormSelect>
            </FormField>
            <FormReadout
              label="Current mode"
              value={selectedGateway ? activeGatewayRuntimeAccessLabel : 'Not paired'}
            />
          </FormGrid>
          {pairingDraft.runtimeAccessMode === 'full_access' ? (
            <div className="gateway-runtime-access-warning">
              <WorkstationSurfaceNotice tone="warning">
                {autonomousAgentMode?.setupWarning
                  || 'Full Access lets Sage run commands, read, write, delete files, and access secrets, browser data, tokens, SSH keys, and connected accounts on this Agent Computer; dedicated hardware is recommended.'}
              </WorkstationSurfaceNotice>
              <label className="gateway-runtime-access-warning__ack">
                <input
                  type="checkbox"
                  checked={pairingDraft.autonomousAgentWarningAccepted}
                  onChange={(event) => {
                    const checked = event.currentTarget.checked;
                    setPairingDraft((current) => ({
                      ...current,
                      autonomousAgentWarningAccepted: checked,
                    }));
                    setPairingIntent(null);
                  }}
                />
                <span>I understand Sage can read, write, delete, run commands, access secrets, browser data, tokens, SSH keys, and connected accounts on this computer.</span>
              </label>
            </div>
          ) : null}
          {pairingDraft.runtimeAccessMode === 'custom' ? (
            <WorkstationSurfaceNotice tone="neutral">
              Custom starts approval-gated. Use it for policy-defined access to folders, terminal/network rules, app/browser access, approval memory, budget, and emergency stop behavior.
            </WorkstationSurfaceNotice>
          ) : null}
          {selectedGateway ? (
            <FormSection
              title="Custom access policy"
              description="Custom starts from Default and only changes after a saved policy."
            >
              <FormGrid columns="repeat(2, minmax(0, 1fr))">
                <FormField label="Allowed folders" hint="Add explicit folder paths this Agent Computer may use.">
                  <FormTokenListEditor
                    value={policyDraft.allowedFolders}
                    onChange={(allowedFolders) => setPolicyDraft((current) => ({ ...current, allowedFolders }))}
                    placeholder="/Users/mansur/Work"
                    addLabel="Add folder"
                    emptyLabel="No folder access saved."
                  />
                </FormField>
                <FormField label="Blocked folders" hint="These paths stay blocked even inside an allowed folder.">
                  <FormTokenListEditor
                    value={policyDraft.blockedFolders}
                    onChange={(blockedFolders) => setPolicyDraft((current) => ({ ...current, blockedFolders }))}
                    placeholder="/Users/mansur/Work/secrets"
                    addLabel="Block folder"
                    emptyLabel="No blocked folders saved."
                  />
                </FormField>
                <FormField label="Network domains" hint="Leave empty to keep network actions approval-gated.">
                  <FormTokenListEditor
                    value={policyDraft.domains}
                    onChange={(domains) => setPolicyDraft((current) => ({ ...current, domains }))}
                    placeholder="example.com"
                    addLabel="Add domain"
                    emptyLabel="No domains allowlisted."
                  />
                </FormField>
                <FormField label="Terminal policy">
                  <FormSelect
                    value={policyDraft.terminalPolicy}
                    onChange={(event) => {
                      const terminalPolicy = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, terminalPolicy }));
                    }}
                  >
                    <option value="allow">Allow</option>
                    <option value="blocked">Blocked</option>
                    <option value="review_required">Review required</option>
                    <option value="allowlist">Approved scripts only</option>
                  </FormSelect>
                </FormField>
                <FormField label="Network policy">
                  <FormSelect
                    value={policyDraft.networkPolicy}
                    onChange={(event) => {
                      const networkPolicy = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, networkPolicy }));
                    }}
                  >
                    <option value="allow">Allow</option>
                    <option value="approval_required">Approval required</option>
                    <option value="allowlist">Allowlisted domains</option>
                    <option value="blocked">Blocked</option>
                  </FormSelect>
                </FormField>
                <FormField label="Browser access">
                  <FormSelect
                    value={policyDraft.browserAccessPolicy}
                    onChange={(event) => {
                      const browserAccessPolicy = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, browserAccessPolicy }));
                    }}
                  >
                    <option value="approval_required">Approval required</option>
                    <option value="allow">Allow within policy</option>
                    <option value="blocked">Blocked</option>
                  </FormSelect>
                </FormField>
                <FormField label="App access">
                  <FormSelect
                    value={policyDraft.appAccessPolicy}
                    onChange={(event) => {
                      const appAccessPolicy = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, appAccessPolicy }));
                    }}
                  >
                    <option value="approval_required">Approval required</option>
                    <option value="allow">Allow within policy</option>
                    <option value="blocked">Blocked</option>
                  </FormSelect>
                </FormField>
                <FormField label="Approval memory seconds">
                  <FormInput
                    type="number"
                    min="60"
                    value={policyDraft.approvalTtlSeconds}
                    onChange={(event) => {
                      const approvalTtlSeconds = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, approvalTtlSeconds }));
                    }}
                  />
                </FormField>
                <FormField label="Max runtime seconds">
                  <FormInput
                    type="number"
                    min="0"
                    value={policyDraft.maxRuntimeSeconds}
                    onChange={(event) => {
                      const maxRuntimeSeconds = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, maxRuntimeSeconds }));
                    }}
                  />
                </FormField>
                <FormField label="Max budget dollars">
                  <FormInput
                    type="number"
                    min="0"
                    step="0.01"
                    value={policyDraft.maxBudgetDollars}
                    onChange={(event) => {
                      const maxBudgetDollars = event.currentTarget.value;
                      setPolicyDraft((current) => ({ ...current, maxBudgetDollars }));
                    }}
                  />
                </FormField>
              </FormGrid>
              <div className="settings-action-row">
                <WorkstationActionButton
                  type="button"
                  disabled={busyActionKey === 'policy:save'}
                  onClick={() => {
                    void handleSaveAgentComputerPolicy();
                  }}
                >
                  {busyActionKey === 'policy:save' ? 'Saving policy…' : 'Save custom policy'}
                </WorkstationActionButton>
                <WorkstationActionButton
                  type="button"
                  tone={emergencyStopActive ? 'secondary' : 'danger'}
                  disabled={busyActionKey === 'policy:stop' || busyActionKey === 'policy:clear-stop'}
                  onClick={() => {
                    void handleAgentComputerEmergencyStop(emergencyStopActive);
                  }}
                >
                  {emergencyStopActive ? 'Clear emergency stop' : 'Emergency stop'}
                </WorkstationActionButton>
              </div>
            </FormSection>
          ) : null}
          <div className="settings-action-row">
            <WorkstationActionButton
              type="button"
              disabled={busyActionKey === 'pairing' || (pairingDraft.runtimeAccessMode === 'full_access' && !pairingDraft.autonomousAgentWarningAccepted)}
              onClick={() => {
              void handleCreatePairingIntent();
            }}
          >
              {busyActionKey === 'pairing' ? 'Creating connection…' : 'Connect a computer'}
            </WorkstationActionButton>
          </div>
          </FormSection>
        ) : null}

        {diagnosticsSectionVisible ? (
          <FormSection
          title="How this trust lane works"
          description="This Device is useful by default. Approval is reserved for risky local and external actions."
        >
          <WorkstationSurfaceList>
            {GATEWAY_MODE_SUMMARIES.map((mode) => (
              <WorkstationSurfaceListItem
                key={mode.title}
                title={mode.title}
                subtitle={mode.subtitle}
                  description={mode.description}
                  actions={(
                    <DataBadge tone={mode.title === 'Full Access' ? 'success' : mode.title === 'Custom' ? 'neutral' : 'warning'}>
                      {mode.title === 'Full Access' ? 'Sage only' : mode.title === 'Custom' ? 'Policy-defined' : 'Approval mode'}
                    </DataBadge>
                  )}
              />
            ))}
          </WorkstationSurfaceList>
          <div className="app-inline-actions app-inline-actions--tight app-inline-actions--wrap">
            {GATEWAY_PERMISSION_CHECKLIST.map((permission) => (
              <DataBadge key={permission} tone="neutral">{permission}</DataBadge>
            ))}
          </div>
          </FormSection>
        ) : null}

        {manualSetupVisible && pairingIntent ? (
          <FormSection
            title="Manual terminal setup"
            description="Use this if guided setup does not work. Copy one command, paste it into Terminal, and press Return."
            className="gateway-pairing-command-section"
          >
            <FormGrid>
              <FormReadout
                label="Expires"
                value={formatTimestamp(pairingIntent.expires_at)}
              />
              <FormReadout
                label="Suggested device"
                value={readString(pairingIntent.display_name, 'Unlabeled device')}
              />
                <FormReadout
                  label="Platform"
                  value={humanizeToken(pairingIntent.platform, 'Unknown')}
                />
                <FormReadout
                  label="Runtime mode"
                  value={runtimeAccessModeToken(readRecord(pairingIntent.metadata).runtime_access_mode) === 'full_access'
                    ? 'Full Access'
                    : runtimeAccessModeToken(readRecord(pairingIntent.metadata).runtime_access_mode) === 'custom'
                      ? 'Custom'
                      : 'Default'}
                />
            </FormGrid>
            <div className="gateway-pairing-command-card">
              <div className="app-inline-actions app-inline-actions--between app-inline-actions--start">
                <div className="gateway-pairing-command-card__copy">
                  <strong>Run on the computer you want to connect</strong>
                  <span>Use this when the connector is offline or paired to the wrong workspace. The status output warns if the workspace does not match.</span>
                </div>
                <div className="app-inline-actions app-inline-actions--tight">
                  <WorkstationActionButton
                    type="button"
                    onClick={() => {
                      void copyToClipboard(
                        'Computer connector setup command',
                        gatewayPairingCommand(pairingIntent.pairing_token, pairingIntent.display_name, workspaceId),
                      );
                    }}
                  >
                    Copy setup command
                  </WorkstationActionButton>
                </div>
              </div>
              <pre className="gateway-pairing-command">
                <code>{gatewayPairingCommand(pairingIntent.pairing_token, pairingIntent.display_name, workspaceId)}</code>
              </pre>
            </div>
          </FormSection>
        ) : null}

        {diagnosticsSectionVisible && loadingRegistrations && !registrationsTimedOut ? (
          <WorkstationSurfaceNotice tone="neutral">Loading device runtimes...</WorkstationSurfaceNotice>
        ) : diagnosticsSectionVisible && gateways.length === 0 ? (
          <EmptyPanel
            title="No computers connected"
            body="Connect a computer to bring hardware connection and governed local execution online for this workspace."
          />
        ) : diagnosticsSectionVisible ? (
          <WorkstationSurfaceList>
            {gateways.map((gateway) => {
              const gatewayId = String(gateway.gateway_id ?? '').trim();
              const selected = gatewayId === selectedGatewayId;
              return (
                <WorkstationSurfaceListItem
                  key={gatewayId}
                  title={readString(gateway.display_name, gatewayId)}
                  subtitle={`${humanizeToken(gateway.platform, 'Unknown platform')}${selected ? ' · Selected' : ''}`}
                  description={`Trust ${humanizeToken(gateway.device_trust_state, 'unknown')} · last seen ${formatRelativeTimestamp(gateway.last_seen_at)}`}
                  actions={(
                    <div className="app-inline-actions app-inline-actions--tight">
                      <DataBadge tone={statusTone(gateway.connection_status ?? gateway.status)}>
                        {humanizeToken(gateway.connection_status ?? gateway.status, 'Unknown')}
                      </DataBadge>
                      <WorkstationActionButton
                        type="button"
                        tone={selected ? 'secondary' : 'primary'}
                        onClick={() => setSelectedGatewayId(gatewayId)}
                      >
                        {selected ? 'Selected' : 'Inspect'}
                      </WorkstationActionButton>
                    </div>
                  )}
                />
              );
            })}
          </WorkstationSurfaceList>
        ) : null}
      </section>

      <CommandSheet
        open={manageOpen}
        title="Manage selected computer"
        description="Reconnect, revoke trust, or open manual setup only when needed."
        onClose={() => setManageOpen(false)}
      >
        <div className="app-stack-3">
          <WorkstationSurfaceNotice tone={myComputerStatus.tone === 'danger' ? 'danger' : myComputerStatus.tone === 'warning' ? 'warning' : myComputerStatus.tone === 'success' ? 'success' : 'neutral'}>
            {normalDeviceTitle} · {normalDeviceLine}
          </WorkstationSurfaceNotice>
          <div className="settings-action-row">
            {myComputerStatus.primaryAction.action.kind === 'open_approvals' ? (
              <WorkstationActionButton
                type="button"
                tone="primary"
                disabled={Boolean(myComputerStatus.primaryAction.disabled)}
                onClick={() => {
                  setManageOpen(false);
                  handlePrimaryAction();
                }}
              >
                Approve action
              </WorkstationActionButton>
            ) : null}
            <WorkstationActionButton
              type="button"
              tone="primary"
              disabled={busyActionKey === 'pairing'}
              onClick={() => {
                setManageOpen(false);
                setManualSetupVisible(true);
              }}
            >
              {busyActionKey === 'pairing' ? 'Creating connection…' : selectedGateway ? 'Reconnect' : 'Connect a computer'}
            </WorkstationActionButton>
            {selectedGateway ? (
              <WorkstationActionButton
                type="button"
                tone="danger"
                disabled={busyActionKey === `revoke:${selectedGatewayId}`}
                onClick={() => {
                  setManageOpen(false);
                  void handleRevokeGateway();
                }}
              >
                {busyActionKey === `revoke:${selectedGatewayId}` ? 'Revoking…' : 'Revoke access'}
              </WorkstationActionButton>
            ) : null}
            {selectedGateway ? (
              <WorkstationActionButton
                type="button"
                tone={emergencyStopActive ? 'secondary' : 'danger'}
                disabled={busyActionKey === 'policy:stop' || busyActionKey === 'policy:clear-stop'}
                onClick={() => {
                  setManageOpen(false);
                  void handleAgentComputerEmergencyStop(emergencyStopActive);
                }}
              >
                {emergencyStopActive ? 'Clear emergency stop' : 'Emergency stop'}
              </WorkstationActionButton>
            ) : null}
            <WorkstationActionButton
              type="button"
              tone="secondary"
              onClick={() => {
                setManageOpen(false);
                setManualSetupVisible(true);
              }}
            >
              Manual setup
            </WorkstationActionButton>
            <WorkstationActionButton
              type="button"
              tone="secondary"
              onClick={() => {
                setStatusMessage(null);
                setErrorMessage(null);
                setManageOpen(false);
                setDiagnosticsVisible(true);
                void refreshRegistrations(true)
                  .then(() => (selectedGatewayId ? refreshGatewayDetail(selectedGatewayId, false, true) : undefined))
                  .catch((error) => {
                    setErrorMessage(error instanceof Error ? error.message : 'Could not refresh device connection state.');
                  });
              }}
            >
              Diagnostics
            </WorkstationActionButton>
          </div>
        </div>
      </CommandSheet>

      {selectedGateway && showStatusSection && expandedDetailVisible ? (
        <WorkstationSurfaceCard
          title="Connection details"
          description="Trust, health, reconnect posture, and diagnostics for the selected computer."
        >
          {loadingGatewayDetail ? (
            <WorkstationSurfaceNotice tone="neutral">Refreshing local device diagnostics…</WorkstationSurfaceNotice>
          ) : null}

          <FormGrid>
            <FormReadout label="Computer" value={readString(selectedGateway.display_name, 'Selected computer')} />
            <FormReadout label="Owner" value={selectedRuntimeOwner} />
            <FormReadout label="State" value={<DataBadge tone={myComputerStatus.tone}>{myComputerStatus.label}</DataBadge>} />
            <FormReadout label="Runtime lane" value={selectedRuntimeLane} />
            <FormReadout label="Approval mode" value={selectedApprovalMode} />
            <FormReadout label="Custom policy" value={customPolicyStateLabel} />
            <FormReadout label="Emergency stop" value={<DataBadge tone={emergencyStopActive ? 'danger' : 'success'}>{emergencyStopActive ? 'Stopped' : 'Ready'}</DataBadge>} />
            <FormReadout label="Platform" value={humanizeToken(selectedGateway.platform, 'Unknown')} />
            <FormReadout label="Trust state" value={trustSummary.trustLabel} />
            <FormReadout label="Last connected" value={trustSummary.lastConnectedLabel} />
            <FormReadout label="Last seen" value={trustSummary.lastSeenLabel} />
            <FormReadout label="Last proof" value={latestProofSummary} />
            <FormReadout label="Last action" value={latestActionSummary} />
            <FormReadout label="Stop/takeover status" value={selectedTakeoverStatus} />
            <FormReadout label="Health" value={<DataBadge tone={doctorStatusDisplay.tone}>{doctorStatusDisplay.label}</DataBadge>} />
            <FormReadout label="Browser readiness" value={<DataBadge tone={statusTone(readRecord(doctor?.browser).status)}>{browserLaneStatus.status}</DataBadge>} />
            <FormReadout label="AI model reachability" value={<DataBadge tone={statusTone(readRecord(doctor?.providers).status)}>{providerStatus.status}</DataBadge>} />
            <FormReadout label="Approvals waiting" value={String(approvalsPendingCount)} />
          </FormGrid>

          <FormSection
            title="Launch certification lanes"
            description="Phone and web should make computer status, approvals, recovery, and channel health obvious."
          >
            <WorkstationSurfaceList>
              {certificationLanes.map((lane) => (
                <WorkstationSurfaceListItem
                  key={lane.id}
                  title={lane.title}
                  subtitle={lane.subtitle}
                  description={lane.description}
                  actions={(
                    <DataBadge tone={lane.tone}>
                      {lane.subtitle}
                    </DataBadge>
                  )}
                />
              ))}
            </WorkstationSurfaceList>
          </FormSection>

          <FormSection
            title="Revoke selected computer"
            description="Revocation is immediate and server-side. The computer must be paired again before this workspace can use local tools or personal channels."
          >
            <div className="settings-action-row">
              <WorkstationActionButton
                type="button"
                tone="danger"
                disabled={busyActionKey === `revoke:${selectedGatewayId}`}
                onClick={() => {
                  void handleRevokeGateway();
                }}
              >
                {busyActionKey === `revoke:${selectedGatewayId}` ? 'Revoking…' : 'Revoke access'}
              </WorkstationActionButton>
            </div>
          </FormSection>

          {(readRecord(doctor?.checkpoint).status || readRecord(doctor?.browser).status || readRecord(doctor?.specialists).status || readRecord(doctor?.providers).status || readRecord(doctor?.quota).status) ? (
            <WorkstationSurfaceList>
              <WorkstationSurfaceListItem
                title="Resume readiness"
                subtitle={checkpointStatus.status}
                description={checkpointStatus.summary}
                actions={(
                  <DataBadge tone={statusTone(readRecord(doctor?.checkpoint).status)}>
                    {checkpointStatus.status}
                  </DataBadge>
                )}
              />
              <WorkstationSurfaceListItem
                title="Browser connection"
                subtitle={browserLaneStatus.status}
                description={browserLaneStatus.summary}
                actions={(
                  <DataBadge tone={statusTone(readRecord(doctor?.browser).status)}>
                    {browserLaneStatus.status}
                  </DataBadge>
                )}
              />
              <WorkstationSurfaceListItem
                title="Build assistant health"
                subtitle={specialistStatus.status}
                description={specialistStatus.summary}
                actions={(
                  <DataBadge tone={statusTone(readRecord(doctor?.specialists).status)}>
                    {specialistStatus.status}
                  </DataBadge>
                )}
              />
              <WorkstationSurfaceListItem
                title="Model provider reachability"
                subtitle={providerStatus.status}
                description={providerStatus.summary}
                actions={(
                  <DataBadge tone={statusTone(readRecord(doctor?.providers).status)}>
                    {providerStatus.status}
                  </DataBadge>
                )}
              />
              <WorkstationSurfaceListItem
                title="Usage limit"
                subtitle={quotaStatus.status}
                description={quotaStatus.summary}
                actions={(
                  <DataBadge tone={statusTone(readRecord(doctor?.quota).status)}>
                    {quotaStatus.status}
                  </DataBadge>
                )}
              />
            </WorkstationSurfaceList>
          ) : null}

          {specialistItems.length > 0 ? (
            <WorkstationSurfaceList>
              {specialistItems.map((item) => (
                <WorkstationSurfaceListItem
                  key={readString(item.deployed_agent_id, readString(item.name, 'specialist'))}
                  title={readString(item.name, 'Build assistant')}
                  subtitle={humanizeToken(item.deployment_state, 'Unknown')}
                  description={readString(item.summary, 'No detail available.')}
                  actions={(
                    <DataBadge tone={statusTone(item.status)}>
                      {humanizeToken(item.status, 'Unknown')}
                    </DataBadge>
                  )}
                />
              ))}
            </WorkstationSurfaceList>
          ) : null}

          {providerItems.length > 0 ? (
            <WorkstationSurfaceList>
              {providerItems.map((item) => (
                <WorkstationSurfaceListItem
                  key={readString(item.id, readString(item.label, 'provider'))}
                  title={readString(item.label, 'AI model provider')}
                  subtitle={humanizeToken(item.state, 'Unknown')}
                  description={readString(item.state_detail, 'No AI model provider detail available.')}
                  actions={(
                    <DataBadge tone={statusTone(item.state === 'active' ? 'pass' : 'fail')}>
                      {humanizeToken(item.state, 'Unknown')}
                    </DataBadge>
                  )}
                />
              ))}
            </WorkstationSurfaceList>
          ) : null}

          {doctorChecks.length > 0 ? (
            <WorkstationSurfaceList>
              {doctorChecks.map((check) => (
                <WorkstationSurfaceListItem
                  key={readString(check.id, 'check')}
                  title={humanizeToken(check.id, 'Check')}
                  description={readString(check.summary, 'No detail available.')}
                  actions={(
                    <DataBadge tone={statusTone(check.status)}>
                      {humanizeToken(check.status, 'Unknown')}
                    </DataBadge>
                  )}
                />
              ))}
            </WorkstationSurfaceList>
          ) : (
            <EmptyPanel
              title="No device checks yet"
              body="Pair and connect a computer to populate health and personal channel status."
            />
          )}
        </WorkstationSurfaceCard>
      ) : null}

      {selectedGateway && showApprovalsSection && expandedDetailVisible ? (
        <WorkstationSurfaceCard
          title="Approval requests"
          description="Resolve risky local actions without leaving the product shell."
        >
          {pendingApprovals.length === 0 ? (
            <EmptyPanel
              title="No permission requests waiting"
              body="Risky local actions will appear here with explicit approve and reject controls."
            />
          ) : (
            <WorkstationSurfaceList>
              {pendingApprovals.map((approval) => {
                const approvalId = String(approval.approval_id ?? '').trim();
                const busyApprove = busyActionKey === `approval:${approvalId}:approved`;
                const busyReject = busyActionKey === `approval:${approvalId}:rejected`;
                const busy = busyApprove || busyReject;
                return (
                  <WorkstationSurfaceListItem
                    key={approvalId}
                    title={humanizeToken(approval.capability_id, 'Local permission request')}
                    subtitle={`${approvalId} · Run ${readString(approval.run_id, 'unlinked')}`}
                    description={readString(approval.note, 'Approval is required before Sage can continue on the paired device.')}
                    actions={(
                      <div className="app-inline-actions app-inline-actions--tight">
                        <DataBadge tone={statusTone(approval.status)}>
                          {humanizeToken(approval.status, 'Pending')}
                        </DataBadge>
                        <WorkstationActionButton
                          type="button"
                          tone="secondary"
                          disabled={busy}
                          onClick={() => {
                            void handleResolveApproval(approvalId, 'approved');
                          }}
                        >
                          {busyApprove ? 'Approving…' : 'Approve'}
                        </WorkstationActionButton>
                        <WorkstationActionButton
                          type="button"
                          tone="danger"
                          disabled={busy}
                          onClick={() => {
                            void handleResolveApproval(approvalId, 'rejected');
                          }}
                        >
                          {busyReject ? 'Rejecting…' : 'Reject'}
                        </WorkstationActionButton>
                      </div>
                    )}
                  />
                );
              })}
            </WorkstationSurfaceList>
          )}
        </WorkstationSurfaceCard>
      ) : null}

      {showActivitySection && expandedDetailVisible ? (
        <WorkstationSurfaceCard
          title="Active computer sessions"
          description="Live cloud, local, and self-hosted computer sessions currently bound to this workspace."
        >
          {activeRuntimeItems.length === 0 ? (
            <EmptyPanel
              title="No active computer sessions"
              body="When an agent has its own computer or attaches to a paired device, the live session appears here before proof events arrive."
            />
          ) : (
            <WorkstationSurfaceList>
              {activeRuntimeItems.map((session) => {
                const sessionId = readString(session.session_id, '');
                const deployedAgentId = readString(session.deployed_agent_id, '');
                const runtimeSessionId = readString(session.runtime_session_id, sessionId);
                const ownerLabel = runtimeSessionOwnerLabel(session);
                const laneLabel = runtimeSessionLaneLabel(session);
                const approvalModeLabel = runtimeSessionApprovalModeLabel(session, approvalsPendingCount);
                const stopKey = `runtime:${sessionId}:stop`;
                return (
                  <WorkstationSurfaceListItem
                    key={sessionId || runtimeSessionId}
                    title={laneLabel}
                    subtitle={`${ownerLabel} · ${approvalModeLabel} · ${humanizeToken(session.channel, 'Web')}`}
                    description={`Session ${runtimeSessionId} · proof ${latestProofSummary} · action ${latestActionSummary} · created ${formatRelativeTimestamp(session.created_at)} · expires ${formatRelativeTimestamp(session.expires_at)}${readString(session.gateway_id, '') ? ` · gateway ${readString(session.gateway_id, '')}` : ''}`}
                    actions={(
                      <div className="app-inline-actions app-inline-actions--tight">
                        <DataBadge tone={statusTone(session.status)}>
                          {humanizeToken(session.status, 'Active')}
                        </DataBadge>
                        <DataBadge tone={approvalModeLabel === 'Needs approval' ? 'warning' : 'neutral'}>
                          {approvalModeLabel}
                        </DataBadge>
                        {session.runtime_provider_id ? (
                          <DataBadge tone="neutral">
                            {readString(session.runtime_provider_id)}
                          </DataBadge>
                        ) : null}
                        <WorkstationActionButton
                          type="button"
                          tone="danger"
                          disabled={Boolean(busyActionKey) || !sessionId || !deployedAgentId}
                          onClick={() => {
                            void handleStopRuntimeSession(session);
                          }}
                        >
                          {busyActionKey === stopKey ? 'Stopping…' : 'Stop'}
                        </WorkstationActionButton>
                      </div>
                    )}
                  />
                );
              })}
            </WorkstationSurfaceList>
          )}
        </WorkstationSurfaceCard>
      ) : null}

      {selectedGateway && showActivitySection && expandedDetailVisible ? (
        <WorkstationSurfaceCard
          title="Device activity"
          description="Inspect governed browser sessions and recent local browser activity without dropping into raw APIs."
        >
          {browserItems.length === 0 ? (
            <EmptyPanel
              title="No device activity tracked"
              body="Once the selected computer starts or attaches a browser session, it will appear here with governed control actions."
            />
          ) : (
            <WorkstationSurfaceList>
              {browserItems.map((session) => {
                const browserSessionId = String(session.browser_session_id ?? '').trim();
                const sessionMode = humanizeToken((session.metadata || {}).browser_session_mode, 'Managed profile');
                const takeoverStatus = browserTakeoverStatus(session);
                const resumeSupported = Boolean(session.resume_supported ?? true);
                const approvalState = session.reviewed_approval_required
                  ? (session.reviewed_approved ? 'Approved control' : 'Approval required')
                  : 'Policy governed';
                return (
                  <WorkstationSurfaceListItem
                    key={browserSessionId}
                    title={readString(session.session_profile, browserSessionId)}
                    subtitle={`${sessionMode} · ${takeoverStatus} · ${approvalState} · ${browserSessionId}`}
                    description={`${readString(session.current_url, 'No active URL reported yet.')} · last proof ${latestProofSummary} · last action ${latestActionSummary}`}
                    actions={(
                      <div className="app-inline-actions app-inline-actions--tight">
                        <DataBadge tone={statusTone(session.status)}>
                          {humanizeToken(session.status, 'Unknown')}
                        </DataBadge>
                        <WorkstationActionButton
                          type="button"
                          tone="secondary"
                          disabled={Boolean(busyActionKey)}
                          onClick={() => {
                            void handleBrowserControl(browserSessionId, 'takeover');
                          }}
                        >
                          {busyActionKey === `browser:${browserSessionId}:takeover` ? 'Working…' : 'Takeover'}
                        </WorkstationActionButton>
                        <WorkstationActionButton
                          type="button"
                          tone="secondary"
                          disabled={Boolean(busyActionKey) || !resumeSupported}
                          onClick={() => {
                            void handleBrowserControl(browserSessionId, 'resume');
                          }}
                        >
                          {busyActionKey === `browser:${browserSessionId}:resume` ? 'Working…' : 'Resume'}
                        </WorkstationActionButton>
                        <WorkstationActionButton
                          type="button"
                          tone="danger"
                          disabled={Boolean(busyActionKey)}
                          onClick={() => {
                            void handleBrowserControl(browserSessionId, 'interrupt');
                          }}
                        >
                          {busyActionKey === `browser:${browserSessionId}:interrupt` ? 'Working…' : 'Interrupt'}
                        </WorkstationActionButton>
                      </div>
                    )}
                  />
                );
              })}
            </WorkstationSurfaceList>
          )}
        </WorkstationSurfaceCard>
      ) : null}
    </div>
  );
}
