"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquare, Plug, Sparkles, X } from "lucide-react";

import { PRIMARY_THREAD_ID } from "@/lib/workspace/workstation-chat-pane-model";
import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";
import { AgentChat } from "./AgentChat";
import { useFleetAgents } from "./fleet-data";
import { findSageAgent } from "./fleet-presentation";

// Fleet-management tools are live end-to-end — the console reflects real capability.
const STARTER_PROMPTS = [
  "Create a support agent for my store",
  "What agents do I have?",
  "Search the web for recent AI news",
];

type ConsoleTab = "chat" | "connect";

/**
 * Personal channel types that route through Sage (the operator), not through
 * any individual specialist agent. These were moved here from per-agent
 * Channels tabs because architecturally, personal-channel replies always
 * answer as Sage regardless of which agent page you configured them from.
 */
const PERSONAL_CHANNELS: { id: string; label: string; platform: string; description: string }[] = [
  { id: "telegram", label: "Telegram", platform: "macos", description: "Your personal Telegram account. Messages are routed to Sage." },
  { id: "whatsapp", label: "WhatsApp", platform: "macos", description: "Your personal WhatsApp account. Messages are routed to Sage." },
  { id: "signal", label: "Signal", platform: "macos", description: "Your personal Signal account via the Gateway." },
  { id: "imessage", label: "iMessage", platform: "macos", description: "Personal iMessage via a Mac running the Gateway." },
];

/**
 * Floating "Ask Sage" corner console — a pill-shaped button fixed at the
 * bottom-right, immediately left of the Help button. Opens a docked panel
 * (not a full page) with two sections: Chat (reuses AgentChat) and Connect
 * (personal channel pairing via GatewayPairPanel).
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
  const [tab, setTab] = useState<ConsoleTab>("chat");
  const [connectChannel, setConnectChannel] = useState<string | null>(null);

  // Close on Escape only — no outside-click handler by design (see docstring).
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Reset state when closed so the next open always starts clean on "chat".
  useEffect(() => {
    if (!open) {
      setTab("chat");
      setConnectChannel(null);
    }
  }, [open]);

  // Don't render anything if this workspace has no Sage install (same guard the
  // rail used to do).
  if (!sageAgent) return null;

  const selectedChannel = PERSONAL_CHANNELS.find((c) => c.id === connectChannel);

  return (
    <div ref={ref} className="fleet-sage-launcher">
      {open && (
        <div className="fleet-sage-console" role="dialog" aria-label="Sage console">
          <div className="fleet-sage-console-header">
            <div className="fleet-sage-console-tabs">
              <button
                type="button"
                className={`fleet-sage-console-tab${tab === "chat" ? " is-active" : ""}`}
                onClick={() => { setTab("chat"); setConnectChannel(null); }}
              >
                <MessageSquare size={13} strokeWidth={1.75} />
                Chat
              </button>
              <button
                type="button"
                className={`fleet-sage-console-tab${tab === "connect" ? " is-active" : ""}`}
                onClick={() => setTab("connect")}
              >
                <Plug size={13} strokeWidth={1.75} />
                Connect
              </button>
            </div>
            <button
              type="button"
              className="fleet-sage-console-close"
              onClick={onClose}
              aria-label="Close Sage"
            >
              <X size={16} strokeWidth={1.75} />
            </button>
          </div>

          <div className="fleet-sage-console-body">
            {tab === "chat" && (
              <AgentChat
                workspaceId={workspaceId}
                threadId={PRIMARY_THREAD_ID}
                emptyIcon={Sparkles}
                emptyTitle="Ask Sage anything"
                emptyBody="Ask Sage to create an agent, configure your fleet, search the web, or check your paired hardware."
                starterPrompts={STARTER_PROMPTS}
                placeholder="Message Sage…"
                sourceTag="fleet_sage_chat"
                liveSyncUrl={`/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`}
              />
            )}

            {tab === "connect" && (
              <div className="fleet-sage-connect">
                <div className="fleet-sage-connect-heading">Connect your accounts</div>
                <p className="fleet-sage-connect-desc">
                  Personal channels route through the Gateway and always answer as Sage.
                  Pair your accounts below.
                </p>

                {!connectChannel ? (
                  <div className="fleet-sage-connect-grid">
                    {PERSONAL_CHANNELS.map((ch) => (
                      <button
                        key={ch.id}
                        type="button"
                        className="fleet-sage-connect-card"
                        onClick={() => setConnectChannel(ch.id)}
                      >
                        <span className="fleet-sage-connect-card-label">{ch.label}</span>
                        <span className="fleet-sage-connect-card-desc">{ch.description}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="fleet-sage-connect-pair">
                    <div className="fleet-sage-connect-back">
                      <button
                        type="button"
                        className="fleet-sage-connect-back-btn"
                        onClick={() => setConnectChannel(null)}
                      >
                        ← Back
                      </button>
                      <span className="fleet-sage-connect-channel-label">
                        {selectedChannel?.label}
                      </span>
                    </div>
                    <p className="fleet-sage-connect-desc">
                      {selectedChannel?.description}
                    </p>
                    <GatewayPairPanel
                      workspaceId={workspaceId}
                      compact
                      defaultPlatform={selectedChannel?.platform}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      <button
        type="button"
        className="fleet-sage-launcher-btn"
        onClick={() => (open ? onClose() : onOpen())}
        aria-label="Ask Sage"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Sparkles size={14} strokeWidth={1.75} />
        <span>Ask Sage</span>
      </button>
    </div>
  );
}
