"use client";

import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { CONNECTOR_ICONS } from "./fleet-icons";
import {
  useFleetAgentConnectors,
  useFleetAgents,
  useFleetProjectConnectors,
  type FleetConnector,
} from "./fleet-data";

function fieldLabel(field: string): string {
  return field.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

// Deliberate fallback for a connector with no mapped logo (CONNECTOR_ICONS
// in fleet-icons.ts should cover the full catalog, but this is the
// deployment-agnostic safety net for whatever gets added to the backend
// catalog before an icon is wired for it — see docs/PLATFORM-MAP.md's
// fleet-icons.ts entry). Renders into the SAME 24px .fleet-connector-picker-icon
// box a real <img> logo would (same size/shape, per MAN-145 founder
// feedback: a bare letter on the muted default background read as broken,
// not as a deliberate placeholder) but gives every connector its own
// deterministic color so the grid doesn't look uniformly grey/unstyled.
// Same small palette + hashing approach as the AgentSigil convention used
// elsewhere in fleet (deterministic per-id color, not random per-render).
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
 * The reuse-or-separate connector picker (UI Phase 1). For each connector the
 * PROJECT already has one or more credentials for, shows one radio-style row
 * per credential (its account label) plus a trailing "Connect different" row
 * — one click to reuse an existing credential (no re-auth), or start a fresh
 * one scoped to just this agent. A connector the project doesn't have yet
 * gets a plain "Connect" action. Shared by the create-agent wizard's
 * Connectors step and the agent detail Connectors tab.
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

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualFieldsFor, setManualFieldsFor] = useState<FleetConnector | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");

  // Split the catalog into three groups:
  //  - priority: the owner's curated stack (PRIORITY_CONNECTOR_IDS order) plus
  //    anything already connected, regardless of id — a live integration never
  //    gets buried. Always shown in full, never filtered by search.
  //  - browsable: everything else this deployment can actually connect
  //    (configured !== false) — reachable via the search box below, not a
  //    click-to-expand disclosure.
  //  - needsSetup: real catalog entries this DEPLOYMENT hasn't been wired
  //    with OAuth credentials for yet (connection_oauth_service.py's
  //    oauth_provider_configured — an operator/env-var gap, not a fake
  //    connector). Kept OUT of the priority row even if the id is one of the
  //    curated 9 (e.g. Zoom locally) so the curated row never shows a dead
  //    card — see the "Needs deployment setup" section below instead, which
  //    explains why and is still reachable via the same search box.
  const { priorityConnectors, browsableConnectors, needsSetupConnectors } = useMemo(() => {
    const rank = new Map(PRIORITY_CONNECTOR_IDS.map((id, i) => [id, i]));
    const priority: FleetConnector[] = [];
    const browsable: FleetConnector[] = [];
    const needsSetup: FleetConnector[] = [];
    for (const c of connectors) {
      const curated = rank.has(c.id) || c.connected;
      if (c.configured === false && !c.connected) {
        needsSetup.push(c);
      } else if (curated) {
        priority.push(c);
      } else {
        browsable.push(c);
      }
    }
    priority.sort((a, b) => (rank.get(a.id) ?? PRIORITY_CONNECTOR_IDS.length) - (rank.get(b.id) ?? PRIORITY_CONNECTOR_IDS.length));
    browsable.sort((a, b) => a.label.localeCompare(b.label));
    needsSetup.sort((a, b) => a.label.localeCompare(b.label));
    return { priorityConnectors: priority, browsableConnectors: browsable, needsSetupConnectors: needsSetup };
  }, [connectors]);

  // Matches on label, id, AND summary — the summary match is what lets
  // someone find a connector by category ("crm", "analytics", "SEO") without
  // knowing its exact name, since fleet-icons.ts/the catalog names don't
  // carry category tags of their own.
  const q = query.trim().toLowerCase();
  const matchesQuery = useCallback((c: FleetConnector) =>
    !q || c.label.toLowerCase().includes(q) || c.id.toLowerCase().includes(q) || c.summary.toLowerCase().includes(q),
  [q]);
  const shownBrowsable = useMemo(() => browsableConnectors.filter(matchesQuery), [browsableConnectors, matchesQuery]);
  const shownNeedsSetup = useMemo(() => needsSetupConnectors.filter(matchesQuery), [needsSetupConnectors, matchesQuery]);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshAgent(), refreshProject()]);
  }, [refreshAgent, refreshProject]);

  const useCredential = useCallback(async (connector: FleetConnector, credentialId: string) => {
    const key = `${connector.id}:${credentialId}`;
    setBusyKey(key);
    setError(null);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ provider: connector.id, connector_key: connector.id, credential_id: credentialId }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
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
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}&connector_key=${encodeURIComponent(connector.id)}`,
        {
          method: "DELETE",
          credentials: "include",
          headers: buildCookieAuthHeaders("DELETE", {}),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
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
      const res = await fetch(`/api/connections/${encodeURIComponent(connector.id)}/setup/start`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId, surface: "apps", metadata: { agent_install_id: agentId } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
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
      const res = await fetch(
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
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
      setManualFieldsFor(null);
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save credentials.");
    } finally {
      setBusyKey(null);
    }
  }, [workspaceId, agentId, manualFieldsFor, fieldValues, refreshAll]);

  if (agentLoading || projectLoading) {
    // Reuses .fleet-connector-picker's real 2-col grid (fleet-theme.css) so
    // the placeholder is the same shape as the suggested-connector cards
    // that replace it — a single 8px bar here previously stood in for what
    // is actually a multi-card grid with title/description/button per card.
    return (
      <div className="fleet-connector-picker" aria-busy="true" aria-label="Loading connectors">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="fleet-connector-picker-item">
            <div className="fleet-skeleton-bar" style={{ width: "40%", height: 13 }} />
            <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, opacity: 0.7 }} />
            <div className="fleet-skeleton-bar" style={{ width: "60%", height: 10, opacity: 0.7 }} />
            <div className="fleet-skeleton-bar" style={{ width: 76, height: 26, marginTop: 4 }} />
          </div>
        ))}
      </div>
    );
  }

  const renderConnector = (c: FleetConnector) => {
    const projectCreds = projectConnectors.filter((pc) => pc.provider === c.id);
        const icon = CONNECTOR_ICONS[c.id];
        const connectBusy = busyKey === `${c.id}:new`;
        const notConfigured = c.configured === false;
        // healthStatus is fetched on every FleetConnector but was previously
        // dropped on the floor — an expired/degraded connector rendered
        // identically to a healthy one. Flag anything other than "healthy" —
        // but ONLY once connected: the backend's default health_status for a
        // never-connected work_app_connector is "not_configured" regardless
        // of whether the provider is genuinely blocked on operator setup or
        // just hasn't been connected by this user yet (see
        // connection_catalog_service.py status_items(), lane
        // LANE_WORK_APP_CONNECTOR: `health_status = "healthy" if connected
        // else "not_configured"`). Gating on `c.connected` here means every
        // not-yet-connected connector (Linear, Notion, Stripe, etc. included)
        // stops rendering a spurious "Not Configured" warning badge on top of
        // its own "Connect" button — that combination read as "this is
        // broken" when it just meant "you haven't connected it yet". The
        // `c.configured === false` case (genuinely needs an operator-
        // registered OAuth app) is already communicated separately via
        // `notConfigured` above.
        const unhealthy = c.connected && Boolean(c.healthStatus) && c.healthStatus !== "healthy";
        const manualFieldsOpen = manualFieldsFor?.id === c.id;
        // `?.` here (not narrowing on manualFieldsOpen) so this stays safe
        // regardless of what TS infers about manualFieldsFor's nullability.
        const manualRequiredFields = manualFieldsOpen ? (manualFieldsFor?.authRequiredFields || []) : [];
        const manualFieldsComplete = manualRequiredFields.every(
          (field) => (fieldValues[field] || "").trim().length > 0
        );
        return (
          <div
            key={c.id}
            className={`fleet-connector-picker-item${notConfigured ? " is-inert" : ""}`}
            title={notConfigured ? "This deployment hasn't been configured with OAuth credentials for this connector yet." : undefined}
          >
            <div className="fleet-connector-picker-head">
              <span className="fleet-connector-picker-icon" aria-hidden="true">
                {icon ? (
                  <img src={icon} alt="" />
                ) : (
                  <span style={{ display: "flex", width: "100%", height: "100%", alignItems: "center", justifyContent: "center", background: monogramColor(c.id), color: "#FFFFFF", borderRadius: 7 }}>
                    {c.label.charAt(0).toUpperCase()}
                  </span>
                )}
              </span>
              <div>
                <div className="fleet-connector-picker-label">
                  {c.label}
                  {unhealthy && (
                    <span
                      className="fleet-badge"
                      style={{ color: "var(--warning-text)", borderColor: "var(--warning-text)" }}
                      title={`Connector health: ${fieldLabel(c.healthStatus)}`}
                    >
                      <AlertTriangle size={10} strokeWidth={2} />
                      {fieldLabel(c.healthStatus)}
                    </span>
                  )}
                </div>
                <div className="fleet-connector-picker-summary">
                  {notConfigured ? "Needs an OAuth client configured on this deployment before it can connect." : c.summary}
                </div>
              </div>
            </div>

            {notConfigured ? null : projectCreds.length > 0 ? (
              <div className="fleet-connector-picker-rows">
                {projectCreds.map((cred) => {
                  const selected = cred.subscribed_agent_ids.includes(agentId);
                  const rowBusy = busyKey === `${c.id}:${cred.id}`;
                  const disconnectBusy = busyKey === `${c.id}:disconnect`;
                  // This account is account/cloud-level, not per-agent — every
                  // OTHER agent already subscribed to it will keep using the
                  // SAME live connection the instant this agent joins too, so
                  // the owner should see that before (and after) clicking.
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
                          onClick={() => useCredential(c, cred.id)}
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
                          // routes_fleet.py fleet_disconnect_agent_connector) —
                          // previously unreachable from the UI entirely, so a
                          // connected connector could never be removed.
                          <button
                            type="button"
                            className="fleet-btn"
                            onClick={() => disconnectConnector(c)}
                            disabled={disconnectBusy}
                            aria-label={`Disconnect ${c.label}`}
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
                  className={`fleet-connector-picker-row${manualFieldsOpen ? " is-selected" : ""}`}
                  disabled={connectBusy}
                  onClick={() => startConnectNew(c)}
                >
                  {connectBusy ? (
                    <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                  ) : (
                    <span className="fleet-connector-picker-radio" aria-hidden="true" />
                  )}
                  <span className="fleet-connector-picker-account fleet-connector-picker-tag">Connect different</span>
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="fleet-btn fleet-connector-picker-connect-btn"
                onClick={() => startConnectNew(c)}
                disabled={connectBusy}
              >
                {connectBusy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Connect"}
              </button>
            )}

            {manualFieldsOpen && (
              <div className="fleet-channel-expand">
                <p className="fleet-channel-expand-hint">Enter credentials for {c.label}.</p>
                {manualRequiredFields.map((field) => (
                  <label key={field} className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                    <span>{fieldLabel(field)}</span>
                    <input
                      type={/key|token|secret|password/i.test(field) ? "password" : "text"}
                      value={fieldValues[field] || ""}
                      onChange={(e) => {
                        // Read the value synchronously — e.currentTarget is
                        // null by the time a state-updater callback runs
                        // (React nulls out the synthetic event after the
                        // handler returns), so capturing it outside the
                        // updater is required, not stylistic.
                        const value = e.currentTarget.value;
                        setFieldValues((cur) => ({ ...cur, [field]: value }));
                      }}
                    />
                  </label>
                ))}
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    className="fleet-btn fleet-btn--accent"
                    onClick={saveManualCredentials}
                    disabled={busyKey === `${c.id}:manual` || !manualFieldsComplete}
                    title={!manualFieldsComplete ? "Fill in every field before saving" : undefined}
                  >
                    {busyKey === `${c.id}:manual` ? "Saving…" : "Save"}
                  </button>
                  <button type="button" className="fleet-btn" onClick={() => setManualFieldsFor(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        );
  };

  const catalogSize = browsableConnectors.length + needsSetupConnectors.length;

  return (
    <div>
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      <div className="fleet-connector-picker">
        {priorityConnectors.map(renderConnector)}
      </div>

      {catalogSize > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="fleet-wizard-label" style={{ margin: "0 0 6px" }}>
            All connectors ({catalogSize})
          </div>
          <input
            type="search"
            className="fleet-wizard-input"
            style={{ marginBottom: 12 }}
            value={query}
            placeholder={`Search ${catalogSize} connector${catalogSize === 1 ? "" : "s"} by name or what it does…`}
            aria-label="Search connectors"
            onChange={(e) => setQuery(e.currentTarget.value)}
          />

          {shownBrowsable.length > 0 && (
            <div className="fleet-connector-picker">
              {shownBrowsable.map(renderConnector)}
            </div>
          )}

          {q && shownBrowsable.length === 0 && shownNeedsSetup.length === 0 && (
            <p className="fleet-composer-pop-empty" style={{ padding: 0 }}>
              No connectors match &ldquo;{query.trim()}&rdquo;.
            </p>
          )}

          {shownNeedsSetup.length > 0 && (
            <div style={{ marginTop: shownBrowsable.length > 0 ? 18 : 0 }}>
              <p className="fleet-channel-expand-hint" style={{ margin: "0 0 8px" }}>
                {needsSetupConnectors.length} connector{needsSetupConnectors.length === 1 ? "" : "s"} below {needsSetupConnectors.length === 1 ? "is" : "are"} real but this deployment hasn&apos;t been configured with OAuth credentials for{" "}
                {needsSetupConnectors.length === 1 ? "it" : "them"} yet — an operator needs to set a client id/secret for the provider before {needsSetupConnectors.length === 1 ? "it" : "they"} can be connected.
              </p>
              <div className="fleet-connector-picker">
                {shownNeedsSetup.map(renderConnector)}
              </div>
            </div>
          )}
        </div>
      )}
      {connectors.length === 0 && <p className="fleet-wizard-hint">No connectors are available yet.</p>}
    </div>
  );
}
