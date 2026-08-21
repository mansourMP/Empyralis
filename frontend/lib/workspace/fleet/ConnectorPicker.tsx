"use client";

/**
 * APPS — a card grid whose face carries the app, and nothing else.
 *
 * The founder, looking at the live surface: *"why do we have those written
 * text right there? If I press connect button it goes to connect. Once I
 * press on the application it opens the card kind of thing, then it shows
 * different things like what kind of tool, what it is and some other things,
 * and same connect button."*
 *
 * ```
 * BEFORE  ×69                            AFTER  ×69
 *  ┌──────────────────────────────┐       ┌────────┐ ┌────────┐
 *  │ [N] Notion                   │       │ [N]    │ │ [L]    │  icon
 *  │     Connect Notion for page, │       │ Notion │ │ Linear │  + name
 *  │     database, and approved…  │       │ Set up │ │ Ready  │  + 1 pill
 *  │     [ Connect ]  ← hairline  │       └────────┘ └────────┘
 *  └──────────────────────────────┘            │
 *   prose nobody reads, on every card          └─ press ─▶ the panel:
 *   69 accent-ish buttons in one view             what it is · what tools it
 *                                                 brings · ONE accent-FILLED
 *                                                 Connect
 * ```
 *
 * THE CARD ACTION IS FULL `fleet-btn--accent-fill`, BY THE FOUNDER'S OWN
 * INSTRUCTION, GIVEN TWICE. His words: "it's going to be FULL purple just
 * like this next button, not like only around it and slightly purple" — the
 * hairline `fleet-btn--accent` was named, explicitly, as the wrong one.
 *
 * An earlier pass shipped the hairline anyway, reasoning that a filled
 * button on every one of ~69 faces is a wall of purple. That reasoning is
 * not wrong on its own terms, but it was not the call to make: he had
 * already been told the trade and had already chosen. Do not "fix" this
 * back to the hairline variant without him saying so — the visual argument
 * has been made and rejected.
 *
 * THE FACE IS THE CHANNELS SHAPE, NOT A COPY OF IT. `.fleet-connector-grid` /
 * `.fleet-connector-card` / `-icon` / `-label` / `-pill` are declared in
 * fleet-theme.css in the SAME rule blocks as their `.fleet-channel-*`
 * siblings and were already sitting there unused by this surface; the panel
 * is the shared `.fleet-channel-banner` inside `.fleet-detail-backdrop`, the
 * same shell a channel card opens. See connector-cards.css for the three
 * things that are genuinely new.
 *
 * STATE STAYS HONEST. Clean must not become quiet-about-a-lie: connected /
 * not connected / not-connectable-yet are three different facts and each
 * keeps its own word on the face (connector-card-face.ts), which the panel
 * then restates rather than recomputes.
 */

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useMemo, useState } from "react";
import { Loader2, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { CONNECTOR_ICONS } from "./fleet-icons";
import { connectorCardFace } from "./connector-card-face";
import { FleetCardGridSkeleton } from "./fleet-states";
import {
  useFleetAgentConnectors,
  useFleetAgents,
  useFleetAgentTools,
  useFleetProjectConnectors,
  type FleetConnector,
} from "./fleet-data";
import "./connector-cards.css";

function fieldLabel(field: string): string {
  return field.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

// Deliberate fallback for a connector with no mapped logo (CONNECTOR_ICONS
// in fleet-icons.ts should cover the full catalog, but this is the
// deployment-agnostic safety net for whatever gets added to the backend
// catalog before an icon is wired for it — see docs/PLATFORM-MAP.md's
// fleet-icons.ts entry). Renders into the SAME icon box a real <img> logo
// would (same size/shape, per MAN-145 founder feedback: a bare letter on the
// muted default background read as broken, not as a deliberate placeholder)
// but gives every connector its own deterministic color so the grid doesn't
// look uniformly grey/unstyled. Same small palette + hashing approach as the
// AgentSigil convention used elsewhere in fleet (deterministic per-id color,
// not random per-render).
const MONOGRAM_COLORS = [
  "#2E5B9E", "#9E3F2E", "#2E9E6C", "#9E2E7C", "#7C9E2E",
  "#2E7C9E", "#9E712E", "#5B2E9E", "#2E9E39", "#9E2E50",
];

function monogramColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return MONOGRAM_COLORS[hash % MONOGRAM_COLORS.length];
}

// The owner's own priority stack — Google Workspace (Gmail/Calendar/Drive)
// plus his 8 (Notion, Linear, Stripe, ClickUp, Airtable, Canva, Asana,
// Zoom) — always rendered first, unfiltered by search. Everything else in
// the catalog (the ~70-connector directory: dev tools, finance, sales
// outreach, etc.) is real and stays reachable below it via a live search —
// MAN-145 founder feedback killed the old click-to-expand "More connectors
// (67)" disclosure ("i must not have this more connectors") because it
// forced scanning 67 cards by eye with no way to jump straight to one.
// Order here IS display order for the priority row.
const PRIORITY_CONNECTOR_IDS = [
  "google_workspace",
  "notion",
  "linear",
  "stripe",
  "clickup",
  "airtable",
  "canva",
  "asana",
  "zoom",
];

/**
 * The reuse-or-separate connector picker. For each connector the PROJECT
 * already has one or more credentials for, the panel shows one radio-style
 * row per credential (its account label) plus a trailing "Connect different"
 * row — one click to reuse an existing credential (no re-auth), or start a
 * fresh one scoped to just this agent. A connector the project doesn't have
 * yet gets a plain "Connect". Shared by the create-agent sequence's Apps step
 * and the agent detail Apps tab.
 */
export function ConnectorPicker({
  workspaceId,
  projectId,
  agentId,
}: {
  workspaceId: string;
  projectId: string;
  agentId: string;
}) {
  const { connectors, loading: agentLoading, refresh: refreshAgent } = useFleetAgentConnectors(workspaceId, agentId);
  const { projectConnectors, loading: projectLoading, refresh: refreshProject } = useFleetProjectConnectors(workspaceId, projectId);
  // Shared with useFleetAgents(workspaceId) callers elsewhere on the page
  // (polled-resource cache keyed by workspaceId) — just for id -> label so
  // the "used by" notice below can name agents instead of showing raw ids.
  const { agents } = useFleetAgents(workspaceId);
  const agentLabelById = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) map.set(a.agent_id, a.label);
    return map;
  }, [agents]);

  // "What kind of tool" — the second thing the founder asked a panel to
  // answer. DERIVED, never authored: this is the agent's own real tool
  // catalog filtered by `requires_connector`, whose only source is
  // fleet_tools.py's _CONNECTOR_REQUIRED_TOOLS. So the list is present
  // exactly where the platform genuinely knows the answer and ABSENT
  // everywhere else — never an empty box, and never a plausible-sounding
  // list nobody can vouch for.
  const { tools } = useFleetAgentTools(workspaceId, agentId);
  const toolsByConnector = useMemo(() => {
    const map = new Map<string, { id: string; label: string }[]>();
    for (const t of tools) {
      if (!t.requires_connector) continue;
      const list = map.get(t.requires_connector) || [];
      list.push({ id: t.id, label: t.label });
      map.set(t.requires_connector, list);
    }
    return map;
  }, [tools]);

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [manualFieldsFor, setManualFieldsFor] = useState<FleetConnector | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");

  // Two groups, not three. There used to be a separate "Needs deployment
  // setup" section under a paragraph explaining, in operator language, that
  // this deployment has no OAuth client id/secret for the nine apps below —
  // the ninth repetition of a sentence each of those cards was already
  // carrying. Those apps are now ordinary cards, sorted last inside the
  // browsable group, wearing the "Unavailable" pill; the reason is said once,
  // in customer language, inside the panel the card opens. They are kept OUT
  // of the priority row (even when the id is one of the curated nine, e.g.
  // Zoom locally) so the top of the surface is only apps you can finish
  // today.
  const { priorityConnectors, browsableConnectors } = useMemo(() => {
    const rank = new Map(PRIORITY_CONNECTOR_IDS.map((id, i) => [id, i]));
    const priority: FleetConnector[] = [];
    const browsable: FleetConnector[] = [];
    for (const c of connectors) {
      const blocked = c.configured === false && !c.connected;
      if (!blocked && (rank.has(c.id) || c.connected)) priority.push(c);
      else browsable.push(c);
    }
    priority.sort((a, b) => (rank.get(a.id) ?? PRIORITY_CONNECTOR_IDS.length) - (rank.get(b.id) ?? PRIORITY_CONNECTOR_IDS.length));
    browsable.sort((a, b) => {
      const aBlocked = a.configured === false && !a.connected;
      const bBlocked = b.configured === false && !b.connected;
      if (aBlocked !== bBlocked) return aBlocked ? 1 : -1;
      return a.label.localeCompare(b.label);
    });
    return { priorityConnectors: priority, browsableConnectors: browsable };
  }, [connectors]);

  // Matches on label, id, AND summary — the summary match is what lets
  // someone find a connector by category ("crm", "analytics", "SEO") without
  // knowing its exact name, since fleet-icons.ts/the catalog names don't
  // carry category tags of their own. The summary no longer appears on any
  // face, which makes searching it MORE useful, not less: it is the one way
  // that text still earns its keep.
  const q = query.trim().toLowerCase();
  const shownBrowsable = useMemo(
    () =>
      browsableConnectors.filter(
        (c) => !q || c.label.toLowerCase().includes(q) || c.id.toLowerCase().includes(q) || c.summary.toLowerCase().includes(q),
      ),
    [browsableConnectors, q],
  );

  const open = useMemo(() => connectors.find((c) => c.id === openId) || null, [connectors, openId]);

  const openPanel = useCallback((id: string) => {
    setError(null);
    setManualFieldsFor(null);
    setOpenId(id);
  }, []);

  const closePanel = useCallback(() => {
    setOpenId(null);
    setManualFieldsFor(null);
    setError(null);
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshAgent(), refreshProject()]);
  }, [refreshAgent, refreshProject]);

  const useCredential = useCallback(async (connector: FleetConnector, credentialId: string) => {
    const key = `${connector.id}:${credentialId}`;
    setBusyKey(key);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ provider: connector.id, connector_key: connector.id, credential_id: credentialId }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch connector.");
    } finally {
      setBusyKey(null);
    }
  }, [workspaceId, agentId, refreshAll]);

  // Backend contract: DELETE .../fleet/agent-connectors?agent_id&connector_key
  // removes ONLY this agent's binding row (server_modules/connectors_actions.py
  // unsubscribe_agent_connector) — the project-scoped credential itself, and
  // any other agent subscribed to it, are untouched.
  const disconnectConnector = useCallback(async (connector: FleetConnector) => {
    const key = `${connector.id}:disconnect`;
    setBusyKey(key);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}&connector_key=${encodeURIComponent(connector.id)}`,
        {
          method: "DELETE",
          credentials: "include",
          headers: buildCookieAuthHeaders("DELETE", {}),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not disconnect.");
    } finally {
      setBusyKey(null);
    }
  }, [workspaceId, agentId, refreshAll]);

  const startConnectNew = useCallback(async (connector: FleetConnector) => {
    const key = `${connector.id}:new`;
    setBusyKey(key);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/connections/${encodeURIComponent(connector.id)}/setup/start`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId, surface: "apps", metadata: { agent_install_id: agentId } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      if (data?.authorization_url) {
        window.location.href = data.authorization_url;
        return;
      }
      const requiredFields: string[] = data?.auth_required_fields?.length
        ? data.auth_required_fields
        : connector.authRequiredFields;
      if (requiredFields && requiredFields.length > 0) {
        setFieldValues({});
        setManualFieldsFor(connector);
        return;
      }
      throw new Error("This connector has no inline setup path yet — open the workspace Connectors page.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start setup.");
    } finally {
      setBusyKey(null);
    }
  }, [workspaceId, agentId]);

  const saveManualCredentials = useCallback(async () => {
    if (!manualFieldsFor) return;
    const key = `${manualFieldsFor.id}:manual`;
    setBusyKey(key);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            provider: manualFieldsFor.id,
            connector_key: manualFieldsFor.id,
            label: manualFieldsFor.label,
            credentials: fieldValues,
          }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setManualFieldsFor(null);
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save credentials.");
    } finally {
      setBusyKey(null);
    }
  }, [workspaceId, agentId, manualFieldsFor, fieldValues, refreshAll]);

  if (agentLoading || projectLoading) {
    // The same grid, the same card box — a placeholder that reflows into a
    // different shape the instant the fetch lands is its own small lie.
    return <FleetCardGridSkeleton cards={9} label="Loading apps" />;
  }

  const connectorIcon = (c: FleetConnector) => {
    const icon = CONNECTOR_ICONS[c.id];
    if (icon) return <img src={icon} alt="" width={32} height={32} />;
    return (
      <span className="fleet-connector-monogram" style={{ background: monogramColor(c.id) }}>
        {c.label.charAt(0).toUpperCase()}
      </span>
    );
  };

  /**
   * The face: a ROW. Icon + name at the left, the action at the right,
   * vertically centred against each other — the founder's own sketch, and
   * Claude's own connector cards, which he named as the reference.
   *
   * TWO CLICK TARGETS, TWO REAL BUTTONS, AND NEITHER NESTED IN THE OTHER.
   * `<button>` inside `<button>` is invalid HTML and browsers un-nest it, so
   * the obvious shape is not available; a `<div onClick>` wrapping a button
   * would work with a mouse and be unreachable without one. So the card is a
   * plain container holding two siblings: the OPEN button, whose `::after`
   * stretches over the whole card (connector-cards.css), and the ACTION
   * button, which is painted above that stretch. The action never bubbles
   * into the card because it was never inside its hit area — the same class
   * of bug as MarkdownLite's "a click means edit this only when it was not
   * already a click on something else", closed by geometry rather than by
   * remembering to call stopPropagation. `stopPropagation` is on the handler
   * anyway, as a second, cheap belt.
   *
   * Keyboard: two ordinary buttons in DOM order, so Tab reaches the card
   * then its action, and Enter/Space activate each — nothing custom.
   */
  const renderCard = (c: FleetConnector) => {
    const face = connectorCardFace(c);
    const busy = busyKey === `${c.id}:new`;
    return (
      <div
        key={c.id}
        className={`fleet-connector-card fleet-connector-card--row${face.muted ? " fleet-connector-card--muted" : ""}${openId === c.id ? " fleet-connector-card--active" : ""}`}
      >
        <button
          type="button"
          className="fleet-connector-card-open"
          onClick={() => openPanel(c.id)}
          aria-haspopup="dialog"
        >
          <span className="fleet-connector-card-icon" aria-hidden="true">{connectorIcon(c)}</span>
          <span className="fleet-connector-card-label">{c.label}</span>
        </button>

        {face.action === "none" ? (
          <span className={`fleet-connector-card-pill fleet-connector-card-pill--${face.state}`}>
            {face.state === "connected" ? <span className="fleet-channel-card-dot" /> : null}
            {face.pill}
          </span>
        ) : (
          // FULL accent fill, on every face. The founder's own instruction,
          // given twice, and the file header above records why it outranks the
          // "~69 filled buttons in one view" arithmetic an earlier pass used
          // to ship the hairline variant here instead. Do not change it back.
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill fleet-connector-card-action"
            onClick={(e) => {
              e.stopPropagation();
              // Opens the panel FIRST, then starts the connection. An OAuth
              // app navigates away before the panel is ever painted, which is
              // the "connects directly" the founder asked for; one that needs
              // pasted fields, or that fails, has somewhere to put its form
              // and its message instead of setting state nothing renders.
              openPanel(c.id);
              void startConnectNew(c);
            }}
            disabled={busy}
          >
            {busy ? (
              <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
            ) : face.action === "reconnect" ? (
              "Reconnect"
            ) : (
              "Connect"
            )}
          </button>
        )}
      </div>
    );
  };

  const catalogSize = browsableConnectors.length;

  // ── The panel a card opens ────────────────────────────────────────────
  const openFace = open ? connectorCardFace(open) : null;
  const openCreds = open ? projectConnectors.filter((pc) => pc.provider === open.id) : [];
  const openTools = open ? toolsByConnector.get(open.id) || [] : [];
  const manualOpen = Boolean(open && manualFieldsFor?.id === open.id);
  const manualRequiredFields = manualOpen ? manualFieldsFor?.authRequiredFields || [] : [];
  const manualComplete = manualRequiredFields.every((field) => (fieldValues[field] || "").trim().length > 0);

  return (
    <div>
      {/* A failure that happened with no panel open (a card's own Connect
          button) still has to be readable. Rendered here as well as inside
          the panel, and only where the panel is NOT the thing showing it —
          one message, never two copies of it on screen at once. */}
      {error && !open ? <p className="fleet-channel-expand-error">{error}</p> : null}

      <div className="fleet-connector-grid">{priorityConnectors.map(renderCard)}</div>

      {catalogSize > 0 && (
        <div className="fleet-connector-browse">
          <label className="fleet-wizard-label" htmlFor="connector-search">
            All apps ({catalogSize})
          </label>
          {/* Placeholder is one word. It was "Search by name or what it
              does…" — a field explaining how a search box works, above a grid
              of logos. A professional tool labels; it does not lecture. The
              field's own quiet treatment is in connector-cards.css. */}
          <input
            id="connector-search"
            type="search"
            className="fleet-wizard-input"
            value={query}
            placeholder="Search"
            onChange={(e) => setQuery(e.currentTarget.value)}
          />

          {shownBrowsable.length > 0 ? (
            <div className="fleet-connector-grid">{shownBrowsable.map(renderCard)}</div>
          ) : (
            <p className="fleet-composer-pop-empty" style={{ padding: 0 }}>
              No apps match &ldquo;{query.trim()}&rdquo;.
            </p>
          )}
        </div>
      )}

      {connectors.length === 0 && <p className="fleet-wizard-hint">No apps are available yet.</p>}

      {open && openFace ? (
        <div
          className="fleet-detail-backdrop"
          onClick={closePanel}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              closePanel();
            }
          }}
        >
          <div
            className="fleet-channel-banner"
            role="dialog"
            aria-modal="true"
            aria-labelledby="connector-detail-heading"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="fleet-channel-banner-header">
              <span className="fleet-channel-banner-icon" aria-hidden="true">{connectorIcon(open)}</span>
              <span className="fleet-channel-banner-title" id="connector-detail-heading">{open.label}</span>
              <button
                type="button"
                className="fleet-detail-close fleet-detail-close--inline"
                onClick={closePanel}
                aria-label="Close"
              >
                <X size={16} strokeWidth={2} />
              </button>
            </div>

            <div className="fleet-channel-banner-body">
              <div className="fleet-connector-panel">
                {/* The same word the face already said. Pressing a card must
                    never change the answer under you. */}
                <p className={`fleet-connector-panel-state fleet-connector-panel-state--${openFace.state}`}>
                  {openFace.state === "connected" ? <span className="fleet-channel-card-dot" /> : null}
                  {openFace.panelState}
                </p>

                {/* What it is — the catalog's own sentence, worth its ink
                    exactly here, where a person has asked about one app. */}
                {open.summary ? <p className="fleet-connector-panel-summary">{open.summary}</p> : null}

                {/* Why it cannot be connected. Once, here, in customer
                    language — never nine repetitions of "Needs an OAuth
                    client configured on this deployment" across the grid. */}
                {openFace.state === "locked" ? (
                  <p className="fleet-connector-panel-blocked">
                    {open.label} isn&apos;t available on Empyralis yet. Nothing to do here — it will show up once we
                    finish setting it up.
                  </p>
                ) : null}

                {openTools.length > 0 ? (
                  <div>
                    <span className="fleet-connector-panel-tools-label">What this brings</span>
                    <ul className="fleet-connector-panel-tools">
                      {openTools.map((t) => (
                        <li key={t.id} className="fleet-connector-panel-tool">
                          <span className="fleet-connector-panel-tool-dot" aria-hidden="true" />
                          {t.label}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {error ? <p className="fleet-channel-expand-error">{error}</p> : null}

                {/* THE ACTION. A locked app gets none at all — a control
                    whose own label admits it does nothing is a design bug,
                    not a caption. */}
                {openFace.state === "locked" ? null : manualOpen ? (
                  <div className="fleet-connector-panel-fields">
                    {manualRequiredFields.map((field) => (
                      <label key={field} className="gw-pair-panel-field" style={{ display: "flex" }}>
                        <span>{fieldLabel(field)}</span>
                        <input
                          type={/key|token|secret|password/i.test(field) ? "password" : "text"}
                          value={fieldValues[field] || ""}
                          onChange={(e) => {
                            // Read the value synchronously — e.currentTarget
                            // is null by the time a state-updater callback
                            // runs (React nulls out the synthetic event after
                            // the handler returns), so capturing it outside
                            // the updater is required, not stylistic.
                            const value = e.currentTarget.value;
                            setFieldValues((cur) => ({ ...cur, [field]: value }));
                          }}
                        />
                      </label>
                    ))}
                    <div className="fleet-connector-panel-actions">
                      <button
                        type="button"
                        className="fleet-btn fleet-btn--accent-fill"
                        onClick={saveManualCredentials}
                        disabled={busyKey === `${open.id}:manual` || !manualComplete}
                        title={!manualComplete ? "Fill in every field before saving" : undefined}
                      >
                        {busyKey === `${open.id}:manual` ? "Saving…" : "Save"}
                      </button>
                      <button type="button" className="fleet-btn" onClick={() => setManualFieldsFor(null)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : openCreds.length > 0 ? (
                  <div className="fleet-connector-picker-rows">
                    {openCreds.map((cred) => {
                      const selected = cred.subscribed_agent_ids.includes(agentId);
                      const rowBusy = busyKey === `${open.id}:${cred.id}`;
                      const disconnectBusy = busyKey === `${open.id}:disconnect`;
                      // This account is account/cloud-level, not per-agent —
                      // every OTHER agent already subscribed to it will keep
                      // using the SAME live connection the instant this agent
                      // joins too, so the owner should see that before (and
                      // after) clicking.
                      const otherSubscribers = cred.subscribed_agent_ids
                        .filter((id) => id !== agentId)
                        .map((id) => agentLabelById.get(id) || id);
                      return (
                        <div key={cred.id} className="fleet-connector-picker-row-group">
                          <div style={{ display: "flex", alignItems: "stretch", gap: 6 }}>
                            <button
                              type="button"
                              className={`fleet-connector-picker-row${selected ? " is-selected" : ""}`}
                              disabled={selected || rowBusy}
                              onClick={() => useCredential(open, cred.id)}
                              style={{ flex: 1 }}
                            >
                              {rowBusy ? (
                                <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                              ) : (
                                <span className="fleet-connector-picker-radio" aria-hidden="true" />
                              )}
                              <span className="fleet-connector-picker-account">
                                {cred.account_label || cred.label || "Connected account"}
                              </span>
                            </button>
                            {selected && (
                              // The only DELETE caller for this endpoint (see
                              // routes_fleet.py fleet_disconnect_agent_connector)
                              // — previously unreachable from the UI entirely,
                              // so a connected connector could never be removed.
                              <button
                                type="button"
                                className="fleet-btn"
                                onClick={() => disconnectConnector(open)}
                                disabled={disconnectBusy}
                                aria-label={`Disconnect ${open.label}`}
                                title="Remove this agent's connection (the credential itself is kept for other agents)"
                              >
                                {disconnectBusy ? (
                                  <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                                ) : (
                                  "Disconnect"
                                )}
                              </button>
                            )}
                          </div>
                          {otherSubscribers.length > 0 && (
                            <p
                              className="fleet-connector-picker-shared-warning"
                              style={{ color: "var(--warning-text)", fontSize: 12, margin: "2px 0 0 22px" }}
                            >
                              Also used by {otherSubscribers.join(", ")} — this is the same account, not a copy.
                            </p>
                          )}
                        </div>
                      );
                    })}
                    <button
                      type="button"
                      className="fleet-connector-picker-row"
                      disabled={busyKey === `${open.id}:new`}
                      onClick={() => startConnectNew(open)}
                    >
                      {busyKey === `${open.id}:new` ? (
                        <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                      ) : (
                        <span className="fleet-connector-picker-radio" aria-hidden="true" />
                      )}
                      <span className="fleet-connector-picker-account fleet-connector-picker-tag">Connect different</span>
                    </button>
                  </div>
                ) : (
                  <div className="fleet-connector-panel-actions">
                    {/* FULL accent fill — the same class the create
                        sequence's own Next button wears. Safe here and only
                        here: the grid behind it spends no accent at all, so
                        this view holds exactly one accent-filled control. */}
                    <button
                      type="button"
                      className="fleet-btn fleet-btn--accent-fill"
                      onClick={() => startConnectNew(open)}
                      disabled={busyKey === `${open.id}:new`}
                    >
                      {busyKey === `${open.id}:new` ? (
                        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
                      ) : (
                        "Connect"
                      )}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
