"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, MessagesSquare } from "lucide-react";

import { useFleetAgents } from "./fleet-data";
import { findSageAgent, timeAgo } from "./fleet-presentation";
import { MarkdownLiteText } from "@/lib/workspace/markdown-lite";
import { FleetChatSkeleton } from "./fleet-states";

/**
 * CONVERSATIONS — the workspace-level view WorkTab.tsx never had: every
 * channel conversation across EVERY agent in one place, each row tagged
 * with provenance (agent · channel · person/group). This is the thing
 * that was promised ("everything goes to the same place, tagged by where
 * it came from") but never built — WorkTab only ever shows one agent's
 * own conversations, sourced from /api/threads (a control-plane store
 * that is dead under SQLite-fallback prod). This view reads
 * server_modules/agent_conversation_memory.py's durable per-(workspace,
 * agent, conversation) JSONL store directly, via
 * GET /api/w/{workspaceId}/conversations[/{conversationId}] —
 * server_modules/routes_conversations.py.
 *
 * Deliberately reuses WorkTab's exact split-view markup and CSS classes
 * (.fleet-work-list / .fleet-work-transcript-pane / .fleet-work-conv-* /
 * .fleet-work-msg-*), including its mobile stacking behavior — same
 * proven pattern, generalized from "one agent, every channel" to "every
 * agent, every channel". Two differences from WorkTab, both structural:
 *
 *  - The list/detail split is TWO requests, not one: the list endpoint
 *    returns summaries only (cheap to poll across every agent × channel
 *    in the workspace), and the selected conversation's full transcript
 *    is fetched separately when the selection changes.
 *  - There is no per-turn timestamp in this store's record shape (role +
 *    content only) — "last activity" is the conversation file's own
 *    mtime, and individual messages simply render without a per-message
 *    time, same graceful omission WorkTab's own markup already supports.
 */

const POLL_MS = 7000;

type ConversationSummary = {
  conversation_id: string;
  agent_id: string;
  surface_channel: string;
  channel_label: string;
  remote_jid: string;
  sender: string;
  turn_count: number;
  last_message_preview: string;
  last_message_role: string;
  last_activity_at: string;
};

type ConversationTurn = {
  role?: string;
  content?: string;
};

type ConversationDetail = {
  conversation_id: string;
  agent_id: string;
  surface_channel: string;
  channel_label: string;
  remote_jid: string;
  sender: string;
  turns: ConversationTurn[];
};

function stamp(c: ConversationSummary): string {
  return `${c.last_activity_at}#${c.turn_count}`;
}

function isAgentSide(role: string): boolean {
  const r = role.toLowerCase();
  return r === "assistant" || r === "agent" || r === "bot";
}

