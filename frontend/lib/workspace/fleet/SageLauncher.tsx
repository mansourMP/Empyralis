"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";

import { PRIMARY_THREAD_ID } from "@/lib/workspace/workstation-chat-pane-model";
import { AgentChat } from "./AgentChat";
import { useFleetAgents } from "./fleet-data";
import { findSageAgent } from "./fleet-presentation";

// Fleet-management tools are live end-to-end — the console reflects real capability.
const STARTER_PROMPTS = [
  "Create a support agent for my store",
  "What agents do I have?",
  "Search the web for recent AI news",
];

/**
 * "Ask AI" console launcher — a rail row rendered at the bottom of
 * PrimaryRail's nav (the fleet-rail-utility group in PrimaryRail.tsx),
 * directly above the Shortcuts row. Opens a docked panel (not a full page)
 * holding one thing: the chat with the operator agent (reuses AgentChat).
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

  // Close on Escape only — no outside-click handler by design (see docstring).
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Don't render anything if this workspace has no Sage install (same guard the
  // rail used to do).
  if (!sageAgent) return null;

  return (
    <div ref={ref} className={`fleet-sage-launcher${open ? " is-open" : ""}`}>
      {open && (
        <div className="fleet-sage-console" role="dialog" aria-label="Ask AI">
          <div className="fleet-sage-console-header">
            {/* A plain title, not a tab strip — with Connect gone (MAN-93)
                there is only one section here, and a lone "Chat" tab would
                read as a control that does nothing. */}
            <div className="fleet-sage-console-title">Ask AI</div>
            <button
              type="button"
              className="fleet-sage-console-close"
              onClick={onClose}
              aria-label="Close Ask AI"
            >
              <X size={16} strokeWidth={1.75} />
            </button>
          </div>

          <div className="fleet-sage-console-body">
            <AgentChat
              workspaceId={workspaceId}
              threadId={PRIMARY_THREAD_ID}
              emptyIcon={Sparkles}
              emptyTitle="Ask AI anything"
              emptyBody="Ask AI to create an agent, configure your fleet, search the web, or check your paired hardware."
              starterPrompts={STARTER_PROMPTS}
              placeholder="Message…"
              sourceTag="fleet_sage_chat"
              liveSyncUrl={`/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`}
            />
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
