"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Brain, History, Sparkles, SquarePen, X } from "lucide-react";

import { AgentChat } from "./AgentChat";
import { useFleetAgents } from "./fleet-data";
import { findSageAgent } from "./fleet-presentation";
import {
  SageHistoryPanel,
  SageMemoryPanel,
  newSageThreadId,
  useSageConversations,
  useSageMemoryFiles,
} from "./SageConsolePanels";

// Fleet-management tools are live end-to-end — the console reflects real capability.
const STARTER_PROMPTS = [
  "Create a support agent for my store",
  "What agents do I have?",
  "Search the web for recent AI news",
];

type ConsoleView = "chat" | "history" | "memory";

const VIEW_TITLES: Record<ConsoleView, string> = {
  chat: "Ask AI",
  history: "History",
  memory: "Memory",
};

/**
 * "Ask AI" console launcher — a rail row rendered at the bottom of
 * PrimaryRail's nav (the fleet-rail-utility group in PrimaryRail.tsx),
 * directly above the Shortcuts row. Opens a docked panel (not a full page)
 * holding the chat with the operator agent (reuses AgentChat), plus two
 * sibling views reached from the header: your past conversations with that
 * agent, and what it remembers. See SageConsolePanels.tsx — neither is a
 * new endpoint and neither is a new route.
 *
 * This console used to carry a second "Connect" tab for pairing personal
 * channels (Telegram/WhatsApp/Signal/iMessage/WeChat). Removed (MAN-93):
 * Ask AI *is* the agent, so offering to "connect" something on top of it
 * misread the surface — you ask it, you don't wire it up. The pairing UI
 * itself is unchanged and still reached where it belongs, from an agent's
 * own Channels tab (PersonalChannelConnectPanel / IMessageSetupPanel in
 * FleetAgentDetail) and from the Hardware page (GatewayPairPanel).
 *
 * Was previously a floating pill fixed to the bottom-right corner; moved
 * into the rail because on mobile that floating pair (this + Help) sat
 * directly on top of the chat composer's Send button, making Send
 * unreachable. Only the user-facing button label changed ("Ask Sage" →
 * "Ask AI") — component/route/class names still say Sage, the operator
 * agent's actual name, which is unrelated, underlying machinery.
 *
 * Unlike the Help popover, this panel does NOT close on outside click — a
 * console you're using while also looking at the page behind it should stay
 * open until you explicitly close it (X button, clicking the launcher again,
 * or Escape). This is a deliberate difference, not an oversight.
 *
 * WHICH THREAD THE CHAT IS ON
 * ---------------------------
 * It used to be the constant `sage-main` (PRIMARY_THREAD_ID) — one id, for
 * every member of every workspace. Two consequences, both addressed here by
 * giving each conversation its own id: there could only ever be ONE Ask AI
 * conversation, so there was no history to show; and because a thread row is
 * keyed by (tenant, workspace, thread_id), a second member's turns were
 * appended to the FIRST member's `sage-main` row — readable by that first
 * member, and 404 (blank console) for the second, whose own words had landed
 * in someone else's transcript. New conversations now get a unique id, so no
 * two people share a row. An existing `sage-main` conversation still opens
 * normally for whoever owns it: it is simply the newest entry in their own
 * history.
 */
