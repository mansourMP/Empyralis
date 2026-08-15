/**
 * Linking a channel that has no credential to paste — QR and friends.
 *
 * WHICH CHANNELS THESE ARE IS DERIVED, AND FROM THE BOX, NOT FROM A LIST
 * ---------------------------------------------------------------------
 * The generated manifest already says a channel takes no pasted credential
 * (`connect_method: "pairing"` — seven channels do today). What it cannot say
 * is HOW such a channel is linked, because that is a property of the plugin
 * INSTALLED ON THIS BOX, not of the pinned OpenClaw version: the manifest is
 * generated against a clean profile with no installable plugins present, so
 * WhatsApp's own metadata is not visible to it at all.
 *
 * OpenClaw answers it directly, per channel, on the box:
 *
 *     openclaw --profile <p> channels capabilities --channel all --json
 *       -> channels[].plugin.gatewayMethodDescriptors[].name
 *
 * A plugin that declares `web.login.start` AND `web.login.wait` owns OpenClaw's
 * QR-login seam — `resolveWebLoginProvider` finds a provider by exactly that
 * test (dist/web-8NJTyC4p.js). Measured on the live rig, openclaw@2026.6.10:
 *
 *     telegram | gatewayMethodDescriptors: []
 *     whatsapp | gatewayMethodDescriptors: ['web.login.start','web.login.wait']
 *
 * So WhatsApp offers a QR and Telegram does not, and nothing here names either.
 * The day a plugin grows that seam it lights up on its own, and the day one
 * loses it the flow disappears rather than offering a control that cannot work.
 *
 * `--channel all` returns the channels that are ENABLED in the effective
 * config, with full plugin metadata, in ONE call — which is also the only set
 * whose link flow can be started, because OpenClaw only registers a channel
 * plugin's provider once its channel is enabled. Confirmed the hard way: with
 * `channels.whatsapp.enabled: false`, `web.login.start` answers
 * `web login provider is not available`; flipping it to true and restarting is
 * what makes a QR appear. That is why `ensureChannelEnabled` exists below and
 * runs before a link is started.
 */

import type { OpenClawCli } from "./openclaw-cli";

const CAPABILITIES_TIMEOUT_MS = 60_000;

/** OpenClaw's own two QR-login method names. A channel is QR-linkable when its
 *  plugin declares BOTH, which is the same test their own provider resolver
 *  makes. Never a channel-id comparison. */
const QR_LOGIN_METHODS = ["web.login.start", "web.login.wait"] as const;

export interface OpenClawChannelLinkShape {
  /** Every gateway method this channel's plugin declares. Reported whole so a
   *  future seam is visible in a support dump before anything consumes it. */
  readonly gateway_methods: readonly string[];
  /** Derived: this channel can be linked by scanning a code from the browser. */
  readonly supports_qr_login: boolean;
}

export function deriveLinkShape(gatewayMethods: readonly string[]): OpenClawChannelLinkShape {
  const methods = gatewayMethods.map((name) => String(name || "").trim()).filter(Boolean);
  return {
    gateway_methods: methods,
    supports_qr_login: QR_LOGIN_METHODS.every((method) => methods.includes(method)),
  };
}

/** Parse `channels capabilities --channel all --json` into per-channel link
 *  shapes. Tolerant by construction: a channel missing from the payload simply
 *  has no entry, which the caller reports as "no link flow known", never as an
 *  error — a channel that is not enabled yet is the normal case, not a fault. */
export function parseChannelCapabilities(stdout: string): Record<string, OpenClawChannelLinkShape> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return {};
  }
  const channels = (parsed as { channels?: unknown } | null)?.channels;
  if (!Array.isArray(channels)) return {};
  const shapes: Record<string, OpenClawChannelLinkShape> = {};
  for (const entry of channels) {
    const plugin = (entry as { plugin?: unknown } | null)?.plugin as
      | { id?: unknown; gatewayMethodDescriptors?: unknown }
      | undefined;
    const id = String(plugin?.id || "").trim();
    if (!id) continue;
    const descriptors = Array.isArray(plugin?.gatewayMethodDescriptors)
      ? plugin.gatewayMethodDescriptors
      : [];
    shapes[id] = deriveLinkShape(
      descriptors.map((descriptor) => String((descriptor as { name?: unknown })?.name || "")),
    );
  }
  return shapes;
}

export async function readChannelLinkShapes(
  cli: OpenClawCli,
): Promise<Record<string, OpenClawChannelLinkShape>> {
  const result = await cli.run(["channels", "capabilities", "--channel", "all", "--json"], {
    timeoutMs: CAPABILITIES_TIMEOUT_MS,
  });
  if (result.code !== 0) return {};
  return parseChannelCapabilities(result.stdout);
}

/** One link attempt's outcome — three facts, never collapsed.
 *
 *  `linked` / `qr_data_url` / `message` answer three different screens: "it is
 *  connected", "here is a code to scan", and "nothing to scan and not connected
 *  either". Collapsing any two of them is how a link flow ends up showing a
 *  stale square forever, or claiming success it cannot see. */
export interface OpenClawChannelLinkOutcome {
  readonly status: "ok" | "refused";
  readonly refusal: { code: string; detail: string } | null;
  readonly linked: boolean;
  readonly qr_data_url: string | null;
  readonly message: string;
}

export interface ChannelLoginHttpRequest {
  readonly action: "start" | "wait";
  readonly timeoutMs: number;
  readonly force?: boolean;
  readonly accountId?: string;
  readonly currentQrDataUrl?: string;
}

/** Turn OpenClaw's loopback HTTP answer into the outcome above.
 *
 *  Pure, and exported, because it is the whole of the interpretation: a test
 *  can pin every branch without a gateway, an OpenClaw, or a network. */
export function projectLinkResponse(body: unknown): OpenClawChannelLinkOutcome {
  const record = (body ?? {}) as Record<string, unknown>;
  if (record.ok !== true) {
    const error = (record.error ?? {}) as Record<string, unknown>;
    return {
      status: "refused",
      refusal: {
        // Classified by their CODE, never by the sentence. An unknown code is
        // still a code; matching on prose is a documented scar in this repo.
        code: String(error.code || "openclaw_channel_link_failed"),
        detail: String(error.message || "OpenClaw did not answer the link request."),
      },
      linked: false,
      qr_data_url: null,
      message: "",
    };
  }
  const result = (record.result ?? {}) as Record<string, unknown>;
  const qr = typeof result.qr_data_url === "string" ? result.qr_data_url : "";
  return {
    status: "ok",
    refusal: null,
    linked: result.connected === true,
    qr_data_url: qr.startsWith("data:image/") ? qr : null,
    message: typeof result.message === "string" ? result.message : "",
  };
}
