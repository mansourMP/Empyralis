"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Inbox as InboxIcon, AlertCircle } from "lucide-react";

import type { FleetAgent } from "../fleet-data";

/**
 * WORK tab — the agent's end-customer conversations, split-view:
 * left = conversation list, right = the selected transcript. Sourced from
 * /api/threads, scoped to this agent's own install id (agent_turn.py tags
 * every specialist turn's thread with it — same table every channel's real
 * turn already writes to, so Telegram/Slack/Discord/web chat conversations
 * for this agent all show up here, not just one channel's worth).
 *
 * Live: polls every 7s with turns included, so new customer messages appear
 * without a refresh, and conversations with activity the operator hasn't
 * opened yet carry an unread dot. Polling (not SSE) is a deliberate call — a
 * stream endpoint would need backend work owned by a parallel session; 7s
 * over the existing REST endpoint is responsive enough for a support inbox
 * without hammering the server.
 */

const POLL_MS = 7000;

type Turn = {
  role?: string;
  content?: string;
  created_at?: string;
};

type Thread = {
  id: string;
  title?: string;
  channel?: string;
  last_turn_at?: string;
  updated_at?: string;
  turns?: Turn[];
};

function threadStamp(t: Thread): string {
  return `${t.last_turn_at || t.updated_at || ""}#${t.turns?.length ?? ""}`;
}

function lastTurn(t: Thread): Turn | undefined {
  const turns = t.turns || [];
  return turns.length > 0 ? turns[turns.length - 1] : undefined;
}

export function WorkTab({
  workspaceId,
  agentId,
  agent,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
}) {
  const url = `/api/threads?workspace_id=${encodeURIComponent(workspaceId)}&agent_id=${encodeURIComponent(agentId)}&include_turns=true&limit=100`;

  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // Per-conversation stamp the operator has already seen. Unread = current
  // stamp is newer than seen (or the conversation appeared after first load).
  const [seen, setSeen] = useState<Record<string, string>>({});
  const firstLoadRef = useRef(true);

  const loadThreads = useCallback(async () => {
    try {
      const r = await fetch(url, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const list = (d?.items || []) as Thread[];
      const arr = Array.isArray(list) ? list : [];
      setError(null);
      setThreads(arr);
      if (firstLoadRef.current) {
        // Nothing is "new" on the operator's first view: seed seen = current.
        const seed: Record<string, string> = {};
        for (const t of arr) seed[t.id] = threadStamp(t);
        setSeen(seed);
        if (arr.length > 0) setSelected(arr[0].id);
        firstLoadRef.current = false;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load conversations");
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    firstLoadRef.current = true;
    setLoading(true);
    void loadThreads();
    const t = setInterval(() => void loadThreads(), POLL_MS);
    return () => clearInterval(t);
  }, [loadThreads]);

  // Keep the open conversation marked read as its stamp advances (poll or open).
  useEffect(() => {
    if (!selected) return;
    const t = threads.find((x) => x.id === selected);
    if (!t) return;
    const stamp = threadStamp(t);
    setSeen((prev) => (prev[selected] === stamp ? prev : { ...prev, [selected]: stamp }));
  }, [threads, selected]);

  const isUnread = (t: Thread): boolean => {
    if (t.id === selected) return false;
    const s = seen[t.id];
    if (s === undefined) return !firstLoadRef.current; // appeared after first load
    return threadStamp(t) > s;
  };

  const unreadCount = threads.reduce((n, t) => n + (isUnread(t) ? 1 : 0), 0);

  if (loading) {
    return (
      <div className="fleet-work-split">
        <div className="fleet-work-list" aria-label="Loading conversations">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="fleet-work-conv">
              <div className="fleet-skeleton-bar" style={{ width: "70%", height: 12 }} />
              <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginTop: 8 }} />
            </div>
          ))}
        </div>
        <div className="fleet-work-transcript" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="fleet-page-state">
        <AlertCircle size={22} strokeWidth={1.75} />
        <div className="fleet-page-state-title">Couldn’t load conversations</div>
        <div className="fleet-page-state-body">{error}. This usually clears on its own — it’ll keep retrying.</div>
      </div>
    );
  }

  if (threads.length === 0) {
    return (
      <div className="fleet-work-empty">
        <div className="fleet-empty-icon">
          <InboxIcon size={20} strokeWidth={1.75} />
        </div>
        <div className="fleet-work-empty-title">No conversations yet</div>
        <div className="fleet-work-empty-desc">
          When {agent?.label || "this agent"} handles end-customer conversations,
          they’ll show up here — every channel, in one place.
        </div>
      </div>
    );
  }

  const isAgentSide = (role: string) => {
    const r = role.toLowerCase();
    return r === "assistant" || r === "agent" || r === "bot";
  };

  const selectedThread = threads.find((t) => t.id === selected);
  const selectedTurns = selectedThread?.turns || [];

  return (
    <div className="fleet-work-split">
      <div className="fleet-work-list">
        {unreadCount > 0 && (
          <div className="fleet-work-list-live" aria-live="polite">
            <span className="fleet-work-conv-dot" /> {unreadCount} new
          </div>
        )}
        {threads.map((t) => {
          const when = t.last_turn_at || t.updated_at || "";
          const unread = isUnread(t);
          const preview = lastTurn(t)?.content || "—";
          return (
            <button
              key={t.id}
              type="button"
              className={`fleet-work-conv${selected === t.id ? " fleet-work-conv--active" : ""}${unread ? " fleet-work-conv--unread" : ""}`}
              onClick={() => setSelected(t.id)}
            >
              <div className="fleet-work-conv-top">
                <span className="fleet-work-conv-title">
                  {unread && <span className="fleet-work-conv-dot" aria-label="new" />}
                  {t.title || t.id}
                </span>
                {when && <span className="fleet-work-conv-time">{new Date(when).toLocaleDateString()}</span>}
              </div>
              <div className="fleet-work-conv-preview">
                {t.channel && <span className="fleet-work-conv-channel">{t.channel}</span>} {preview}
              </div>
            </button>
          );
        })}
      </div>
      <div className="fleet-work-transcript">
        {selectedTurns.length === 0 ? (
          <div className="fleet-page-state-body">Select a conversation to read it.</div>
        ) : (
          selectedTurns.map((m, i) => (
            <div
              key={i}
              className={`fleet-work-msg fleet-work-msg--${isAgentSide(m.role || "") ? "agent" : "user"}`}
            >
              <div className="fleet-work-msg-role">{m.role || "—"}</div>
              <div className="fleet-work-msg-body">{m.content || ""}</div>
              {m.created_at && <div className="fleet-work-msg-time">{new Date(m.created_at).toLocaleString()}</div>}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
