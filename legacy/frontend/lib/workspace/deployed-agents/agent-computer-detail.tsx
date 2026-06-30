'use client';

import { memo } from 'react';

import { DataBadge } from '@/lib/ui/data-table';
import type { RuntimeAttachmentSnapshot, StudioAgentComputerSurface } from './types';
import { AGENT_STUDIO_OBJECT_LABELS } from './constants';
import { formatTimestamp, humanizeToken, readRecord, readString } from './utils';

function formatOptionalTimestamp(value: unknown, fallback: string): string {
  return readString(value) ? formatTimestamp(value) : fallback;
}

function computerValue(computer: StudioAgentComputerSurface | RuntimeAttachmentSnapshot | null, key: string): unknown {
  if (!computer) {
    return null;
  }
  if (key in computer) {
    return (computer as Record<string, unknown>)[key];
  }
  return readRecord((computer as StudioAgentComputerSurface).record)[key];
}

export const AgentComputerDetailView = memo(({
  computer,
}: {
  computer: StudioAgentComputerSurface | RuntimeAttachmentSnapshot | null;
}) => {
  if (!computer) {
    return (
      <div className="studio-agent-detail-empty" aria-label="Agent Computer detail">
        <strong>Select an Agent Computer</strong>
        <span>Runtime health, permissions, attached agents, and logs will appear here.</span>
      </div>
    );
  }

  const name = readString(computerValue(computer, 'name') ?? computerValue(computer, 'label'), 'Agent Computer');
  const status = readString(computerValue(computer, 'status'), 'unknown');
  const nodeKind = readString(computerValue(computer, 'nodeKind') ?? computerValue(computer, 'node_kind'), 'runtime');
  const online = computerValue(computer, 'online') === true;
  const healthy = computerValue(computer, 'healthy') === true;
  const heartbeatAt = readString(computerValue(computer, 'heartbeatAt') ?? computerValue(computer, 'heartbeat_at') ?? computerValue(computer, 'last_seen_at'));
  const capabilities = Array.isArray(computerValue(computer, 'capabilities'))
    ? computerValue(computer, 'capabilities') as unknown[]
    : [];
  const capabilityIds = capabilities.map((item) => readString(item).toLowerCase()).filter(Boolean);
  const ownerApproved = computerValue(computer, 'ownerApproved') === true || computerValue(computer, 'owner_approved') === true;
  const externalAgentProxyReady = online && healthy && capabilityIds.includes('external_agent_proxy');
  const externalAgentBridgeLabel = externalAgentProxyReady
    ? 'Ready'
    : !online
      ? 'Agent Computer offline'
      : !healthy
        ? 'Needs health check'
        : 'Local bridge unavailable';
  const statusTone = online && healthy ? 'success' : 'warning';

  return (
    <div className="studio-agent-computer-compact studio-agent-detail-motion" aria-label="Agent Computer detail">
      <header className="studio-agent-computer-compact__header">
        <div>
          <span>{AGENT_STUDIO_OBJECT_LABELS.agent_computer}</span>
          <strong>{name}</strong>
        </div>
        <DataBadge tone={statusTone}>{online && healthy ? 'Available' : humanizeToken(status, 'Unknown')}</DataBadge>
      </header>

      <div className="studio-agent-computer-compact__rows">
        <div>
          <span>Status</span>
          <strong>{online ? 'Online' : 'Offline'}</strong>
        </div>
        <div>
          <span>Health</span>
          <strong>{healthy ? 'Healthy' : 'Needs check'}</strong>
        </div>
        <div>
          <span>Last seen</span>
          <strong>{formatOptionalTimestamp(heartbeatAt, 'Not seen')}</strong>
        </div>
        <div>
          <span>Bridge</span>
          <strong>{externalAgentBridgeLabel}</strong>
        </div>
      </div>

      <div className="studio-agent-computer-compact__badges">
        <DataBadge tone={online ? 'success' : 'warning'}>{online ? 'Connected' : 'Offline'}</DataBadge>
        <DataBadge tone={healthy ? 'success' : 'warning'}>{healthy ? 'Health OK' : 'Needs health check'}</DataBadge>
        <DataBadge tone={ownerApproved ? 'success' : 'neutral'}>{ownerApproved ? 'Owner approved' : 'Approval pending'}</DataBadge>
        <DataBadge tone={externalAgentProxyReady ? 'success' : 'warning'}>
          {externalAgentProxyReady ? 'Bridge ready' : 'Bridge unavailable'}
        </DataBadge>
        <DataBadge tone="neutral">{humanizeToken(nodeKind, 'Runtime')}</DataBadge>
        {capabilities.length > 0 ? <DataBadge tone="neutral">{capabilities.length} capabilities</DataBadge> : null}
      </div>
    </div>
  );
});

AgentComputerDetailView.displayName = 'AgentComputerDetailView';
