'use client';

import { useEffect, useMemo, useState } from 'react';

import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import {
  FormField,
  FormGrid,
  FormInput,
} from '@/lib/ui/form-controls';
import { AppButton } from '@/lib/ui/primitives';
import {
  WorkstationSurfaceCard,
  WorkstationSurfaceList,
  WorkstationSurfaceListItem,
  WorkstationSurfaceNotice,
  WorkstationSurfaceRoot,
  WorkstationSurfaceStat,
  WorkstationSurfaceStatGrid,
} from '@/lib/workspace/workstation-surface-primitives';

type BillingSummaryPayload = Record<string, unknown> & {
  configured?: boolean;
  portal_available?: boolean;
  account?: Record<string, unknown> | null;
  subscription?: Record<string, unknown> | null;
  usage?: Record<string, unknown> | null;
  hosted_sage_ai?: Record<string, unknown> | null;
  plans?: Array<Record<string, unknown>>;
};

type CreditUsagePayload = Record<string, unknown> & {
  items?: Array<Record<string, unknown>>;
  summary?: Record<string, unknown> | null;
};

type UsageLedgerFilter = 'all' | 'sage' | 'studio' | 'mini_app' | 'computer_runtime' | 'non_platform';

const USAGE_LEDGER_FILTERS: Array<{ id: UsageLedgerFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'sage', label: 'Sage' },
  { id: 'studio', label: 'Studio' },
  { id: 'mini_app', label: 'Mini-apps' },
  { id: 'computer_runtime', label: 'Runtime' },
  { id: 'non_platform', label: 'No-credit rows' },
];

function usageLedgerQuery(filter: UsageLedgerFilter): {
  surface?: string | null;
  creditType?: string | null;
} {
  if (filter === 'sage' || filter === 'studio' || filter === 'mini_app') {
    return { surface: filter };
  }
  if (filter === 'computer_runtime') {
    return { creditType: 'computer_runtime' };
  }
  return {};
}

function readText(value: unknown, fallback = 'n/a'): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function formatCredits(value: unknown): string {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(Math.max(0, Math.round(readNumber(value))));
}

function formatSignedCredits(value: unknown): string {
  const amount = Math.round(readNumber(value));
  if (amount === 0) {
    return '0 credits';
  }
  const formatted = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Math.abs(amount));
  return `${amount > 0 ? '-' : '+'}${formatted} credits`;
}

function surfaceLabel(value: unknown): string {
  const token = readText(value, '').toLowerCase();
  if (token === 'studio') {
    return 'Studio';
  }
  if (token === 'mini_app') {
    return 'Mini-app';
  }
  if (token === 'sage') {
    return 'Sage';
  }
  return token ? token.replace(/_/g, ' ') : 'Usage';
}

function creditTypeLabel(value: unknown): string {
  const token = readText(value, '').toLowerCase();
  if (token === 'computer_runtime') {
    return 'Runtime';
  }
  if (token === 'ai_tokens') {
    return 'AI';
  }
  if (token === 'connector_read') {
    return 'Connector';
  }
  if (token === 'mini_app_action') {
    return 'Mini-app action';
  }
  return token ? token.replace(/_/g, ' ') : 'Usage';
}

function usageRowSubtitle(item: Record<string, unknown>): string {
  const payer = readText(item.payer, '').toLowerCase();
  const credits = readNumber(item.credits_debited, 0);
  if (payer !== 'platform_credits') {
    return 'No Workspace AI usage';
  }
  return credits > 0 ? formatSignedCredits(credits) : '0 credits';
}

function usageRowDescription(item: Record<string, unknown>): string {
  const parts = [
    surfaceLabel(item.surface),
    creditTypeLabel(item.credit_type),
    readText(item.provider, ''),
    readText(item.model, ''),
    readText(item.runtime_target, ''),
  ].filter(Boolean);
  return parts.join(' · ') || 'Usage ledger row';
}

