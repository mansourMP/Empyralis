'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';

import type {
  AgentChannelBindingRecord,
  ChannelAccountRecord,
  ChannelCatalogItemRecord,
} from '@/lib/workspace/workstation-client';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import type {
  AgentIntegrationConnectorCard,
} from './types';
import {
  readString,
} from './utils';

const STUDIO_CHANNEL_SURFACE_LANE = 'studio_business_connector';
const PERSONAL_CHANNEL_SURFACE_LANE = 'personal_gateway';
const CHANNEL_BRAND_IMAGES: Record<string, string> = {
  discord: '/brand-assets/channels/discord.svg?v=3',
  discord_bot: '/brand-assets/channels/discord.svg?v=3',
  discord_webhook: '/brand-assets/channels/discord.svg?v=3',
  email: '/brand-assets/apps/gmail.svg?v=3',
  github: '/brand-assets/apps/github.svg?v=3',
  google_workspace: '/brand-assets/apps/gmail.svg?v=3',
  gmail: '/brand-assets/apps/gmail.svg?v=3',
  imessage: '/brand-assets/channels/imessage.svg?v=3',
  linear: '/brand-assets/apps/linear.svg?v=3',
  matrix: '/brand-assets/generic/api.svg?v=3',
  microsoft_365: '/brand-assets/apps/microsoft365.svg?v=3',
  microsoft_teams: '/brand-assets/apps/microsoft365.svg?v=3',
  notion: '/brand-assets/apps/notion.svg?v=3',
  slack: '/brand-assets/channels/slack.svg?v=3',
  slack_events: '/brand-assets/channels/slack.svg?v=3',
  smtp: '/brand-assets/generic/api.svg?v=3',
  smtp_imap: '/brand-assets/generic/api.svg?v=3',
  teams: '/brand-assets/apps/microsoft365.svg?v=3',
  telegram: '/brand-assets/channels/telegram.svg?v=3',
  telegram_bot: '/brand-assets/channels/telegram.svg?v=3',
  telegram_bot_api: '/brand-assets/channels/telegram.svg?v=3',
  web_chat: '/brand-assets/generic/browser.svg?v=3',
  web_widget: '/brand-assets/generic/browser.svg?v=3',
  webhook: '/brand-assets/generic/webhook.svg?v=3',
  whatsapp: '/brand-assets/channels/whatsapp.svg?v=3',
  whatsapp_business: '/brand-assets/channels/whatsapp.svg?v=3',
  whatsapp_twilio: '/brand-assets/channels/whatsapp.svg?v=3',
};

function readStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => readString(item)).filter(Boolean)
    : [];
}

function channelAccountMatches(item: ChannelCatalogItemRecord, account: ChannelAccountRecord): boolean {
  const provider = readString(account.provider).toLowerCase();
  const accepted = new Set(
    [
      readString(item.account_provider),
      readString(item.connector_id),
      readString(item.provider),
      readString(item.channel_key) === 'whatsapp_business' ? 'twilio_whatsapp' : '',
      readString(item.channel_key) === 'microsoft_365' ? 'outlook' : '',
    ]
      .map((token) => token.toLowerCase())
      .filter(Boolean),
  );
  return Boolean(provider && accepted.has(provider));
}

function isNotFoundError(error: unknown): boolean {
  return Boolean(error && typeof error === 'object' && (error as { status?: unknown }).status === 404);
}

function channelBrandImage(item: Record<string, unknown>): string | null {
  const tokens = [
    readString(item.channel_key),
    readString(item.binding_channel_key),
    readString(item.connector_id),
    readString(item.account_provider),
    readString(item.provider),
  ];
  for (const token of tokens) {
    const normalized = token.toLowerCase();
    if (normalized && CHANNEL_BRAND_IMAGES[normalized]) {
      return CHANNEL_BRAND_IMAGES[normalized];
    }
  }
  return null;
}

