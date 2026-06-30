'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  Bot,
  Boxes,
  Check,
  Compass,
  Copy,
  ExternalLink,
  Package,
  Plug,
  Sparkles,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

import { AppButton } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';

import styles from './discovery-pane.module.css';

type DiscoveryFilter = 'all' | 'apps' | 'agents' | 'tools' | 'mcp' | 'skills' | 'bundles';

type DiscoveryFeedItem = {
  id: string;
  type: 'public_app' | 'agent_template' | 'mcp' | 'skill' | 'bundle' | string;
  title: string;
  description?: string | null;
  summary?: string | null;
  actor?: {
    label?: string | null;
    byline?: string | null;
  } | null;
  components?: string[];
  badges?: string[];
  permission_summary?: string[];
  blueprint?: {
    status?: string | null;
    source?: string | null;
    created_by?: string | null;
  } | null;
  provenance?: {
    label?: string | null;
  } | null;
  installed?: boolean;
  action?: {
    kind?: string | null;
    label?: string | null;
    enabled?: boolean;
  } | null;
};

type DiscoveryFeedPayload = {
  items?: DiscoveryFeedItem[];
};

type DiscoveryAdoptPayload = {
  open_url?: string | null;
};

const DISCOVERY_FILTERS: readonly DiscoveryFilter[] = ['all', 'apps', 'agents', 'tools', 'mcp', 'skills', 'bundles'];

function readItems(payload: unknown): DiscoveryFeedItem[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as DiscoveryFeedPayload).items;
  return Array.isArray(items)
    ? items.filter((item): item is DiscoveryFeedItem => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeDiscoveryFilter(value: string | null): DiscoveryFilter {
  const normalized = (value || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
  if (normalized === 'agent_template' || normalized === 'agent_templates') {
    return 'agents';
  }
  if (normalized === 'connector' || normalized === 'connectors') {
    return 'mcp';
  }
  if (normalized === 'tool') {
    return 'tools';
  }
  if (normalized === 'skill') {
    return 'skills';
  }
  if (normalized === 'bundle') {
    return 'bundles';
  }
  return DISCOVERY_FILTERS.some((filter) => filter === normalized)
    ? normalized as DiscoveryFilter
    : 'all';
}

function iconForItem(item: DiscoveryFeedItem): LucideIcon {
  switch (item.type) {
    case 'public_app':
      return Package;
    case 'agent_template':
      return Bot;
    case 'mcp':
      return Plug;
    case 'skill':
      return Wrench;
    case 'bundle':
      return Boxes;
    default:
      return Sparkles;
  }
}

function fallbackActionLabel(item: DiscoveryFeedItem): string {
  if (item.type === 'public_app') {
    return item.installed ? 'Open in Apps' : 'Clone app blueprint';
  }
  if (item.action?.label) {
    return item.action.label;
  }
  if (item.type === 'agent_template') {
    return 'Copy to Studio';
  }
  return item.installed ? 'Open' : 'Add';
}

function targetLabel(item: DiscoveryFeedItem): string {
  if (item.type === 'public_app') {
    return 'App';
  }
  if (item.type === 'agent_template') {
    return 'Agent Template';
  }
  if (item.type === 'mcp') {
    return 'MCP';
  }
  if (item.type === 'skill') {
    return 'Skill';
  }
  if (item.type === 'bundle') {
    return 'Bundle';
  }
  return 'Capability';
}

function sectionKeyForItem(item: DiscoveryFeedItem): 'apps' | 'agent_templates' | 'capabilities' {
  if (item.type === 'public_app') {
    return 'apps';
  }
  return item.type === 'agent_template' ? 'agent_templates' : 'capabilities';
}

function labelForFilter(filter: DiscoveryFilter): string {
  switch (filter) {
    case 'agents':
      return 'Agent Templates';
    case 'tools':
      return 'Tools';
    case 'apps':
      return 'Apps';
    case 'mcp':
      return 'MCP';
    case 'skills':
      return 'Skills';
    case 'bundles':
      return 'Bundles';
    case 'all':
    default:
      return 'All';
  }
}

function visibleSectionForFilter(filter: DiscoveryFilter): 'all' | 'apps' | 'agent_templates' | 'capabilities' {
  if (filter === 'apps') {
    return 'apps';
  }
  if (filter === 'agents') {
    return 'agent_templates';
  }
  if (filter === 'all') {
    return 'all';
  }
  return 'capabilities';
}

function DiscoverySkeleton() {
  return (
    <div className={styles.feedGrid} aria-label="Loading Discovery">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className={styles.card}>
          <SkeletonBlock height={24} width="52%" />
          <SkeletonBlock height={52} />
          <SkeletonBlock height={32} width="72%" />
        </div>
      ))}
    </div>
  );
}

