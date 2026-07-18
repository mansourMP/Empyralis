"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Cpu, MoreHorizontal, Server, Terminal } from "lucide-react";

import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ConfirmDialog } from "@/lib/ui/confirm-dialog";
import { PlatformNotification } from "@/lib/ui/platform-notification";
import { StatusChip, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { formatDateTime } from "@/lib/workspace/fleet/fleet-presentation";
import { HardwareRenameField } from "@/lib/workspace/fleet/hardware-rename-field";
import { connectionPresentation } from "@/lib/workspace/fleet/gateway-box-picker";
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

export default function HardwarePage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const [regs, setRegs] = useState<Registration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [vpsPanelOpen, setVpsPanelOpen] = useState(false);
  const [vpsInitialProvider, setVpsInitialProvider] = useState<VpsProviderId | null>(null);
  const [sshPanelOpen, setSshPanelOpen] = useState(false);
  const [showManualPairing, setShowManualPairing] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [pendingRemove, setPendingRemove] = useState<{ gatewayId: string; label: string } | null>(null);
  // Row-level "⋯" menu: one open at a time (opening a new row's menu closes
  // any other), and one row renaming at a time — both single shared values,
  // same convention as pendingRemove/removingId above.
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [disconnectNotice, setDisconnectNotice] = useState<string | null>(null);
  // "Latest call wins" — mirrors useWorkspaceGateways.refresh() in
  // gateway-box-picker.tsx. loadRegistrations is called from several places
  // (mount, pairing, VPS/SSH connect, remove) that can overlap; without this,
  // an older, slower in-flight fetch resolving after a newer one can stomp
  // good data with a stale (sometimes empty) list — the reported blank page.
  const requestIdRef = useRef(0);

  const loadRegistrations = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      const list = d?.items || d?.registrations || (Array.isArray(d) ? d : []);
      if (requestIdRef.current === requestId) setRegs(Array.isArray(list) ? list : []);
    } catch (e) {
      if (requestIdRef.current === requestId) setError(e instanceof Error ? e.message : "Could not load computers");
    } finally {
      if (requestIdRef.current === requestId) setLoading(false);
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

  const openProviderPanel = (providerId: VpsProviderId) => {
    setVpsInitialProvider(providerId);
    setVpsPanelOpen(true);
  };

  // Removing a device revokes it — the machine has to be paired from
  // scratch afterward, no undo. The X on the row only opens the confirm
  // dialog below; this is the function that actually revokes, and it only
  // ever runs from the dialog's Confirm button.
  const confirmRemove = async () => {
    const gatewayId = pendingRemove?.gatewayId;
    const label = pendingRemove?.label;
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
      setPendingRemove(null);
      setDisconnectNotice(`${label || "Computer"} disconnected`);
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
    const presentation = connectionPresentation(r);
    const detailHref = `/w/${encodeURIComponent(workspaceId)}/hardware/${encodeURIComponent(gatewayId)}`;
    return (
      <div
        key={gatewayId}
        className="fleet-list-row"
        style={{ cursor: "pointer" }}
        role="link"
        tabIndex={0}
        onClick={() => router.push(detailHref)}
        onKeyDown={(e) => {
          // Enter/Space bubbles up from nested interactive elements (the
          // menu trigger, its menu items, the rename input) — without this
          // guard, activating any of THEM also navigated the row, cancelling
          // their own Enter/Space handling. Only fire when the row itself is
          // the focused/keyed element.
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            router.push(detailHref);
          }
        }}
      >
        <TintTile tint={isCloud ? "blue" : "teal"}>
          {isCloud ? <Server size={15} strokeWidth={1.75} /> : <Cpu size={15} strokeWidth={1.75} />}
        </TintTile>
        <span className="fleet-list-row-main">
          <HardwareRenameField
            gatewayId={gatewayId}
            displayName={r.display_name || r.hardware_label || r.platform || gatewayId || "Computer"}
            onRenamed={(next) => handleRenamed(gatewayId, next)}
            editing={renamingId === gatewayId}
            onEditingChange={(next) => setRenamingId(next ? gatewayId : null)}
          />
          <span className="fleet-list-row-desc">
            {/* hardware_label ("Provider · Region"), not display_name — the
                title above already shows display_name via HardwareRenameField,
                so this line stays the underlying technical descriptor instead
                of repeating the same value twice. */}
            {isCloud ? r.hardware_label || "Agent Computer" : r.platform || "unknown platform"}
            {r.last_seen_at ? ` · last seen ${formatDateTime(r.last_seen_at)}` : ""}
          </span>
        </span>
        <span className="fleet-list-row-meta" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <StatusChip tone={presentation.tone} label={presentation.label} />
          <HardwareRowMenu
            label={r.display_name || r.hardware_label || r.platform || "computer"}
            isOpen={openMenuId === gatewayId}
            onOpenChange={(open) => setOpenMenuId(open ? gatewayId : null)}
            disabled={removingId === gatewayId}
            removing={removingId === gatewayId}
            onRename={() => setRenamingId(gatewayId)}
            onDisconnect={() =>
              setPendingRemove({
                gatewayId,
                label: r.display_name || r.hardware_label || r.platform || "this computer",
              })
            }
          />
        </span>
      </div>
    );
  };

  return (
    <main className="fleet-content">
      {/* Each box row opens its own machine-detail ROUTE (hardware/[gatewayId]),
          matching the Projects list -> project-detail pattern — a deliberate
          departure from this list's earlier "a box row already says
          everything, no right panel" note. That note ruled out a side PANEL
          on this page; a dedicated detail page is a different shape, not a
          reversal of it. */}
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

      <ConfirmDialog
        open={pendingRemove !== null}
        title="Remove this computer?"
        body={`"${pendingRemove?.label}" will be disconnected and revoked. Agents lose hardware access through it immediately, and it has to be paired from scratch to reconnect — this can't be undone.`}
        confirmLabel="Remove"
        confirmTone="danger"
        busy={removingId !== null}
        onConfirm={() => void confirmRemove()}
        onCancel={() => setPendingRemove(null)}
      />

      {disconnectNotice ? (
        <PlatformNotification
          tone="success"
          title="Computer disconnected"
          detail={disconnectNotice}
          onClose={() => setDisconnectNotice(null)}
        />
      ) : null}
    </main>
  );
}

