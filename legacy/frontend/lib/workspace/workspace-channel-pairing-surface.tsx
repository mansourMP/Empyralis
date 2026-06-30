'use client';

import { useEffect, useMemo, useState } from 'react';

import { AppButton } from '@/lib/ui/primitives';
import { CommandSheet } from '@/lib/ui/command-sheet';
import { ConfirmDialog } from '@/lib/ui/confirm-dialog';
import { DataBadge, DataTable, DataTableCell, DataTableHeader, DataTableHeaderCell, DataTableRow } from '@/lib/ui/data-table';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { FormGrid, FormReadout } from '@/lib/ui/form-controls';
import { ListDetailColumns, ListDetailPanel, ListDetailShell } from '@/lib/ui/list-detail';
import { ModalSection } from '@/lib/ui/modal';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StateBanner } from '@/lib/ui/state-banner';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { requestWorkspaceJson } from '@/lib/workspace/workspace-json-request';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';

type ChannelPairingFeatureId = 'settings' | 'integrations';
type ChannelProvider = 'telegram' | 'whatsapp';

type ChannelLinkRecord = {
  link_id: string;
  provider: string;
  external_subject_hint?: string | null;
  workspace_id: string;
  scopes: string[];
  linked_at?: number | null;
  revoked_at?: number | null;
  revoked_reason?: string | null;
  status: 'active' | 'revoked';
};

type ChannelPairingIntentRecord = {
  intent_id: string;
  provider: string;
  workspace_id: string;
  scopes: string[];
  allow_relink: boolean;
  created_at: number;
  expires_at: number;
  pairing_code: string;
  connect_url?: string;
  instructions: string;
  legacy_pairing_command?: string;
};

type ChannelLinksResponse = {
  ok: boolean;
  links: ChannelLinkRecord[];
};

type ChannelPairingIntentResponse = {
  ok: boolean;
  intent: ChannelPairingIntentRecord;
};

const CHANNEL_PROVIDER_DEFINITIONS: Record<
  ChannelProvider,
  {
    label: string;
    legacyCommandExample: string;
    helpText: string;
    capabilityKey: string;
  }
> = {
  telegram: {
    label: 'Telegram',
    legacyCommandExample: '/pair EMP-ABCD-EFGH',
    helpText: 'Start the Telegram channel. If it is not linked, it sends an Empyralis connect link and setup finishes here.',
    capabilityKey: 'telegram_channel_enabled',
  },
  whatsapp: {
    label: 'WhatsApp',
    legacyCommandExample: 'pair EMP-ABCD-EFGH',
    helpText: 'Start the WhatsApp channel. If it is not linked, it sends an Empyralis connect link and setup finishes here.',
    capabilityKey: 'whatsapp_channel_enabled',
  },
};

function formatTimestamp(value: number | null | undefined): string {
  if (!value) {
    return 'n/a';
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value * 1000));
  } catch {
    return String(value);
  }
}

function isIntentActive(intent: ChannelPairingIntentRecord | null | undefined): boolean {
  return Boolean(intent && intent.expires_at * 1000 > Date.now());
}

function isTechnicalIdentifier(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  return /^https?:\/\//i.test(trimmed)
    || /^[0-9a-f]{8}-[0-9a-f-]{8,}$/i.test(trimmed)
    || /^[A-Za-z0-9_-]{18,}$/.test(trimmed);
}

function truncateTechnicalIdentifier(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length <= 12) {
    return trimmed;
  }
  return `${trimmed.slice(0, 4)}…${trimmed.slice(-4)}`;
}

function channelLinkDisplayLabel(link: ChannelLinkRecord): string {
  const hint = String(link.external_subject_hint || '').trim();
  if (hint) {
    return isTechnicalIdentifier(hint) ? truncateTechnicalIdentifier(hint) : hint;
  }
  return `Linked user ${truncateTechnicalIdentifier(link.link_id)}`;
}

function summarizeLinkStatus(links: ChannelLinkRecord[]): string {
  const activeCount = links.filter((link) => link.status === 'active').length;
  const revokedCount = links.filter((link) => link.status === 'revoked').length;
  return `${activeCount} active · ${revokedCount} revoked`;
}

