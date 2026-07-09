"use client";

import { useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import type { FleetAgent } from "../fleet-data";
import {
  GatewayBoxPicker,
  resolveHardwarePlacement,
  useWorkspaceGateways,
  type HardwarePlacementTone,
} from "../gateway-box-picker";

const ACCESS_OPTIONS: { value: string; label: string; body: string }[] = [
  { value: "none", label: "Cloud only", body: "No computer access — runs entirely in the cloud." },
  { value: "gateway", label: "Paired computer", body: "Shell, filesystem, and browser access on a box you've paired." },
  { value: "vps", label: "Cloud VPS", body: "Same tool access, on a computer Empyralis provisioned for you." },
  { value: "all", label: "Full hardware access", body: "Any paired computer or cloud VPS in this workspace." },
];

function dotClass(tone: HardwarePlacementTone): string {
  if (tone === "online" || tone === "cloud") return "is-online";
  if (tone === "degraded") return "is-degraded";
  return "is-offline"; // offline, unpaired
}

/**
 * HARDWARE tab — the agent's real hardware placement, and the one control
 * that sets it (moved here from the Model tab, which kept a second,
 * confusable "brain runs on" picker for a different field — see ModelTab).
 *
 * Backed by hardware_access (none/gateway/vps/all) + preferred_gateway_id,
 * joined against live /gateway/registrations — deliberately never
 * runtime_target / hardware_status (see resolveHardwarePlacement for why
 * that pipeline is inert for real Fleet agents).
 */
export function HardwareTab({
  workspaceId,
  agentId,
  agent,
  onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Called after a successful save so the caller can refetch — the Overview
   *  property and list rows read this same placement from a separate fetch
   *  of `agent`, and should catch up immediately rather than waiting out the
   *  next poll. */
  onSaved?: () => void;
}) {
  const { gateways, loading: gatewaysLoading } = useWorkspaceGateways(workspaceId);
  const locked = !!agent?.hardware_access_locked;
  const preset = (agent?.capability_preset || "").toLowerCase();

  const [access, setAccess] = useState((agent?.hardware_access || "none").toLowerCase());
  const [preferredGateway, setPreferredGateway] = useState(agent?.preferred_gateway_id || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resync when the agent record (re)loads under us — e.g. switching agents,
  // or another tab's edit landing — but never while a save is in flight.
  useEffect(() => {
    if (saving) return;
    setAccess((agent?.hardware_access || "none").toLowerCase());
    setPreferredGateway(agent?.preferred_gateway_id || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.hardware_access, agent?.preferred_gateway_id]);

  const placement = resolveHardwarePlacement(access, preferredGateway, gateways);

  async function persist(nextAccess: string, nextGateway: string) {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`,
        {
          method: "PATCH",
          credentials: "include",
          headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            patch: {
              // The real 4-value column, verbatim — no collapsing to a
              // none/gateway boolean the way the old Model-tab control did,
              // which silently downgraded "vps"/"all" to "gateway" on save.
              hardware_access: nextAccess,
              preferred_gateway_id: nextAccess === "none" ? "" : nextGateway,
            },
          }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
      // Roll the control back to the last-known-saved values rather than
      // leaving it pointed at a selection that didn't actually persist.
      setAccess((agent?.hardware_access || "none").toLowerCase());
      setPreferredGateway(agent?.preferred_gateway_id || "");
    } finally {
      setSaving(false);
    }
  }

  function selectAccess(next: string) {
    if (saving || next === access) return;
    setAccess(next);
    void persist(next, preferredGateway);
  }

  function selectGateway(next: string) {
    setPreferredGateway(next);
    void persist(access, next);
  }

  return (
    <div className="fleet-detail-pad fleet-hw">
      <div className="fleet-detail-section-title">Running on</div>
      <div className="fleet-hw-card">
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Placement</span>
          <span className="fleet-hw-value">
            <span className={`fleet-detail-dot ${dotClass(placement.tone)}`} aria-hidden />
            {gatewaysLoading ? "Loading…" : placement.label}
          </span>
        </div>
      </div>

      <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Hardware access</div>
      {locked ? (
        <p className="fleet-hw-note" style={{ marginTop: 0 }}>
          This is a <strong>Knowledge</strong> agent — hardware access is off and policy-locked. To
          grant hardware, change its capability preset (<span style={{ textTransform: "capitalize" }}>{preset || "knowledge"}</span>) on the
          Model tab; a knowledge agent can't be given hardware directly.
        </p>
      ) : (
        <>
          <div className="fleet-wizard-options">
            {ACCESS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`fleet-wizard-option${access === opt.value ? " is-selected" : ""}`}
                disabled={saving}
                onClick={() => selectAccess(opt.value)}
              >
                <span className="fleet-wizard-option-label">{opt.label}</span>
                <span className="fleet-wizard-option-body">{opt.body}</span>
              </button>
            ))}
          </div>
          {access !== "none" && (
            <>
              <GatewayBoxPicker
                workspaceId={workspaceId}
                value={preferredGateway}
                disabled={saving}
                onChange={selectGateway}
              />
              <p className="fleet-channel-expand-hint">
                Optional — leave unset to use whichever paired computer is online.
              </p>
            </>
          )}
          <div style={{ marginTop: 12, minHeight: 20 }}>
            {saving && <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Saving…</span>}
            {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
          </div>
        </>
      )}
    </div>
  );
}