// Plain-text preview snippet (list row) — same treatment as WorkTab's own
// stripMarkdownPreview: strip markdown syntax rather than render it, since
// a one-line truncated preview has no room for real formatting.
function stripMarkdownPreview(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

// WhatsApp JIDs survive the write path's ":" -> "_" sanitization as
// "<number>_s.whatsapp.net" / "<group-id>_g.us" (see
// agent_conversation_memory.py's _split_conversation_key) — trim the
// well-known suffix for a cleaner "who" tag. Purely cosmetic: the
// underlying store has no display-name field at all, so this is still
// the raw identifier, just tidied up, never a fabricated name.
function prettySender(sender: string): string {
  return sender.replace(/_s\.whatsapp\.net$/, "").replace(/_g\.us$/, " (group)");
}

export function ConversationsView({ workspaceId }: { workspaceId: string }) {
  const { agents } = useFleetAgents(workspaceId);
  const sageAgent = useMemo(() => findSageAgent(agents), [agents]);
  const agentLabel = useCallback(
    (agentId: string): string => {
      if (!agentId) return sageAgent?.label || "Ask AI";
      const a = agents.find((x) => x.agent_id === agentId);
      return a?.label || "Unnamed agent";
    },
    [agents, sageAgent],
  );

  const listUrl = `/api/w/${encodeURIComponent(workspaceId)}/conversations`;

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Per-conversation stamp the operator has already seen — same scheme as
  // WorkTab: unread = current stamp is newer than seen (or the
  // conversation appeared after first load).
  const [seen, setSeen] = useState<Record<string, string>>({});
  const firstLoadRef = useRef(true);

  const loadList = useCallback(async () => {
    try {
      const r = await fetch(listUrl, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const list = Array.isArray(d?.conversations) ? (d.conversations as ConversationSummary[]) : [];
      setError(null);
      setConversations(list);
      if (firstLoadRef.current) {
        const seed: Record<string, string> = {};
        for (const c of list) seed[c.conversation_id] = stamp(c);
        setSeen(seed);
        if (list.length > 0) setSelectedId(list[0].conversation_id);
        firstLoadRef.current = false;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load conversations");
    } finally {
      setLoading(false);
    }
  }, [listUrl]);

  useEffect(() => {
    firstLoadRef.current = true;
    setLoading(true);
    void loadList();
    const t = setInterval(() => void loadList(), POLL_MS);
    return () => clearInterval(t);
  }, [loadList]);

  // Keep the open conversation marked read as its stamp advances (poll or open).
  useEffect(() => {
    if (!selectedId) return;
    const c = conversations.find((x) => x.conversation_id === selectedId);
    if (!c) return;
    const s = stamp(c);
    setSeen((prev) => (prev[selectedId] === s ? prev : { ...prev, [selectedId]: s }));
  }, [conversations, selectedId]);

  const isUnread = (c: ConversationSummary): boolean => {
    if (c.conversation_id === selectedId) return false;
    const s = seen[c.conversation_id];
    if (s === undefined) return !firstLoadRef.current; // appeared after first load
    return stamp(c) > s;
  };

  const unreadCount = conversations.reduce((n, c) => n + (isUnread(c) ? 1 : 0), 0);

  // The selected conversation's transcript is a SEPARATE fetch (the list
  // endpoint intentionally returns summaries only, to keep the poll
  // payload small across every agent × channel) — re-fetched whenever the
  // selection changes, or the selected row's own stamp advances (new
  // activity while it's already open), matching WorkTab's live-update feel.
  const selected = conversations.find((c) => c.conversation_id === selectedId) || null;
  const selectedStamp = selected ? stamp(selected) : "";

  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/conversations/${encodeURIComponent(selectedId)}`, {
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d?.ok && d.conversation) {
          setDetail(d.conversation as ConversationDetail);
        } else {
          setDetail(null);
          setDetailError(String(d?.error || "Conversation not found"));
        }
      })
      .catch((e) => {
        if (!cancelled) setDetailError(e instanceof Error ? e.message : "Could not load conversation");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, selectedId, selectedStamp]);

  if (loading) {
    return (
      <main className="fleet-content fleet-content--split">
        <div className="fleet-work-split">
          <div className="fleet-work-list" aria-label="Loading conversations">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="fleet-work-conv">
                <div className="fleet-skeleton-bar" style={{ width: "70%", height: 12 }} />
                <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginTop: 8 }} />
              </div>
            ))}
          </div>
          {/* A blank pane reserved zero shape for what resolves into chat
              bubbles the instant a thread auto-selects — reuse the same
              chat-bubble skeleton the detail-loading branch below already
              uses for exactly this pane. */}
          <div className="fleet-work-transcript-pane">
            <FleetChatSkeleton label="Loading conversation" />
          </div>
        </div>
      </main>
    );
  }

  if (error) {
    return (
      <main className="fleet-content">
        <div className="fleet-page-state">
          <AlertCircle size={22} strokeWidth={1.75} />
          <div className="fleet-page-state-title">Couldn’t load conversations</div>
          <div className="fleet-page-state-body">{error}. This usually clears on its own — it’ll keep retrying.</div>
        </div>
      </main>
    );
  }

  const selectedTurns = detail?.turns || [];
  const selectedWho = detail ? prettySender(detail.sender) : selected ? prettySender(selected.sender) : "";
  const selectedAgentLabel = selected ? agentLabel(selected.agent_id) : "";
  const selectedChannel = detail?.channel_label || selected?.channel_label || "";

  return (
    <main className="fleet-content fleet-content--split">
      <div className="fleet-work-split">
        <div className="fleet-work-list">
          {conversations.length === 0 ? (
            <div className="fleet-work-list-empty">
              <div className="fleet-work-list-empty-title">No conversations yet</div>
              <div className="fleet-work-list-empty-desc">
                When your agents talk with someone over any connected channel, every conversation shows up here — tagged by agent, channel, and who it’s with.
              </div>
            </div>
          ) : null}
          {unreadCount > 0 && (
            <div className="fleet-work-list-live" aria-live="polite">
              <span className="fleet-work-conv-dot" /> {unreadCount} new
            </div>
          )}
          {conversations.map((c) => {
            const who = prettySender(c.sender) || "Untitled conversation";
            const unread = isUnread(c);
            return (
              <button
                key={c.conversation_id}
                type="button"
                className={`fleet-work-conv${selectedId === c.conversation_id ? " fleet-work-conv--active" : ""}${unread ? " fleet-work-conv--unread" : ""}`}
                onClick={() => setSelectedId(c.conversation_id)}
              >
                <div className="fleet-work-conv-top">
                  <span className="fleet-work-conv-title">
                    {unread && <span className="fleet-work-conv-dot" aria-label="new" />}
                    {who}
                  </span>
                  {c.last_activity_at && <span className="fleet-work-conv-time">{timeAgo(c.last_activity_at)}</span>}
                </div>
                <div className="fleet-work-conv-preview">
                  <span className="fleet-work-conv-channel">{c.channel_label}</span>
                  <span className="fleet-work-conv-who">{who}</span>
                  <span className="fleet-work-conv-agent">{agentLabel(c.agent_id)}</span>
                  {stripMarkdownPreview(c.last_message_preview)}
                </div>
              </button>
            );
          })}
        </div>
        <div className="fleet-work-transcript-pane">
          {selected && (
            <div className="fleet-work-transcript-header">
              <span className="fleet-work-transcript-header-title">{selectedWho || "Untitled conversation"}</span>
              <span className="fleet-work-transcript-header-meta">
                {[selectedAgentLabel, selectedChannel, selected.last_activity_at ? `last active ${timeAgo(selected.last_activity_at)}` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>
          )}
          {detailLoading && selectedTurns.length === 0 ? (
            <FleetChatSkeleton label="Loading conversation" />
          ) : (
          <div className="fleet-work-transcript">
            {detailError && selectedTurns.length === 0 ? (
              <div className="fleet-page-state-body">{detailError}</div>
            ) : selectedTurns.length === 0 ? (
              <div className="fleet-page-state-body">Select a conversation to read it.</div>
            ) : (
              selectedTurns.map((m, i) => (
                <div key={i} className={`fleet-work-msg fleet-work-msg--${isAgentSide(m.role || "") ? "agent" : "user"}`}>
                  <div className="fleet-work-msg-role">
                    {isAgentSide(m.role || "") ? selectedAgentLabel : selectedWho || "Them"}
                  </div>
                  <div className="fleet-work-msg-body">
                    <MarkdownLiteText text={m.content || ""} />
                  </div>
                </div>
              ))
            )}
          </div>
          )}
        </div>
      </div>
    </main>
  );
}
