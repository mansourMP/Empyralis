"use client";

import { useEffect, useState } from "react";

/** Per-box AI-runtime detection (BYO-brain Phase 1), surfaced so users don't
 *  pick a box that can't run the brain. */
export type LlmRuntimeSummary = {
  ollama?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  claude_code?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  codex?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  local_model_ready?: boolean;
};

/** A paired Gateway box (subset of /api/gateway/registrations items). */
export type FleetGateway = {
  gateway_id?: string;
  id?: string;
  display_name?: string | null;
  platform?: string | null;
  hardware_label?: string | null;
  status?: string | null;
  connection_status?: string | null;
  llm_runtimes?: LlmRuntimeSummary | null;
};

/** Whether this box has a local model runtime (Ollama) ready to serve turns. */
export function gatewayLocalModelReady(g: FleetGateway): boolean {
  return Boolean(g.llm_runtimes?.local_model_ready);
}

export function gatewayId(g: FleetGateway): string {
  return String(g.gateway_id || g.id || "").trim();
}

export function gatewayLabel(g: FleetGateway): string {
  return (
    String(g.display_name || "").trim() ||
    String(g.hardware_label || "").trim() ||
    String(g.platform || "").trim() ||
    gatewayId(g) ||
    "Computer"
  );
}

export function gatewayIsOnline(g: FleetGateway): boolean {
  return `${g.connection_status || ""} ${g.status || ""}`.toLowerCase().includes("online");
}

/** Fetches the workspace's paired Gateway boxes — the same endpoint the
 *  Hardware page uses. Read-only; safe to call from any tab. */
export function useWorkspaceGateways(workspaceId: string) {
  const [gateways, setGateways] = useState<FleetGateway[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
          { credentials: "include" },
        );
        const data = res.ok ? await res.json() : {};
        const list = data?.items || data?.registrations || (Array.isArray(data) ? data : []);
        if (!cancelled) setGateways(Array.isArray(list) ? list : []);
      } catch {
        if (!cancelled) setGateways([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  return { gateways, loading };
}

/** Box-picker: choose which paired Gateway runs the agent's brain.
 *  Phase 0 wires the plumbing (capture into model_config.gateway_binding);
 *  the modes that use it are still "coming soon". */
export function GatewayBoxPicker({
  workspaceId,
  value,
  onChange,
  disabled,
  requireLocalModel,
}: {
  workspaceId: string;
  value: string;
  onChange: (gatewayId: string) => void;
  disabled?: boolean;
  /** When true (mode = "Run locally"), annotate each box with Ollama readiness
   *  and warn if the selected box can't serve a local model. */
  requireLocalModel?: boolean;
}) {
  const { gateways, loading } = useWorkspaceGateways(workspaceId);
  const selected = gateways.find((g) => gatewayId(g) === value);
  const selectedMissingLocalModel = Boolean(
    requireLocalModel && selected && !gatewayLocalModelReady(selected),
  );

  return (
    <div style={{ marginTop: 12 }}>
      <label className="fleet-wizard-label">Which computer runs it?</label>
      {loading ? (
        <p className="fleet-channel-expand-hint">Loading your paired computers…</p>
      ) : gateways.length === 0 ? (
        <p className="fleet-channel-expand-hint">
          No paired computers yet. Pair one from the Hardware page, then choose it here.
        </p>
      ) : (
        <>
          <select
            className="fleet-wizard-input"
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.currentTarget.value)}
          >
            <option value="">Select a computer…</option>
            {gateways.map((g) => {
              const id = gatewayId(g);
              const online = gatewayIsOnline(g);
              const suffix = requireLocalModel
                ? (gatewayLocalModelReady(g) ? "· Ollama ready" : "· no local model")
                : (online ? "· online" : "· offline");
              return (
                <option key={id} value={id}>
                  {gatewayLabel(g)} {suffix}
                </option>
              );
            })}
          </select>
          {selectedMissingLocalModel ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              This computer has no local model runtime detected. Install and start Ollama on it (then
              reconnect the gateway), or pick a box that shows “Ollama ready”.
            </p>
          ) : (
            <p className="fleet-channel-expand-hint">
              The brain runs on this machine. Empyralis only sends the prompt and receives the reply —
              it never sees any local credentials.
            </p>
          )}
        </>
      )}
    </div>
  );
}
