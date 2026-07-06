"use client";

import { useEffect, useState } from "react";
import { Inbox as InboxIcon } from "lucide-react";

import type { FleetAgent } from "../fleet-data";

/**
 * WORK tab — the agent's end-customer conversations, split-view:
 * left = conversation list, right = the selected transcript. Sourced from the
 * deployed-agent conversation endpoints. Internal agents (Sage, unpublished
 * specialists) have none yet, so this resolves to a clean empty state.
 */

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
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [msgLoading, setMsgLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${base}?${q}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { ok: false }))
      .then((d) => {
        if (cancelled) return;
        const list = (d?.conversations || d?.sessions || d?.items || []) as Conversation[];
        const arr = Array.isArray(list) ? list : [];
        setConvos(arr);
        if (arr.length > 0) setSelected(convId(arr[0]));
      })
      .catch(() => { if (!cancelled) setConvos([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [base, q]);

  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    let cancelled = false;
    setMsgLoading(true);
    fetch(`${base}/${encodeURIComponent(selected)}?${q}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { ok: false }))
      .then((d) => {
        if (cancelled) return;
        const msgs = (d?.messages || d?.turns || d?.transcript || []) as Message[];
        setMessages(Array.isArray(msgs) ? msgs : []);
      })
      .catch(() => { if (!cancelled) setMessages([]); })
      .finally(() => { if (!cancelled) setMsgLoading(false); });
    return () => { cancelled = true; };
  }, [selected, base, q]);

  if (loading) {
    return <div className="fleet-detail-pad"><div className="fleet-page-state-body">Loading conversations…</div></div>;
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
        {convos.map((c) => {
          const id = convId(c);
          const when = c.last_message_at || c.updated_at || "";
          return (
            <button
              key={id}
              type="button"
              className={`fleet-work-conv${selected === id ? " fleet-work-conv--active" : ""}`}
              onClick={() => setSelected(id)}
            >
              <div className="fleet-work-conv-top">
                <span className="fleet-work-conv-title">{c.title || c.customer || id}</span>
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
