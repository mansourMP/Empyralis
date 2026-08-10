/**
 * The channel "doors" model — the ways a single channel can be connected, and
 * the rule that decides whether the customer is asked to choose between them.
 *
 * Split out of FleetAgentDetail.tsx (no React, no CSS import, no "use client")
 * for the same reason openclaw-channel-copy.ts is: channel-doors.test.ts runs
 * under plain `npx tsx`, outside Next.js/webpack, and a module that imports a
 * stylesheet cannot be loaded that way. Before this split, every door string
 * was pinned into openclaw-channel-copy.test.ts BY HAND as a copy-pasted
 * literal — a check whose expected set was transcribed from the thing it
 * checks, which goes stale silently the moment someone edits the component
 * (CLAUDE.md: "a check that derives its own expectations from the thing it
 * checks is blind, and reports 'passed'"). The test now imports the real data.
 *
 * THE DOOR-COUNT RULE — DERIVED, NEVER AUTHORED PER CHANNEL
 * ---------------------------------------------------------
 * Whether a channel shows a picker is a function of how many real doors it
 * has, and of nothing else:
 *
 *     real doors == 1   ─▶  "direct":  the card opens straight into that
 *                                      door's setup. An intermediate screen
 *                                      offering one option is a dead click,
 *                                      and a dead control is a product-law
 *                                      violation (CLAUDE.md).
 *     real doors >= 2   ─▶  "picker":  the card opens the door choice FIRST,
 *                                      because the choice has consequences
 *                                      that differ (see `consequence`).
 *     real doors == 0   ─▶  "none":    nothing to open.
 *
 * Hardcoding "Telegram gets a picker" would rot on contact: as transported
 * paths land, Telegram gains a third door and WhatsApp a second. The count
 * drives the behaviour so that adding a door to this table is the whole
 * change.
 */

/** The consequence of walking through a door, stated ON THE DOOR — before it
 *  is chosen, never in a warning after a QR code has been scanned.
 *
 *  This is the entire reason a picker earns its place. Telegram's two doors
 *  are not two setup procedures, they are two RISK profiles: one runs a bot
 *  that is a separate identity, the other signs in as the owner and puts the
 *  owner's own number in reach of a platform ban. That has already happened
 *  to a real person here. One clear line, stated plainly — a professional
 *  tool labels, it does not lecture (CLAUDE.md craft doctrine), so this is a
 *  fact about what happens, never a paragraph of warnings.
 *
 *  `risk` is only ever set where the consequence is real and specific.
 *  Never invent one to make a door look symmetrical. */
export type ChannelDoorConsequence = {
  tone: "safe" | "risk";
  text: string;
};

export type ChannelDoor = {
  key: string;
  label: string;
  /** What this door IS, in one line. Setup instructions belong in the setup
   *  form the door opens, not here. */
  body: string;
  /** False = not built on this deployment. Such a door is not rendered at all
   *  and does not count toward the picker threshold — a door that cannot be
   *  walked through is not a way to connect. */
  real: boolean;
  /** This door binds to THIS agent's own gateway (a paired computer / VPS): a
   *  "full account" login runs a real client process on that box, which a
   *  cloud-only agent has nowhere to run. Surfaced on the door face BEFORE the
   *  pick, and an unmet requirement makes the door unavailable rather than a
   *  pick that fails once opened. */
  requiresHardware?: boolean;
  consequence?: ChannelDoorConsequence;
};

/** Each channel shows only the connection MODES that are real, safe, and built
 *  today for that platform — never a door that fails, and never a second mode
 *  standing in for one that doesn't exist yet. "Full account" doors bind to
 *  THIS agent's own preferred_gateway_id (see ChannelsTab's `agentGatewayId`),
 *  never to a workspace-wide/Sage-routed session. */
