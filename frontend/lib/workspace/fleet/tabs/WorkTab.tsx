"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Inbox as InboxIcon, AlertCircle } from "lucide-react";

import type { FleetAgent } from "../fleet-data";

/**
 * WORK tab — the agent's end-customer conversations, split-view:
 * left = conversation list, right = the selected transcript. Sourced from the
 * deployed-agent conversation endpoints. Internal agents (Sage, unpublished
 * specialists) have none yet, so this resolves to a clean empty state.
 *
 * Live: the conversation list and the open transcript re-poll every 7s, so new
 * customer messages appear without a refresh, and conversations with activity
 * the operator hasn't opened yet carry an unread dot. Polling (not SSE) is a
 * deliberate call — a stream endpoint would need backend work owned by a
 * parallel session; 7s over the existing REST endpoints is responsive enough
 * for a support inbox without hammering the server.
 */

const POLL_MS = 7000;

type Conversation = {
  session_id?: string;
  id?: string;
  title?: string;
  customer?: string;
  preview?: string;
  last_message?: string;
  last_message_at?: string;
  updated_at?: string;
  message_count?: number;
};

type Message = {
  role?: string;
  author?: string;
  content?: string;
  text?: string;
  created_at?: string;
};

function convId(c: Conversation): string {
  return String(c.session_id || c.id || "");
}

// A monotonic "freshness" stamp per conversation: newest activity time, and the
// message count as a tiebreaker so a new message with an equal timestamp still
// reads as fresh.
function convStamp(c: Conversation): string {
  return `${c.last_message_at || c.updated_at || ""}#${c.message_count ?? ""}`;
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
  const base = `/api/deployed-agents/${encodeURIComponent(agentId)}/conversations`;
  const q = `workspace_id=${encodeURIComponent(workspaceId)}`;

  const [convos, setConvos] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [msgLoading, setMsgLoading] = useState(false);
  // Per-conversation stamp the operator has already seen. Unread = current
  // stamp is newer than seen (or the conversation appeared after first load).
  const [seen, setSeen] = useState<Record<string, string>>({});
  const firstLoadRef = useRef(true);

  const loadConversations = useCallback(async () => {
    try {
      const r = await fetch(`${base}?${q}`, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const list = (d?.conversations || d?.sessions || d?.items || []) as Conversation[];
      const arr = Array.isArray(list) ? list : [];
      setError(null);
      setConvos(arr);
      if (firstLoadRef.current) {
        // Nothing is "new" on the operator's first view: seed seen = current.
        const seed: Record<string, string> = {};
        for (const c of arr) seed[convId(c)] = convStamp(c);
        setSeen(seed);
        if (arr.length > 0) setSelected(convId(arr[0]));
        firstLoadRef.current = false;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load conversations");
    } finally {
      setLoading(false);
    }
  }, [base, q]);

  // Poll the conversation list. Re-seeds firstLoad on agent switch.
  useEffect(() => {
    firstLoadRef.current = true;
    setLoading(true);
    void loadConversations();
    const t = setInterval(() => void loadConversations(), POLL_MS);
    return () => clearInterval(t);
  }, [loadConversations]);

  const loadMessages = useCallback(async (id: string, isPoll: boolean) => {
    if (!isPoll) setMsgLoading(true);
    try {
      const r = await fetch(`${base}/${encodeURIComponent(id)}?${q}`, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const msgs = (d?.messages || d?.turns || d?.transcript || []) as Message[];
      setMessages(Array.isArray(msgs) ? msgs : []);
    } catch {
      if (!isPoll) setMessages([]);
    } finally {
      if (!isPoll) setMsgLoading(false);
    }
  }, [base, q]);

  // Poll the open transcript so new messages stream in.
  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    void loadMessages(selected, false);
    const t = setInterval(() => void loadMessages(selected, true), POLL_MS);
    return () => clearInterval(t);
  }, [selected, loadMessages]);

  // Keep the open conversation marked read as its stamp advances (poll or open).
  useEffect(() => {
    if (!selected) return;
    const c = convos.find((x) => convId(x) === selected);
    if (!c) return;
    const stamp = convStamp(c);
    setSeen((prev) => (prev[selected] === stamp ? prev : { ...prev, [selected]: stamp }));
  }, [convos, selected]);

  const isUnread = (c: Conversation): boolean => {
    const id = convId(c);
    if (id === selected) return false;
    const s = seen[id];
    if (s === undefined) return !firstLoadRef.current; // appeared after first load
    return convStamp(c) > s;
  };

  const unreadCount = convos.reduce((n, c) => n + (isUnread(c) ? 1 : 0), 0);

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

  if (convos.length === 0) {
    return (
      <div className="fleet-work-empty">
        <div className="fleet-empty-icon">
          <InboxIcon size={20} strokeWidth={1.75} />
        </div>
        <div className="fleet-work-empty-title">No conversations yet</div>
        <div className="fleet-work-empty-desc">
          When {agent?.label || "this agent"} handles end-customer conversations,
          they’ll show up here — one thread per customer, with the full transcript.
        </div>
      </div>
    );
  }

  const isAgentSide = (role: string) => {
    const r = role.toLowerCase();
    return r === "assistant" || r === "agent" || r === "bot";
  };

  return (
    <div className="fleet-work-split">
      <div className="fleet-work-list">
        {unreadCount > 0 && (
          <div className="fleet-work-list-live" aria-live="polite">
            <span className="fleet-work-conv-dot" /> {unreadCount} new
          </div>
        )}
        {convos.map((c) => {
          const id = convId(c);
          const when = c.last_message_at || c.updated_at || "";
          const unread = isUnread(c);
          return (
            <button
              key={id}
              type="button"
              className={`fleet-work-conv${selected === id ? " fleet-work-conv--active" : ""}${unread ? " fleet-work-conv--unread" : ""}`}
              onClick={() => setSelected(id)}
            >
              <div className="fleet-work-conv-top">
                <span className="fleet-work-conv-title">
                  {unread && <span className="fleet-work-conv-dot" aria-label="new" />}
                  {c.title || c.customer || id}
                </span>
                {when && <span className="fleet-work-conv-time">{new Date(when).toLocaleDateString()}</span>}
              </div>
              <div className="fleet-work-conv-preview">{c.preview || c.last_message || "—"}</div>
            </button>
          );
        })}
      </div>
      <div className="fleet-work-transcript">
        {msgLoading ? (
          <div className="fleet-page-state-body">Loading transcript…</div>
        ) : messages.length === 0 ? (
          <div className="fleet-page-state-body">Select a conversation to read it.</div>
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`fleet-work-msg fleet-work-msg--${isAgentSide(m.role || "") ? "agent" : "user"}`}
            >
              <div className="fleet-work-msg-role">{m.author || m.role || "—"}</div>
              <div className="fleet-work-msg-body">{m.content || m.text || ""}</div>
              {m.created_at && <div className="fleet-work-msg-time">{new Date(m.created_at).toLocaleString()}</div>}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
