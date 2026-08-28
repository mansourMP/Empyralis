"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useState } from "react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { stripEnvelopeHeader } from "./AgentChat";
import { timeAgo } from "./fleet-presentation";

/**
 * The one thing the Ask AI console gains beside its chat: HISTORY — your own
 * past conversations with the workspace agent, reopenable. It lives *inside*
 * the console; it does not earn a route or a tab of its own.
 *
 * Nothing here is a new endpoint. History reads GET /api/threads (registered
 * by runtime_runs_api.register_run_routes, mounted under /api by
 * routes_runs.py), the same control-plane thread store AgentChat already
 * loads a single thread from.
 *
 * There used to be a MEMORY view here too, reading the agent's memory tree
 * and rendering it with MemoryTab's MemoryPreview. Removed 2026-08-01
 * (founder ruling): Ask AI is a personal helper, not a deployed agent, so a
 * memory-index surface is not something it needs — and what that view
 * actually showed was the authoring scaffold ("keep it dense, no filler…"),
 * an instruction written for an agent, never for a person. Memory that a
 * human has a reason to read still lives on the agent detail page's Memory
 * tab, where it is about a specific deployed agent.
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
   *  line, not the latest one. Built from the *enveloped* message, so it
   *  arrives with the attribution header on the front; stripped below for
   *  the same reason the transcript strips it. */
  title: string;
  lastActivityAt: string | null;
};

type RawThread = Record<string, unknown>;

function toConversation(raw: RawThread): SageConversation | null {
  const id = String(raw?.id ?? "").trim();
  if (!id || !isSageConsoleThread(id)) return null;
  return {
    id,
    title: stripEnvelopeHeader(String(raw?.title ?? "").trim()).trim() || "New chat",
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
      const res = await fleetAuthorizedFetch(
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
    <div className="fleet-assistant-panel" role="list">
      {conversations.map((c) => {
        const isActive = c.id === activeThreadId;
        return (
          <button
            key={c.id}
            type="button"
            role="listitem"
            className={`fleet-assistant-history-row${isActive ? " is-active" : ""}`}
            onClick={() => onOpen(c.id)}
          >
            <span className="fleet-assistant-history-row-title">{c.title}</span>
            <span className="fleet-assistant-history-row-time">
              {isActive ? "Open" : timeAgo(c.lastActivityAt)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
