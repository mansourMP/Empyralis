"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { timeAgo } from "./fleet-presentation";
import { MemoryPreview } from "./tabs/MemoryTab";

/**
 * The two things the Ask AI console gains beside its chat: HISTORY (your own
 * past conversations with the workspace agent, reopenable) and MEMORY (what
 * that agent actually remembers). Both live *inside* the console — neither
 * earns a route or a tab of its own.
 *
 * Nothing here is a new endpoint. History reads GET /api/threads (registered
 * by runtime_runs_api.register_run_routes, mounted under /api by
 * routes_runs.py), the same control-plane thread store AgentChat already
 * loads a single thread from. Memory reads the agent's memory tree —
 * /api/w/{ws}/fleet/agents/{id}/memory/{tree,file} — the exact pair the
 * agent detail page's Memory tab uses, and renders it with that tab's own
 * MemoryPreview, so "memory" means one thing in this product, not two.
 *
 * PRIVACY (CLAUDE.md: "Conversations are private. Work is shared.")
 * -----------------------------------------------------------------
 * GET /threads scopes rows to owner_user_id server-side for ordinary
 * members — but NOT for a privileged caller (admin flag, admin allow-list,
 * or api_key auth): runtime_runs_api's list_threads passes owner_user_id=None
 * for those, and the SQL then returns every thread in the workspace,
 * teammates' included. So this hook re-scopes client-side to the signed-in
 * account's own id and drops anything else, unconditionally. An unowned
 * (NULL owner) row is dropped too — it is not provably mine. If the account
 * hasn't resolved yet there is no id to compare against, so the list is
 * empty rather than unfiltered. Belt and braces, in that order.
 */

/** Ask AI console threads carry this prefix. `sage-main` — the single
 *  workspace-wide id every member's console used to write to — matches it
 *  too, so an existing conversation still lists and reopens for whoever
 *  owns it. New conversations get a unique id instead of that shared one
 *  (see newSageThreadId). */
export const SAGE_THREAD_PREFIX = "sage-";

/** The server owns the thread id for *direct-chat* turns and names it
 *  `thread_sage_{workspace}_{owner}` (agent_registry_repository.
 *  build_master_thread_id) — same agent, same person, reached from another
 *  surface, so those belong in this history too. Per-specialist chats
 *  (`thread_agent_{agentId}`, the agent detail page's Chat tab) do not:
 *  they are a different agent's conversation and have their own home. */
const SERVER_OWNED_SAGE_THREAD_PREFIX = "thread_sage_";