// Per-row "⋯" actions menu — replaces the old bare "×" remove button +
// HardwareRenameField's own hover-pencil with one consistent trigger.
// Hand-rolled (no existing role="menu"/Popover/Dropdown primitive found
// under lib/ui or lib/workspace) but the outside-click + Escape handling
// mirrors FleetToolbar.tsx's identical popover pattern exactly.
function HardwareRowMenu({
  label,
  isOpen,
  onOpenChange,
  onRename,
  onDisconnect,
  disabled,
  removing,
}: {
  label: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
  onDisconnect: () => void;
  disabled?: boolean;
  removing?: boolean;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      onOpenChange(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen, onOpenChange]);

  return (
    <div className="fleet-list-row-menu-wrap" ref={ref}>
      <button
        type="button"
        className={`fleet-list-row-menu-trigger${isOpen ? " is-open" : ""}`}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label={`Actions for ${label}`}
        onClick={(e) => {
          e.stopPropagation();
          onOpenChange(!isOpen);
        }}
      >
        {removing ? "…" : <MoreHorizontal size={14} strokeWidth={1.75} />}
      </button>
      {isOpen && (
        <div className="fleet-list-row-menu" role="menu">
          <button
            type="button"
            role="menuitem"
            className="fleet-list-row-menu-item"
            onClick={(e) => {
              e.stopPropagation();
              onOpenChange(false);
              onRename();
            }}
          >
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
            onClick={(e) => {
              e.stopPropagation();
              onOpenChange(false);
              onDisconnect();
            }}
          >
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}