export function SageLauncher({
  workspaceId,
  open,
  onOpen,
  onClose,
}: {
  workspaceId: string;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const { agents } = useFleetAgents(workspaceId);
  const sageAgent = findSageAgent(agents);
  const ref = useRef<HTMLDivElement | null>(null);

  const [view, setView] = useState<ConsoleView>("chat");
  const [threadId, setThreadId] = useState<string | null>(null);

  const { conversations, loaded, refresh } = useSageConversations(workspaceId, open);
  const memoryFiles = useSageMemoryFiles(workspaceId, sageAgent?.agent_id ?? null, open);

  // Resume where this person left off: the newest conversation they own, or a
  // fresh one if they have none. Only fires while no conversation is chosen,
  // so reopening the console keeps whichever one was on screen.
  useEffect(() => {
    if (!open || !loaded || threadId) return;
    setThreadId(conversations[0]?.id ?? newSageThreadId());
  }, [open, loaded, threadId, conversations]);

  // Close on Escape only — no outside-click handler by design (see docstring).
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const openConversation = useCallback((id: string) => {
    setThreadId(id);
    setView("chat");
  }, []);

  const startNewConversation = useCallback(() => {
    setThreadId(newSageThreadId());
    setView("chat");
  }, []);

  const toggleView = useCallback((next: ConsoleView) => {
    setView((cur) => (cur === next ? "chat" : next));
  }, []);

  // No dead controls: History only when there is another conversation to go
  // back to, New chat only once the open one has actually been used (its
  // thread row exists the moment the first turn is recorded), Memory only
  // when the agent has a memory tree to show.
  const hasOtherConversations = useMemo(
    () => conversations.some((c) => c.id !== threadId),
    [conversations, threadId],
  );
  const currentHasContent = useMemo(
    () => conversations.some((c) => c.id === threadId),
    [conversations, threadId],
  );

  // Don't render anything if this workspace has no Sage install (same guard the
  // rail used to do).
  if (!sageAgent) return null;

  return (
    <div ref={ref} className={`fleet-sage-launcher${open ? " is-open" : ""}`}>
      {open && (
        <div className="fleet-sage-console" role="dialog" aria-label="Ask AI">
          <div className="fleet-sage-console-header">
            <h2 className="fleet-sage-console-title">{VIEW_TITLES[view]}</h2>
            <div className="fleet-sage-console-actions">
              {currentHasContent && (
                <button
                  type="button"
                  className="fleet-sage-console-action"
                  onClick={startNewConversation}
                  aria-label="New chat"
                  title="New chat"
                >
                  <SquarePen size={15} strokeWidth={1.75} />
                </button>
              )}
              {hasOtherConversations && (
                <button
                  type="button"
                  className={`fleet-sage-console-action${view === "history" ? " is-active" : ""}`}
                  onClick={() => toggleView("history")}
                  aria-pressed={view === "history"}
                  aria-label="History"
                  title="History"
                >
                  <History size={15} strokeWidth={1.75} />
                </button>
              )}
              {memoryFiles.length > 0 && (
                <button
                  type="button"
                  className={`fleet-sage-console-action${view === "memory" ? " is-active" : ""}`}
                  onClick={() => toggleView("memory")}
                  aria-pressed={view === "memory"}
                  aria-label="Memory"
                  title="Memory"
                >
                  <Brain size={15} strokeWidth={1.75} />
                </button>
              )}
              <button
                type="button"
                className="fleet-sage-console-close"
                onClick={onClose}
                aria-label="Close Ask AI"
              >
                <X size={16} strokeWidth={1.75} />
              </button>
            </div>
          </div>

          <div className="fleet-sage-console-body">
            {view === "history" && (
              <SageHistoryPanel
                conversations={conversations}
                activeThreadId={threadId}
                onOpen={openConversation}
              />
            )}
            {view === "memory" && (
              <SageMemoryPanel workspaceId={workspaceId} agentId={sageAgent.agent_id} files={memoryFiles} />
            )}
            {view === "chat" && threadId && (
              <AgentChat
                key={threadId}
                workspaceId={workspaceId}
                threadId={threadId}
                emptyIcon={Sparkles}
                emptyTitle="Ask AI anything"
                emptyBody="Ask AI to create an agent, configure your fleet, search the web, or check your paired hardware."
                starterPrompts={STARTER_PROMPTS}
                placeholder="Message…"
                sourceTag="fleet_sage_chat"
                liveSyncUrl={`/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`}
                onTurnComplete={refresh}
              />
            )}
          </div>
        </div>
      )}
      <button
        type="button"
        className={`fleet-rail-item fleet-sage-launcher-btn${open ? " fleet-rail-item--active" : ""}`}
        onClick={() => (open ? onClose() : onOpen())}
        aria-label="Ask AI"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Ask AI"
      >
        <span className="fleet-rail-item-icon">
          <Sparkles size={16} strokeWidth={1.75} />
        </span>
        <span className="fleet-rail-item-label">Ask AI</span>
      </button>
    </div>
  );
}