export function DiscoveryPane() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const services = useWorkspaceServices();
  const requestedFilter = normalizeDiscoveryFilter(searchParams.get('filter') || searchParams.get('category'));
  const [filter, setFilter] = useState<DiscoveryFilter>(requestedFilter);
  const [items, setItems] = useState<DiscoveryFeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adoptingItemId, setAdoptingItemId] = useState<string | null>(null);
  const activeRequestControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setFilter((current) => (current === requestedFilter ? current : requestedFilter));
  }, [requestedFilter]);

  const loadFeed = useCallback(async (nextFilter: DiscoveryFilter) => {
    setLoading(true);
    setError(null);
    activeRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    activeRequestControllerRef.current = requestController;
    try {
      const payload = await services.client.listDiscoveryFeed({ filter: nextFilter });
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return;
      }
      setItems(readItems(payload));
    } catch (loadError) {
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return;
      }
      setError(loadError instanceof Error ? loadError.message : 'Discover could not refresh.');
      setItems([]);
    } finally {
      if (activeRequestControllerRef.current === requestController) {
        activeRequestControllerRef.current = null;
      }
      if (!requestController.signal.aborted) {
        setLoading(false);
      }
    }
  }, [services.client]);

  useEffect(() => {
    void loadFeed(filter);
    return () => {
      activeRequestControllerRef.current?.abort();
      activeRequestControllerRef.current = null;
    };
  }, [filter, loadFeed]);

  const handleAdopt = useCallback(async (item: DiscoveryFeedItem) => {
    if (item.action?.enabled === false) {
      return;
    }
    setAdoptingItemId(item.id);
    setError(null);
    try {
      const payload = await services.client.adoptDiscoveryItem({ feedItemId: item.id }) as DiscoveryAdoptPayload | null;
      await loadFeed(filter);
      if (payload?.open_url) {
        router.push(payload.open_url);
      }
    } catch (adoptError) {
      setError(adoptError instanceof Error ? adoptError.message : 'This item could not be added.');
    } finally {
      setAdoptingItemId(null);
    }
  }, [filter, loadFeed, router, services.client]);

  const renderedItems = useMemo(() => items, [items]);
  const renderedSections = useMemo(() => {
    const apps: DiscoveryFeedItem[] = [];
    const agentTemplates: DiscoveryFeedItem[] = [];
    const capabilities: DiscoveryFeedItem[] = [];
    for (const item of renderedItems) {
      const sectionKey = sectionKeyForItem(item);
      if (sectionKey === 'apps') {
        apps.push(item);
      } else if (sectionKey === 'agent_templates') {
        agentTemplates.push(item);
      } else {
        capabilities.push(item);
      }
    }
    return [
      { id: 'apps', title: 'Apps', items: apps },
      { id: 'agent_templates', title: 'Agent Templates', items: agentTemplates },
      { id: 'capabilities', title: 'Capabilities', items: capabilities },
    ].filter((section) => {
      const visibleSection = visibleSectionForFilter(filter);
      return section.items.length > 0 && (visibleSection === 'all' || section.id === visibleSection);
    });
  }, [filter, renderedItems]);

  return (
    <WorkstationSurfaceRoot surface="marketplace">
      <main className={styles.shell} aria-label="Discover">
        <div className={styles.content}>
          <header className={styles.header}>
            <p className={styles.kicker}>Discover</p>
            <div className={styles.titleBlock}>
              <h1 className={styles.contentTitle}>{labelForFilter(filter)}</h1>
              <p className={styles.subtitle}>Clone useful app blueprints, copy agent templates, or add trusted capabilities to this workspace.</p>
            </div>
          </header>

          {error ? (
            <section className={styles.notice} aria-live="polite">
              <div>
                <strong>Discover could not refresh.</strong>
                <span>{error}</span>
              </div>
              <AppButton type="button" tone="secondary" onClick={() => { void loadFeed(filter); }}>
                Retry
              </AppButton>
            </section>
          ) : null}

          {loading ? <DiscoverySkeleton /> : null}

          {!loading && !error && renderedItems.length === 0 ? (
            <section className={styles.empty}>
              <Compass aria-hidden="true" />
              <div>
                <h2>No discoveries yet</h2>
                <p>Public apps and copyable Studio templates will appear here.</p>
              </div>
            </section>
          ) : null}

          {!loading && renderedSections.length > 0 ? (
            renderedSections.map((section) => (
              <section key={section.id} className={styles.feedSection} aria-labelledby={`discovery-section-${section.id}`}>
                <div className={styles.sectionHeader}>
                  <h2 id={`discovery-section-${section.id}`}>{section.title}</h2>
                  <span>{section.items.length}</span>
                </div>
                <div className={styles.feedGrid}>
                  {section.items.map((item) => {
                    const Icon = iconForItem(item);
                    const actionDisabled = item.action?.enabled === false || adoptingItemId === item.id;
                    const actionLabel = adoptingItemId === item.id
                      ? item.type === 'public_app' ? 'Cloning...' : 'Adding...'
                      : fallbackActionLabel(item);
                    return (
                      <article key={item.id} className={styles.card}>
                        <div className={styles.cardHeader}>
                          <div className={styles.iconWrap} aria-hidden="true">
                            <Icon />
                          </div>
                          <div className={styles.cardTitleBlock}>
                            <p className={styles.cardType}>{targetLabel(item)}</p>
                            <h3 className={styles.cardTitle}>{item.title}</h3>
                            <p className={styles.byline}>{item.actor?.byline || 'by Empyralis'}</p>
                          </div>
                        </div>

                        <p className={styles.description}>{item.description || item.summary || 'Ready to add to this workspace.'}</p>
                        {item.summary ? <p className={styles.summary}>{item.summary}</p> : null}
                        {item.provenance?.label || item.blueprint?.status ? (
                          <p className={styles.provenanceLine}>
                            {[item.provenance?.label, item.blueprint?.status ? String(item.blueprint.status).replace(/[_-]+/g, ' ') : null].filter(Boolean).join(' · ')}
                          </p>
                        ) : null}

                        {Array.isArray(item.components) && item.components.length > 0 ? (
                          <div className={styles.components}>
                            {item.components.slice(0, 4).map((component) => (
                              <span key={component}>{component}</span>
                            ))}
                          </div>
                        ) : null}
                        {Array.isArray(item.permission_summary) && item.permission_summary.length > 0 ? (
                          <div className={styles.permissionSummary}>
                            {item.permission_summary.slice(0, 3).map((permission) => (
                              <span key={permission}>{permission}</span>
                            ))}
                          </div>
                        ) : null}

                        <div className={styles.footer}>
                          <div className={styles.badges}>
                            {(item.badges || [targetLabel(item)]).slice(0, 3).map((badge) => (
                              <span key={badge} className={styles.badge}>
                                {badge}
                              </span>
                            ))}
                          </div>
                          <button
                            type="button"
                            className={styles.primaryAction}
                            disabled={actionDisabled}
                            onClick={() => {
                              void handleAdopt(item);
                            }}
                          >
                            {item.installed ? <Check aria-hidden="true" /> : item.type === 'public_app' || item.type === 'agent_template' ? <Copy aria-hidden="true" /> : <ExternalLink aria-hidden="true" />}
                            {actionLabel}
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            ))
          ) : null}
        </div>
      </main>
    </WorkstationSurfaceRoot>
  );
}
