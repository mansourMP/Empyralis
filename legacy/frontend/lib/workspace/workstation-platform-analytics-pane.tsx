'use client';

import { useEffect, useMemo, useState } from 'react';

import {
  DataTable,
  DataTableCell,
  DataTableHeader,
  DataTableHeaderCell,
  DataTableRow,
} from '@/lib/ui/data-table';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { FormGrid, FormReadout } from '@/lib/ui/form-controls';
import { ListDetailColumns, ListDetailPanel, ListDetailShell } from '@/lib/ui/list-detail';
import { AppButton } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StateBanner } from '@/lib/ui/state-banner';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readItems(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : [];
}

function readText(value: unknown, fallback = 'n/a'): string {
  const token = typeof value === 'string' ? value.trim() : '';
  return token || fallback;
}

function readNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  const token = typeof value === 'string' ? Number(value) : NaN;
  return Number.isFinite(token) ? token : fallback;
}

function formatUsd(value: unknown): string {
  return `$${readNumber(value, 0).toFixed(2)}`;
}

function formatPercent(value: unknown): string {
  return `${readNumber(value, 0).toFixed(2)}%`;
}

function formatProviderModel(row: Record<string, unknown>): string {
  const provider = readText(row.provider, 'unknown');
  const model = readText(row.model, 'unknown');
  return `${provider} · ${model}`;
}

