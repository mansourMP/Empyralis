'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Archive, Download, File, FileText, Image, RefreshCw, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { EmptyPanel } from '@/lib/ui/empty-panel';
import { joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { WorkstationClientError } from '@/lib/workspace/workstation-client';
import { useWorkspaceServices, useWorkstationStreamState } from '@/lib/workspace/workspace-services';
import {
  WorkstationSurfaceNotice,
  WorkstationSurfaceRoot,
} from '@/lib/workspace/workstation-surface-primitives';

type ArtifactRecord = Record<string, unknown> & {
  artifact_id?: string | null;
  id?: string | null;
  label?: string | null;
  file_name?: string | null;
  mime_type?: string | null;
  content_type?: string | null;
  byte_size?: number | null;
  created_at?: string | null;
  run_id?: string | null;
};

type ArtifactFilterId = 'all' | 'images' | 'files';
type ArtifactPreviewKind = 'document' | 'image' | 'archive' | 'file';

function normalizeArtifactItems(payload: unknown): ArtifactRecord[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as Record<string, unknown>).items;
  return Array.isArray(items)
    ? items.filter((item): item is ArtifactRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function formatTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    return 'No timestamp recorded';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatByteSize(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return 'Unknown size';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function artifactKind(mediaType: string): ArtifactFilterId {
  if (mediaType.startsWith('image/')) {
    return 'images';
  }
  return 'files';
}

function artifactPreviewKind(mediaType: string): ArtifactPreviewKind {
  if (mediaType.startsWith('image/')) {
    return 'image';
  }
  if (mediaType.includes('zip') || mediaType.includes('octet-stream') || mediaType.includes('tar') || mediaType.includes('gzip')) {
    return 'archive';
  }
  if (
    mediaType.startsWith('text/')
    || mediaType.includes('json')
    || mediaType.includes('pdf')
    || mediaType.includes('markdown')
    || mediaType.includes('xml')
  ) {
    return 'document';
  }
  return 'file';
}

function artifactKindLabel(kind: ArtifactFilterId): string {
  if (kind === 'images') {
    return 'Images';
  }
  if (kind === 'files') {
    return 'Files';
  }
  return 'All';
}

function artifactKindIcon(kind: ArtifactFilterId | ArtifactPreviewKind): LucideIcon {
  if (kind === 'document') {
    return FileText;
  }
  if (kind === 'images' || kind === 'image') {
    return Image;
  }
  if (kind === 'archive') {
    return Archive;
  }
  return File;
}

function toArtifactId(item: ArtifactRecord, fallbackIndex: number): string {
  return String(item.artifact_id ?? item.id ?? '').trim() || `artifact-${fallbackIndex}`;
}

function artifactName(item: ArtifactRecord, fallbackId: string): string {
  return readString(item.label ?? item.file_name, fallbackId);
}

export function WorkstationArtifactsPane() {
  const services = useWorkspaceServices();
  const streamState = useWorkstationStreamState();
  const activityRefreshTimerRef = useRef<number | null>(null);
  const [items, setItems] = useState<ArtifactRecord[]>([]);
  const [activeFilter, setActiveFilter] = useState<ArtifactFilterId>('all');
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [restrictedReason, setRestrictedReason] = useState<string | null>(null);

  const applyLoadError = useCallback((loadError: unknown) => {
    if (loadError instanceof WorkstationClientError && loadError.status === 403) {
      setItems([]);
      setRestrictedReason('Artifacts are not enabled for this workspace yet.');
      setError(null);
      setIsLoading(false);
      return;
    }
    setError(loadError instanceof Error ? loadError.message : 'Files are unavailable right now.');
    setIsLoading(false);
  }, []);

  const refresh = useCallback(async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    setRestrictedReason(null);
    const payload = await services.client.listArtifacts({ limit: 80 });
    setItems(normalizeArtifactItems(payload));
    setIsLoading(false);
  }, [services.client]);

  useEffect(() => {
    let cancelled = false;
    void refresh(true).catch((loadError) => {
      if (cancelled) {
        return;
      }
      applyLoadError(loadError);
    });
    return () => {
      cancelled = true;
    };
  }, [applyLoadError, refresh]);

  useEffect(() => {
    if (streamState.activity.version === 0) {
      return;
    }
    if (activityRefreshTimerRef.current !== null) {
      window.clearTimeout(activityRefreshTimerRef.current);
    }
    activityRefreshTimerRef.current = window.setTimeout(() => {
      activityRefreshTimerRef.current = null;
      void refresh(false).catch(applyLoadError);
    }, 750);
    return () => {
      if (activityRefreshTimerRef.current !== null) {
        window.clearTimeout(activityRefreshTimerRef.current);
        activityRefreshTimerRef.current = null;
      }
    };
  }, [applyLoadError, refresh, streamState.activity.version]);

  const kindCounts = useMemo(
    () => items.reduce<Record<ArtifactFilterId, number>>((counts, item) => {
      const mediaType = readString(item.mime_type ?? item.content_type, 'artifact');
      counts.all += 1;
      counts[artifactKind(mediaType)] += 1;
      return counts;
    }, {
      all: 0,
      images: 0,
      files: 0,
    }),
    [items],
  );
  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item, index) => {
      const artifactId = toArtifactId(item, index);
      const mediaType = readString(item.mime_type ?? item.content_type, 'artifact');
      if (activeFilter !== 'all' && artifactKind(mediaType) !== activeFilter) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      return [
        artifactName(item, artifactId),
        artifactId,
        readString(item.run_id, ''),
        mediaType,
      ].join(' ').toLowerCase().includes(normalizedQuery);
    });
  }, [activeFilter, items, query]);
  const filterOptions: ArtifactFilterId[] = ['all', 'images', 'files'];

  return (
    <WorkstationSurfaceRoot surface="artifacts">
      <main className="artifact-library-page">
        <section className="artifact-library-hero" aria-label="Library overview">
          <h2>Library</h2>
          <div className="artifact-library-hero__actions" aria-label="Library actions">
            <label className="artifact-library-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search"
              />
            </label>
            <button
              type="button"
              className="artifact-library-icon-button"
              aria-label="Refresh library"
              onClick={() => {
                void refresh(false).catch(applyLoadError);
              }}
            >
              <RefreshCw size={16} aria-hidden="true" />
            </button>
          </div>
        </section>

        <div className="artifact-library-filters" aria-label="Library filters">
          {filterOptions.map((filterId) => (
            <button
              key={filterId}
              type="button"
              className={joinClassNames(
                'artifact-library-filter',
                activeFilter === filterId && 'artifact-library-filter--active',
              )}
              aria-pressed={activeFilter === filterId}
              onClick={() => setActiveFilter(filterId)}
            >
              <span>{artifactKindLabel(filterId)}</span>
              <strong>{kindCounts[filterId]}</strong>
            </button>
          ))}
        </div>

        {error ? <WorkstationSurfaceNotice tone="neutral">Library items could not refresh. Try again when ready.</WorkstationSurfaceNotice> : null}
        {restrictedReason ? <WorkstationSurfaceNotice tone="neutral">{restrictedReason}</WorkstationSurfaceNotice> : null}

        {isLoading ? (
          <div className="artifact-library-loading">
            <SkeletonBlock height="11rem" />
            <SkeletonBlock height="11rem" />
            <SkeletonBlock height="11rem" />
          </div>
        ) : items.length === 0 ? (
          <EmptyPanel
            title={restrictedReason ? 'Library is not enabled here' : 'No library items yet'}
            body={restrictedReason
              ? 'When artifacts are available for this workspace, generated files, images, reports, code, pages, and downloads will appear here.'
              : 'Generated files, images, reports, code, pages, and downloads will appear here.'}
          />
        ) : (
          <section className="artifact-library-gallery" aria-label="Library results">
            {visibleItems.length === 0 ? (
              <EmptyPanel
                title="No matching files"
                body="Adjust the search or filter to see more library items."
              />
            ) : (
              <div className="artifact-file-grid">
                {visibleItems.map((item, index) => {
                  const artifactId = toArtifactId(item, index);
                  const mediaType = readString(item.mime_type ?? item.content_type, 'artifact');
                  const itemKind = artifactPreviewKind(mediaType);
                  const ItemIcon = artifactKindIcon(itemKind);
                  const isDocument = itemKind === 'document';
                  return (
                    <button
                      key={artifactId}
                      type="button"
                      className={joinClassNames('artifact-file-tile', `artifact-file-tile--${itemKind}`)}
                      onClick={() => {
                        window.location.assign(services.client.artifactDownloadUrl(artifactId));
                      }}
                    >
                      <span className="artifact-file-tile__preview" aria-hidden="true">
                        {isDocument ? (
                          <span className="artifact-file-tile__document-lines">
                            <span />
                            <span />
                            <span />
                          </span>
                        ) : (
                          <ItemIcon size={34} />
                        )}
                        <span className="artifact-file-tile__download">
                          <Download size={15} />
                        </span>
                      </span>
                      <span className="artifact-file-tile__title">{artifactName(item, artifactId)}</span>
                      <span className="artifact-file-tile__meta">
                        {formatTimestamp(item.created_at)} · {formatByteSize(item.byte_size)}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        )}
      </main>
    </WorkstationSurfaceRoot>
  );
}
