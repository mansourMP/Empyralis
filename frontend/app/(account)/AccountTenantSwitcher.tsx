'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  Bot,
  Monitor,
  Moon,
  Settings2,
  SunMedium,
  type LucideIcon,
} from 'lucide-react';

import { joinClassNames } from '@/lib/ui/primitives';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import { createWorkstationClient } from '@/lib/workspace/workstation-client';
import {
  resolvePrimaryProductWorkspaceId,
  resolveRouteWorkspaceId,
} from '@/lib/shell/workspace-membership-model';
import {
  buildWorkspaceRouteHref,
  getWorkspaceNavRouteDefinition,
  resolveWorkspaceRouteIdFromSegment,
  type WorkspaceRouteId,
} from '../../shared/nav-manifest';
import { useAppTheme } from '@/lib/ui/app-theme';
import { APP_THEME_ATTRIBUTE } from '@/lib/ui/tokens';

type RailDestinationId = 'chat' | 'settings';

type RailDestination = {
  id: RailDestinationId;
  label: string;
  defaultRouteId: WorkspaceRouteId;
  icon: LucideIcon;
  dataLinkId?: string;
};

const PRIMARY_DESTINATIONS: RailDestination[] = [
  { id: 'chat', label: 'Sage', defaultRouteId: 'chat', icon: Bot, dataLinkId: 'sage' },
];

const SECONDARY_DESTINATIONS: RailDestination[] = [
  { id: 'settings', label: 'Settings', defaultRouteId: 'settings', icon: Settings2 },
];

function buildDestinationHref(
  workspaceId: string,
  destination: { defaultRouteId: WorkspaceRouteId },
): string {
  return buildWorkspaceRouteHref(workspaceId, destination.defaultRouteId);
}

function extractRouteWorkspaceId(pathname: string | null): string | null {
  if (!pathname) {
    return null;
  }

  const segments = pathname.split('/').filter(Boolean);
  if (segments[0] !== 'w' || !segments[1]) {
    return null;
  }

  return decodeURIComponent(segments[1]);
}

function extractActiveRouteId(pathname: string | null): WorkspaceRouteId | null {
  if (!pathname) {
    return null;
  }

  const segments = pathname.split('/').filter(Boolean);
  if (segments[0] !== 'w' || segments.length < 3) {
    return null;
  }

  return resolveWorkspaceRouteIdFromSegment(segments.slice(2).join('/'));
}

function extractActiveRailId(pathname: string | null): RailDestinationId {
  const routeId = extractActiveRouteId(pathname);
  if (!routeId) {
    return 'chat';
  }

  const destinationId = getWorkspaceNavRouteDefinition(routeId).destinationId;
  if (destinationId === 'settings') {
    return 'settings';
  }
  return 'chat';
}

