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

/** Whether this box has the given subscription CLI installed + authenticated
 *  (BYO-brain readiness, same shape as gatewayLocalModelReady's Ollama check
 *  but for claude_code/codex). */
export function gatewayRuntimeReady(g: FleetGateway, runtime: "claude_code" | "codex"): boolean {
  return Boolean(g.llm_runtimes?.[runtime]?.detected);
}

export type HardwarePlacementTone = "cloud" | "online" | "offline" | "degraded" | "unpaired";

export type HardwarePlacement = { label: string; tone: HardwarePlacementTone };

function connectionTone(g: FleetGateway): HardwarePlacementTone {
  const raw = `${g.connection_status || g.status || ""}`.toLowerCase();
  if (raw === "online") return "online";
  if (raw === "degraded" || raw === "reconnecting") return "degraded";
  return "offline";
}

/** Real placement + live health for "Running on: …" — built ONLY from
 *  hardware_access + preferred_gateway_id + this same registrations join,
 *  never from runtime_target / derivePlacement() / agent.hardware_status.
 *  Those all key off a runtime_profile foreign key that real Fleet agents
 *  never update after creation, so they read as a permanent, wrong "Cloud". */
export function resolveHardwarePlacement(
  hardwareAccess: string | undefined,
  preferredGatewayId: string | undefined,
  gateways: FleetGateway[],
): HardwarePlacement {
  const access = (hardwareAccess || "none").toLowerCase();
  if (access === "none") return { label: "Cloud", tone: "cloud" };
  const preferred = String(preferredGatewayId || "").trim();
  if (preferred) {
    const match = gateways.find((g) => gatewayId(g) === preferred);
    if (match) return { label: gatewayLabel(match), tone: connectionTone(match) };
    return { label: "Paired computer (disconnected)", tone: "offline" };
  }
  if (gateways.length === 0) return { label: "No computer paired yet", tone: "unpaired" };
  return { label: "Any paired computer", tone: gateways.some(gatewayIsOnline) ? "online" : "offline" };
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

const RUNTIME_LABELS: Record<"claude_code" | "codex", string> = {
  claude_code: "Claude Code",
  codex: "Codex",
};

/** Box-picker: choose which paired Gateway runs the agent's brain.
 *  Phase 0 wires the plumbing (capture into model_config.gateway_binding);
 *  the modes that use it are still "coming soon". */
export function GatewayBoxPicker({
  workspaceId,
  value,
  onChange,
  disabled,
  requireLocalModel,
  requireRuntime,
}: {
  workspaceId: string;
  value: string;
  onChange: (gatewayId: string) => void;
  disabled?: boolean;
  /** When true (mode = "Run locally"), annotate each box with Ollama readiness
   *  and warn if the selected box can't serve a local model. */
  requireLocalModel?: boolean;
  /** When set (mode = "Your subscription"), annotate each box with whether
   *  that specific CLI is installed + authenticated there, and warn if the
   *  selected box doesn't have it. */
  requireRuntime?: "claude_code" | "codex";
}) {
  const { gateways, loading } = useWorkspaceGateways(workspaceId);
  const selected = gateways.find((g) => gatewayId(g) === value);
  const selectedMissingLocalModel = Boolean(
    requireLocalModel && selected && !gatewayLocalModelReady(selected),
  );
  const selectedMissingRuntime = Boolean(
    requireRuntime && selected && !gatewayRuntimeReady(selected, requireRuntime),
  );
  const runtimeLabel = requireRuntime ? RUNTIME_LABELS[requireRuntime] : "";

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
                : requireRuntime
                ? (gatewayRuntimeReady(g, requireRuntime) ? `· ${runtimeLabel} ready` : "· not detected")
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
          ) : selectedMissingRuntime ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              This computer doesn't have {runtimeLabel} installed and signed in. Install and authenticate{" "}
              {runtimeLabel} there (then reconnect the gateway), or pick a box that shows “{runtimeLabel} ready”.
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
