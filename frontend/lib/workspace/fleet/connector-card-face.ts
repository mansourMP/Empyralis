/**
 * What ONE word an Apps card face shows, and the same fact restated inside
 * the panel that card opens.
 *
 * The face is `icon + name + one pill` — the shape CLAUDE.md records as
 * settled and twice-corrected for Channels, now shared by Apps. So the pill
 * is the ONLY place the grid may say anything about state, and the panel must
 * agree with it: pressing a card can change what you can DO, never what is
 * true. One function decides both, for the same reason `channelCardPill`
 * derives from `remediationFor` one surface over — a pill and a panel that
 * compute state separately drift, silently, in the direction of the one
 * nobody re-reads.
 *
 * THREE FACTS, NEVER COLLAPSED (CLAUDE.md's own law, applied here):
 *
 * ```
 *   connected            "Ready"        --online-text     ● dot
 *   connected, unwell    "Reconnect"    --offline-text
 *   not connected        "Set up"       --text-secondary
 *   not connectable yet  "Unavailable"  --text-muted      ← a FOURTH fact
 * ```
 *
 * "Unavailable" is not "Set up" and not an error: the deployment has never
 * been given an OAuth client for it, which is somebody else's action, not the
 * customer's. It reads quiet rather than red, and its one-sentence reason
 * lives in the panel — said ONCE, instead of nine times across the grid,
 * which is what the founder objected to.
 */

export type ConnectorFaceState = "connected" | "reconnect" | "setup" | "locked";

export type ConnectorFace = {
  state: ConnectorFaceState;
  /** The one word on the card face, when the right-hand slot is a PILL. */
  pill: string;
  /** The same fact, restated at the top of the panel the card opens. */
  panelState: string;
  /** Quiet the icon + label. Never `:disabled` — the card still opens. */
  muted: boolean;
  /**
   * What sits at the right end of the row.
   *
   * The founder's own sketch is `[N] Notion … [Connect]` — the BUTTON is the
   * affordance, which is why the "Set up" text label under the name is gone.
   * But a button is an ACTION, and two of the four states have no action to
   * offer: a connected app is done, and an app this deployment cannot connect
   * has nothing a customer could press. Those states keep the pill, so the
   * face still answers "what is true" without ever rendering a control that
   * does nothing — the same slot, two different kinds of thing.
   */
  action: "connect" | "reconnect" | "none";
};

/**
 * `connected` and `configured` come straight off `FleetConnector`;
 * `healthStatus` is only meaningful ONCE CONNECTED. The backend's default for
 * a never-connected work_app_connector is the literal string
 * `"not_configured"` regardless of whether the provider is genuinely blocked
 * on operator setup (see connection_catalog_service.py's `status_items()`), so
 * reading health on a not-yet-connected app would paint a warning on every
 * app nobody has connected yet — which is most of them.
 */
export function connectorCardFace(input: {
  connected: boolean;
  configured: boolean;
  healthStatus: string;
}): ConnectorFace {
  if (!input.configured && !input.connected) {
    return { state: "locked", pill: "Unavailable", panelState: "Not available yet", muted: true, action: "none" };
  }
  if (input.connected && input.healthStatus && input.healthStatus !== "healthy") {
    return { state: "reconnect", pill: "Reconnect", panelState: "Needs reconnecting", muted: false, action: "reconnect" };
  }
  if (input.connected) {
    return { state: "connected", pill: "Ready", panelState: "Connected", muted: false, action: "none" };
  }
  return { state: "setup", pill: "Set up", panelState: "Not connected", muted: false, action: "connect" };
}
