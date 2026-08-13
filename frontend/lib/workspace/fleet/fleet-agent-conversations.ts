"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useState } from "react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { stripEnvelopeHeader } from "./AgentChat";
import type { SageConversation } from "./SageConsolePanels";

/**
 * Per-agent conversation history for the Fleet agent detail page's Chat tab —
 * the sibling of SageConsolePanels' useSageConversations, scoped to ONE
 * specialist agent instead of the workspace's Ask AI console.
 *
 * Reads the same GET /api/threads endpoint SageConsolePanels already reads
 * (runtime_runs_api.register_run_routes' list_threads, mounted under /api by
 * routes_runs.py) and adds the `agent_id` query param that route already
 * accepts — it forwards straight into thread_service.list_threads'
 * active_agent_install_id, which control_plane_repository.list_agent_threads
 * turns into `WHERE master_agent_install_id = $n`. The SQL does the per-agent
 * scoping; unlike SageConsolePanels' isSageConsoleThread, no client-side id
 * prefix filter is needed here.
 *
 * THE POISONED-THREAD ESCAPE HATCH
 * ---------------------------------
 * Every agent's Chat tab used to hang off exactly one permanent thread id —
 * `thread_agent_{agentId}` (defaultAgentThreadId below), forever. A bad turn
 * recorded on it stayed in every future turn's history with no way out.
 * newAgentThreadId mints a genuinely different id (never reused), so a fresh
 * conversation has no prior turns for the model to read back as fact — this
 * is the actual fix, not a client-side filter over the same poisoned rows.
 *
 * PRIVACY, same reasoning as useSageConversations: GET /threads only scopes
 * to owner_user_id for ordinary members, not for a privileged/admin caller
 * (list_threads passes owner_user_id=None for those, so the SQL returns
 * every thread in the workspace). Re-scope client-side to the signed-in
 * account's own id, unconditionally — belt and braces, in that order.
 */

const AGENT_THREAD_PREFIX = "thread_agent_";

/** The Chat tab's original, permanent thread id — FleetAgentDetail.tsx used
 *  this as the ONLY thread id before conversation history existed, so it is
 *  still where every pre-existing agent's history (poisoned or not) lives.
 *  New conversations never reuse it. */
export function defaultAgentThreadId(agentId: string): string {
  return `${AGENT_THREAD_PREFIX}${agentId}`;
}

export function newAgentThreadId(agentId: string): string {
  return `${AGENT_THREAD_PREFIX}${agentId}_${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function agentThreadStorageKey(workspaceId: string, agentId: string): string {
  return `empyralis.chat.agent-thread.v1:${workspaceId}:${agentId}`;
}

/** Which conversation this browser last had open with this agent — read
 *  synchronously at mount so the Chat tab never flashes the legacy thread
 *  before swapping to whatever was last picked (New chat / History). Best
 *  effort only: a private/locked-down browser with no localStorage just
 *  falls back to the legacy default every time, same as before this existed. */
export function readPersistedAgentThreadId(workspaceId: string, agentId: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(agentThreadStorageKey(workspaceId, agentId));
  } catch {
    return null;
  }
}

export function persistAgentThreadId(workspaceId: string, agentId: string, threadId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(agentThreadStorageKey(workspaceId, agentId), threadId);
  } catch {
    // Best effort — a full/blocked localStorage just means the next visit
    // reopens the legacy default instead of the last-picked conversation.
  }
}

type RawThread = Record<string, unknown>;

function toConversation(raw: RawThread): SageConversation | null {
  const id = String(raw?.id ?? "").trim();
  if (!id) return null;
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

export function useAgentConversations(workspaceId: string, agentId: string, enabled: boolean) {
  const { state } = useAccountShell();
  const ownerUserId = String(state.account?.id ?? "").trim();

  const [conversations, setConversations] = useState<SageConversation[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    if (!ownerUserId || !agentId) {
      setConversations([]);
      setLoaded(true);
      return;
    }
    try {
      const res = await fleetAuthorizedFetch(
        `/api/threads?workspace_id=${encodeURIComponent(workspaceId)}&agent_id=${encodeURIComponent(agentId)}&limit=50`,
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
      // history/new-chat controls simply stay hidden until the next refresh
      // lands (same trade-off useSageConversations makes).
      setConversations([]);
    } finally {
      setLoaded(true);
    }
  }, [workspaceId, agentId, ownerUserId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
  }, [enabled, refresh]);

  return { conversations, loaded, refresh };
}
