/**
 * Capability advertisement for OpenClaw-transported channels.
 *
 * The cloud refuses a `channel.inbound` event for a channel this gateway
 * never advertised (`personal_channels_service._assert_gateway_advertised_
 * personal_channel`). That check is a real gate, so these capabilities are
 * published ONLY when this box is actually configured to run the OpenClaw
 * bridge — i.e. when `EMPYRALIS_BRIDGE_TOKEN` is set and the loopback
 * intake therefore exists. No token, no listener, no advertisement.
 *
 * DRIFT: the channel list below is duplicated in Python
 * (`channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS`). That
 * duplication is deliberate and safe in one direction only: the cloud is
 * the authority. A channel advertised here but absent there is rejected
 * loudly by `assert_personal_gateway_channel` at the first message; a
 * channel present there but not advertised here simply never arrives.
 * Neither drift direction silently half-works.
 */

import { OPENCLAW_CHANNEL_KEY_PREFIX } from "./inbound-payload";

/** OpenClaw plugin ids for the platforms Empyralis transports through it.
 *  Matches CHANNEL-ADOPTION-PLAN.md step 7's target market list. Channels
 *  Empyralis already has a first-party runtime for are deliberately absent
 *  until their cut-over (step 6) actually happens. */
export const OPENCLAW_TRANSPORT_CHANNEL_IDS: readonly string[] = [
  "feishu",
  "line",
  // `qqbot` is OpenClaw's own channel id (dist/message-channel-constants-*.js,
  // and `channels.qqbot` in its config schema). It was `qq` here until
  // 2026-08-08, which broke the lane in BOTH directions and silently: a real
  // QQ message would have arrived as `openclaw_qqbot` (a key the cloud does
  // not know), and every outbound send would have been rejected with
  // "unsupported channel: qq". Renamed everywhere before any credentials
  // exist — see channel_lane_contract_service's comment.
  "qqbot",
  "zalo",
  "msteams",
];

let openclawTransportEnabled = false;

export function setOpenClawTransportEnabled(enabled: boolean): void {
  openclawTransportEnabled = enabled;
}

export function openClawTransportCapabilities(): string[] {
  if (!openclawTransportEnabled) return [];
  // `channel.openclaw.<id>` is one of the four fragment shapes
  // _registration_advertises_personal_channel accepts for the channel_key
  // `openclaw_<id>`; see that function.
  return OPENCLAW_TRANSPORT_CHANNEL_IDS.map((id) => `channel.openclaw.${id}`);
}

/** The channel_keys those capabilities correspond to — exported so a test
 *  can assert the two stay in step without re-deriving the prefix. */
export function openClawTransportChannelKeys(): string[] {
  return OPENCLAW_TRANSPORT_CHANNEL_IDS.map((id) => `${OPENCLAW_CHANNEL_KEY_PREFIX}${id}`);
}
