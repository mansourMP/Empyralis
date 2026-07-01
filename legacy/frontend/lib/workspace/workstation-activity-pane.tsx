'use client';

import { useEffect, useState } from 'react';
import { BookOpen, FileText } from 'lucide-react';

import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import {
  WorkstationSurfaceRoot,
} from '@/lib/workspace/workstation-surface-primitives';

type MemoryItem = {
  name: string;
  description: string;
  content: string;
};

type MemorySnapshot = {
  items: MemoryItem[];
  workspace: string;
  agent: string;
};

function MemoryEntryCard({ item }: { item: MemoryItem }) {
  const body = item.content
    .replace(/^---[\s\S]*?---\n?/, '')  // strip frontmatter
    .trim();

  return (
    <details
      style={{
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '12px 16px',
        marginBottom: 8,
        cursor: 'pointer',
      }}
    >
      <summary style={{ fontWeight: 600, fontSize: '0.95rem' }}>
        {item.name}
        {item.description ? (
          <span style={{ fontWeight: 400, color: 'var(--muted)', marginLeft: 8, fontSize: '0.85rem' }}>
            — {item.description}
          </span>
        ) : null}
      </summary>
      <pre
        style={{
          marginTop: 12,
          whiteSpace: 'pre-wrap',
          fontFamily: 'inherit',
          fontSize: '0.875rem',
          lineHeight: 1.6,
          color: 'var(--foreground)',
        }}
      >
        {body || '(empty)'}
      </pre>
    </details>
  );
}

export function WorkstationActivityPane() {
  const services = useWorkspaceServices();

  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const loadMemory = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = await services.client.listSageMemory();
      const items: MemoryItem[] = Array.isArray((payload as Record<string, unknown>)?.items)
        ? ((payload as Record<string, unknown>).items as MemoryItem[])
        : [];
      setSnapshot({
        items,
        workspace: String((payload as Record<string, unknown>)?.workspace ?? 'default'),
        agent: String((payload as Record<string, unknown>)?.agent ?? 'sage'),
      });
      setStatusMessage(items.length > 0 ? null : 'No memories yet. Sage writes memories as it works.');
    } catch (err) {
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadMemory();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const items = snapshot?.items ?? [];
  const hasItems = items.length > 0;

  return (
    <WorkstationSurfaceRoot surface="memory">
      {statusMessage ? (
        <PlatformNotification
          tone="info"
          title="Memory"
          detail={statusMessage}
          onClose={() => setStatusMessage(null)}
        />
      ) : null}

      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16 }}>
          <SkeletonBlock width="60%" />
          <SkeletonBlock width="80%" />
          <SkeletonBlock width="40%" />
        </div>
      ) : error ? (
        <DataPaneError error={error} onRetry={() => { void loadMemory(); }} label="Memory" />
      ) : (
        <div style={{ padding: 16 }}>
          {hasItems ? (
            <>
              <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginBottom: 16 }}>
                {items.length} memor{items.length === 1 ? 'y' : 'ies'} stored by {snapshot?.agent ?? 'sage'}.
                Read-only — Sage manages its own memory.
              </p>
              {items.map((item) => (
                <MemoryEntryCard key={item.name} item={item} />
              ))}
            </>
          ) : (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>
              <FileText size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <p>No memories yet.</p>
              <p style={{ fontSize: '0.85rem' }}>
                Sage writes memories as it learns about you through chat.
              </p>
            </div>
          )}
        </div>
      )}
    </WorkstationSurfaceRoot>
  );
}
