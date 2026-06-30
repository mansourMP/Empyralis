'use client';

import { memo, useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type { DeployedAgentRecord } from '@/lib/workspace/workstation-client';
import type {
  AgentOperationalMetrics,
  RuntimeAttachmentSnapshot,
  StudioAgentComputerSurface,
  StudioConnectedExternalAgent,
} from './types';
import {
  CUSTOM_STUDIO_TEMPLATE,
} from './constants';
import {
  resolveExternalProviderBadge,
} from './external-agent-provider-badges';
import {
  readString,
  humanizeToken,
  deploymentStateLabel,
  rosterStatusTone,
  studioAgentDisplayName,
} from './utils';

const AgentRosterItem = memo(({
  agent,
  selected,
  agentMetrics,
  onSelectAgent,
}: {
  agent: DeployedAgentRecord;
  selected: boolean;
  agentMetrics: AgentOperationalMetrics | null;
  onSelectAgent: (id: string) => void;
}) => {
  const agentId = readString(agent.id);
  const stateLabel = deploymentStateLabel(agent.deployment_state);
  const displayName = studioAgentDisplayName(agent, agentId);
  const displayInitial = (displayName.trim().charAt(0) || 'A').toUpperCase();
  const latestActivityLabel = agentMetrics?.latestActivityLabel;

  return (
    <button
      type="button"
      className={joinClassNames(
        'studio-agents-nav__agent',
        'studio-agents-nav__agent--external',
        selected && 'studio-agents-nav__agent--active',
      )}
      aria-label={`${displayName}, ${stateLabel}${latestActivityLabel ? `, ${latestActivityLabel}` : ''}`}
      aria-selected={selected}
      onClick={() => onSelectAgent(agentId)}
    >
      <span className="studio-agents-nav__avatar" aria-hidden="true">{displayInitial}</span>
      <span className="studio-agents-nav__copy">
        <span className="studio-agents-nav__label">{displayName}</span>
      </span>
      <span className={joinClassNames('studio-agents-nav__status', `studio-agents-nav__status--${rosterStatusTone(agent.deployment_state)}`)}>
        {stateLabel}
      </span>
    </button>
  );
});

AgentRosterItem.displayName = 'AgentRosterItem';

const ConnectedExternalAgentRosterItem = memo(({
  agent,
  selected,
  onSelectAgent,
}: {
  agent: StudioConnectedExternalAgent;
  selected: boolean;
  onSelectAgent: (id: string) => void;
}) => {
  const agentId = readString(agent.id);
  const displayName = readString(agent.name ?? agent.label, agentId || 'Connected Agent');
  const connectionState = readString(agent.connection_state, 'unverified');
  const providerBadge = resolveExternalProviderBadge(agent.provider_kind);
  const connectionLabel = humanizeToken(connectionState, 'Unverified');

  return (
    <button
      type="button"
      className={joinClassNames(
        'studio-agents-nav__agent',
        selected && 'studio-agents-nav__agent--active',
      )}
      aria-label={`${displayName}, connected agent, ${providerBadge.label}, ${connectionLabel}`}
      aria-selected={selected}
      onClick={() => onSelectAgent(agentId)}
    >
      <span className="studio-agents-nav__copy">
        <span className="studio-agents-nav__identity">
          <span className="studio-agents-nav__label">{displayName}</span>
          <span className="studio-agents-nav__provider-badge" title={`${providerBadge.label} connected agent`}>
            {providerBadge.imageSrc ? (
              <img src={providerBadge.imageSrc} alt="" aria-hidden="true" />
            ) : (
              <span>{providerBadge.initials}</span>
            )}
          </span>
        </span>
      </span>
      <span className={joinClassNames('studio-agents-nav__status', connectionState === 'verified' ? 'studio-agents-nav__status--live' : connectionState === 'revoked' ? 'studio-agents-nav__status--danger' : 'studio-agents-nav__status--warning')}>
        {connectionLabel}
      </span>
    </button>
  );
});

ConnectedExternalAgentRosterItem.displayName = 'ConnectedExternalAgentRosterItem';

export interface AgentRosterSidebarProps {
  collapsed?: boolean;
  showAgentsIndex: boolean;
  onOpenCreateWizard: (templateId: string) => void;
  onRefreshAgents: () => void;
  agents: DeployedAgentRecord[];
  connectedExternalAgents: StudioConnectedExternalAgent[];
  agentComputers: Array<StudioAgentComputerSurface | RuntimeAttachmentSnapshot>;
  selectedAgentId: string | null;
  selectedExternalAgentId: string | null;
  selectedAgentComputerId: string | null;
  onSelectAgent: (id: string) => void;
  onSelectExternalAgent: (id: string) => void;
  onSelectAgentComputer: (id: string) => void;
  agentMetricsById: Record<string, AgentOperationalMetrics>;
  isAgentListPriming: boolean;
  isAgentListUnavailable: boolean;
  rosterSearchQuery: string;
  onChangeRosterSearch: (value: string) => void;
  totalStudioObjectCount: number;
  visibleStudioObjectCount: number;
}

export const AgentRosterSidebar = memo(({
  collapsed = false,
  showAgentsIndex,
  onOpenCreateWizard,
  onRefreshAgents,
  agents,
  connectedExternalAgents,
  agentComputers,
  selectedAgentId,
  selectedExternalAgentId,
  selectedAgentComputerId,
  onSelectAgent,
  onSelectExternalAgent,
  onSelectAgentComputer,
  agentMetricsById,
  isAgentListPriming,
  isAgentListUnavailable,
  rosterSearchQuery,
  onChangeRosterSearch,
  totalStudioObjectCount,
  visibleStudioObjectCount,
}: AgentRosterSidebarProps) => {
  const [searchExpanded, setSearchExpanded] = useState(false);
  const searchVisible = searchExpanded || rosterSearchQuery.trim().length > 0;
  const visibleItemCount = visibleStudioObjectCount;
  const hasSearchQuery = rosterSearchQuery.trim().length > 0;
  const emptyTitle = hasSearchQuery ? 'No matching Studio objects' : 'No Studio objects yet';
  const emptyDetail = hasSearchQuery
    ? 'Try another search.'
    : 'Create a native agent or connect another runtime when ready.';

  const connectedAgentItems = !collapsed ? connectedExternalAgents.map((agent, index) => {
        const agentId = readString(agent.id, `connected-agent-${index}`);
        return (
          <ConnectedExternalAgentRosterItem
            key={agentId}
            agent={agent}
            selected={agentId === selectedExternalAgentId}
            onSelectAgent={onSelectExternalAgent}
          />
        );
      }) : null;
  void agentComputers;
  void selectedAgentComputerId;
  void onSelectAgentComputer;

  return (
    <div className="app-stack-4">
      {showAgentsIndex ? (
        <>
          {!collapsed ? (
            <div className="studio-agents-nav__toolbar">
              <div className="studio-agents-nav__toolbar-head">
                <div className="studio-agents-nav__toolbar-actions">
                  <AppButton
                    type="button"
                    tone="secondary"
                    className="studio-agents-nav__icon-button"
                    onClick={() => setSearchExpanded((current) => !current)}
                    aria-label="Search Studio objects"
                    title="Search Studio objects"
                    aria-pressed={searchVisible}
                  >
                    <Search size={16} strokeWidth={1.9} aria-hidden="true" />
                  </AppButton>
                  <AppButton
                    type="button"
                    tone="primary"
                    className="studio-agents-nav__icon-button studio-agents-nav__icon-button--primary"
                    onClick={() => onOpenCreateWizard(CUSTOM_STUDIO_TEMPLATE.id)}
                    aria-label="Add agent"
                    title="Add agent"
                  >
                    <Plus size={18} strokeWidth={2} aria-hidden="true" />
                  </AppButton>
                </div>
              </div>
              {searchVisible ? (
                <label className="studio-agents-nav__search">
                  <Search size={14} strokeWidth={1.9} aria-hidden="true" />
                  <input
                    type="search"
                    value={rosterSearchQuery}
                    placeholder="Search agents"
                    aria-label="Search Studio objects"
                    onChange={(event) => onChangeRosterSearch(event.currentTarget.value)}
                  />
                </label>
              ) : null}
            </div>
          ) : null}

          <div
            className={joinClassNames(
              'studio-agents-nav',
              collapsed && 'studio-agents-nav--collapsed',
              visibleItemCount === 0 && 'studio-agents-nav--empty',
            )}
            aria-label="Studio objects"
          >
            {collapsed && visibleItemCount === 0 ? null : isAgentListPriming ? (
              <div className="studio-agents-nav__loading">
                <SkeletonBlock height="4.5rem" />
                <SkeletonBlock height="4.5rem" />
                <SkeletonBlock height="4.5rem" />
              </div>
            ) : isAgentListUnavailable ? (
              <div className="studio-agents-nav__empty">
                <div className="studio-agents-nav__empty-copy">
                  <strong>Could not load native agents</strong>
                  <span>Retry the agent list, or create a new native Studio agent.</span>
                </div>
                <div className="studio-agents-nav__empty-actions">
                  <AppButton type="button" tone="primary" onClick={onRefreshAgents}>
                    Retry
                  </AppButton>
                  <AppButton type="button" tone="secondary" onClick={() => onOpenCreateWizard(CUSTOM_STUDIO_TEMPLATE.id)}>
                    Create native agent
                  </AppButton>
                </div>
              </div>
            ) : visibleItemCount === 0 ? (
              <div className="studio-agents-nav__items">
                <div className="studio-agents-nav__empty">
                  <div className="studio-agents-nav__empty-copy">
                    <strong>{emptyTitle}</strong>
                    <span>{emptyDetail}</span>
                  </div>
                  {totalStudioObjectCount > 0 && hasSearchQuery ? null : (
                    <AppButton type="button" tone="primary" onClick={() => onOpenCreateWizard(CUSTOM_STUDIO_TEMPLATE.id)}>
                      Add agent
                    </AppButton>
                  )}
                </div>
              </div>
            ) : (
              <div className="studio-agents-nav__items">
                {agents.map((agent, index) => {
                  const agentId = readString(agent.id, `deployed-agent-${index}`);
                  return (
                    <AgentRosterItem
                      key={agentId}
                      agent={agent}
                      selected={agentId === selectedAgentId}
                      agentMetrics={agentMetricsById[agentId] ?? null}
                      onSelectAgent={onSelectAgent}
                    />
                  );
                })}
                {connectedAgentItems}
              </div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
});

AgentRosterSidebar.displayName = 'AgentRosterSidebar';