function hostedCreditValue(hostedSageAi: Record<string, unknown>, usdKey: string, creditKey: string): number {
  const explicitCredits = readNumber(hostedSageAi[creditKey], Number.NaN);
  if (Number.isFinite(explicitCredits)) {
    return explicitCredits;
  }
  const creditsPerUsd = readNumber(hostedSageAi.credits_per_usd, 1000);
  return readNumber(hostedSageAi[usdKey]) * creditsPerUsd;
}

export function WorkstationBillingPane() {
  const { bootstrap } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const [summary, setSummary] = useState<BillingSummaryPayload | null>(null);
  const [creditUsage, setCreditUsage] = useState<CreditUsagePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [usageLoading, setUsageLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingPlanId, setPendingPlanId] = useState<string | null>(null);
  const [portalPending, setPortalPending] = useState(false);
  const [creditCapDraft, setCreditCapDraft] = useState('0.50');
  const [creditCapPending, setCreditCapPending] = useState(false);
  const [creditPurchaseAmount, setCreditPurchaseAmount] = useState('');
  const [creditPurchasePending, setCreditPurchasePending] = useState(false);
  const [usageFilter, setUsageFilter] = useState<UsageLedgerFilter>('all');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void Promise.allSettled([
      services.client.getBillingSummary(),
      services.client.getCreditUsage({ limit: 30, ...usageLedgerQuery(usageFilter) }),
    ])
      .then(([summaryResult, usageResult]) => {
        if (cancelled) {
          return;
        }
        if (summaryResult.status === 'fulfilled') {
          setSummary((summaryResult.value ?? null) as BillingSummaryPayload | null);
        } else {
          setSummary(null);
          setError(summaryResult.reason instanceof Error ? summaryResult.reason.message : 'Billing summary is unavailable.');
        }
        if (usageResult.status === 'fulfilled') {
          setCreditUsage((usageResult.value ?? null) as CreditUsagePayload | null);
        } else {
          setCreditUsage(null);
        }
        setLoading(false);
        setUsageLoading(false);
      })
      .catch((loadError) => {
        if (cancelled) {
          return;
        }
        setSummary(null);
        setCreditUsage(null);
        setError(loadError instanceof Error ? loadError.message : 'Billing summary is unavailable.');
        setLoading(false);
        setUsageLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [services.client, usageFilter]);

  const subscription = summary && typeof summary.subscription === 'object'
    ? summary.subscription as Record<string, unknown>
    : {};
  const account = summary && typeof summary.account === 'object'
    ? summary.account as Record<string, unknown>
    : {};
  const usage = summary && typeof summary.usage === 'object'
    ? summary.usage as Record<string, unknown>
    : {};
  const hostedSageAi = summary && typeof summary.hosted_sage_ai === 'object'
    ? summary.hosted_sage_ai as Record<string, unknown>
    : {};
  const availablePlans = useMemo(
    () => Array.isArray(summary?.plans) ? summary?.plans as Array<Record<string, unknown>> : [],
    [summary?.plans],
  );
  const limitEntries = Object.entries(bootstrap.entitlements.limits);
  const currentPlanId = readText(subscription.effective_plan_id ?? bootstrap.entitlements.plan, bootstrap.entitlements.plan);
  const currentPlanLabel = readText(subscription.label ?? bootstrap.entitlements.label, bootstrap.entitlements.label);
  const subscriptionStatus = readText(subscription.status, 'active');
  const currentPeriodEnd = readText(subscription.current_period_end, '');
  const hostedCreditsPerUsd = Math.max(1, readNumber(hostedSageAi.credits_per_usd, 1000));
  const hostedCreditsRemaining = hostedCreditValue(hostedSageAi, 'monthly_remaining_usd', 'monthly_credits_remaining');
  const hostedCreditsCap = hostedCreditValue(hostedSageAi, 'monthly_cap_usd', 'monthly_credit_cap');
  const hostedCreditsUsed = hostedCreditValue(hostedSageAi, 'monthly_cost_usd', 'monthly_credits_used');
  const creditUsageSummary = creditUsage && typeof creditUsage.summary === 'object'
    ? creditUsage.summary as Record<string, unknown>
    : {};
  const creditUsageRows = useMemo(
    () => {
      const rows = Array.isArray(creditUsage?.items) ? (creditUsage.items as Array<Record<string, unknown>>) : [];
      if (usageFilter === 'non_platform') {
        return rows.filter((item) => readText(item.payer, '').toLowerCase() !== 'platform_credits');
      }
      return rows;
    },
    [creditUsage?.items, usageFilter],
  );

  useEffect(() => {
    setCreditCapDraft(String(Math.max(0, Math.round(hostedCreditsCap))));
  }, [hostedCreditsCap]);

  const reloadSummary = () => {
    setLoading(true);
    setUsageLoading(true);
    setError(null);
    void Promise.allSettled([
      services.client.getBillingSummary(),
      services.client.getCreditUsage({ limit: 30, ...usageLedgerQuery(usageFilter) }),
    ])
      .then(([summaryResult, usageResult]) => {
        if (summaryResult.status === 'fulfilled') {
          setSummary((summaryResult.value ?? null) as BillingSummaryPayload | null);
        }
        if (usageResult.status === 'fulfilled') {
          setCreditUsage((usageResult.value ?? null) as CreditUsagePayload | null);
        }
        setLoading(false);
        setUsageLoading(false);
      })
      .catch((loadError) => {
        setSummary(null);
        setCreditUsage(null);
        setError(loadError instanceof Error ? loadError.message : 'Billing summary is unavailable.');
        setLoading(false);
        setUsageLoading(false);
      });
  };

  const chooseUsageFilter = (nextFilter: UsageLedgerFilter) => {
    setUsageFilter(nextFilter);
    setUsageLoading(true);
  };

  const saveHostedCreditCap = () => {
    const parsed = Number.parseFloat(creditCapDraft);
    const nextCredits = Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : Number.NaN;
    if (!Number.isFinite(nextCredits)) {
      setError('Enter a valid hosted credit cap.');
      return;
    }
    const nextCapUsd = nextCredits / hostedCreditsPerUsd;
    setCreditCapPending(true);
    setError(null);
    void services.client.updateWorkspaceRouting({
      adminDefaults: {
        hosted_sage_ai_policy: nextCredits > 0 ? 'enabled_with_cap' : 'disabled',
        hosted_sage_ai_monthly_cap_usd: Number(nextCapUsd.toFixed(6)),
      },
    })
      .then(() => {
        reloadSummary();
      })
      .catch((saveError) => {
        setError(saveError instanceof Error ? saveError.message : 'Hosted credit cap could not be saved.');
      })
      .finally(() => {
        setCreditCapPending(false);
      });
  };

  const creditBalanceUsd = readNumber(hostedSageAi.credit_balance_usd, 0);
  const creditBalanceCredits = readNumber(hostedSageAi.credit_balance_credits, 0);
  const totalAvailableCredits = readNumber(hostedSageAi.total_available_credits, hostedCreditsRemaining);

  const startCreditPurchase = (amountUsd: number) => {
    setCreditPurchasePending(true);
    setError(null);
    void services.client.createCreditPurchaseSession({ amountUsd })
      .then((payload) => {
        const url = readText((payload ?? {}).checkout_url, '');
        if (url) {
          window.location.href = url;
          return;
        }
        reloadSummary();
      })
      .catch((requestError) => {
        setError(requestError instanceof Error ? requestError.message : 'Credit purchase could not be started.');
      })
      .finally(() => {
        setCreditPurchasePending(false);
      });
  };

  return (
    <WorkstationSurfaceRoot surface="admin/billing">
      {loading ? (
        <WorkstationSurfaceNotice>Loading billing summary.</WorkstationSurfaceNotice>
      ) : null}
      {error ? (
        <WorkstationSurfaceNotice tone="danger">Billing could not refresh. Try again when ready.</WorkstationSurfaceNotice>
      ) : null}
      <WorkstationSurfaceStatGrid>
        <WorkstationSurfaceStat
          label="Current plan"
          value={currentPlanId}
          hint={currentPlanLabel}
        />
        <WorkstationSurfaceStat
          label="Status"
          value={subscriptionStatus}
          hint={currentPeriodEnd ? `Current period ends at ${currentPeriodEnd}.` : 'Canonical subscription status from billing state.'}
        />
        <WorkstationSurfaceStat
          label="Billing system"
          value={readText(summary?.configured ? 'stripe' : 'offline', 'offline')}
          hint={summary?.configured ? 'Stripe checkout and portal are configured.' : 'Stripe is not configured for this environment.'}
        />
        <WorkstationSurfaceStat
          label="Hosted credits"
          value={formatCredits(hostedCreditsRemaining)}
          hint={`${formatCredits(hostedCreditsUsed)} used of ${formatCredits(hostedCreditsCap)} this month.`}
        />
      </WorkstationSurfaceStatGrid>
      <WorkstationSurfaceCard
        title="Usage ledger"
        description="Credit and runtime usage across Sage, Studio, mini-app invokes, and Cloud Computer sessions."
        actions={(
          <AppButton type="button" tone="secondary" onClick={reloadSummary}>
            Refresh
          </AppButton>
        )}
      >
        <WorkstationSurfaceStatGrid>
          <WorkstationSurfaceStat
            label="Rows"
            value={formatCredits(creditUsageSummary.count)}
            hint="Recent metered entries."
          />
          <WorkstationSurfaceStat
            label="Credits spent"
            value={formatCredits(creditUsageSummary.total_credits_debited)}
            hint="Empyralis credit debit total in this view."
          />
          <WorkstationSurfaceStat
            label="Platform cost"
            value={`$${readNumber(creditUsageSummary.total_platform_cost_usd, 0).toFixed(4)}`}
            hint="Internal provider/runtime cost."
          />
        </WorkstationSurfaceStatGrid>
        <WorkstationSurfaceList>
          <WorkstationSurfaceListItem
            title="View"
            subtitle={USAGE_LEDGER_FILTERS.find((item) => item.id === usageFilter)?.label ?? 'All'}
            description="Filter recent ledger rows by product surface, runtime, or non-platform payer."
            actions={(
              <>
                {USAGE_LEDGER_FILTERS.map((item) => (
                  <AppButton
                    key={item.id}
                    type="button"
                    tone={usageFilter === item.id ? 'primary' : 'secondary'}
                    onClick={() => chooseUsageFilter(item.id)}
                  >
                    {item.label}
                  </AppButton>
                ))}
              </>
            )}
          />
        </WorkstationSurfaceList>
        {usageLoading ? (
          <WorkstationSurfaceNotice>Loading usage ledger.</WorkstationSurfaceNotice>
        ) : creditUsageRows.length > 0 ? (
          <WorkstationSurfaceList>
            {creditUsageRows.slice(0, 12).map((item, index) => {
              const rowId = readText(item.id, `usage-${index}`);
              return (
                <WorkstationSurfaceListItem
                  key={rowId}
                  title={readText(item.label, surfaceLabel(item.surface))}
                  subtitle={usageRowSubtitle(item)}
                  description={usageRowDescription(item)}
                />
              );
            })}
          </WorkstationSurfaceList>
        ) : (
          <WorkstationSurfaceNotice>No metered credit usage has been recorded yet.</WorkstationSurfaceNotice>
        )}
      </WorkstationSurfaceCard>
      <WorkstationSurfaceCard
        title="Hosted AI credits"
        description="Empyralis-hosted model usage for users who do not want to manage API keys."
      >
        <WorkstationSurfaceList>
          <WorkstationSurfaceListItem
            title="Availability"
            subtitle={readBoolean(hostedSageAi.allowed) ? 'Available' : 'Not active'}
            description={readText(hostedSageAi.message, 'Hosted Sage AI is ready for this workspace.')}
          />
          <WorkstationSurfaceListItem
            title="Monthly cap"
            subtitle={`${formatCredits(hostedCreditsCap)} credits`}
            description="Hard cap before hosted model usage stops for this workspace."
          />
          <WorkstationSurfaceListItem
            title="Owner control"
            subtitle={`${formatCredits(hostedCreditsCap)} monthly credits`}
            description="Edit this before sharing the public demo. The server enforces the cap before hosted model calls; BYOK users use their own provider."
          />
          <WorkstationSurfaceListItem
            title="Monthly usage"
            subtitle={`${formatCredits(hostedCreditsUsed)} used · ${formatCredits(hostedCreditsRemaining)} remaining`}
            description={`${String(readNumber(usage.hosted_sage_runs_monthly))} hosted Sage runs this month.`}
          />
          <WorkstationSurfaceListItem
            title="Policy"
            subtitle={readText(hostedSageAi.policy, 'disabled')}
            description="BYOK providers remain available separately for power users."
          />
          <WorkstationSurfaceListItem
            title="Default execution path"
            subtitle="Hosted by Empyralis"
            description="Normal users stay on hosted credits first. BYOK providers and local models remain advanced setup paths."
          />
          <WorkstationSurfaceListItem
            title="Credit balance"
            subtitle={`${formatCredits(creditBalanceCredits)} credits`}
            description="Purchased credits that roll over and are consumed after the monthly cap."
          />
          <WorkstationSurfaceListItem
            title="Total available"
            subtitle={`${formatCredits(totalAvailableCredits)} credits`}
            description="Combined monthly remaining + purchased credit balance."
          />
        </WorkstationSurfaceList>
        <WorkstationSurfaceNotice tone="neutral">
          Hosted credits are the launch default. Premium local power comes from a paired computer or dedicated agent machine, not from asking normal users to manage provider keys.
        </WorkstationSurfaceNotice>
        <FormGrid columns="minmax(0, 1fr) auto">
          <FormField label="Monthly hosted AI credits" hint="Credits. Use 500 for the launch free-credit budget. Set 0 to disable hosted AI for this workspace.">
            <FormInput
              type="number"
              min="0"
              step="1"
              value={creditCapDraft}
              onChange={(event) => setCreditCapDraft(event.currentTarget.value)}
            />
          </FormField>
          <div className="settings-action-row">
            <AppButton
              type="button"
              disabled={creditCapPending}
              onClick={saveHostedCreditCap}
            >
              {creditCapPending ? 'Saving…' : 'Save cap'}
            </AppButton>
          </div>
        </FormGrid>
        <WorkstationSurfaceNotice tone="neutral">
          Purchase additional credits that never expire and are consumed after your monthly cap.
        </WorkstationSurfaceNotice>
        <FormGrid columns="repeat(4, 1fr)">
          {[5, 10, 25, 50].map((amount) => (
            <AppButton
              key={amount}
              type="button"
              tone="secondary"
              disabled={creditPurchasePending}
              onClick={() => startCreditPurchase(amount)}
            >
              ${amount}
            </AppButton>
          ))}
        </FormGrid>
        <FormGrid columns="minmax(0, 1fr) auto">
          <FormField label="Custom amount" hint="USD. Minimum $1, maximum $500.">
            <FormInput
              type="number"
              min="1"
              max="500"
              step="1"
              placeholder="Enter amount in USD"
              value={creditPurchaseAmount}
              onChange={(event) => setCreditPurchaseAmount(event.currentTarget.value)}
            />
          </FormField>
          <div className="settings-action-row">
            <AppButton
              type="button"
              disabled={creditPurchasePending || !creditPurchaseAmount}
              onClick={() => {
                const parsed = Number.parseFloat(creditPurchaseAmount);
                if (Number.isFinite(parsed) && parsed >= 1) {
                  startCreditPurchase(Math.min(500, parsed));
                }
              }}
            >
              {creditPurchasePending ? 'Purchasing…' : 'Purchase credits'}
            </AppButton>
          </div>
        </FormGrid>
      </WorkstationSurfaceCard>
      <WorkstationSurfaceCard
        title="Subscription"
        description="Canonical billing state for this workspace."
        actions={(
          <div className="app-inline-actions">
            <AppButton type="button" tone="secondary" onClick={reloadSummary}>
              Refresh
            </AppButton>
            <AppButton
              type="button"
              tone="secondary"
              disabled={!readBoolean(summary?.portal_available) || portalPending}
              onClick={() => {
                setPortalPending(true);
                void services.client.createBillingPortalSession()
                  .then((payload) => {
                    const url = readText((payload ?? {}).portal_url, '');
                    if (url) {
                      window.location.href = url;
                      return;
                    }
                    reloadSummary();
                  })
                  .catch((requestError) => {
                    setError(requestError instanceof Error ? requestError.message : 'Billing portal is unavailable.');
                  })
                  .finally(() => {
                    setPortalPending(false);
                  });
              }}
            >
              Manage billing
            </AppButton>
          </div>
        )}
      >
        <WorkstationSurfaceList>
          <WorkstationSurfaceListItem
            title="Billing email"
            subtitle={readText(account.billing_email, 'No billing email on file')}
            description="The billing contact currently associated with the workspace account."
          />
          <WorkstationSurfaceListItem
            title="Customer account"
            subtitle={readText(account.provider_customer_id, 'Not created yet')}
            description="The canonical billing customer identifier used for subscription and portal access."
          />
          <WorkstationSurfaceListItem
            title="Resolved plan"
            subtitle={currentPlanLabel}
            description={`Entitlements currently resolve to the ${currentPlanId} plan.`}
          />
        </WorkstationSurfaceList>
      </WorkstationSurfaceCard>
      <WorkstationSurfaceCard title="Available plans" description="Plans with configured checkout can be activated immediately.">
        {availablePlans.length === 0 ? (
          <WorkstationSurfaceNotice>No billable plans are currently configured.</WorkstationSurfaceNotice>
        ) : (
          <WorkstationSurfaceList>
            {availablePlans.map((plan) => {
              const planId = readText(plan.plan_id, 'free');
              const isCurrent = readBoolean(plan.current);
              const checkoutEnabled = readBoolean(plan.checkout_enabled);
              return (
                <WorkstationSurfaceListItem
                  key={planId}
                  title={readText(plan.label, planId)}
                  subtitle={isCurrent ? 'Current plan' : checkoutEnabled ? 'Checkout ready' : 'Unavailable in this environment'}
                  description={checkoutEnabled
                    ? 'Start a Stripe checkout session for this workspace.'
                    : planId === 'free'
                      ? 'Free is the default workspace plan and does not require checkout.'
                      : 'This plan needs a configured Stripe price before checkout can start.'}
                  actions={(
                    <AppButton
                      type="button"
                      disabled={isCurrent || !checkoutEnabled || pendingPlanId === planId}
                      onClick={() => {
                        setPendingPlanId(planId);
                        void services.client.createBillingCheckoutSession({ planId })
                          .then((payload) => {
                            const url = readText((payload ?? {}).checkout_url, '');
                            if (url) {
                              window.location.href = url;
                              return;
                            }
                            reloadSummary();
                          })
                          .catch((requestError) => {
                            setError(requestError instanceof Error ? requestError.message : 'Checkout could not be started.');
                          })
                          .finally(() => {
                            setPendingPlanId((current) => (current === planId ? null : current));
                          });
                      }}
                    >
                      {isCurrent ? 'Current' : pendingPlanId === planId ? 'Starting…' : 'Upgrade'}
                    </AppButton>
                  )}
                />
              );
            })}
          </WorkstationSurfaceList>
        )}
      </WorkstationSurfaceCard>
      <WorkstationSurfaceCard title="Resolved limits" description="Current workspace limits derived from the authoritative billing-backed entitlement state.">
        {limitEntries.length === 0 ? (
          <WorkstationSurfaceNotice>No explicit limit values are present for this plan.</WorkstationSurfaceNotice>
        ) : (
          <WorkstationSurfaceList>
            {limitEntries.map(([key, value]) => (
              <WorkstationSurfaceListItem
                key={key}
                title={key}
                subtitle={value === null ? 'unlimited' : String(value)}
                description="Canonical entitlement limit"
              />
            ))}
          </WorkstationSurfaceList>
        )}
      </WorkstationSurfaceCard>
    </WorkstationSurfaceRoot>
  );
}
