"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Check, Copy, Cpu, Loader2, Server } from "lucide-react";

import { StatusChip, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { deriveStatus, formatDateTime, timeAgo, type AgentStatusTone } from "@/lib/workspace/fleet/fleet-presentation";
import { HardwareRenameField } from "@/lib/workspace/fleet/hardware-rename-field";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import {
  gatewayId as idOf,
  gatewayLabel,
  gatewayIsOnline,
  gatewayRuntimeState,
  runtimeStateLabel,
  runtimeStateTone,
  RUNTIME_LABELS,
  useWorkspaceGateways,
  type FleetGateway,
  type ServiceInventoryItem,
  type RuntimeState,
} from "@/lib/workspace/fleet/gateway-box-picker";

/** Non-subscription capabilities read straight off the raw heartbeat
 *  inventory (metadata.service_inventory) — already flowing in the same
 *  /gateway/registrations response the box picker uses, just never rendered
 *  anywhere before this page. claude_cli/codex_cli are handled separately
 *  below via gatewayRuntimeState, since their generic `status` field can't
 *  by itself tell "missing" apart from "installed, not signed in". */
const CAPABILITY_ORDER = ["claude_cli", "codex_cli", "docker", "ollama", "postgres", "gpu"] as const;
const CAPABILITY_LABEL: Record<string, string> = {
  claude_cli: "Claude Code",
  codex_cli: "Codex",
  docker: "Docker",
  ollama: "Ollama",
  postgres: "PostgreSQL",
  gpu: "GPU",
};

function serviceItemPresentation(status: string | undefined): { tone: AgentStatusTone; label: string } {
  const s = (status || "").toLowerCase();
  if (s === "ready") return { tone: "ready", label: "Ready" };
  if (s === "degraded") return { tone: "degraded", label: "Degraded" };
  if (s === "missing" || s === "offline") return { tone: "unknown", label: "Not detected" };
  return { tone: "unknown", label: "Unknown" };
}

function connectionPresentation(status: string): { tone: AgentStatusTone; label: string } {
  const s = status.toLowerCase();
  if (s === "online") return { tone: "online", label: "Online" };
  if (s === "degraded") return { tone: "degraded", label: "Degraded" };
  if (s === "reconnecting") return { tone: "degraded", label: "Reconnecting" };
  if (s === "revoked") return { tone: "error", label: "Revoked" };
  return { tone: "offline", label: "Offline" };
}

/** The exact remediation commands this product already tells owners to run
 *  elsewhere (server_modules/platform_event.py's CLI_SUBSCRIPTION_* errors),
 *  chosen here specifically because they don't need a local GUI browser —
 *  the realistic case for a box managed remotely over SSH: setup-token and
 *  device-auth both avoid the localhost-callback OAuth path that needs a
 *  browser ON this same machine. */
const INSTALL_COMMAND: Record<"claude_code" | "codex", string> = {
  claude_code: "npm install -g @anthropic-ai/claude-code",
  codex: "npm install -g @openai/codex",
};
const LOGIN_COMMAND: Record<"claude_code" | "codex", string> = {
  claude_code: "claude setup-token",
  codex: "codex login --device-auth",
};

function CopyableCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API can be unavailable — the command is still selectable
      // text in the box below either way.
    }
  }, [command]);
  return (
    <div className="gw-pair-panel-row" style={{ marginTop: 6, alignItems: "stretch" }}>
      <pre className="gw-pair-panel-command" style={{ flex: 1, margin: 0 }}>
        <code>{command}</code>
      </pre>
      <button type="button" className="fleet-btn" onClick={handleCopy}>
        {copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

const VERIFY_POLL_MS = 5_000;
const VERIFY_TIMEOUT_MS = 90_000;

/** Guided install + sign-in for one subscription CLI, plus the verify loop —
 *  the honest, manual fallback this build ships (no automated one-click
 *  login; that needs a new Gateway session primitive, out of scope here).
 *  Renders nothing once `state` is "ready" — the same `gateways` data this
 *  reads flows from the parent's useWorkspaceGateways, so a successful
 *  verify's refresh() naturally re-renders this away without a separate
 *  "verified" callback. */
function CliSetupGuidance({
  runtime,
  state,
  gatewayId,
  refresh,
}: {
  runtime: "claude_code" | "codex";
  state: RuntimeState;
  gatewayId: string;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
}) {
  const label = RUNTIME_LABELS[runtime];
  const [polling, setPolling] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  // A verify that's already succeeded (state flipped to "ready" via the
  // parent's own poll or a real heartbeat) should stop any timer still
  // running rather than let it spin to its own timeout.
  useEffect(() => {
    if (state === "ready" && pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
      setPolling(false);
    }
  }, [state]);

  const startVerify = useCallback(() => {
    setTimedOut(false);
    setPolling(true);
    const startedAt = Date.now();
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      if (Date.now() - startedAt > VERIFY_TIMEOUT_MS) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        pollRef.current = null;
        setPolling(false);
        setTimedOut(true);
        return;
      }
      const list = await refresh({ silent: true });
      const match = list.find((g) => idOf(g) === gatewayId);
      if (match && gatewayRuntimeState(match, runtime) === "ready") {
        if (pollRef.current) window.clearInterval(pollRef.current);
        pollRef.current = null;
        setPolling(false);
      }
    }, VERIFY_POLL_MS);
  }, [gatewayId, runtime, refresh]);

  if (state === "ready") return null;

  return (
    <div className="fleet-hw-note" style={{ marginTop: 8, paddingTop: 10, borderTop: "1px solid var(--border)", width: "100%" }}>
      {state === "missing" ? (
        <>
          <p style={{ margin: "0 0 4px" }}>Install {label} on this computer, over your own SSH session:</p>
          <CopyableCommand command={INSTALL_COMMAND[runtime]} />
          <p style={{ margin: "10px 0 4px" }}>Then sign in:</p>
          <CopyableCommand command={LOGIN_COMMAND[runtime]} />
        </>
      ) : (
        <>
          <p style={{ margin: "0 0 4px" }}>{label} is installed but not signed in. Run this on the computer:</p>
          <CopyableCommand command={LOGIN_COMMAND[runtime]} />
        </>
      )}
      {runtime === "claude_code" ? (
        <p style={{ margin: "10px 0 0" }}>
          This prints a token — export it as <code className="fleet-md-code">CLAUDE_CODE_OAUTH_TOKEN</code> in
          the same environment the Gateway process runs in, then restart the Gateway. If it can't open a
          browser directly, it prints a URL to open elsewhere and a code to paste back here.
        </p>
      ) : (
        <p style={{ margin: "10px 0 0" }}>
          Open the URL it prints, on any device, and enter the code it shows — nothing needs to be typed
          back into this computer's terminal.
        </p>
      )}
      <p style={{ margin: "8px 0 0", color: "var(--text-muted)" }}>
        Empyralis never sees or stores this credential — the CLI reads its own login directly on this
        computer, the same way it would if you were sitting at it.
      </p>
      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={startVerify} disabled={polling}>
          {polling ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {polling ? "Verifying…" : "Verify"}
        </button>
        {timedOut && (
          <span className="fleet-channel-expand-error" style={{ margin: 0 }}>
            Still not detected — this box heartbeats roughly every 20s, so a real change should show up
            within a couple of tries. Run{" "}
            <code className="fleet-md-code">{runtime === "claude_code" ? "claude /status" : "codex login status"}</code>{" "}
            on the computer to confirm it worked there, then verify again.
          </span>
        )}
      </div>
    </div>
  );
}