export function newSageThreadId(): string {
  return `${SAGE_THREAD_PREFIX}${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isSageConsoleThread(id: string): boolean {
  return id.startsWith(SAGE_THREAD_PREFIX) || id.startsWith(SERVER_OWNED_SAGE_THREAD_PREFIX);
}

export type SageConversation = {
  id: string;
  /** The thread's own title, which control_plane_repository builds from the
   *  FIRST message and then never overwrites (ensure_agent_thread's
   *  `CASE WHEN title = 'New chat'` upsert) — so it really is the opening
   *  line, not the latest one. */
  title: string;
  lastActivityAt: string | null;
};

type RawThread = Record<string, unknown>;

function toConversation(raw: RawThread): SageConversation | null {
  const id = String(raw?.id ?? "").trim();
  if (!id || !isSageConsoleThread(id)) return null;
  return {
    id,
    title: String(raw?.title ?? "").trim() || "New chat",
    lastActivityAt:
      String(raw?.last_turn_at ?? "").trim() ||
      String(raw?.updated_at ?? "").trim() ||
      String(raw?.created_at ?? "").trim() ||
      null,
  };
}

export function useSageConversations(workspaceId: string, enabled: boolean) {
  const { state } = useAccountShell();
  const ownerUserId = String(state.account?.id ?? "").trim();

  const [conversations, setConversations] = useState<SageConversation[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    if (!ownerUserId) {
      setConversations([]);
      setLoaded(true);
      return;
    }
    try {
      const res = await fetch(
        `/api/threads?workspace_id=${encodeURIComponent(workspaceId)}&limit=50`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items: RawThread[] = Array.isArray(data?.items) ? data.items : [];
      const mine = items
        .filter((item) => String(item?.owner_user_id ?? "").trim() === ownerUserId)
        .map(toConversation)
        .filter((c): c is SageConversation => c !== null);
      mine.sort((a, b) => String(b.lastActivityAt ?? "").localeCompare(String(a.lastActivityAt ?? "")));
      setConversations(mine);
    } catch {
      // A failed list is not worth an error banner over a working chat — the
      // history control simply doesn't appear until the next refresh lands.
      setConversations([]);
    } finally {
      setLoaded(true);
    }
  }, [workspaceId, ownerUserId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
  }, [enabled, refresh]);

  return { conversations, loaded, refresh };
}

export function SageHistoryPanel({
  conversations,
  activeThreadId,
  onOpen,
}: {
  conversations: SageConversation[];
  activeThreadId: string | null;
  onOpen: (threadId: string) => void;
}) {
  return (
    <div className="fleet-sage-panel" role="list">
      {conversations.map((c) => {
        const isActive = c.id === activeThreadId;
        return (
          <button
            key={c.id}
            type="button"
            role="listitem"
            className={`fleet-sage-history-row${isActive ? " is-active" : ""}`}
            onClick={() => onOpen(c.id)}
          >
            <span className="fleet-sage-history-row-title">{c.title}</span>
            <span className="fleet-sage-history-row-time">
              {isActive ? "Open" : timeAgo(c.lastActivityAt)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

type MemoryFile = { path: string };

/** The agent's memory tree, read from the same endpoint the agent detail
 *  page's Memory tab reads. Fetched while the console is open so the
 *  console knows whether a Memory control has anything to open — a control
 *  over an empty tree would be a dead control. */
export function useSageMemoryFiles(workspaceId: string, agentId: string | null, enabled: boolean) {
  const [files, setFiles] = useState<MemoryFile[]>([]);

  useEffect(() => {
    if (!enabled || !agentId) return;
    let cancelled = false;
    const base = `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/memory`;
    fetch(`${base}/tree`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const indexPath = String(d?.index?.path || "").trim();
        const index: MemoryFile[] = indexPath ? [{ path: indexPath }] : [];
        const topics: MemoryFile[] = Array.isArray(d?.topics)
          ? d.topics
              .map((t: any) => ({ path: String(t?.path || t?.name || t || "").trim() }))
              .filter((t: MemoryFile) => t.path && t.path !== indexPath)
          : [];
        setFiles([...index, ...topics]);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, agentId, enabled]);

  return files;
}

export function SageMemoryPanel({
  workspaceId,
  agentId,
  files,
}: {
  workspaceId: string;
  agentId: string;
  files: MemoryFile[];
}) {
  const base = `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/memory`;
  const [selected, setSelected] = useState<string>(files[0]?.path || "");
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!selected && files[0]?.path) setSelected(files[0].path);
  }, [files, selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    fetch(`${base}/file?path=${encodeURIComponent(selected)}`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setContent(String(d?.content ?? d?.text ?? ""));
      })
      .catch(() => {
        if (!cancelled) setContent("");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [base, selected]);

  return (
    <div className="fleet-sage-panel">
      {/* One file is not a choice — the switcher only appears once there is
          something to switch between. */}
      {files.length > 1 && (
        <div className="fleet-sage-memory-files">
          {files.map((f) => (
            <button
              key={f.path}
              type="button"
              className={`fleet-sage-memory-file${selected === f.path ? " is-active" : ""}`}
              onClick={() => setSelected(f.path)}
            >
              <FileText size={12} strokeWidth={1.75} />
              <span className="fleet-sage-memory-file-path">{f.path}</span>
            </button>
          ))}
        </div>
      )}
      <div className="fleet-sage-memory-body">
        {loading ? <div className="fleet-page-state-body">Loading…</div> : <MemoryPreview content={content} />}
      </div>
    </div>
  );
}
