"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Inbox as InboxIcon, AlertCircle } from "lucide-react";

import type { FleetAgent } from "../fleet-data";
import { timeAgo } from "../fleet-presentation";

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

type TurnActor = {
  type?: string;
  id?: string;
  display_name?: string;
};

type Turn = {
  role?: string;
  content?: string;
  created_at?: string;
  // Present on every turn the backend returns (agent_turn.py's TurnActor,
  // serialized) but never read here before now — the customer/sender
  // identity behind a conversation was already on the wire, just unused.
  actor?: TurnActor;
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

function isCustomerRole(role: string): boolean {
  const r = role.toLowerCase();
  return r !== "assistant" && r !== "agent" && r !== "bot";
}

// Who this conversation was with. There is no thread-level "customer" field
// in this API (agent_threads has no such column) — the identity lives per
// turn, in the human side's actor.display_name. Cost has no equivalent
// anywhere in this pipeline (only a page-level aggregate exists, on a
// different endpoint) so it is deliberately not surfaced here.
function conversationWho(t: Thread): string {
  for (const turn of t.turns || []) {
    if (!isCustomerRole(turn.role || "")) continue;
    const name = (turn.actor?.display_name || "").trim();
    if (name) return name;
  }
  return "";
}

// Never a raw id — falls back to the customer's name, then an honest
// placeholder. t.id used to leak here whenever the backend hadn't set a
// title yet.
function conversationTitle(t: Thread, who: string): string {
  const title = (t.title || "").trim();
  if (title) return title;
  if (who) return who;
  return "Untitled conversation";
}

// Plain-text preview snippet (list row) — strip markdown syntax rather than
// render it, since a one-line truncated preview has no room for real
// formatting; this just keeps the literal punctuation out of it.
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

// ── Markdown-lite ────────────────────────────────────────────────────────
// Chat turns are plain strings that may contain **bold**, *italic*/_italic_,
// `code`, [links](url), and simple lists — enough that agent replies with
// real formatting don't show up as literal asterisks. Not a full markdown
// document renderer (no new dependency for what's still a chat bubble, not
// a doc viewer): headings/tables/blockquotes are deliberately out of scope.

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_/g;
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<code key={`${keyPrefix}-${i++}`} className="fleet-md-code">{m[1]}</code>);
    } else if (m[2] !== undefined) {
      nodes.push(
        <a key={`${keyPrefix}-${i++}`} href={m[3]} target="_blank" rel="noreferrer" className="fleet-link">
          {m[2]}
        </a>,
      );
    } else if (m[4] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}-${i++}`}>{m[4]}</strong>);
    } else if (m[5] !== undefined) {
      nodes.push(<em key={`${keyPrefix}-${i++}`}>{m[5]}</em>);
    } else if (m[6] !== undefined) {
      nodes.push(<em key={`${keyPrefix}-${i++}`}>{m[6]}</em>);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

type Block = { type: "p" | "ul" | "ol"; text?: string; items?: string[] };

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let listType: "ul" | "ol" | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ type: "p", text: para.join("\n") });
    para = [];
  };
  const flushList = () => {
    if (listType && list.length) blocks.push({ type: listType, items: list });
    list = [];
    listType = null;
  };

  for (const line of lines) {
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (bullet) {
      flushPara();
      if (listType !== "ul") { flushList(); listType = "ul"; }
      list.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (listType !== "ol") { flushList(); listType = "ol"; }
      list.push(numbered[1]);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

function MessageBody({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return null;
  return (
    <>
      {blocks.map((b, bi) => {
        if (b.type === "ul" || b.type === "ol") {
          const ListTag = b.type;
          return (
            <ListTag key={bi} className="fleet-md-list">
              {(b.items || []).map((item, ii) => (
                <li key={ii}>{renderInline(item, `${bi}-${ii}`)}</li>
              ))}
            </ListTag>
          );
        }
        return <p key={bi} className="fleet-md-p">{renderInline(b.text || "", `${bi}`)}</p>;
      })}
    </>
  );
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
  const agentName = agent?.label || "This agent";

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
          When {agentName} handles end-customer conversations, they’ll show up here — every channel, in
          one place.
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
  const selectedWho = selectedThread ? conversationWho(selectedThread) : "";
  const startedAt = selectedTurns[0]?.created_at;

  return (
    <div className="fleet-work-split">
      <div className="fleet-work-list">
        {unreadCount > 0 && (
          <div className="fleet-work-list-live" aria-live="polite">
            <span className="fleet-work-conv-dot" /> {unreadCount} new
          </div>
        )}
        {threads.map((t) => {
          const who = conversationWho(t);
          const unread = isUnread(t);
          const preview = lastTurn(t)?.content || "—";
          const when = t.last_turn_at || t.updated_at || "";
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
                  {conversationTitle(t, who)}
                </span>
                {when && <span className="fleet-work-conv-time">{timeAgo(when)}</span>}
              </div>
              <div className="fleet-work-conv-preview">
                {t.channel && <span className="fleet-work-conv-channel">{t.channel}</span>}
                {who && <span className="fleet-work-conv-who">{who}</span>}
                <span className="fleet-work-conv-agent">{agentName}</span>
                {stripMarkdownPreview(preview)}
              </div>
            </button>
          );
        })}
      </div>
      <div className="fleet-work-transcript-pane">
        {selectedThread && (
          <div className="fleet-work-transcript-header">
            <span className="fleet-work-transcript-header-title">
              {conversationTitle(selectedThread, selectedWho)}
            </span>
            <span className="fleet-work-transcript-header-meta">
              {[selectedWho || "Unknown caller", selectedThread.channel, startedAt ? `started ${timeAgo(startedAt)}` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
        )}
        <div className="fleet-work-transcript">
          {selectedTurns.length === 0 ? (
            <div className="fleet-page-state-body">Select a conversation to read it.</div>
          ) : (
            selectedTurns.map((m, i) => (
              <div
                key={i}
                className={`fleet-work-msg fleet-work-msg--${isAgentSide(m.role || "") ? "agent" : "user"}`}
              >
                <div className="fleet-work-msg-role">
                  {isAgentSide(m.role || "") ? agentName : selectedWho || "Customer"}
                </div>
                <div className="fleet-work-msg-body">
                  <MessageBody text={m.content || ""} />
                </div>
                {m.created_at && <div className="fleet-work-msg-time">{timeAgo(m.created_at)}</div>}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