function IntegrationsSkeleton() {
  return (
    <ListDetailColumns
      primary={(
        <div className="app-stack-4">
          <ListDetailPanel eyebrow="Providers" title="Loading provider state">
            <SkeletonBlock height="4rem" />
            <SkeletonBlock height="4rem" />
          </ListDetailPanel>
          <ListDetailPanel eyebrow="Links" title="Loading linked channels">
            <SkeletonBlock height="2.8rem" />
            <SkeletonBlock height="2.8rem" />
            <SkeletonBlock height="2.8rem" />
          </ListDetailPanel>
        </div>
      )}
      secondary={(
        <ListDetailPanel eyebrow="Selection" title="Loading connection detail">
          <SkeletonBlock height="3.2rem" />
          <SkeletonBlock height="3.2rem" />
          <SkeletonBlock height="3.2rem" />
        </ListDetailPanel>
      )}
    />
  );
}

export function WorkspaceChannelPairingSurface({
  featureId,
}: {
  featureId: ChannelPairingFeatureId;
}) {
  const { bootstrap, hasCapability, shellProfile } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const workspaceId = bootstrap.workspace.id;
  const linksCacheKey = 'channel-pairing:links';
  const intentsCacheKey = 'channel-pairing:intents';

  const initialLinks = useMemo(
    () =>
      services.queryClient.peek<ChannelLinksResponse>(linksCacheKey)
      ?? services.persistence.getJson<ChannelLinksResponse>(linksCacheKey)
      ?? { ok: true, links: [] },
    [services],
  );
  const initialIntents = useMemo(
    () =>
      services.persistence.getJson<Partial<Record<ChannelProvider, ChannelPairingIntentRecord | null>>>(intentsCacheKey)
      ?? {},
    [services],
  );

  const [linksResponse, setLinksResponse] = useState<ChannelLinksResponse>(initialLinks);
  const [intentByProvider, setIntentByProvider] = useState<Partial<Record<ChannelProvider, ChannelPairingIntentRecord | null>>>(initialIntents);
  const [selectedProvider, setSelectedProvider] = useState<ChannelProvider>('telegram');
  const [loading, setLoading] = useState(false);
  const [isLoadedOnce, setIsLoadedOnce] = useState(initialLinks.links.length > 0);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionProvider, setActionProvider] = useState<ChannelProvider | null>(null);
  const [pendingRelinkProvider, setPendingRelinkProvider] = useState<ChannelProvider | null>(null);
  const [revokingLinkId, setRevokingLinkId] = useState<string | null>(null);
  const [pendingRevokeLink, setPendingRevokeLink] = useState<ChannelLinkRecord | null>(null);
  const [codeSheetProvider, setCodeSheetProvider] = useState<ChannelProvider | null>(null);

  useEffect(() => {
    services.persistence.setJson(intentsCacheKey, intentByProvider);
  }, [intentByProvider, intentsCacheKey, services]);

  const canPairChannels = hasCapability('channel_pairing_enabled');
  const activeLinks = useMemo(
    () => linksResponse.links.filter((link) => link.status === 'active'),
    [linksResponse.links],
  );
  const revokedLinks = useMemo(
    () => linksResponse.links.filter((link) => link.status === 'revoked'),
    [linksResponse.links],
  );
  const selectedDefinition = CHANNEL_PROVIDER_DEFINITIONS[selectedProvider];
  const selectedIntent = intentByProvider[selectedProvider] ?? null;
  const selectedProviderLinks = activeLinks.filter((link) => link.provider === selectedProvider);
  const providerEnabled = hasCapability(selectedDefinition.capabilityKey);
  const visibleProviders: ChannelProvider[] = ['telegram', 'whatsapp'];

  async function refreshLinks(options: { silent?: boolean } = {}): Promise<ChannelLinksResponse> {
    if (!options.silent) {
      setLoading(true);
    }
    setErrorMessage(null);

    try {
      const payload = await requestWorkspaceJson<ChannelLinksResponse>(
        services,
        `/api/channel-pairing/links?workspace_id=${encodeURIComponent(workspaceId)}&include_revoked=true`,
      );
      services.queryClient.set(linksCacheKey, payload);
      services.persistence.setJson(linksCacheKey, payload);
      setLinksResponse(payload);
      setIsLoadedOnce(true);
      if (!options.silent) {
        setStatusMessage(`Refreshed channel link state from the backend. ${summarizeLinkStatus(payload.links)}`);
      }
      return payload;
    } catch (error) {
      const fallback = services.persistence.getJson<ChannelLinksResponse>(linksCacheKey);
      if (fallback) {
        setLinksResponse(fallback);
        setIsLoadedOnce(true);
        setStatusMessage('Showing cached channel connection state because the backend is temporarily unreachable.');
      }
      const message = error instanceof Error ? error.message : 'Could not load channel links.';
      setErrorMessage(message);
      throw error;
    } finally {
      if (!options.silent) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void refreshLinks({ silent: initialLinks.links.length > 0 }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const createIntentRequest = async (provider: ChannelProvider, allowRelink: boolean) => {
    if (!canPairChannels) {
      setErrorMessage('Channel connection is blocked for this workspace membership.');
      return;
    }
    if (!hasCapability(CHANNEL_PROVIDER_DEFINITIONS[provider].capabilityKey)) {
      setErrorMessage(`${CHANNEL_PROVIDER_DEFINITIONS[provider].label} is not enabled for this workspace plan.`);
      return;
    }

    setActionProvider(provider);
    setErrorMessage(null);
    try {
      const payload = await requestWorkspaceJson<ChannelPairingIntentResponse>(
        services,
        '/api/channel-pairing/intents',
        {
          method: 'POST',
          body: JSON.stringify({
            provider,
            workspace_id: workspaceId,
            allow_relink: allowRelink,
            metadata: {
              source: 'frontend_v2',
              surface: featureId,
            },
          }),
        },
      );
      setIntentByProvider((current) => ({
        ...current,
        [provider]: payload.intent,
      }));
      setSelectedProvider(provider);
      setCodeSheetProvider(provider);
      setStatusMessage(
        `${CHANNEL_PROVIDER_DEFINITIONS[provider].label} connection prepared. Expires ${formatTimestamp(payload.intent.expires_at)}.`,
      );
      await refreshLinks({ silent: true }).catch(() => undefined);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not prepare channel connection.');
    } finally {
      setActionProvider(null);
      setPendingRelinkProvider(null);
    }
  };

  const createIntent = async (provider: ChannelProvider) => {
    const providerLinks = activeLinks.filter((link) => link.provider === provider);
    if (providerLinks.length > 0) {
      setPendingRelinkProvider(provider);
      return;
    }
    await createIntentRequest(provider, false);
  };

  const revokeLink = async (link: ChannelLinkRecord) => {
    setRevokingLinkId(link.link_id);
    setErrorMessage(null);
    try {
      await requestWorkspaceJson<{ ok: boolean; link: ChannelLinkRecord }>(
        services,
        `/api/channel-pairing/links/${encodeURIComponent(link.link_id)}/revoke`,
        {
          method: 'POST',
          body: JSON.stringify({
            confirm: true,
            reason: `Revoked from ${featureId} surface in the frontend v2 shell.`,
          }),
        },
      );
      setStatusMessage(
        `${CHANNEL_PROVIDER_DEFINITIONS[link.provider as ChannelProvider]?.label ?? link.provider} link revoked.`,
      );
      await refreshLinks({ silent: true });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not revoke channel link.');
    } finally {
      setRevokingLinkId(null);
      setPendingRevokeLink(null);
    }
  };

  const title = featureId === 'settings' ? 'Channel connections' : 'Studio · Channels';
  const intro =
    featureId === 'settings'
      ? 'Connect Telegram and WhatsApp identities from Empyralis, then use the channel only for messages.'
      : 'Connect customer channels, manage linked identities, and keep launch-ready coverage in one place.';

  return (
    <WorkstationSurfaceRoot surface={featureId}>
      <ListDetailShell
        title={title}
        subtitle={intro}
        actions={(
          <AppButton
            type="button"
            tone="secondary"
            onClick={() => {
              void refreshLinks();
            }}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </AppButton>
        )}
      >
        <StateBanner
          tone={canPairChannels ? 'neutral' : 'warning'}
          title={canPairChannels ? 'Channel connections are available' : 'Channel connections are blocked'}
          detail={`${bootstrap.workspace.label} · shell profile ${shellProfile.label}`}
        >
          {canPairChannels
            ? `Link status ${summarizeLinkStatus(linksResponse.links)}.`
            : 'Channel connections require the correct workspace role and channel access settings.'}
        </StateBanner>

        {statusMessage ? (
          <StateBanner tone="success" title={featureId === 'integrations' ? 'Channels updated' : 'Integrations updated'}>
            {statusMessage}
          </StateBanner>
        ) : null}
        {errorMessage ? (
          <StateBanner tone="danger" title={featureId === 'integrations' ? 'Channels need attention' : 'Integrations need attention'}>
            {errorMessage}
          </StateBanner>
        ) : null}

        {(!isLoadedOnce && loading) ? (
          <IntegrationsSkeleton />
        ) : (
          <ListDetailColumns
            primary={(
              <div className="app-stack-4">
                <ListDetailPanel
                  eyebrow="Providers"
                  title="Connected channel providers"
                  subtitle="Select a provider to review readiness, active links, and the latest platform connection."
                >
                  <div className="app-stack-3">
                    {visibleProviders.map((provider) => {
                      const definition = CHANNEL_PROVIDER_DEFINITIONS[provider];
                      const providerLinks = activeLinks.filter((link) => link.provider === provider);
                      const enabled = hasCapability(definition.capabilityKey);
                      const selected = provider === selectedProvider;
                      return (
                        <button
                          key={provider}
                          type="button"
                          onClick={() => setSelectedProvider(provider)}
                          className={`app-card-button${selected ? ' app-card-button--selected' : ''}`}
                        >
                          <div className="app-inline-actions app-inline-actions--between app-inline-actions--start">
                            <strong className="app-card-button__title">{definition.label}</strong>
                            <DataBadge tone={!enabled ? 'warning' : providerLinks.length > 0 ? 'success' : 'accent'}>
                              {!enabled ? 'Disabled' : providerLinks.length > 0 ? `${providerLinks.length} linked` : 'Ready'}
                            </DataBadge>
                          </div>
                          <span className="app-card-button__subtitle">
                            {definition.helpText}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </ListDetailPanel>

                <ListDetailPanel
                  eyebrow="Links"
                  title="Active channel links"
                  subtitle={`${activeLinks.length} active links currently connected to this workspace.`}
                >
                  {activeLinks.length === 0 ? (
                    <EmptyPanel
                      title="No active links"
                      body={`Prepare a connection, open ${selectedDefinition.label}, and finish linking from the Empyralis connect link.`}
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 0.75fr) minmax(0, 1fr) minmax(0, 0.9fr) auto">
                        <DataTableHeaderCell>Provider</DataTableHeaderCell>
                        <DataTableHeaderCell>Linked subject</DataTableHeaderCell>
                        <DataTableHeaderCell>Linked</DataTableHeaderCell>
                        <DataTableHeaderCell align="end">Actions</DataTableHeaderCell>
                      </DataTableHeader>
                      {activeLinks.map((link) => (
                        <DataTableRow
                          key={link.link_id}
                          columns="minmax(0, 0.75fr) minmax(0, 1fr) minmax(0, 0.9fr) auto"
                        >
                          <DataTableCell primary={CHANNEL_PROVIDER_DEFINITIONS[link.provider as ChannelProvider]?.label ?? link.provider} />
                          <DataTableCell
                            primary={channelLinkDisplayLabel(link)}
                            secondary={link.scopes.join(', ') || 'default scopes'}
                          />
                          <DataTableCell primary={formatTimestamp(link.linked_at)} />
                          <DataTableCell
                            align="end"
                            primary={(
                              <div className="app-inline-actions app-inline-actions--end app-inline-actions--tight">
                                <AppButton
                                  type="button"
                                  tone="secondary"
                                  onClick={() => {
                                    if (link.provider === 'telegram' || link.provider === 'whatsapp') {
                                      setSelectedProvider(link.provider);
                                    }
                                  }}
                                  disabled={link.provider !== 'telegram' && link.provider !== 'whatsapp'}
                                >
                                  Inspect
                                </AppButton>
                                <AppButton
                                  type="button"
                                  tone="danger"
                                  onClick={() => setPendingRevokeLink(link)}
                                  disabled={revokingLinkId === link.link_id}
                                >
                                  {revokingLinkId === link.link_id ? 'Revoking…' : 'Revoke'}
                                </AppButton>
                              </div>
                            )}
                          />
                        </DataTableRow>
                      ))}
                    </DataTable>
                  )}
                </ListDetailPanel>

                {revokedLinks.length > 0 ? (
                  <ListDetailPanel
                    eyebrow="History"
                    title="Revoked link history"
                    subtitle={`${revokedLinks.length} revoked links retained for workspace traceability.`}
                  >
                    <div className="app-stack-3">
                      {revokedLinks.map((link) => (
                        <div
                          key={link.link_id}
                          className="app-card-button"
                        >
                          <strong className="app-card-button__title">
                            {CHANNEL_PROVIDER_DEFINITIONS[link.provider as ChannelProvider]?.label ?? link.provider} · {channelLinkDisplayLabel(link)}
                          </strong>
                          <span className="app-card-button__subtitle">
                            Revoked {formatTimestamp(link.revoked_at)} · {link.revoked_reason ?? 'No reason recorded.'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </ListDetailPanel>
                ) : null}
              </div>
            )}
            secondary={(
              <ListDetailPanel
                eyebrow="Selection"
                title={`${selectedDefinition.label} connection`}
                subtitle="Current provider readiness, latest platform connection, and active workspace link status."
                actions={(
                  <div className="app-inline-actions">
                    {selectedIntent && isIntentActive(selectedIntent) ? (
                      <AppButton
                        type="button"
                        tone="secondary"
                        onClick={() => setCodeSheetProvider(selectedProvider)}
                      >
                        View connection
                      </AppButton>
                    ) : null}
                    <AppButton
                      type="button"
                      disabled={!providerEnabled || actionProvider === selectedProvider || loading}
                      onClick={() => {
                        void createIntent(selectedProvider);
                      }}
                    >
                      {actionProvider === selectedProvider
                        ? 'Creating…'
                        : selectedProviderLinks.length > 0
                          ? 'Prepare re-link'
                          : 'Prepare connection'}
                    </AppButton>
                  </div>
                )}
              >
                <StateBanner
                  tone={!providerEnabled ? 'warning' : selectedProviderLinks.length > 0 ? 'success' : 'neutral'}
                  title={!providerEnabled ? 'Provider disabled for this plan' : selectedProviderLinks.length > 0 ? 'Provider linked' : 'Provider ready to connect'}
                  detail={`${selectedProviderLinks.length} active links · setup stays inside Empyralis`}
                >
                  {selectedDefinition.helpText}
                </StateBanner>

                <FormGrid>
                  <FormReadout label="Workspace" value={bootstrap.workspace.label} />
                  <FormReadout label="Shell profile" value={shellProfile.label} />
                  <FormReadout label="Connection link" value={selectedIntent?.connect_url || 'Prepare a connection first'} />
                  <FormReadout label="Plan enabled" value={providerEnabled ? 'Yes' : 'No'} />
                  <FormReadout label="Active links" value={String(selectedProviderLinks.length)} />
                  <FormReadout label="Legacy fallback" value={selectedIntent && isIntentActive(selectedIntent) ? selectedIntent.pairing_code : 'No active fallback'} />
                </FormGrid>
                {selectedProviderLinks.length === 0 ? (
                  <EmptyPanel
                    title={`No active ${selectedDefinition.label} links`}
                    body={`Prepare a connection and link your first ${selectedDefinition.label} identity to this workspace from Empyralis.`}
                  />
                ) : (
                  <div className="app-stack-3">
                    {selectedProviderLinks.map((link) => (
                      <div
                        key={link.link_id}
                        className="app-card-button"
                      >
                        <strong className="app-card-button__title">{channelLinkDisplayLabel(link)}</strong>
                        <span className="app-card-button__subtitle">
                          Linked {formatTimestamp(link.linked_at)} · scopes {link.scopes.join(', ') || 'default'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </ListDetailPanel>
            )}
          />
        )}
      </ListDetailShell>

      <CommandSheet
        open={Boolean(codeSheetProvider && intentByProvider[codeSheetProvider] && isIntentActive(intentByProvider[codeSheetProvider] ?? null))}
        title={codeSheetProvider ? `${CHANNEL_PROVIDER_DEFINITIONS[codeSheetProvider].label} connection` : 'Channel connection'}
        description="Open the channel normally. If it is not linked, it sends a secure Empyralis connect link and setup finishes here."
        onClose={() => setCodeSheetProvider(null)}
        actions={<AppButton type="button" tone="secondary" onClick={() => setCodeSheetProvider(null)}>Done</AppButton>}
      >
        {codeSheetProvider && intentByProvider[codeSheetProvider] ? (
          <>
            <ModalSection title="Platform connection" description="Use the connect link when the channel asks you to finish setup in Empyralis.">
              <FormGrid columns="1fr">
                <FormReadout label="Connect link" value={intentByProvider[codeSheetProvider]?.connect_url ?? 'Open the channel to request a link.'} />
                <FormReadout label="Expires" value={formatTimestamp(intentByProvider[codeSheetProvider]?.expires_at ?? null)} />
                <FormReadout label="Legacy fallback code" value={intentByProvider[codeSheetProvider]?.pairing_code ?? 'n/a'} />
              </FormGrid>
            </ModalSection>
            <ModalSection title="Instructions" description="Setup and relinking stay inside Empyralis. The channel is only the message surface.">
              <div className="app-meta-value app-meta-value--body">
                {intentByProvider[codeSheetProvider]?.instructions ?? CHANNEL_PROVIDER_DEFINITIONS[codeSheetProvider].helpText}
              </div>
            </ModalSection>
          </>
        ) : null}
      </CommandSheet>

      <ConfirmDialog
        open={Boolean(pendingRelinkProvider)}
        title="Prepare re-link"
        body={pendingRelinkProvider ? `${CHANNEL_PROVIDER_DEFINITIONS[pendingRelinkProvider].label} already has an active link in this workspace. Prepare a re-link anyway?` : 'Prepare a re-link anyway?'}
        confirmLabel="Prepare re-link"
        confirmTone="primary"
        busy={pendingRelinkProvider ? actionProvider === pendingRelinkProvider : false}
        onCancel={() => setPendingRelinkProvider(null)}
        onConfirm={() => {
          if (!pendingRelinkProvider) {
            return;
          }
          void createIntentRequest(pendingRelinkProvider, true);
        }}
      />

      <ConfirmDialog
        open={Boolean(pendingRevokeLink)}
        title="Revoke linked channel"
        body={pendingRevokeLink ? `Revoke ${CHANNEL_PROVIDER_DEFINITIONS[pendingRevokeLink.provider as ChannelProvider]?.label ?? pendingRevokeLink.provider} link ${channelLinkDisplayLabel(pendingRevokeLink)}?` : 'Revoke linked channel?'}
        confirmLabel="Revoke link"
        busy={pendingRevokeLink ? revokingLinkId === pendingRevokeLink.link_id : false}
        onCancel={() => setPendingRevokeLink(null)}
        onConfirm={() => {
          if (!pendingRevokeLink) {
            return;
          }
          void revokeLink(pendingRevokeLink);
        }}
      />
    </WorkstationSurfaceRoot>
  );
}
