"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Plus, Search } from "lucide-react";

import { type FleetAgent } from "./fleet-data";
import { deriveStatus } from "./fleet-presentation";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { planConversationList, conversationPreview, conversationTimestamp } from "./agents-conversation-list";
import { rememberLastViewedAgent } from "./AgentsList";

/**
 * The workspace-level Agents list pane — see agents-conversation-list.ts's
 * header for the full "why" (the rail-morph the founder rejected, and why
 * this lives in the content area instead). Rendered only by
 * app/(account)/w/[workspaceId]/agents/layout.tsx, and only at 2+ real
 * agents (a list of one is worse than no list — the same call this
 * codebase already makes everywhere else a count decides a shape).
 *
 * Deliberately narrow: name, a one-line activity preview, a relative
 * timestamp, a live status dot. No brain/placement/channel/cost columns —
 * those belong to the fleet-management table this replaces (AgentsList /
 * AgentsBoard / AgentsGroupedList, still real components, no longer wired
 * to this route — see this repo's own notes on why), which was answering
 * "how is my fleet running", a different question from "which conversation
 * am I picking up." No board/grouped/sort-by picker either, for the same
 * reason Telegram's and claude.ai's own chat lists don't have one: recency
 * is the one ordering a conversation list needs.
 */
export function AgentConversationList({
  workspaceId,
  agents,
  activeAgentId,
}: {
  workspaceId: string;
  agents: FleetAgent[];
  activeAgentId: string | null;
}) {
  const [query, setQuery] = useState("");
  const rows = useMemo(() => planConversationList(agents, query), [agents, query]);
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  return (
    <nav className="fleet-agents-conversation-list" aria-label="Agents">
      <div className="fleet-agents-conversation-list-header">
        <div className="fleet-agents-conversation-search">
          <Search size={13} strokeWidth={1.75} aria-hidden="true" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search agents"
            aria-label="Search agents"
          />
        </div>
        {/* Real link, not a button that creates straight from here — reuses
            the bare index's own zero-decision quick-create (agents/page.tsx,
            the same ?new=1 hand-off "+ New project" already uses), so there
            is exactly one create path rather than a second one grown here.
            Works from any agent's own chat page too: it navigates to the
            index first, where the create effect actually runs. */}
        <Link href={`${base}/agents?new=1`} className="fleet-icon-btn" aria-label="New agent" title="New agent">
          <Plus size={15} strokeWidth={2} aria-hidden="true" />
        </Link>
      </div>
      <div className="fleet-agents-conversation-list-rows">
        {rows.length === 0 ? (
          <div className="fleet-agents-conversation-list-empty">No agents match “{query}”.</div>
        ) : (
          rows.map((agent) => {
            // The workspace's OWN routed agent page (agents/[agentId]/[tab]),
            // never the project-scoped .../projects/{pid}/agents/{id}/chat
            // URL — that route lives outside this layout's own subtree, so
            // navigating there would unmount this very list, defeating the
            // one property (list stays put) this whole redesign exists for.
            const href = `${base}/agents/${encodeURIComponent(agent.agent_id)}/chat`;
            const status = deriveStatus(agent.hardware_status || "unknown", Boolean(agent.stopped?.active), Boolean(agent.current_run_id));
            const active = agent.agent_id === activeAgentId;
            const timestamp = conversationTimestamp(agent.last_activity);
            return (
              <Link
                key={agent.agent_id}
                href={href}
                className={`fleet-conversation-row${active ? " fleet-conversation-row--active" : ""}`}
                aria-current={active ? "page" : undefined}
                onClick={() => rememberLastViewedAgent(agent.agent_id)}
              >
                <span className="fleet-conversation-row-avatar">
                  <AgentSigil seed={agent.agent_id} size={32} />
                  <StatusDot tone={status.tone} size={9} />
                </span>
                <span className="fleet-conversation-row-text">
                  <span className="fleet-conversation-row-line1">
                    <span className="fleet-conversation-row-name">{agent.label || "Unnamed agent"}</span>
                    {timestamp && <span className="fleet-conversation-row-time">{timestamp}</span>}
                  </span>
                  <span className="fleet-conversation-row-preview">{conversationPreview(agent)}</span>
                </span>
              </Link>
            );
          })
        )}
      </div>
    </nav>
  );
}
