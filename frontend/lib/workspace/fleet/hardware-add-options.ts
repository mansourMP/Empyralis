/**
 * WAYS TO ADD A COMPUTER — the one rule deciding what the Hardware page's
 * top row offers and which of those offers can actually be completed today.
 *
 * Pure data + pure functions in its own module for the same reason
 * channel-doors.ts, agent-count-shape.ts and channel-hardware-tier.ts are:
 * hardware-add-options.test.ts imports THE REAL RULE and runs under plain
 * `tsx`, outside Next.js/webpack, so nothing here may import React, CSS or
 * a component.
 *
 * ── What this replaced, and why ──────────────────────────────────────────
 * The page used to open with a paragraph of prose and three stat tiles
 * ("1 Channel connected · 0 Connectors connected · 2/2 Computers online").
 * Two of those three are not properties of hardware at all, and the
 * paragraph lectured where a professional tool labels. The founder rejected
 * the whole header outright. What belongs at the top of a page whose job is
 * "connect a computer" is THE WAYS TO CONNECT ONE — nothing else.
 *
 * ── AVAILABILITY IS DERIVED, NEVER A HARDCODED "these are the ones that
 *    work today" LIST ─────────────────────────────────────────────────────
 * `GET /api/hardware/vps/provider-availability` already answers this from
 * the backend's own env-var presence check
 * (vps_provisioning_service.provider_availability), and the cloud-VPS setup
 * MODAL already reads it. The list page did not — so a provider that could
 * only fail after the click looked identical to one that works, and the
 * customer discovered the difference one screen too late. This module is
 * where that same fact now reaches the page BEFORE the click.
 *
 * FAILS OPEN, deliberately: `null`/absent availability (the fetch has not
 * landed, or failed) renders every provider as available. A failed
 * availability check must never be the reason a working provider looks
 * broken — the same posture, for the same reason, as the modal's own
 * `providerAvailability?.[id] !== false`.
 *
 * ── AN UNAVAILABLE OPTION IS NOT A DISABLED BUTTON ───────────────────────
 * The founder's instruction for AWS specifically: "visible, clearly not
 * available yet, with a real reason — and NEVER as a broken button or an
 * error." So `state: "unavailable"` carries a real reason and the caller
 * renders it as an inert card, not a `<button disabled>`. That is the same
 * resolution channel-doors.ts reached for a door that cannot be walked
 * through, and it satisfies "no dead controls" without hiding a provider
 * the founder has said he intends to configure.
 */

/** One way to add a computer. */
export type HardwareAddOptionKind = "cloud" | "ssh" | "device";

/** Two states only. "available" means clicking it starts a flow that can
 *  actually be completed; "unavailable" means it cannot be, and says why. */
export type HardwareAddOptionState = "available" | "unavailable";

export interface HardwareAddOption {
  key: string;
  kind: HardwareAddOptionKind;
  label: string;
  /** One short line. Never a sentence about mechanism, never a paragraph. */
  detail: string;
  state: HardwareAddOptionState;
  /** Present only when state === "unavailable" — the real reason, in
   *  customer language, never an error code and never a stack of prose. */
  unavailableReason: string | null;
}

/** A cloud provider as the page already knows it, passed IN rather than
 *  imported: CLOUD_VPS_PROVIDERS lives beside a React component that
 *  imports CSS, which this module (and its `tsx`-run test) cannot load.
 *  The caller maps the real catalog onto this shape, so the ids and labels
 *  still come from one place. */
export interface CloudProviderInput {
  id: string;
  label: string;
  detail: string;
}

export interface HardwareAddOptionsInput {
  /** In the order the page should offer them. */
  cloudProviders: readonly CloudProviderInput[];
  /** providerId -> can a customer complete this flow today. `null` (never
   *  fetched / fetch failed) and a missing key both mean "assume yes". */
  providerAvailability: Partial<Record<string, boolean>> | null;
  /** True when this customer already has a working connection for that
   *  provider. An existing connection proves the operator side worked at
   *  least once, so it is never blocked by an availability miss — same
   *  carve-out the setup modal already makes. */
  connectedProviderIds?: readonly string[];
}

/** The one sentence a customer reads when a cloud provider's own setup
 *  cannot be completed on this deployment. Deliberately says nothing about
 *  env vars, credentials or operators — it names the fact and who has to
 *  act, and nothing else. */
export function cloudProviderUnavailableReason(label: string): string {
  // The card's own badge already says "Not available yet", so this must not
  // repeat it — measured live, the repeated version ran to three lines and
  // stretched every sibling card in the row to match.
  return `Nothing for you to do — ${label} will appear here when it's ready.`;
}

export function planHardwareAddOptions(input: HardwareAddOptionsInput): HardwareAddOption[] {
  const connected = new Set(input.connectedProviderIds ?? []);
  const options: HardwareAddOption[] = input.cloudProviders.map((provider) => {
    // Unknown reads as available — see this module's own doc comment on
    // failing open. An existing connection outranks the check entirely.
    const available = connected.has(provider.id) || input.providerAvailability?.[provider.id] !== false;
    return {
      key: provider.id,
      kind: "cloud" as const,
      label: provider.label,
      detail: provider.detail,
      state: available ? ("available" as const) : ("unavailable" as const),
      unavailableReason: available ? null : cloudProviderUnavailableReason(provider.label),
    };
  });

  // SSH and your own computer have no operator dependency at all — there is
  // nothing on Empyralis's side that can be unconfigured for either, so
  // neither takes an availability input. Do not add one speculatively.
  options.push({
    key: "ssh",
    kind: "ssh",
    label: "A server you already have",
    detail: "Connect over SSH",
    state: "available",
    unavailableReason: null,
  });
  options.push({
    key: "device",
    kind: "device",
    label: "This computer",
    detail: "Your own Mac or Linux machine",
    state: "available",
    unavailableReason: null,
  });
  return options;
}

/** True when at least one option can actually be used. The page has no
 *  honest header to render if every single way in is closed — which cannot
 *  happen today (SSH and this-computer are unconditional) but is asserted
 *  rather than assumed, so a future input that closes everything is caught
 *  by a test instead of shipping an empty row. */
export function hasUsableAddOption(options: readonly HardwareAddOption[]): boolean {
  return options.some((option) => option.state === "available");
}
