"use client";

import { useState } from "react";
import { Play, Square, TriangleAlert } from "lucide-react";

import { resumeFleetWorkspace, stopFleetWorkspace, useFleetWorkspace } from "@/lib/workspace/fleet/fleet-data";
import { timeAgo } from "@/lib/workspace/fleet/fleet-presentation";

/** Workspace-wide emergency stop — kill_switch_gate's workspace:{id} key.
 *  Blocks every agent's turns immediately (checked before any LLM call, see
 *  agent_turn_runtime_service._run_sage_action_loop_v3). A confirm step
 *  guards the action itself; a real error path guards against silently
 *  believing it worked. Extracted from settings/page.tsx (was
 *  StopAllAgentsSection) unchanged when Settings moved to a grouped rail —
 *  now renders inside the "Workspace" section only. */
export function EmergencyStopSection({ workspaceId }: { workspaceId: string }) {
  const { workspace, loading, refresh } = useFleetWorkspace(workspaceId);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stopped = workspace?.stopped;

  async function handleConfirmStop() {
    setBusy(true);
    setError(null);
    const result = await stopFleetWorkspace(workspaceId, reason);
    setBusy(false);
    if (result.ok) {
      setConfirming(false);
      setReason("");
      await refresh();
    } else {
      setError(result.error || "Could not stop all agents.");
    }
  }

  async function handleResume() {
    setBusy(true);
    setError(null);
    const result = await resumeFleetWorkspace(workspaceId);
    setBusy(false);
    if (result.ok) await refresh();
    else setError(result.error || "Could not resume agents.");
  }

  return (
    <>
      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Emergency stop</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Immediately stops every agent in this workspace from replying, on every channel. Nothing is deleted — resume
        at any time to pick back up where they left off.
      </p>

      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}

      {loading ? (
        // Neither of this section's own states is a `.fleet-list-row` — it's
        // always either a bare button (the common case, not stopped) or a
        // bordered `.fleet-card`. Which one it resolves to can't be known
        // before the fetch returns (same ambiguity FleetBoardSkeleton's own
        // doc comment accepts for a board's real column contents), so this
        // matches the BUTTON shape — the default, far more common state —
        // rather than a container type (`.fleet-list-row`) neither state
        // actually uses.
        <div className="fleet-skeleton-bar" style={{ width: 150, height: 32, borderRadius: "var(--radius-control, 6px)" }} aria-busy="true" aria-label="Loading" />
      ) : stopped?.active ? (
        <div className="fleet-card" style={{ borderColor: "var(--accent)", padding: "var(--space-3)" }}>
          <div className="fleet-stop-chip" style={{ marginBottom: 8 }}>
            <Square size={12} strokeWidth={2} />
            All agents stopped
          </div>
          <div className="fleet-list-row-desc">
            Stopped by {stopped.stopped_by_label || "an owner"}
            {stopped.at ? ` · ${timeAgo(stopped.at)}` : ""}
            {stopped.reason ? ` · "${stopped.reason}"` : ""}
          </div>
          <button type="button" className="fleet-btn" style={{ marginTop: 12 }} disabled={busy} onClick={handleResume}>
            <Play size={14} strokeWidth={1.75} /> Resume all agents
          </button>
        </div>
      ) : confirming ? (
        <div className="fleet-card" style={{ borderColor: "var(--offline-text)", padding: "var(--space-3)" }}>
          <div className="fleet-list-row-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <TriangleAlert size={15} strokeWidth={1.75} />
            Stop every agent in this workspace?
          </div>
          <p className="fleet-list-row-desc" style={{ marginTop: 4 }}>
            No one will get a reply from any agent here — on any channel — until you resume.
          </p>
          <input
            className="fleet-wizard-input"
            placeholder="Reason (optional, visible in the activity log)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ marginTop: 10 }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="fleet-btn" disabled={busy} onClick={() => { setConfirming(false); setReason(""); }}>
              Cancel
            </button>
            <button type="button" className="fleet-btn fleet-btn--accent" disabled={busy} onClick={handleConfirmStop}>
              {busy ? "Stopping…" : "Confirm stop"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="fleet-btn" onClick={() => setConfirming(true)}>
          <Square size={14} strokeWidth={1.75} /> Stop all agents
        </button>
      )}
    </>
  );
}
