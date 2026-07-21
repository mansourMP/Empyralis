"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Cpu, MemoryStick, MoreHorizontal, Server, Terminal } from "lucide-react";

import { GatewayPairPanel } from "@/lib/gateway/GatewayPairPanel";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ConfirmDialog } from "@/lib/ui/confirm-dialog";
import { PlatformNotification } from "@/lib/ui/platform-notification";
import { StatusChip, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { formatDateTime } from "@/lib/workspace/fleet/fleet-presentation";
import { HardwareRenameField } from "@/lib/workspace/fleet/hardware-rename-field";
import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import {
  connectionPresentation,
  type FleetGateway,
  type GatewayResources,
} from "@/lib/workspace/fleet/gateway-box-picker";
import { CountryFlag, resolveRegionCountry } from "@/lib/workspace/geo/country-flag";
import {
  CLOUD_VPS_PROVIDERS,
  CLOUD_VPS_PROVIDER_IDS,
  CloudVpsSetupPanel,
  type VpsOAuthResumePayload,
  type VpsProviderId,
} from "@/lib/workspace/cloud-vps-setup-panel";
import { SshServerConnectPanel } from "@/lib/workspace/ssh-server-connect-panel";

// Reuse the real gateway shape (gateway-box-picker.tsx's FleetGateway) instead
// of a hand-trimmed local type — the enrichment below needs hardware_provider/
// hardware_region/gateway_version/metadata, all already defined there and
// already present on every /api/gateway/registrations item.
type Registration = FleetGateway;

// providerId -> display label. CLOUD_VPS_PROVIDERS only covers the three
// providers the "Connect a cloud server" grid currently offers (digitalocean/
// google/aws); hetzner/vultr are real backend-recognized providers too (see
// gateway_registry_service.py's _CLOUD_PROVIDER_LABELS) that a box can carry
// from an older/manual pairing even though they're not offered in this UI
// today. Falling back to a title-cased raw id (mirrors that same backend
// fallback) rather than a blank keeps every real provider honestly labeled.
const CLOUD_PROVIDER_LABELS: Record<string, string> = {
  digitalocean: "DigitalOcean",
  hetzner: "Hetzner",
  vultr: "Vultr",
  aws: "AWS",
  google: "Google Cloud",
  gcp: "Google Cloud",
};

function cloudProviderLabel(providerId: string | null | undefined): string {
  const id = (providerId || "").trim().toLowerCase();
  if (!id) return "Cloud";
  return CLOUD_PROVIDER_LABELS[id] || id.charAt(0).toUpperCase() + id.slice(1);
}

/** hardware_label from the backend is already "{Provider} · {Region}"
 *  (_hardware_presentation in gateway_registry_service.py) — the Type
 *  column already shows the provider, so Location only needs the region
 *  half. Splitting it off the combined label avoids re-deriving region text
 *  from scratch while not repeating "DigitalOcean" twice on the same row.
 *  Falls back to the raw hardware_region id, then to the full label, and
 *  only says "Unknown region" when there's truly nothing to show. */
function regionOnlyLabel(hardwareLabel: string | null | undefined, hardwareRegion: string | null | undefined): string {
  const label = (hardwareLabel || "").trim();
  const sepIndex = label.indexOf(" · ");
  if (sepIndex >= 0) {
    const region = label.slice(sepIndex + 3).trim();
    if (region) return region;
  }
  return (hardwareRegion || "").trim() || label || "Unknown region";
}

/** True for a genuinely local machine — the Gateway's own handshake reports
 *  its real OS/arch (e.g. "darwin-arm64"), unlike the SSH-pairing flow which
 *  hardcodes platform to the literal string "linux" for any remote box (see
 *  routes_gateway.py's create_gateway_ssh_pairing). Both share
 *  hardware_kind "personal_device" on the backend, so platform is the only
 *  honest signal that distinguishes an owner's own Mac from a remote SSH
 *  server — never label the latter "Local computer". */
function isDarwinLocal(r: Registration): boolean {
  return String(r.platform || "").trim().toLowerCase().startsWith("darwin");
}

/** Non-cloud location text. A real local Mac has no `metadata.host` — falls
 *  through to "Local". An SSH-paired remote box DOES carry the host it was
 *  paired with (metadata.host, set by create_gateway_ssh_pairing) — showing
 *  that real host instead of a blanket "Local" avoids mislabeling a remote
 *  machine as local hardware. */
function localDeviceLocationText(r: Registration): string {
  const host = typeof r.metadata?.host === "string" ? r.metadata.host.trim() : "";
  return host || "Local";
}

function gatewayResourcesOf(r: Registration): GatewayResources | null | undefined {
  return r.metadata?.resources ?? r.resources;
}

/** Tiny optional CPU/mem readout — only renders the metrics this box
 *  actually reported (mirrors buildGaugeCards in the machine-detail page);
 *  a box on an older build with no `resources` at all renders nothing. */
function HardwareResourceChip({ resources }: { resources: GatewayResources | null | undefined }) {
  if (!resources) return null;
  const cpuOk = typeof resources.cpu_pct === "number" && Number.isFinite(resources.cpu_pct);
  const memOk =
    typeof resources.memory_used_bytes === "number" && Number.isFinite(resources.memory_used_bytes) &&
    typeof resources.memory_total_bytes === "number" && (resources.memory_total_bytes as number) > 0;
  if (!cpuOk && !memOk) return null;
  return (
    <span className="fleet-hw-list-resource-chip" title="Live resource usage">
      {cpuOk && (
        <span className="fleet-hw-list-resource-item">
          <Cpu size={11} strokeWidth={1.75} />
          {Math.round(Math.max(0, Math.min(100, resources.cpu_pct as number)))}%
        </span>
      )}
      {memOk && (
        <span className="fleet-hw-list-resource-item">
          <MemoryStick size={11} strokeWidth={1.75} />
          {Math.round(((resources.memory_used_bytes as number) / (resources.memory_total_bytes as number)) * 100)}%
        </span>
      )}
    </span>
  );
}

export default function HardwarePage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const [regs, setRegs] = useState<Registration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Same fleet-agents fetch every other Fleet surface shares (fleet-data.ts's
  // shared-poll cache dedups this against Agents/Projects/PrimaryRail, so
  // this list doesn't add a new network request) — read here only for the
  // per-row "N agents" count below (agent.preferred_gateway_id === this
  // gateway's id), the same binding the machine detail page uses.
  const { agents } = useFleetAgents(workspaceId);
  const [vpsPanelOpen, setVpsPanelOpen] = useState(false);
  const [vpsInitialProvider, setVpsInitialProvider] = useState<VpsProviderId | null>(null);
  const [vpsOAuthResumePayload, setVpsOAuthResumePayload] = useState<VpsOAuthResumePayload | null>(null);
  const [sshPanelOpen, setSshPanelOpen] = useState(false);
  const [showManualPairing, setShowManualPairing] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [pendingRemove, setPendingRemove] = useState<{ gatewayId: string; label: string } | null>(null);
  // Cloud servers only: full destroy (tear down the provider droplet + revoke),
  // distinct from pendingRemove/Disconnect which only revokes the pairing and
  // leaves the droplet running (and billing) at the provider.
  const [pendingDelete, setPendingDelete] = useState<{ gatewayId: string; vpsId: string; label: string; provider: string } | null>(null);
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

  // Resumes the DigitalOcean/Google cloud-VPS OAuth wizard when the browser
  // lands back here after the OAuth round-trip: CloudVpsSetupPanel now
  // navigates this same tab straight to the provider's authorize page (no
  // popup, no window.postMessage — see startDigitalOceanOAuth /
  // startGoogleOAuth in cloud-vps-setup-panel.tsx), and the backend's
  // callback redirects back here with the result in the query string — see
  // _vps_oauth_hardware_redirect_url in routes_gateway.py. Runs once on
  // mount; strips the query string afterward so a refresh or back
  // navigation doesn't replay the same OAuth result into the panel again.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const providerParam = params.get("vps_oauth_provider");
    const matchedProvider = CLOUD_VPS_PROVIDER_IDS.find((id) => id === providerParam);
    if (!matchedProvider) return;
    const errorMessage = params.get("vps_oauth_error");
    const connectedProvider = params.get("vps_oauth");
    if (!errorMessage && !connectedProvider) return;
    setVpsInitialProvider(matchedProvider);
    setVpsOAuthResumePayload({
      provider: matchedProvider,
      error: errorMessage || undefined,
      tokenId: params.get("token_id") || undefined,
      setupId: params.get("setup_id") || undefined,
    });
    setVpsPanelOpen(true);
    router.replace(`/w/${encodeURIComponent(workspaceId)}/hardware`, { scroll: false });
    // Mount-only: reads window.location.search once, deliberately not
    // re-run when workspaceId/router identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // Cloud servers only: destroy the provider droplet (stops billing) via the
  // VPS teardown endpoint, then revoke the paired gateway registration so the
  // row clears. vps_id is threaded onto the registration metadata by
  // vps_provisioning_service. Only ever runs from the delete confirm dialog.
  const confirmDelete = async () => {
    const target = pendingDelete;
    if (!target) return;
    setRemovingId(target.gatewayId);
    try {
      if (target.vpsId) {
        const res = await fetch(`/api/hardware/vps/${encodeURIComponent(target.vpsId)}`, {
          method: "DELETE",
          credentials: "include",
          headers: buildCookieAuthHeaders("DELETE"),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      }
      // Revoke the pairing too so the row disappears; best-effort (the droplet
      // is already gone, which is the destructive part the user asked for).
      await fetch(`/api/gateway/registrations/${encodeURIComponent(target.gatewayId)}/revoke`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ reason: "server_deleted_from_hardware_page" }),
      }).catch(() => undefined);
      setPendingDelete(null);
      setDisconnectNotice(`${target.label} deleted`);
      await loadRegistrations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete that server");
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

  // agent.preferred_gateway_id -> count of agents pinned to that box — the
  // same binding the machine detail page filters on (boundAgents there).
  // Keyed by gateway id so the row below is a plain map lookup.
  const agentCountByGateway = useMemo(() => {
    const map = new Map<string, number>();
    for (const a of agents) {
      const gw = String(a.preferred_gateway_id || "").trim();
      if (!gw) continue;
      map.set(gw, (map.get(gw) || 0) + 1);
    }
    return map;
  }, [agents]);

  const renderRow = (r: Registration) => {
    const gatewayId = String(r.gateway_id || r.id || "");
    const isCloud = r.hardware_kind === "cloud_vps";
    const isLocalMac = !isCloud && isDarwinLocal(r);
    const presentation = connectionPresentation(r);
    const detailHref = `/w/${encodeURIComponent(workspaceId)}/hardware/${encodeURIComponent(gatewayId)}`;
    const typeLabel = isCloud ? `Cloud · ${cloudProviderLabel(r.hardware_provider)}` : isLocalMac ? "Local computer" : "Computer";
    const flagCode = isCloud
      ? resolveRegionCountry(r.hardware_provider, r.hardware_region || "", r.hardware_label || r.hardware_region || "")
      : "";
    const locationText = isCloud ? regionOnlyLabel(r.hardware_label, r.hardware_region) : localDeviceLocationText(r);
    const agentCount = agentCountByGateway.get(gatewayId) || 0;
    const agentsText = agentCount === 0 ? "No agents" : `${agentCount} agent${agentCount === 1 ? "" : "s"}`;
    const resources = gatewayResourcesOf(r);
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
          {/* Type/what-it-is line: "Cloud · DigitalOcean" or "Local computer",
              plus the raw platform (e.g. darwin-arm64) as a secondary detail —
              real backend fields only, never fabricated. */}
          <span className="fleet-hw-list-type">
            {typeLabel}
            {r.platform ? ` · ${r.platform}` : ""}
          </span>
          {/* Location + agent count + last-seen line. Cloud boxes get a
              country flag (resolveRegionCountry returns "" — no flag — when
              the region can't be resolved, never a wrong guess); local
              machines show "Local" with no flag. */}
          <span className="fleet-list-row-desc" style={{ display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "normal" }}>
            {flagCode ? <CountryFlag code={flagCode} size={12} /> : null}
            <span>{locationText}</span>
            <span>· {agentsText}</span>
            {r.last_seen_at ? <span>· last seen {formatDateTime(r.last_seen_at)}</span> : null}
          </span>
        </span>
        <span className="fleet-list-row-meta" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <HardwareResourceChip resources={resources} />
          {r.gateway_version ? (
            <span className="fleet-hw-list-version" title={`Gateway v${r.gateway_version}`}>
              v{r.gateway_version}
            </span>
          ) : null}
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
            canDelete={isCloud}
            onDelete={() =>
              setPendingDelete({
                gatewayId,
                vpsId: String((r.metadata as Record<string, unknown> | undefined)?.vps_id || ""),
                label: r.display_name || r.hardware_label || r.platform || "this server",
                provider: cloudProviderLabel(r.hardware_provider) || "the provider",
              })
            }
          />
        </span>
      </div>
    );
  };

  return (
    <main className="fleet-content fleet-content--wide">
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
        initialOAuthResult={vpsOAuthResumePayload}
        onOAuthResultConsumed={() => setVpsOAuthResumePayload(null)}
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

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this server?"
        body={`"${pendingDelete?.label}" will be permanently destroyed at ${pendingDelete?.provider} — the server is torn down and its billing stops. Any data on it is gone. This can't be undone.`}
        confirmLabel="Delete server"
        confirmTone="danger"
        busy={removingId !== null}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
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
  onDelete,
  canDelete,
  disabled,
  removing,
}: {
  label: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
  onDisconnect: () => void;
  onDelete?: () => void;
  canDelete?: boolean;
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
          {canDelete && onDelete ? (
            <button
              type="button"
              role="menuitem"
              className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
              onClick={(e) => {
                e.stopPropagation();
                onOpenChange(false);
                onDelete();
              }}
            >
              Delete server
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
