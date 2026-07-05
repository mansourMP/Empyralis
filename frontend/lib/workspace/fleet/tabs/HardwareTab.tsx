"use client";

import Link from "next/link";
import { Lock } from "lucide-react";

import type { FleetAgent } from "../fleet-data";

const ACCESS_LABELS: Record<string, string> = {
  none: "No hardware access",
  gateway: "Paired computer (gateway)",
  vps: "Cloud VPS",
  all: "Full hardware access",
};

/**
 * HARDWARE tab — the agent's hardware access policy and runtime placement.
 * Makes the knowledge-preset lock visible: a locked agent can't be granted
 * hardware without changing its preset (enforced server-side in Phase 5B).
 */
export function HardwareTab({
  workspaceId,
  agentId,
  agent,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
}) {
  const access = (agent?.hardware_access || "none").toLowerCase();
  const locked = !!agent?.hardware_access_locked;
  const preset = (agent?.capability_preset || "").toLowerCase();
  const status = agent?.hardware_status || "unknown";

  return (
    <div className="fleet-detail-pad fleet-hw">
      <div className="fleet-detail-section-title">Hardware access</div>
      <div className="fleet-hw-card">
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Access level</span>
          <span className="fleet-hw-value">
            {ACCESS_LABELS[access] || access}
            {locked && (
              <span className="fleet-badge fleet-badge--lock">
                <Lock size={11} strokeWidth={2} /> Locked
              </span>
            )}
          </span>
        </div>
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Runtime</span>
          <span className="fleet-hw-value">{agent?.runtime_target || "cloud"}</span>
        </div>
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Computer status</span>
          <span className="fleet-hw-value">
            <span className={`fleet-detail-dot ${status === "online" ? "is-online" : status === "offline" ? "is-offline" : ""}`} />
            {status}
          </span>
        </div>
      </div>

      {locked ? (
        <div className="fleet-hw-note">
          This is a <strong>Knowledge</strong> agent — hardware access is off and
          policy-locked. To grant hardware, change its capability preset on the
          Model tab (a knowledge agent can’t be given hardware directly).
        </div>
      ) : access === "none" ? (
        <div className="fleet-hw-note">
          This agent runs in the cloud with no computer access. Pair a computer on
          the{" "}
          <Link href={`/w/${encodeURIComponent(workspaceId)}/hardware`} className="fleet-link">
            Hardware
          </Link>{" "}
          page, then grant it here.
        </div>
      ) : (
        <div className="fleet-hw-note">
          Manage paired computers on the{" "}
          <Link href={`/w/${encodeURIComponent(workspaceId)}/hardware`} className="fleet-link">
            Hardware
          </Link>{" "}
          page.
        </div>
      )}
    </div>
  );
}
