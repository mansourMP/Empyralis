"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Cpu, Server, Terminal, X } from "lucide-react";

import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { StatusChip, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import {
  CLOUD_VPS_PROVIDERS,
  CLOUD_VPS_PROVIDER_IDS,
  CloudVpsSetupPanel,
  type VpsProviderId,
} from "@/lib/workspace/cloud-vps-setup-panel";
import { SshServerConnectPanel } from "@/lib/workspace/ssh-server-connect-panel";

type Registration = {
  gateway_id?: string;
  id?: string;
  display_name?: string;
  platform?: string;
  status?: string;
  connection_status?: string;
  last_seen_at?: string;
  hardware_kind?: "cloud_vps" | "personal_device" | string;
  hardware_label?: string;
};

/** Click-to-rename a paired box's nickname. gatewayLabel() (used everywhere
 *  this registration is displayed — the Hardware tab's "Running on" header,
 *  box pickers, etc.) already prefers display_name over the derived
 *  "Provider · Region" label, so setting it here is the whole fix. */
function HardwareRenameField({
  gatewayId,
  displayName,
  onRenamed,
}: {
  gatewayId: string;
  displayName: string;
  onRenamed: (next: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!editing) setDraft(displayName);
  }, [displayName, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (!next || next === displayName) {
      setDraft(displayName);
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/rename`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ display_name: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEditing(false);
      onRenamed(next);
    } catch {
      setDraft(displayName); // roll back — the row still shows the last-saved name
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="fleet-list-row-title-input"
        value={draft}
        disabled={saving}
        maxLength={80}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setDraft(displayName); setEditing(false); }
        }}
      />
    );
  }

  return (
    <button
      type="button"
      className="fleet-list-row-title-edit"
      title="Rename this computer"
      onClick={(e) => { e.stopPropagation(); setEditing(true); }}
    >
      {displayName}
    </button>
  );
}

export default function HardwarePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const [regs, setRegs] = useState<Registration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [vpsPanelOpen, setVpsPanelOpen] = useState(false);
  const [vpsInitialProvider, setVpsInitialProvider] = useState<VpsProviderId | null>(null);
  const [sshPanelOpen, setSshPanelOpen] = useState(false);
  const [showManualPairing, setShowManualPairing] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const loadRegistrations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      const list = d?.items || d?.registrations || (Array.isArray(d) ? d : []);
      setRegs(Array.isArray(list) ? list : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load computers");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!cancelled) await loadRegistrations();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadRegistrations]);

  const isOnline = (r: Registration) => {
    const s = `${r.connection_status || ""} ${r.status || ""}`.toLowerCase();
    return s.includes("online") || s.includes("active") || s.includes("connected");
  };

  const openProviderPanel = (providerId: VpsProviderId) => {
    setVpsInitialProvider(providerId);
    setVpsPanelOpen(true);
  };

  const handleRemove = async (gatewayId: string) => {
    if (!gatewayId) return;
    setRemovingId(gatewayId);
    try {
      const res = await fetch(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/revoke`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ reason: "removed_from_hardware_page" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadRegistrations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove that computer");
    } finally {
      setRemovingId(null);
    }
  };

  const handleRenamed = (gatewayId: string, nextName: string) => {
    setRegs((current) =>
      current.map((r) =>
        String(r.gateway_id || r.id || "") === gatewayId ? { ...r, display_name: nextName } : r,
      ),
    );
  };

  const cloudServers = regs.filter((r) => r.hardware_kind === "cloud_vps");
  const devices = regs.filter((r) => r.hardware_kind !== "cloud_vps");

  const renderRow = (r: Registration) => {
    const gatewayId = String(r.gateway_id || r.id || "");
    const isCloud = r.hardware_kind === "cloud_vps";
    const online = isOnline(r);
    return (
      <div key={gatewayId} className="fleet-list-row" style={{ cursor: "default" }}>
        <TintTile tint={isCloud ? "blue" : "teal"}>
          {isCloud ? <Server size={15} strokeWidth={1.75} /> : <Cpu size={15} strokeWidth={1.75} />}
        </TintTile>
        <span className="fleet-list-row-main">
          <HardwareRenameField
            gatewayId={gatewayId}
            displayName={r.display_name || r.hardware_label || r.platform || gatewayId || "Computer"}
            onRenamed={(next) => handleRenamed(gatewayId, next)}
          />
          <span className="fleet-list-row-desc">
            {isCloud ? r.hardware_label || "Agent Computer" : r.platform || "unknown platform"}
            {r.last_seen_at ? ` · last seen ${new Date(r.last_seen_at).toLocaleString()}` : ""}
          </span>
        </span>
        <span className="fleet-list-row-meta" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <StatusChip tone={online ? "online" : "offline"} label={online ? "Online" : "Offline"} />
          <button
            type="button"
            className="fleet-list-row-remove"
            disabled={removingId === gatewayId}
            onClick={() => void handleRemove(gatewayId)}
            aria-label={`Remove ${r.display_name || r.hardware_label || "computer"}`}
          >
            {removingId === gatewayId ? "…" : <X size={14} strokeWidth={1.75} />}
          </button>
        </span>
      </div>
    );
  };

  return (
    <main className="fleet-content">
      {/* A box row already says everything — no right panel on Hardware (contract). */}
      <div className="fleet-detail-section-title">Connect a cloud server</div>
          <div className="fleet-provider-grid">
            {CLOUD_VPS_PROVIDER_IDS.map((providerId) => {
              const provider = CLOUD_VPS_PROVIDERS[providerId];
              return (
                <button
                  key={providerId}
                  type="button"
                  className="fleet-provider-card"
                  onClick={() => openProviderPanel(providerId)}
                >
                  <span className="fleet-provider-card-top">
                    <img src={provider.logoSrc} alt="" className="fleet-provider-card-logo" aria-hidden="true" />
                    <span className="fleet-provider-card-badge">{provider.accountMethod}</span>
                  </span>
                  <span className="fleet-provider-card-title">{provider.label}</span>
                  <span className="fleet-provider-card-desc">{provider.tagline}</span>
                </button>
              );
            })}
            <button type="button" className="fleet-provider-card" onClick={() => setSshPanelOpen(true)}>
              <span className="fleet-provider-card-top">
                <Terminal size={26} strokeWidth={1.5} aria-hidden="true" />
              </span>
              <span className="fleet-provider-card-title">Your own server</span>
              <span className="fleet-provider-card-desc">Connect over SSH — host, port, and a password or key.</span>
            </button>
          </div>

          {loading ? (
            <div className="fleet-list" style={{ marginTop: "var(--space-4)" }}>
              <div className="fleet-list-row">
                <div className="fleet-skeleton-bar" style={{ width: "35%", height: 12 }} />
              </div>
            </div>
          ) : error ? (
            <div className="fleet-page-state-body">{error}</div>
          ) : regs.length === 0 ? (
            <div className="fleet-empty">
              <div className="fleet-empty-title">No computers connected yet</div>
              <div className="fleet-empty-desc">Connect a cloud server above to give agents hardware access.</div>
            </div>
          ) : (
            <>
              {cloudServers.length > 0 && (
                <>
                  <div className="fleet-hw-group-title">Cloud servers</div>
                  <div className="fleet-list">{cloudServers.map(renderRow)}</div>
                </>
              )}
              {devices.length > 0 && (
                <>
                  <div className="fleet-hw-group-title">Your devices</div>
                  <div className="fleet-list">{devices.map(renderRow)}</div>
                </>
              )}
            </>
          )}

          <div style={{ marginTop: "var(--space-6)" }}>
            {showManualPairing ? (
              <>
                <div className="fleet-detail-section-title">Add your own computer</div>
                <GatewayPairPanel workspaceId={workspaceId} compact onPaired={() => void loadRegistrations()} />
              </>
            ) : (
              <button type="button" className="fleet-secondary-toggle" onClick={() => setShowManualPairing(true)}>
                Or add your own computer instead
              </button>
            )}
          </div>

      <CloudVpsSetupPanel
        open={vpsPanelOpen}
        workspaceId={workspaceId}
        initialProviderId={vpsInitialProvider}
        onClose={() => setVpsPanelOpen(false)}
        onConnected={async () => {
          setVpsPanelOpen(false);
          await loadRegistrations();
        }}
      />

      <SshServerConnectPanel
        open={sshPanelOpen}
        workspaceId={workspaceId}
        onClose={() => setSshPanelOpen(false)}
        onConnected={async () => {
          setSshPanelOpen(false);
          await loadRegistrations();
        }}
      />
    </main>
  );
}
