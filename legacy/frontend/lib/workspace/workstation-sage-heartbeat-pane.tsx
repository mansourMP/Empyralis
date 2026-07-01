'use client';

import { useEffect, useState } from 'react';
import { ListTodo, MessageSquare, Clock } from 'lucide-react';

import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { useWorkspaceServices, useWorkstationStreamState } from '@/lib/workspace/workspace-services';
import {
  WorkstationSurfaceRoot,
  WorkstationSurfaceStat,
  WorkstationSurfaceStatGrid,
} from '@/lib/workspace/workstation-surface-primitives';

type ConversationMeta = {
  chat_id: string;
  message_count: number;
  last_activity: string | null;
};

type TaskSnapshot = {
  conversations: ConversationMeta[];
  total_messages: number;
  workspace: string;
  channel: string;
};

function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'unknown';
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return 'unknown';
  }
}

export function WorkstationSageWorkCenterPane() {
  const services = useWorkspaceServices();

  const [snapshot, setSnapshot] = useState<TaskSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const activityVersion = useWorkstationStreamState().activity.version;

  const loadTasks = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = await services.client.getSageHeartbeat();
      const conversations: ConversationMeta[] = Array.isArray(
        (payload as Record<string, unknown>)?.conversations,
      )
        ? ((payload as Record<string, unknown>).conversations as ConversationMeta[])
        : [];
      setSnapshot({
        conversations,
        total_messages: Number((payload as Record<string, unknown>)?.total_messages ?? 0),
        workspace: String((payload as Record<string, unknown>)?.workspace ?? 'default'),
        channel: String((payload as Record<string, unknown>)?.channel ?? 'web'),
      });
    } catch (err) {
      setError(err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadTasks();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch on chat activity (debounced)
  useEffect(() => {
    if (activityVersion === 0) return;
    const t = setTimeout(() => { void loadTasks(); }, 750);
    return () => clearTimeout(t);
  }, [activityVersion]);  // eslint-disable-line react-hooks/exhaustive-deps

  const conversations = snapshot?.conversations ?? [];
  const hasActivity = conversations.length > 0;

  return (
    <WorkstationSurfaceRoot surface="tasks">
      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16 }}>
          <SkeletonBlock width="50%" />
          <SkeletonBlock width="70%" />
          <SkeletonBlock width="30%" />
        </div>
      ) : error ? (
        <DataPaneError error={error} onRetry={() => { void loadTasks(); }} label="Tasks" />
      ) : (
        <div style={{ padding: 16 }}>
          {hasActivity ? (
            <>
              <WorkstationSurfaceStatGrid>
                <WorkstationSurfaceStat
                  label="Conversations"
                  value={String(conversations.length)}
                  hint="Conversation sessions tracked"
                />
                <WorkstationSurfaceStat
                  label="Total messages"
                  value={String(snapshot?.total_messages ?? 0)}
                  hint="Across all conversations"
                />
              </WorkstationSurfaceStatGrid>

              <p style={{ color: 'var(--muted)', fontSize: '0.8rem', marginTop: 16, marginBottom: 8 }}>
                Recent activity — derived from conversation history. No task engine exists.
              </p>

              {conversations.map((conv) => (
                <div
                  key={conv.chat_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '10px 12px',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius)',
                    marginBottom: 6,
                  }}
                >
                  <MessageSquare size={16} style={{ opacity: 0.5, flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>
                      {conv.chat_id.length > 12
                        ? `${conv.chat_id.slice(0, 8)}...${conv.chat_id.slice(-4)}`
                        : conv.chat_id}
                    </div>
                    <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>
                      {conv.message_count} message{conv.message_count !== 1 ? 's' : ''}
                    </div>
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      color: 'var(--muted)',
                      fontSize: '0.8rem',
                      flexShrink: 0,
                    }}
                  >
                    <Clock size={12} />
                    {formatRelativeTime(conv.last_activity)}
                  </div>
                </div>
              ))}
            </>
          ) : (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>
              <ListTodo size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <p>No activity yet.</p>
              <p style={{ fontSize: '0.85rem' }}>
                Activity will appear here as you chat with Sage.
              </p>
            </div>
          )}
        </div>
      )}
    </WorkstationSurfaceRoot>
  );
}
