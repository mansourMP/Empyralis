"use client";

import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";

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

// The owner's own priority stack — Google Workspace (Gmail/Calendar/Drive)
// plus his 8 (Notion, Linear, Stripe, ClickUp, Airtable, Canva, Asana,
// Zoom) — always rendered first and never collapsed. Everything else in
// the catalog (the ~65-connector directory: dev tools, finance, sales
// outreach, etc.) is real and stays reachable, just tucked behind a
// closed-by-default "More connectors" disclosure so it can grow without
// burying what the owner actually uses. Order here IS display order.
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
  const [moreOpen, setMoreOpen] = useState(false);

  // Split the catalog into the owner's priority stack (always shown, in
  // PRIORITY_CONNECTOR_IDS order) and everything else (the wider directory —
  // dev tools, finance, sales-outreach, etc. — real connectors, just not
  // what he reaches for daily). A connector already connected always counts
  // as priority regardless of id, so a live integration never gets hidden
  // behind the disclosure the moment it falls outside the curated 9.
  const { priorityConnectors, moreConnectors } = useMemo(() => {
    const rank = new Map(PRIORITY_CONNECTOR_IDS.map((id, i) => [id, i]));
    const priority: FleetConnector[] = [];
    const more: FleetConnector[] = [];
    for (const c of connectors) {
      if (rank.has(c.id) || c.connected) priority.push(c);
      else more.push(c);
    }
    priority.sort((a, b) => (rank.get(a.id) ?? PRIORITY_CONNECTOR_IDS.length) - (rank.get(b.id) ?? PRIORITY_CONNECTOR_IDS.length));
    return { priorityConnectors: priority, moreConnectors: more };
  }, [connectors]);

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
    return (
      <div className="fleet-activity-skeleton" aria-label="Loading connectors">
        <div className="fleet-skeleton-bar" style={{ width: "70%" }} />
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
        // identically to a healthy one. Flag anything other than "healthy".
        const unhealthy = Boolean(c.healthStatus) && c.healthStatus !== "healthy";
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
            title={notConfigured ? "Not configured on this deployment" : undefined}
          >
            <div className="fleet-connector-picker-head">
              <span className="fleet-connector-picker-icon" aria-hidden="true">
                {icon ? <img src={icon} alt="" /> : c.label.charAt(0)}
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
                  {notConfigured ? "Not configured on this deployment" : c.summary}
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

  return (
    <div>
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      <div className="fleet-connector-picker">
        {priorityConnectors.map(renderConnector)}
      </div>
      {moreConnectors.length > 0 && (
        <div className={`fleet-disclosure${moreOpen ? " is-open" : ""}`} style={{ marginTop: 14 }}>
          <button
            type="button"
            className="fleet-disclosure-trigger"
            onClick={() => setMoreOpen((o) => !o)}
            aria-expanded={moreOpen}
          >
            <ChevronRight size={13} strokeWidth={2} className="fleet-disclosure-chevron" />
            More connectors ({moreConnectors.length})
          </button>
          {moreOpen && (
            <div className="fleet-disclosure-body">
              <div className="fleet-connector-picker">
                {moreConnectors.map(renderConnector)}
              </div>
            </div>
          )}
        </div>
      )}
      {connectors.length === 0 && <p className="fleet-wizard-hint">No connectors are available yet.</p>}
    </div>
  );
}
