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
 * DRIFT: this list used to be a five-entry array typed out here, duplicating
 * a five-entry tuple and a five-entry label map on the Python side. Three
 * hand-maintained copies of a set nobody should have been curating — there is
 * no per-channel Empyralis code on this lane at all, both legs do a bare
 * prefix strip/prepend, so every channel OpenClaw carries already works
 * through the identical path.
 *
 * It is now GENERATED. `./generated-openclaw-channels.ts` is emitted in the
 * same pass as `server_modules/openclaw_channel_manifest.json`, from one
 * parse of a pinned OpenClaw install, and its active/superseded split is read
 * out of Python's single ownership resolution rather than re-decided here.
 * `server_modules/tests/test_openclaw_channel_registry.py` fails if the two
 * generated artifacts ever disagree.
 *
 * The cloud remains the authority regardless: a channel advertised here but
 * absent there is rejected loudly by `assert_personal_gateway_channel` at the
 * first message; a channel present there but not advertised here simply never
 * arrives. Neither drift direction silently half-works.
 */

import { GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS } from "./generated-openclaw-channels";
import { OPENCLAW_CHANNEL_KEY_PREFIX } from "./inbound-payload";

/**
 * OpenClaw plugin ids for the channels this transport is the LIVE
 * implementation of.
 *
 * Every channel the pinned OpenClaw carries, MINUS the platforms Empyralis
 * already implements first-party. Advertising an overlapping channel would
 * let a second runtime answer on an account a first-party runtime already
 * owns — two replies to one real person, and no way for them to tell which
 * one spoke. That exclusion is COMPUTED from the first-party catalogs, never
 * listed, so a platform OpenClaw adds later that collides with one of ours is
 * excluded the day it ships (see
 * `channel_lane_contract_service.OPENCLAW_TRANSPORT_OWNERSHIP`).
 *
 * The ids are OpenClaw's own, verbatim, straight out of their registry —
 * which is what makes the `openclaw_<id>` invariant automatic rather than a
 * rule to remember. It was hand-typed exactly once, `qq` instead of their
 * `qqbot`, and that broke the lane in BOTH directions and silently: inbound
 * would have arrived under a channel_key the cloud does not know, outbound
 * was rejected with "unsupported channel: qq".
 */
export const OPENCLAW_TRANSPORT_CHANNEL_IDS: readonly string[] =
  GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS;

if (OPENCLAW_TRANSPORT_CHANNEL_IDS.length === 0) {
  // A silently empty transport advertises nothing, so every OpenClaw inbound
  // is refused by `_assert_gateway_advertised_personal_channel` with no error
  // on this side saying why. Fail at load instead of running as a gateway
  // that carries no channels while looking healthy.
  throw new Error(
    "OpenClaw transport resolved to zero channels. Regenerate with " +
      "`python3 scripts/generate_openclaw_channel_manifest.py`.",
  );
}

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
