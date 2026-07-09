"use client";

import { useCallback, useState } from "react";
import { Loader2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { CONNECTOR_ICONS } from "./fleet-icons";
import {
  useFleetAgentConnectors,
  useFleetProjectConnectors,
  type FleetConnector,
} from "./fleet-data";

function fieldLabel(field: string): string {
  return field.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

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

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualFieldsFor, setManualFieldsFor] = useState<FleetConnector | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

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

  return (
    <div className="fleet-connector-picker">
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      {connectors.map((c) => {
        const projectCreds = projectConnectors.filter((pc) => pc.provider === c.id);
        const icon = CONNECTOR_ICONS[c.id];
        const connectBusy = busyKey === `${c.id}:new`;
        const notConfigured = c.configured === false;
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
                <div className="fleet-connector-picker-label">{c.label}</div>
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
                  return (
                    <button
                      key={cred.id}
                      type="button"
                      className={`fleet-connector-picker-row${selected ? " is-selected" : ""}`}
                      disabled={selected || rowBusy}
                      onClick={() => useCredential(c, cred.id)}
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
                  );
                })}
                <button
                  type="button"
                  className={`fleet-connector-picker-row${manualFieldsFor?.id === c.id ? " is-selected" : ""}`}
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

            {manualFieldsFor?.id === c.id && (
              <div className="fleet-channel-expand">
                <p className="fleet-channel-expand-hint">Enter credentials for {c.label}.</p>
                {(manualFieldsFor.authRequiredFields || []).map((field) => (
                  <label key={field} className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                    <span>{fieldLabel(field)}</span>
                    <input
                      type={/key|token|secret|password/i.test(field) ? "password" : "text"}
                      value={fieldValues[field] || ""}
                      onChange={(e) => setFieldValues((cur) => ({ ...cur, [field]: e.currentTarget.value }))}
                    />
                  </label>
                ))}
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    className="fleet-btn fleet-btn--accent"
                    onClick={saveManualCredentials}
                    disabled={busyKey === `${c.id}:manual`}
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
      })}
      {connectors.length === 0 && <p className="fleet-wizard-hint">No connectors are available yet.</p>}
    </div>
  );
}