function channelBrandFallback(item: Record<string, unknown>): string {
  const label = readString(item.label, readString(item.channel_key, '?'));
  if (/email/i.test(label)) {
    return '@';
  }
  return label.trim().charAt(0).toUpperCase() || '?';
}

function ChannelBrandMark({ item }: { item: Record<string, unknown> }) {
  const image = channelBrandImage(item);
  return (
    <span className="studio-agent-integrations__brand" aria-hidden="true">
      {image ? (
        <img src={image} alt="" className="studio-agent-integrations__brand-image" />
      ) : (
        <span className="studio-agent-integrations__brand-fallback">{channelBrandFallback(item)}</span>
      )}
    </span>
  );
}

export function AgentIntegrationsSections({
  connectorCards,
  workspaceId,
  selectedAgentId,
  view = 'integrations',
}: {
  connectorCards: AgentIntegrationConnectorCard[];
  workspaceId: string;
  selectedAgentId: string | null;
  view?: 'channels' | 'integrations';
}) {
  const router = useRouter();
  const services = useWorkspaceServices();
  const [channelCatalog, setChannelCatalog] = useState<ChannelCatalogItemRecord[]>([]);
  const [channelAccounts, setChannelAccounts] = useState<ChannelAccountRecord[]>([]);
  const [channelBindings, setChannelBindings] = useState<AgentChannelBindingRecord[]>([]);
  const [channelLoading, setChannelLoading] = useState(false);
  const [channelError, setChannelError] = useState<string | null>(null);
  const [channelActionKey, setChannelActionKey] = useState<string | null>(null);
  const [channelNotice, setChannelNotice] = useState<string | null>(null);
  const studioCatalog = useMemo(
    () => channelCatalog.filter((item) => {
      const surfaceSupport = readStringList(item.surface_support);
      const runtimeLane = readString(item.runtime_lane);
      return surfaceSupport.includes('studio') || runtimeLane === STUDIO_CHANNEL_SURFACE_LANE;
    }),
    [channelCatalog],
  );
  const businessChannelCatalog = useMemo(
    () => studioCatalog.filter((item) => readString(item.surface_kind, 'messaging_channel') === 'messaging_channel'),
    [studioCatalog],
  );
  const connectedAppCatalog = useMemo(
    () => studioCatalog.filter((item) => readString(item.surface_kind) === 'connected_app'),
    [studioCatalog],
  );
  const appConnectorCards = useMemo(
    () => connectorCards.filter((connector) => !['telegram', 'whatsapp'].includes(connector.id)),
    [connectorCards],
  );
  const personalCatalog = useMemo(
    () => channelCatalog.filter((item) => readString(item.runtime_lane) === PERSONAL_CHANNEL_SURFACE_LANE),
    [channelCatalog],
  );
  const bindingByCatalogOrChannel = useMemo(() => {
    const out = new Map<string, AgentChannelBindingRecord>();
    channelBindings.forEach((binding) => {
      const catalogId = readString(binding.catalog_id);
      const channelKey = readString(binding.channel_key);
      if (catalogId) {
        out.set(catalogId, binding);
      }
      if (channelKey) {
        out.set(channelKey, binding);
      }
    });
    return out;
  }, [channelBindings]);

  async function loadChannelState() {
    if (!selectedAgentId) {
      setChannelBindings([]);
    }
    setChannelLoading(true);
    setChannelError(null);
    try {
      const [catalogPayload, accountsPayload] = await Promise.all([
        services.client.listStudioChannelCatalog(),
        services.client.listStudioChannelAccounts(),
      ]);
      let bindingsPayload: { items?: AgentChannelBindingRecord[] | null } = { items: [] };
      if (selectedAgentId) {
        try {
          bindingsPayload = await services.client.listAgentChannelBindings({ deployedAgentId: selectedAgentId });
        } catch (error) {
          if (!isNotFoundError(error)) {
            throw error;
          }
        }
      }
      setChannelCatalog(Array.isArray(catalogPayload?.items) ? catalogPayload.items : []);
      setChannelAccounts(Array.isArray(accountsPayload?.items) ? accountsPayload.items : []);
      setChannelBindings(Array.isArray(bindingsPayload?.items) ? bindingsPayload.items : []);
    } catch (error) {
      setChannelError(error instanceof Error ? error.message : 'Unable to load channel setup.');
    } finally {
      setChannelLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    async function load() {
      if (!active) {
        return;
      }
      await loadChannelState();
    }
    void load();
    return () => {
      active = false;
    };
  }, [selectedAgentId, services]);

  async function runChannelAction(
    actionKey: string,
    action: () => Promise<Record<string, unknown> | null>,
    notice: string,
  ) {
    setChannelActionKey(actionKey);
    setChannelError(null);
    setChannelNotice(null);
    try {
      await action();
      setChannelNotice(notice);
      await loadChannelState();
    } catch (error) {
      setChannelError(error instanceof Error ? error.message : 'Channel action failed.');
    } finally {
      setChannelActionKey(null);
    }
  }

  function renderCatalogSetupCard(item: ChannelCatalogItemRecord) {
    const channelKey = readString(item.channel_key);
    const bindingKey = readString(item.binding_channel_key, channelKey);
    const existing = bindingByCatalogOrChannel.get(channelKey) || bindingByCatalogOrChannel.get(bindingKey);
    const matchingAccount = channelAccounts.find((account) => channelAccountMatches(item, account)) ?? null;
    const needsAccount = Boolean(readString(item.account_provider) || readString(item.connector_id));
    const canBind = Boolean(selectedAgentId && !existing && (!needsAccount || matchingAccount) && readString(item.runtime_lane) !== PERSONAL_CHANNEL_SURFACE_LANE);
    const busyKey = `catalog:${channelKey}`;
    const busy = channelActionKey === busyKey;
    const surfaceKind = readString(item.surface_kind, 'messaging_channel');
    const label = readString(item.label, channelKey);
    const stageLabel = readString(item.stage, 'planned').replace(/_/g, ' ');
    const requirementLabel = existing
      ? 'Already bound'
      : matchingAccount
        ? 'Saved account available'
        : needsAccount
          ? 'Needs account'
          : 'No account required';
    const capabilityTags = readStringList(item.capabilities).map((tag) => tag.replace(/_/g, ' '));
    const visibleCapabilityTags = capabilityTags.slice(0, 2);
    const hiddenCapabilityCount = Math.max(0, capabilityTags.length - visibleCapabilityTags.length);
    return (
      <article key={channelKey} className="studio-agent-integrations__provider-card studio-agent-integrations__provider-card--compact">
        <div className="studio-agent-integrations__provider-main">
          <ChannelBrandMark item={item} />
          <div className="studio-agent-integrations__provider-copy">
            <div className="studio-agent-integrations__provider-title-row">
              <strong>{label}</strong>
              <span className={joinClassNames('studio-agent-integrations__status', existing && 'studio-agent-integrations__status--ready')}>
                {existing ? 'Bound' : readString(item.runtime_lane) === PERSONAL_CHANNEL_SURFACE_LANE ? 'Sage only' : stageLabel}
              </span>
            </div>
            <span className="studio-agent-integrations__provider-requirement">{requirementLabel}</span>
            <div className="sage-unified-expand__tag-row">
              {visibleCapabilityTags.map((tag) => (
                <span key={tag} className="sage-unified-expand__tag">{tag}</span>
              ))}
              {hiddenCapabilityCount > 0 ? (
                <span className="sage-unified-expand__tag">+{hiddenCapabilityCount}</span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="studio-agent-integrations__actions">
          <AppButton
            type="button"
            tone={canBind ? 'primary' : 'secondary'}
            disabled={!canBind || busy}
            onClick={() => runChannelAction(
              busyKey,
              () => services.client.createAgentChannelBinding({
                deployedAgentId: selectedAgentId || '',
                catalogId: channelKey,
                accountRef: readString(matchingAccount?.account_ref) || null,
              }),
              surfaceKind === 'connected_app' ? 'Agent app binding created.' : 'Agent channel binding created.',
            )}
          >
            {existing ? 'Bound' : matchingAccount ? 'Use setup' : 'Setup required'}
          </AppButton>
        </div>
      </article>
    );
  }

  if (view === 'channels') {
    return (
      <div className="studio-agent-integrations">
        <section className="studio-agent-integrations__section" aria-label="Agent channel bindings">
          <div className="studio-agent-integrations__head">
            <div>
              <span>Messaging</span>
              <strong>Customer channels</strong>
              <p>Connect where customers talk to this agent.</p>
            </div>
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => router.push(`/w/${encodeURIComponent(workspaceId)}/channels`)}
            >
              Manage accounts
            </AppButton>
          </div>
          <div className="studio-agent-integrations__channel-summary">
            <span>{channelAccounts.length} reusable account{channelAccounts.length === 1 ? '' : 's'}</span>
            <span>{channelBindings.length} binding{channelBindings.length === 1 ? '' : 's'} on this agent</span>
            <span>{businessChannelCatalog.length} business channel{businessChannelCatalog.length === 1 ? '' : 's'}</span>
            <span>{personalCatalog.length} personal channel{personalCatalog.length === 1 ? '' : 's'} kept in Sage</span>
          </div>
          {channelError ? (
            <div className="deployed-agents-overlay__empty">{channelError}</div>
          ) : null}
          {channelNotice ? (
            <div className="studio-agent-integrations__notice">{channelNotice}</div>
          ) : null}
          <div className="studio-agent-integrations__provider-grid">
            {channelLoading ? (
              <div className="deployed-agents-overlay__empty">Loading channel setup...</div>
            ) : channelBindings.length === 0 ? (
              <div className="deployed-agents-overlay__empty">No channel bindings yet. Choose a channel below.</div>
            ) : channelBindings.map((binding) => {
              const channelKey = readString(binding.channel_key);
              const enabled = binding.enabled !== false;
              const status = readString(binding.status, enabled ? 'enabled' : 'paused');
              const actionBusy = channelActionKey === `binding:${channelKey}`;
              return (
                <article key={channelKey} className="studio-agent-integrations__provider-card">
                  <div className="studio-agent-integrations__provider-main">
                    <ChannelBrandMark item={binding} />
                    <div className="studio-agent-integrations__provider-copy">
                      <strong>{readString(binding.label, channelKey)}</strong>
                      <span>{readString(binding.account_label, 'No account label')} · {readString(binding.endpoint_key, 'No endpoint')}</span>
                      <span>{readString(binding.runtime_lane, STUDIO_CHANNEL_SURFACE_LANE)} · {binding.live_capable ? 'Live-capable adapter' : 'Foundation adapter'}</span>
                      <div className="studio-agent-integrations__actions">
                        <AppButton
                          type="button"
                          tone="secondary"
                          disabled={actionBusy}
                          onClick={() => runChannelAction(
                            `binding:${channelKey}`,
                            () => services.client.testAgentChannelBinding({ deployedAgentId: selectedAgentId || '', channelKey }),
                            'Dry-run channel test recorded.',
                          )}
                        >
                          Dry-run test
                        </AppButton>
                        <AppButton
                          type="button"
                          tone="secondary"
                          disabled={actionBusy}
                          onClick={() => runChannelAction(
                            `binding:${channelKey}`,
                            () => (enabled
                              ? services.client.pauseAgentChannelBinding({ deployedAgentId: selectedAgentId || '', channelKey })
                              : services.client.resumeAgentChannelBinding({ deployedAgentId: selectedAgentId || '', channelKey })),
                            enabled ? 'Channel binding paused.' : 'Channel binding resumed.',
                          )}
                        >
                          {enabled ? 'Pause' : 'Resume'}
                        </AppButton>
                        <AppButton
                          type="button"
                          tone="danger"
                          disabled={actionBusy}
                          onClick={() => runChannelAction(
                            `binding:${channelKey}`,
                            () => services.client.revokeAgentChannelBinding({ deployedAgentId: selectedAgentId || '', channelKey }),
                            'Channel binding revoked.',
                          )}
                        >
                          Revoke
                        </AppButton>
                      </div>
                    </div>
                  </div>
                  <span className={joinClassNames('studio-agent-integrations__status', enabled && status !== 'revoked' && 'studio-agent-integrations__status--ready')}>
                    {status}
                  </span>
                </article>
              );
            })}
          </div>
          <div className="studio-agent-integrations__subhead">
            <strong>Available business channels</strong>
            <span>Customer-safe routes for deployed agents.</span>
          </div>
          <div className="studio-agent-integrations__provider-grid">
            {businessChannelCatalog.map(renderCatalogSetupCard)}
          </div>
          <div className="studio-agent-integrations__foot">
            Personal channels stay in Sage. Studio uses official business channels.
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="studio-agent-integrations">
      <section className="studio-agent-integrations__section" aria-label="Agent app integrations">
        <div className="studio-agent-integrations__head">
          <div>
            <span>Apps and systems</span>
            <strong>External accounts this agent can use</strong>
            <p>Use this for work systems the agent may read or act inside. Customer chat channels live under Channels.</p>
          </div>
          <AppButton
            type="button"
            tone="secondary"
            onClick={() => router.push(`/w/${encodeURIComponent(workspaceId)}/integrations`)}
          >
            Manage accounts
          </AppButton>
        </div>
        <div className="studio-agent-integrations__channel-summary">
          <span>{channelAccounts.length} reusable account{channelAccounts.length === 1 ? '' : 's'}</span>
          <span>{channelBindings.length} binding{channelBindings.length === 1 ? '' : 's'} on this agent</span>
          <span>{connectedAppCatalog.length} connected app{connectedAppCatalog.length === 1 ? '' : 's'}</span>
        </div>
        {channelError ? (
          <div className="deployed-agents-overlay__empty">{channelError}</div>
        ) : null}
        {channelNotice ? (
          <div className="studio-agent-integrations__notice">{channelNotice}</div>
        ) : null}
        <div className="studio-agent-integrations__subhead">
          <strong>Connected app bindings</strong>
          <span>Reusable account routes for work systems.</span>
        </div>
        <div className="studio-agent-integrations__provider-grid">
          {channelLoading ? (
            <div className="deployed-agents-overlay__empty">Loading app setup...</div>
          ) : connectedAppCatalog.length === 0 ? (
            <div className="deployed-agents-overlay__empty">No app permissions are available yet.</div>
          ) : connectedAppCatalog.map(renderCatalogSetupCard)}
        </div>
        <div className="studio-agent-integrations__subhead">
          <strong>Available app surfaces</strong>
          <span>Install or bind accounts before enabling permissions in Actions.</span>
        </div>
        <div className="sage-unified-grid sage-unified-grid--4">
          {appConnectorCards.map((connector) => (
            <article key={connector.id} className="sage-unified-card deployed-agents-overlay__connector-card">
              <span className="sage-integration-brand" aria-hidden="true">
                <img src={connector.image} alt="" className="sage-integration-brand__image" />
              </span>
              <strong className="sage-unified-card__title">{connector.label}</strong>
              <span className={joinClassNames('sage-unified-card__status', connector.connected && 'sage-unified-card__status--connected')}>
                {connector.connected ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
                {connector.statusLabel}
              </span>
              <div className="sage-unified-expand__tag-row">
                {connector.capabilityTags.map((tag) => (
                  <span key={tag} className="sage-unified-expand__tag">{tag}</span>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