export const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  sage_telegram_hosted: [
    {
      key: "byo_bot",
      label: "Chatbot",
      body: "People message a separate bot you create. This agent never touches your own Telegram account.",
      real: true,
      consequence: {
        tone: "safe",
        text: "Your own account can't be banned. Bots can't read every message in a group.",
      },
    },
    {
      key: "full_account",
      label: "Full account",
      body: "The agent signs in as you — phone, code, and 2FA if enabled — and sees exactly what you see.",
      real: true,
      requiresHardware: true,
      consequence: {
        tone: "risk",
        text: "Telegram can ban your number for automated use.",
      },
    },
  ],
  slack: [
    {
      key: "oauth",
      label: "App",
      body: "Connect a Slack workspace — signed mentions and DMs route to your AI.",
      real: true,
    },
  ],
  discord_bot: [
    {
      key: "byo_bot",
      label: "Bot",
      body: "Give this agent its own Discord bot — paste the token from Discord's developer portal.",
      real: true,
    },
  ],
  whatsapp_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "The agent signs in as you — scan a QR code or use a pairing code — and sees exactly what you see.",
      real: true,
      requiresHardware: true,
      consequence: {
        tone: "risk",
        text: "WhatsApp can ban your number for automated use.",
      },
    },
  ],
  signal_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "This agent's own Signal, via a signal-cli bridge on its computer. Requires a real signal-cli install — no cloud path.",
      real: true,
      requiresHardware: true,
    },
  ],
  imessage_personal: [
    {
      key: "full_account",
      label: "Full account",
      body: "This agent's own iMessage, via imsg — a small CLI that talks to Messages.app on the Mac it runs on.",
      real: true,
      requiresHardware: true,
    },
  ],
  wechat_official: [
    {
      key: "app_credential_pair",
      label: "Official Account / WeCom",
      body: "This agent's own WeChat Official Account or WeChat Work (WeCom) bot — paste the AppID/AppSecret (or CorpID/CorpSecret/AgentId) from your own admin console.",
      real: true,
    },
  ],
};

/** What the panel a channel card opens should do, decided by door COUNT.
 *  `doors` is the real-door list in both non-empty modes, so a caller never
 *  re-filters and can never disagree with the mode. */
export type ChannelDoorPlan =
  | { mode: "none"; doors: ChannelDoor[] }
  | { mode: "direct"; door: ChannelDoor; doors: ChannelDoor[] }
  | { mode: "picker"; doors: ChannelDoor[] };

/** The whole behavioural rule, in one place, driven by the count. */
export function planChannelDoors(channelId: string | null | undefined): ChannelDoorPlan {
  const doors = (channelId ? CHANNEL_DOORS[channelId] || [] : []).filter((door) => door.real);
  if (doors.length === 0) return { mode: "none", doors };
  if (doors.length === 1) return { mode: "direct", door: doors[0], doors };
  return { mode: "picker", doors };
}

/** Whether this agent can walk through this door RIGHT NOW.
 *
 *  `doorConnected` is deliberately an input rather than something computed
 *  here: an already-connected full-account door stays available even with no
 *  gateway currently attached, because the customer must still be able to open
 *  it to see (and disconnect) the session that exists. */
export type ChannelDoorHardwareState = "not-required" | "ready" | "missing";

export function channelDoorHardwareState(
  door: ChannelDoor,
  { hasHardware, doorConnected }: { hasHardware: boolean; doorConnected: boolean },
): ChannelDoorHardwareState {
  if (!door.requiresHardware) return "not-required";
  if (hasHardware || doorConnected) return "ready";
  return "missing";
}

export function isChannelDoorAvailable(
  door: ChannelDoor,
  state: { hasHardware: boolean; doorConnected: boolean },
): boolean {
  return channelDoorHardwareState(door, state) !== "missing";
}

/** The hardware fact, on the door face, before the pick. `not-required` has
 *  nothing to say — a note that says "no computer needed" is noise on six of
 *  the seven channels. */
export function channelDoorHardwareNote(state: ChannelDoorHardwareState): string | null {
  switch (state) {
    case "ready":
      return "Needs a computer — this agent has one";
    case "missing":
      return "Needs a computer — connect one on the Hardware tab";
    default:
      return null;
  }
}

/** What a door the agent cannot currently complete says INSTEAD of a setup
 *  form. Not a caption under a live-looking control: the control is not
 *  rendered at all. */
export function channelDoorUnavailableReason(door: ChannelDoor): string {
  return door.requiresHardware
    ? "This one runs on the agent's own computer, and this agent doesn't have one yet. Connect a computer on the Hardware tab, then come back."
    : "This one can't be set up from here yet.";
}