export function WorkstationPlatformAnalyticsPane() {
  const { bootstrap, hasCapability } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const isPlatformAdmin = hasCapability('platform_admin_enabled');
  const isWorkspaceOwner = bootstrap.membership.role === 'owner' || bootstrap.membership.role === 'admin';
  const canViewUsage = isPlatformAdmin || isWorkspaceOwner;

  const loadAnalytics = async () => {
    const response = isPlatformAdmin
      ? await services.client.getPlatformAnalytics()
      : await services.client.getUsageSummary({ period: 'month' });
    setPayload(response);
  };

  useEffect(() => {
    if (!canViewUsage) {
      setPayload(null);
      setIsLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void loadAnalytics()
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Platform analytics are unavailable.');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [canViewUsage, isPlatformAdmin, services.client]);

  const summary = useMemo(() => readRecord(payload?.summary), [payload]);
  const deployments = useMemo(() => readItems(payload?.deployments), [payload]);
  const costBreakdown = useMemo(() => readItems(payload?.model_cost_breakdown), [payload]);
  const providerBreakdown = useMemo(() => readItems(payload?.by_provider), [payload]);
  const modelBreakdown = useMemo(() => readItems(payload?.by_model), [payload]);
  const dailyBreakdown = useMemo(() => readItems(payload?.daily), [payload]);

  return (
    <div data-workstation-surface="admin/platform">
      <ListDetailShell
        title={isPlatformAdmin ? 'Platform analytics' : 'Usage'}
        subtitle={isPlatformAdmin
          ? 'Platform-scoped cross-workspace analytics for auth-admin operators. These numbers are not limited to the current workspace.'
          : 'Workspace-owner usage for the current account this month, including metered cost, runs, and provider/model breakdown.'}
        actions={(
          <AppButton
            type="button"
            tone="secondary"
            disabled={isLoading || !canViewUsage}
            onClick={() => {
              setStatus(null);
              setError(null);
              setIsLoading(true);
              void loadAnalytics()
                .then(() => {
                  setStatus(isPlatformAdmin ? 'Platform analytics refreshed.' : 'Usage refreshed.');
                })
                .catch((loadError) => {
                  setError(loadError instanceof Error ? loadError.message : isPlatformAdmin ? 'Platform analytics are unavailable.' : 'Usage is unavailable.');
                })
                .finally(() => {
                  setIsLoading(false);
                });
            }}
          >
            Refresh
          </AppButton>
        )}
      >
        {!canViewUsage ? (
          <StateBanner
            tone="danger"
            title="Owner access required"
            detail="Usage is limited to the workspace owner or workspace admin. Cross-workspace platform analytics remain auth-admin only."
          />
        ) : null}
        {status ? <StateBanner tone="success" title="Updated" detail={status} /> : null}
        {error ? (
          <StateBanner
            tone="danger"
            title={isPlatformAdmin ? 'Platform analytics could not refresh' : 'Usage could not refresh'}
            detail="Retry when the service is ready."
          />
        ) : null}
        {isPlatformAdmin ? (
          <ListDetailColumns
            primary={(
              <div className="app-stack-4">
                <ListDetailPanel
                  eyebrow="Platform scope"
                  title="Cross-workspace totals"
                  subtitle="Thirty-day active users and current-month activity rolled up across deployed agents."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="5rem" />
                      <SkeletonBlock height="5rem" />
                    </div>
                  ) : (
                    <FormGrid columns="repeat(2, minmax(0, 1fr))">
                      <FormReadout label="Active external users (30d)" value={String(readNumber(summary.total_active_external_users_30d, 0))} />
                      <FormReadout label="Total messages (30d)" value={String(readNumber(summary.total_messages, 0))} />
                      <FormReadout label="Deployed agents" value={String(readNumber(summary.total_deployed_agents, 0))} />
                      <FormReadout label="Current month cost" value={formatUsd(summary.current_month_cost_usd)} />
                      <FormReadout label="Message volume today" value={String(readNumber(readRecord(summary.message_volume).day, 0))} />
                      <FormReadout label="Message volume this week" value={String(readNumber(readRecord(summary.message_volume).week, 0))} />
                    </FormGrid>
                  )}
                </ListDetailPanel>

                <ListDetailPanel
                  eyebrow="Deployments"
                  title="Most active deployed agents"
                  subtitle="Sorted by message volume and active users, with a billing proxy attached to each deployment row."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="3.2rem" />
                      <SkeletonBlock height="3.2rem" />
                      <SkeletonBlock height="3.2rem" />
                    </div>
                  ) : deployments.length === 0 ? (
                    <EmptyPanel
                      title="No deployed agents yet"
                      body="Create and deploy your first specialist to start tracking usage, activity, and cost here."
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 1.5fr) minmax(10rem, 1fr) minmax(10rem, 1fr) minmax(9rem, 0.8fr)">
                        <DataTableHeaderCell>Deployed agent</DataTableHeaderCell>
                        <DataTableHeaderCell>Activity</DataTableHeaderCell>
                        <DataTableHeaderCell>Billing proxy</DataTableHeaderCell>
                        <DataTableHeaderCell>Current month cost</DataTableHeaderCell>
                      </DataTableHeader>
                      {deployments.map((row, index) => {
                        const workspace = readRecord(row.workspace);
                        const messageVolume = readRecord(row.message_volume);
                        const billingProxy = readRecord(row.billing_proxy);
                        const costBurn = readRecord(row.cost_burn);
                        const deployedAgentId = readText(row.deployed_agent_id, `deployment-${index}`);
                        return (
                          <DataTableRow
                            key={deployedAgentId}
                            columns="minmax(0, 1.5fr) minmax(10rem, 1fr) minmax(10rem, 1fr) minmax(9rem, 0.8fr)"
                          >
                            <DataTableCell
                              primary={readText(row.name, deployedAgentId)}
                              secondary={`${readText(workspace.label, readText(workspace.id, 'workspace'))} · ${formatProviderModel(row)}`}
                              meta={readText(row.deployment_state, 'draft')}
                            />
                            <DataTableCell
                              primary={`${readNumber(messageVolume.month, 0)} messages / month`}
                              secondary={`${readNumber(row.active_users_last_30d, 0)} active users / 30d`}
                              meta={`day ${readNumber(messageVolume.day, 0)} · week ${readNumber(messageVolume.week, 0)}`}
                            />
                            <DataTableCell
                              primary={readText(billingProxy.effective_plan_label, 'Free')}
                              secondary={`${readText(billingProxy.subscription_status, 'inactive')} · deployment ${readText(billingProxy.deployment_billing_plan, 'free')}`}
                              meta={readText(billingProxy.provider, 'stripe')}
                            />
                            <DataTableCell
                              primary={formatUsd(costBurn.current_burn_usd)}
                              secondary={`${readNumber(costBurn.runs_count, 0)} metered runs`}
                              meta={readText(costBurn.usage_month, 'current cycle')}
                            />
                          </DataTableRow>
                        );
                      })}
                    </DataTable>
                  )}
                </ListDetailPanel>
              </div>
            )}
            secondary={(
              <div className="app-stack-4">
                <ListDetailPanel
                  eyebrow="Cost mix"
                  title="Model and provider cost breakdown"
                  subtitle="Current-month estimated cost grouped by provider and model from the deployed-agent cost ledger."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="3rem" />
                      <SkeletonBlock height="3rem" />
                    </div>
                  ) : costBreakdown.length === 0 ? (
                    <EmptyPanel
                      title="No metered runs yet"
                      body="Run Sage or a specialist with a metered model to populate provider and cost breakdowns."
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)">
                        <DataTableHeaderCell>Provider / model</DataTableHeaderCell>
                        <DataTableHeaderCell>Runs</DataTableHeaderCell>
                        <DataTableHeaderCell>Tokens</DataTableHeaderCell>
                        <DataTableHeaderCell>Cost share</DataTableHeaderCell>
                      </DataTableHeader>
                      {costBreakdown.map((row, index) => (
                        <DataTableRow
                          key={`${readText(row.provider, 'provider')}:${readText(row.model, String(index))}`}
                          columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)"
                        >
                          <DataTableCell
                            primary={formatProviderModel(row)}
                            secondary={formatUsd(row.total_cost_usd)}
                          />
                          <DataTableCell primary={String(readNumber(row.runs_count, 0))} />
                          <DataTableCell primary={String(readNumber(row.total_tokens, 0))} />
                          <DataTableCell
                            primary={formatPercent(row.share_percent)}
                            secondary={formatUsd(row.total_cost_usd)}
                          />
                        </DataTableRow>
                      ))}
                    </DataTable>
                  )}
                </ListDetailPanel>
              </div>
            )}
          />
        ) : canViewUsage ? (
          <ListDetailColumns
            primary={(
              <div className="app-stack-4">
                <ListDetailPanel
                  eyebrow="This month"
                  title="Workspace usage"
                  subtitle="Metered usage aggregated for the current authenticated owner session."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="5rem" />
                      <SkeletonBlock height="5rem" />
                    </div>
                  ) : (
                    <FormGrid columns="repeat(2, minmax(0, 1fr))">
                      <FormReadout label="Current month cost" value={formatUsd(payload?.total_cost_usd)} />
                      <FormReadout label="Metered runs" value={String(readNumber(payload?.runs_count, 0))} />
                      <FormReadout label="Total tokens" value={String(readNumber(payload?.total_tokens, 0))} />
                      <FormReadout label="Membership role" value={bootstrap.membership.role} />
                    </FormGrid>
                  )}
                </ListDetailPanel>

                <ListDetailPanel
                  eyebrow="Providers"
                  title="Provider usage mix"
                  subtitle="Monthly token and cost totals grouped by provider for this account."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="3.2rem" />
                      <SkeletonBlock height="3.2rem" />
                    </div>
                  ) : providerBreakdown.length === 0 ? (
                    <EmptyPanel
                      title="No metered usage yet"
                      body="Once this workspace records metered model usage, provider totals will appear here."
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)">
                        <DataTableHeaderCell>Provider</DataTableHeaderCell>
                        <DataTableHeaderCell>Runs</DataTableHeaderCell>
                        <DataTableHeaderCell>Tokens</DataTableHeaderCell>
                        <DataTableHeaderCell>Cost</DataTableHeaderCell>
                      </DataTableHeader>
                      {providerBreakdown.map((row, index) => (
                        <DataTableRow
                          key={`${readText(row.provider, 'provider')}:${index}`}
                          columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)"
                        >
                          <DataTableCell
                            primary={readText(row.provider, 'unknown')}
                            secondary={formatPercent(row.percentage)}
                          />
                          <DataTableCell primary={String(readNumber(row.runs_count, 0))} />
                          <DataTableCell primary={String(readNumber(row.total_tokens, 0))} />
                          <DataTableCell primary={formatUsd(row.total_cost_usd)} />
                        </DataTableRow>
                      ))}
                    </DataTable>
                  )}
                </ListDetailPanel>
              </div>
            )}
            secondary={(
              <div className="app-stack-4">
                <ListDetailPanel
                  eyebrow="Models"
                  title="Model breakdown"
                  subtitle="Current-month cost grouped by provider and model for this account."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="3rem" />
                      <SkeletonBlock height="3rem" />
                    </div>
                  ) : modelBreakdown.length === 0 ? (
                    <EmptyPanel
                      title="No model data yet"
                      body="Once metered runs are recorded, this view will break usage down by model."
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)">
                        <DataTableHeaderCell>Provider / model</DataTableHeaderCell>
                        <DataTableHeaderCell>Runs</DataTableHeaderCell>
                        <DataTableHeaderCell>Tokens</DataTableHeaderCell>
                        <DataTableHeaderCell>Cost</DataTableHeaderCell>
                      </DataTableHeader>
                      {modelBreakdown.map((row, index) => (
                        <DataTableRow
                          key={`${readText(row.provider, 'provider')}:${readText(row.model, String(index))}`}
                          columns="minmax(0, 1.4fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)"
                        >
                          <DataTableCell
                            primary={formatProviderModel(row)}
                            secondary={formatUsd(row.total_cost_usd)}
                          />
                          <DataTableCell primary={String(readNumber(row.runs_count, 0))} />
                          <DataTableCell primary={String(readNumber(row.total_tokens, 0))} />
                          <DataTableCell primary={formatUsd(row.total_cost_usd)} />
                        </DataTableRow>
                      ))}
                    </DataTable>
                  )}
                </ListDetailPanel>

                <ListDetailPanel
                  eyebrow="Daily"
                  title="Recent usage days"
                  subtitle="Daily token and cost totals for the current month."
                >
                  {isLoading ? (
                    <div className="app-stack-3">
                      <SkeletonBlock height="3rem" />
                      <SkeletonBlock height="3rem" />
                    </div>
                  ) : dailyBreakdown.length === 0 ? (
                    <EmptyPanel
                      title="No daily usage yet"
                      body="As soon as metered runs are recorded, daily usage will appear here."
                    />
                  ) : (
                    <DataTable>
                      <DataTableHeader columns="minmax(0, 1fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)">
                        <DataTableHeaderCell>Date</DataTableHeaderCell>
                        <DataTableHeaderCell>Runs</DataTableHeaderCell>
                        <DataTableHeaderCell>Tokens</DataTableHeaderCell>
                        <DataTableHeaderCell>Cost</DataTableHeaderCell>
                      </DataTableHeader>
                      {dailyBreakdown.map((row, index) => (
                        <DataTableRow
                          key={`${readText(row.date, String(index))}`}
                          columns="minmax(0, 1fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr) minmax(8rem, 0.8fr)"
                        >
                          <DataTableCell primary={readText(row.date, 'unknown')} />
                          <DataTableCell primary={String(readNumber(row.runs_count, 0))} />
                          <DataTableCell primary={String(readNumber(row.total_tokens, 0))} />
                          <DataTableCell primary={formatUsd(row.total_cost_usd)} />
                        </DataTableRow>
                      ))}
                    </DataTable>
                  )}
                </ListDetailPanel>
              </div>
            )}
          />
        ) : null}
      </ListDetailShell>
    </div>
  );
}