export function AccountTenantSwitcher() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const { resolvedTheme } = useAppTheme();
  const [usageCost, setUsageCost] = useState<number | null>(null);
  const [pendingRailId, setPendingRailId] = useState<RailDestinationId | null>(null);
  const routeWorkspaceId = resolveRouteWorkspaceId(
    state.workspaceMemberships,
    extractRouteWorkspaceId(pathname),
  );
  const activeRailId = extractActiveRailId(pathname);
  const visibleRailId = pendingRailId ?? activeRailId;
  const activeWorkspaceId =
    routeWorkspaceId
    ?? state.selectedWorkspaceId
    ?? resolvePrimaryProductWorkspaceId(state.workspaceMemberships)
    ?? state.workspaceMemberships[0]?.workspace.id
    ?? null;
  const activeMembership = state.workspaceMemberships.find((item) => item.workspace.id === activeWorkspaceId) ?? null;
  const activeTenantId = activeMembership?.workspace.tenantId ?? state.workspaceMemberships[0]?.workspace.tenantId ?? '';
  const currentTheme = state.globalTheme;
  const accountInitial = String(state.account?.displayName || state.account?.email || 'You').trim().charAt(0).toUpperCase() || 'Y';
  const nextTheme = currentTheme === 'system'
    ? 'light'
    : currentTheme === 'light'
      ? 'dark'
      : 'system';
  const themeLabel = currentTheme === 'system'
    ? 'Theme: System'
    : currentTheme === 'light'
      ? 'Theme: Light'
      : 'Theme: Dark';
  const ThemeIcon = currentTheme === 'system'
    ? Monitor
    : currentTheme === 'light'
      ? SunMedium
      : Moon;
  const usageClient = useMemo(() => {
    if (!activeWorkspaceId || !activeTenantId) {
      return null;
    }
    const cache = new Map<string, unknown>();
    return createWorkstationClient({
      scope: {
        workspaceId: activeWorkspaceId,
        tenantId: activeTenantId,
        kernelKey: `rail-usage:${activeWorkspaceId}`,
      },
      transport: {
        request: (path, init) => fetch(path, {
          ...init,
          credentials: 'same-origin',
          cache: 'no-store',
        }),
      },
      queryClient: {
        peek: <T,>(key: string) => (cache.get(key) as T | undefined) ?? null,
        set: <T,>(key: string, value: T) => {
          cache.set(key, value);
          return value;
        },
      },
      realtime: {
        trackEventSource: <T extends EventSource>(source: T) => source,
      },
      getApiBaseUrl: () => '',
    });
  }, [activeTenantId, activeWorkspaceId]);

  useEffect(() => {
    setPendingRailId(null);
  }, [activeRailId, pathname]);

  useEffect(() => {
    if (!pendingRailId) {
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      setPendingRailId(null);
    }, 4_000);
    return () => window.clearTimeout(timeout);
  }, [pendingRailId]);

  const prefetchHref = useCallback((href: string) => {
    if (href !== '/') {
      router.prefetch(href);
    }
  }, [router]);

  const markNavigationPending = useCallback((destinationId: RailDestinationId, href: string) => {
    prefetchHref(href);
    if (href !== pathname) {
      setPendingRailId(destinationId);
    }
  }, [pathname, prefetchHref]);

  useEffect(() => {
    if (!activeWorkspaceId) {
      return;
    }
    for (const destination of [...PRIMARY_DESTINATIONS, ...SECONDARY_DESTINATIONS]) {
      router.prefetch(buildWorkspaceRouteHref(activeWorkspaceId, destination.defaultRouteId));
    }
  }, [activeWorkspaceId, router]);

  useEffect(() => {
    let cancelled = false;

    if (!usageClient) {
      setUsageCost(null);
      return () => {
        cancelled = true;
      };
    }

    void usageClient.getUsageSummary({ period: 'month' })
      .then((payload) => {
        if (cancelled) {
          return;
        }
        const rawCost = typeof payload.total_cost_usd === 'number'
          ? payload.total_cost_usd
          : Number(payload.total_cost_usd);
        setUsageCost(Number.isFinite(rawCost) && rawCost > 0 ? rawCost : null);
      })
      .catch(() => {
        if (!cancelled) {
          setUsageCost(null);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [usageClient]);

  return (
    <div data-workstation-switcher="rail" className="account-switcher">
      <nav aria-label="Primary destinations" className="account-switcher__cluster account-switcher__cluster--upper">
        <div className="account-switcher__nav account-switcher__nav--primary">
          {PRIMARY_DESTINATIONS.map((destination) => {
            const href = activeWorkspaceId ? buildDestinationHref(activeWorkspaceId, destination) : '/';
            const isVisibleActive = visibleRailId === destination.id;
            const isPending = pendingRailId === destination.id;
            return (
              <Link
                key={destination.id}
                href={href}
                prefetch
                aria-current={activeRailId === destination.id ? 'page' : undefined}
                data-workstation-destination-link={destination.dataLinkId ?? destination.id}
                className={joinClassNames(
                  'account-switcher__link',
                  isVisibleActive && 'account-switcher__link--active',
                  isPending && 'account-switcher__link--pending',
                )}
                aria-label={destination.label}
                title={destination.label}
                onPointerEnter={() => prefetchHref(href)}
                onFocus={() => prefetchHref(href)}
                onClick={() => markNavigationPending(destination.id, href)}
              >
                <destination.icon aria-hidden="true" />
              </Link>
            );
          })}
        </div>
      </nav>
      <div className="account-switcher__cluster account-switcher__cluster--lower">
        {typeof usageCost === 'number' ? (
          <div
            className="account-switcher__usage-indicator"
            title="Usage this month"
            aria-label={`Usage this month ${usageCost.toFixed(2)} dollars`}
          >
            ${usageCost.toFixed(2)}
          </div>
        ) : null}
        <button
          type="button"
          className="account-switcher__link account-switcher__theme-toggle"
          aria-label={`${themeLabel}. Click to switch to ${nextTheme}.`}
          title={`${themeLabel}. Click to switch to ${nextTheme}.`}
          onClick={() => {
            actions.setGlobalTheme(nextTheme);
          }}
        >
          <ThemeIcon aria-hidden="true" />
        </button>
        <nav aria-label="Settings destination" className="account-switcher__nav account-switcher__nav--secondary">
          {SECONDARY_DESTINATIONS.map((destination) => {
            const href = activeWorkspaceId ? buildWorkspaceRouteHref(activeWorkspaceId, destination.defaultRouteId) : '/';
            const isVisibleActive = visibleRailId === destination.id;
            const isPending = pendingRailId === destination.id;
            return (
              <Link
                key={destination.id}
                href={href}
                prefetch
                aria-current={activeRailId === destination.id ? 'page' : undefined}
                data-workstation-destination-link={destination.dataLinkId ?? destination.id}
                className={joinClassNames(
                  'account-switcher__link',
                  isVisibleActive && 'account-switcher__link--active',
                  isPending && 'account-switcher__link--pending',
                )}
                aria-label={destination.label}
                title={destination.label}
                onPointerEnter={() => prefetchHref(href)}
                onFocus={() => prefetchHref(href)}
                onClick={() => markNavigationPending(destination.id, href)}
              >
                <destination.icon aria-hidden="true" />
              </Link>
            );
          })}
        </nav>
        <Link
          href="/settings/account"
          prefetch
          className="account-switcher__link account-switcher__avatar"
          aria-label="Account"
          title={state.account?.displayName || state.account?.email || 'Account'}
        >
          {accountInitial}
        </Link>
      </div>
    </div>
  );
}
