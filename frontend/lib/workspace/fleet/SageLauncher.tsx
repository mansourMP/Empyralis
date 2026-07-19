"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquare, Plug, Sparkles, X } from "lucide-react";

import { PRIMARY_THREAD_ID } from "@/lib/workspace/workstation-chat-pane-model";
import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";
import { AgentChat } from "./AgentChat";
import { useFleetAgents } from "./fleet-data";
import { findSageAgent } from "./fleet-presentation";
import { PersonalChannelConnectPanel } from "./PersonalChannelConnectPanel";
import type { PersonalChannelKey } from "./personal-channel-pairing";

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
  { id: "telegram", label: "Telegram", platform: "macos", description: "Your personal Telegram account. Messages are routed to your AI." },
  { id: "whatsapp", label: "WhatsApp", platform: "macos", description: "Your personal WhatsApp account. Messages are routed to your AI." },
  { id: "signal", label: "Signal", platform: "macos", description: "Your personal Signal account via the Gateway." },
  { id: "imessage", label: "iMessage", platform: "macos", description: "Personal iMessage via a Mac running the Gateway." },
  { id: "wechat", label: "WeChat", platform: "macos", description: "Your personal WeChat account via the Gateway." },
];

// Only these two have a real setup/status/disconnect backend today — the
// rest have no in-app pairing flow at all, see NOT_YET_SUPPORTED below.
const REAL_PAIRING_CHANNEL_KEYS: Record<string, PersonalChannelKey> = {
  telegram: "telegram_personal",
  whatsapp: "whatsapp_personal",
};

// These channels have no web-UI pairing flow -- either the bridge has to be
// run on the user's own machine first (Signal, iMessage), or no working
// bridge exists yet at all (WeChat, which has no official personal-account
// API to build one against). Say so plainly instead of falling through to
// the Gateway hardware-pairing panel, which has nothing to do with these
// accounts and would look like a working "connect" flow when it isn't one.
const NOT_YET_SUPPORTED_CHANNELS: Record<string, string> = {
  signal: "Signal requires a signal-cli bridge already running on your own computer — there's no in-app setup for this yet. If you run signal-cli, point your Gateway at it with the EMPYRALIS_SIGNAL_BRIDGE environment variables.",
  imessage: "iMessage requires a Mac running BlueBubbles Server — there's no in-app setup for this yet. Point your Gateway at it with the EMPYRALIS_BLUEBUBBLES_SERVER_URL and EMPYRALIS_BLUEBUBBLES_PASSWORD environment variables.",
  wechat: "Personal WeChat has no official API to build a bridge against, so this isn't supported yet.",
};

/**
 * "Ask AI" console launcher — a rail row rendered at the bottom of
 * PrimaryRail's nav (the fleet-rail-utility group in PrimaryRail.tsx),
 * directly above the Shortcuts row. Opens a docked panel (not a full page)
 * with two sections: Chat (reuses AgentChat) and Connect (personal channel
 * pairing via GatewayPairPanel).
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
    <div ref={ref} className={`fleet-sage-launcher${open ? " is-open" : ""}`}>
      {open && (
        <div className="fleet-sage-console" role="dialog" aria-label="Ask AI">
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
              aria-label="Close Ask AI"
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
                emptyTitle="Ask AI anything"
                emptyBody="Ask AI to create an agent, configure your fleet, search the web, or check your paired hardware."
                starterPrompts={STARTER_PROMPTS}
                placeholder="Message…"
                sourceTag="fleet_sage_chat"
                liveSyncUrl={`/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`}
              />
            )}

            {tab === "connect" && (
              <div className="fleet-sage-connect">
                <div className="fleet-sage-connect-heading">Connect your accounts</div>
                <p className="fleet-sage-connect-desc">
                  Personal channels route through the Gateway and always answer as your AI.
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
                    {selectedChannel && REAL_PAIRING_CHANNEL_KEYS[selectedChannel.id] ? (
                      <PersonalChannelConnectPanel
                        workspaceId={workspaceId}
                        channelKey={REAL_PAIRING_CHANNEL_KEYS[selectedChannel.id]}
                        label={selectedChannel.label}
                      />
                    ) : selectedChannel && NOT_YET_SUPPORTED_CHANNELS[selectedChannel.id] ? (
                      <div className="fleet-sage-connect-unsupported">
                        {NOT_YET_SUPPORTED_CHANNELS[selectedChannel.id]}
                      </div>
                    ) : (
                      <GatewayPairPanel
                        workspaceId={workspaceId}
                        compact
                        defaultPlatform={selectedChannel?.platform}
                      />
                    )}
                  </div>
                )}
              </div>
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