export default function GatewayDetailPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const targetGatewayId = String(params?.gatewayId || "");
  const { gateways, loading, refresh } = useWorkspaceGateways(workspaceId);
  const { agents, loading: agentsLoading } = useFleetAgents(workspaceId);

  const gateway = useMemo(() => gateways.find((g) => idOf(g) === targetGatewayId), [gateways, targetGatewayId]);
  useBreadcrumbLabel(targetGatewayId, gateway ? gatewayLabel(gateway) : null);
  const boundAgents = useMemo(
    () => agents.filter((a) => (a.preferred_gateway_id || "") === targetGatewayId),
    [agents, targetGatewayId],
  );

  const backHref = `/w/${encodeURIComponent(workspaceId)}/hardware`;
  const backLink = (
    <Link href={backHref} className="fleet-secondary-toggle" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <ArrowLeft size={14} strokeWidth={1.75} /> Back to Hardware
    </Link>
  );

  if (loading) {
    return (
      <main className="fleet-content">
        {backLink}
        <div className="fleet-list" style={{ marginTop: 16 }}>
          <div className="fleet-list-row">
            <div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} />
          </div>
        </div>
      </main>
    );
  }

  if (!gateway) {
    return (
      <main className="fleet-content">
        {backLink}
        <div className="fleet-empty" style={{ marginTop: 20 }}>
          <div className="fleet-empty-title">This computer isn't in your workspace</div>
          <div className="fleet-empty-desc">It may have been removed, or the link is stale.</div>
        </div>
      </main>
    );
  }

  const isCloud = gateway.hardware_kind === "cloud_vps";
  const connStatus = (gateway.connection_status || (gatewayIsOnline(gateway) ? "online" : "offline")).toLowerCase();
  const connPresentation = connectionPresentation(connStatus);
  const heartbeatAge = gateway.heartbeat_age_seconds;
  const serviceInventory: ServiceInventoryItem[] = gateway.metadata?.service_inventory || [];
  const byId = new Map(serviceInventory.map((item) => [String(item.id || ""), item]));

  return (
    <main className="fleet-content">
      {backLink}

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
        <TintTile tint={isCloud ? "blue" : "teal"} size={36}>
          {isCloud ? <Server size={18} strokeWidth={1.75} /> : <Cpu size={18} strokeWidth={1.75} />}
        </TintTile>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <HardwareRenameField
            gatewayId={targetGatewayId}
            displayName={gatewayLabel(gateway)}
            onRenamed={() => void refresh()}
          />
          <span className="fleet-list-row-desc">
            {isCloud ? gateway.hardware_label || "Agent Computer" : gateway.platform || "unknown platform"}
          </span>
        </div>
      </div>

      <div className="fleet-detail-section-title" style={{ marginTop: 24 }}>Health</div>
      <div className="fleet-hw-card">
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Connection</span>
          <span className="fleet-hw-value">
            <StatusChip tone={connPresentation.tone} label={connPresentation.label} />
          </span>
        </div>
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Last heartbeat</span>
          <span className="fleet-hw-value">
            {typeof heartbeatAge === "number"
              ? heartbeatAge < 60 ? `${heartbeatAge}s ago` : timeAgo(gateway.last_heartbeat_at)
              : gateway.last_heartbeat_at ? timeAgo(gateway.last_heartbeat_at) : "—"}
          </span>
        </div>
        <div className="fleet-hw-row">
          <span className="fleet-hw-label">Paired since</span>
          <span className="fleet-hw-value">{gateway.created_at ? formatDateTime(gateway.created_at) : "—"}</span>
        </div>
        {gateway.runtime_access_label && (
          <div className="fleet-hw-row">
            <span className="fleet-hw-label">Shell access</span>
            <span className="fleet-hw-value">{gateway.runtime_access_label}</span>
          </div>
        )}
      </div>

      <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Capabilities</div>
      <div className="fleet-hw-card">
        {CAPABILITY_ORDER.map((id) => {
          const isCli = id === "claude_cli" || id === "codex_cli";
          if (isCli) {
            const runtime: "claude_code" | "codex" = id === "claude_cli" ? "claude_code" : "codex";
            const state = gatewayRuntimeState(gateway, runtime);
            return (
              <div
                className="fleet-hw-row"
                key={id}
                style={{ flexDirection: "column", alignItems: "stretch", justifyContent: "flex-start", gap: 4 }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                  <span className="fleet-hw-label">{CAPABILITY_LABEL[id]}</span>
                  <span className="fleet-hw-value">
                    <StatusChip tone={runtimeStateTone(state)} label={runtimeStateLabel(state)} />
                  </span>
                </div>
                <CliSetupGuidance runtime={runtime} state={state} gatewayId={targetGatewayId} refresh={refresh} />
              </div>
            );
          }
          const item = byId.get(id);
          const presentation = serviceItemPresentation(item?.status);
          return (
            <div className="fleet-hw-row" key={id}>
              <span className="fleet-hw-label">{CAPABILITY_LABEL[id]}</span>
              <span className="fleet-hw-value">
                <StatusChip tone={presentation.tone} label={presentation.label} />
              </span>
            </div>
          );
        })}
      </div>

      <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>
        Agents running here{boundAgents.length > 0 ? ` · ${boundAgents.length}` : ""}
      </div>
      {agentsLoading ? (
        <div className="fleet-list">
          <div className="fleet-list-row">
            <div className="fleet-skeleton-bar" style={{ width: "50%", height: 12 }} />
          </div>
        </div>
      ) : boundAgents.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No agents are pinned to this computer</div>
          <div className="fleet-empty-desc">
            Bind an agent to it from that agent's Hardware tab — this only lists agents whose "which
            computer" choice points here specifically, not every agent allowed to use any paired box.
          </div>
        </div>
      ) : (
        <div className="fleet-list">
          {boundAgents.map((a) => {
            const status = deriveStatus(a.hardware_status, a.stopped?.active, Boolean(a.current_run_id));
            return (
              <div key={a.agent_id} className="fleet-list-row" style={{ cursor: "default" }}>
                <span className="fleet-list-row-main">
                  <span className="fleet-list-row-title">{a.label || "Unnamed agent"}</span>
                  <span className="fleet-list-row-desc">{a.role}</span>
                </span>
                <span className="fleet-list-row-meta">
                  <StatusChip tone={status.tone} label={status.label} />
                </span>
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
