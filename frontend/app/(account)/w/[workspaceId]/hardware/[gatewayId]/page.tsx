"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Cpu, Loader2, Server } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { StatusChip, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { deriveStatus, formatDateTime, timeAgo, type AgentStatusTone } from "@/lib/workspace/fleet/fleet-presentation";
import { HardwareRenameField } from "@/lib/workspace/fleet/hardware-rename-field";
import { useBreadcrumbLabel } from "@/lib/workspace/fleet/Breadcrumbs";
import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import {
  gatewayId as idOf,
  gatewayLabel,
  connectionPresentation,
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

const VERIFY_POLL_MS = 5_000;
const VERIFY_TIMEOUT_MS = 90_000;
const LOGIN_EVENTS_POLL_MS = 2_000;

function createRunId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `run_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

async function postCliAction(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}) as Record<string, unknown>);
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

type CliLoginOutputPayload = {
  run_id?: string;
  event?: "output" | "done";
  kind?: "url" | "code_prompt";
  text?: string;
  ok?: boolean;
  error?: string;
  error_kind?: string;
};

/** The async `done` event's error_kind is a raw Gateway-side signal (not run
 *  through the backend's platform-voice classifier the way a synchronous
 *  POST failure is) — this is the one place that needs its own honest,
 *  typed translation. */
function friendlyLoginFailure(errorKind: string | undefined, error: string | undefined, label: string): string {
  switch (errorKind) {
    case "not_installed":
      return `${label} isn't installed on this computer yet — install it first, then sign in.`;
    case "timeout":
      return "Sign-in timed out before it was approved. Try again.";
    case "cancelled":
      return "Sign-in was cancelled.";
    case "crash":
      return `${label} sign-in exited unexpectedly on this computer. Try again.`;
    default:
      return error || `${label} sign-in failed unexpectedly. Try again.`;
  }
}

type CliInstallState = "idle" | "installing" | "error";
type CliLoginPhase = "idle" | "starting" | "active" | "submitting" | "cancelling" | "error";

/** Real install + sign-in for one subscription CLI, wired to Build F's
 *  cli.install / cli.login.* routes — replaces the SSH copy-paste guidance
 *  this build shipped before. Reuses the same verify-poll pattern (refresh()
 *  on an interval, checking gatewayRuntimeState) to detect the box's own
 *  state catching up to a completed install or sign-in; a login run ALSO
 *  polls its own /cli/login/{run_id}/events endpoint for the URL/code the
 *  vendor's CLI prints, since that's a separate, faster-moving async stream
 *  from the box's heartbeat-driven runtime state. Renders nothing once
 *  `state` is "ready" — the same `gateways` data this reads flows from the
 *  parent's useWorkspaceGateways, so a successful verify's refresh()
 *  naturally re-renders this away. */
function CliSetupControl({
  runtime,
  state,
  gatewayId,
  workspaceId,
  refresh,
}: {
  runtime: "claude_code" | "codex";
  state: RuntimeState;
  gatewayId: string;
  workspaceId: string;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
}) {
  const label = RUNTIME_LABELS[runtime];

  // ---- shared: poll the box's own reported state until it moves past its
  // current one (install: past "missing"; sign-in: all the way to "ready") ----
  const [verifying, setVerifying] = useState(false);
  const [verifyTimedOut, setVerifyTimedOut] = useState(false);
  const verifyPollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (verifyPollRef.current) window.clearInterval(verifyPollRef.current);
    };
  }, []);

  useEffect(() => {
    if (state === "ready" && verifyPollRef.current) {
      window.clearInterval(verifyPollRef.current);
      verifyPollRef.current = null;
      setVerifying(false);
    }
  }, [state]);

  const verifyUntil = useCallback(
    (predicate: (s: RuntimeState) => boolean) => {
      setVerifyTimedOut(false);
      setVerifying(true);
      const startedAt = Date.now();
      if (verifyPollRef.current) window.clearInterval(verifyPollRef.current);
      verifyPollRef.current = window.setInterval(async () => {
        if (Date.now() - startedAt > VERIFY_TIMEOUT_MS) {
          if (verifyPollRef.current) window.clearInterval(verifyPollRef.current);
          verifyPollRef.current = null;
          setVerifying(false);
          setVerifyTimedOut(true);
          return;
        }
        const list = await refresh({ silent: true });
        const match = list.find((g) => idOf(g) === gatewayId);
        if (match && predicate(gatewayRuntimeState(match, runtime))) {
          if (verifyPollRef.current) window.clearInterval(verifyPollRef.current);
          verifyPollRef.current = null;
          setVerifying(false);
        }
      }, VERIFY_POLL_MS);
    },
    [gatewayId, runtime, refresh],
  );

  // ---- install ----
  const [installState, setInstallState] = useState<CliInstallState>("idle");
  const [installError, setInstallError] = useState<string | null>(null);

  const runInstall = useCallback(async () => {
    setInstallState("installing");
    setInstallError(null);
    try {
      await postCliAction(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/install`, {
        runtime,
        run_id: createRunId(),
        workspace_id: workspaceId,
      });
      setInstallState("idle");
      verifyUntil((s) => s !== "missing");
    } catch (err) {
      setInstallState("error");
      setInstallError(err instanceof Error ? err.message : "Install failed.");
    }
  }, [gatewayId, runtime, workspaceId, verifyUntil]);

  // ---- sign-in ----
  const [loginPhase, setLoginPhase] = useState<CliLoginPhase>("idle");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [urlText, setUrlText] = useState<string | null>(null);
  const [codePromptText, setCodePromptText] = useState<string | null>(null);
  const [codeInput, setCodeInput] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const eventsPollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (eventsPollRef.current) window.clearInterval(eventsPollRef.current);
    };
  }, []);

  const stopEventsPoll = useCallback(() => {
    if (eventsPollRef.current) {
      window.clearInterval(eventsPollRef.current);
      eventsPollRef.current = null;
    }
  }, []);

  const pollLoginEvents = useCallback(
    (runId: string) => {
      stopEventsPoll();
      eventsPollRef.current = window.setInterval(async () => {
        try {
          const res = await fetch(
            `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/${encodeURIComponent(runId)}/events`,
            { credentials: "include" },
          );
          if (!res.ok) return;
          const data = await res.json().catch(() => null);
          const items: Array<{ payload?: CliLoginOutputPayload }> = Array.isArray(data?.items) ? data.items : [];
          for (const item of items) {
            const payload = item?.payload || {};
            if (payload.event === "output" && payload.kind === "url" && payload.text) setUrlText(payload.text);
            if (payload.event === "output" && payload.kind === "code_prompt" && payload.text) setCodePromptText(payload.text);
            if (payload.event === "done") {
              stopEventsPoll();
              if (payload.ok) {
                setLoginPhase("idle");
                setActiveRunId(null);
                verifyUntil((s) => s === "ready");
              } else {
                setLoginPhase("error");
                setLoginError(friendlyLoginFailure(payload.error_kind, payload.error, label));
              }
            }
          }
        } catch {
          // a transient poll failure isn't fatal — the next tick tries again
        }
      }, LOGIN_EVENTS_POLL_MS);
    },
    [gatewayId, stopEventsPoll, verifyUntil, label],
  );

  const startLogin = useCallback(async () => {
    setLoginPhase("starting");
    setLoginError(null);
    setUrlText(null);
    setCodePromptText(null);
    setCodeInput("");
    setSubmitError(null);
    const runId = createRunId();
    try {
      await postCliAction(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/start`, {
        runtime,
        run_id: runId,
        workspace_id: workspaceId,
      });
      setActiveRunId(runId);
      setLoginPhase("active");
      pollLoginEvents(runId);
    } catch (err) {
      setLoginPhase("error");
      setLoginError(err instanceof Error ? err.message : "Sign-in failed to start.");
    }
  }, [gatewayId, runtime, workspaceId, pollLoginEvents]);

  const submitCode = useCallback(async () => {
    const code = codeInput.trim();
    if (!activeRunId || !code) return;
    setLoginPhase("submitting");
    setSubmitError(null);
    try {
      await postCliAction(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/${encodeURIComponent(activeRunId)}/input`,
        { code, workspace_id: workspaceId },
      );
      setCodeInput("");
      setLoginPhase("active");
    } catch (err) {
      setLoginPhase("active");
      setSubmitError(err instanceof Error ? err.message : "Couldn't submit that code — try again.");
    }
  }, [gatewayId, activeRunId, codeInput, workspaceId]);

  const cancelLogin = useCallback(async () => {
    if (!activeRunId) return;
    stopEventsPoll();
    setLoginPhase("cancelling");
    try {
      await postCliAction(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/${encodeURIComponent(activeRunId)}/cancel`,
        { workspace_id: workspaceId },
      );
    } catch {
      // best-effort — the Gateway's own session timeout is the backstop
    } finally {
      setLoginPhase("idle");
      setActiveRunId(null);
      setUrlText(null);
      setCodePromptText(null);
      setSubmitError(null);
    }
  }, [gatewayId, activeRunId, workspaceId, stopEventsPoll]);

  if (state === "ready") return null;

  return (
    <div className="fleet-hw-note" style={{ marginTop: 8, paddingTop: 10, borderTop: "1px solid var(--border)", width: "100%" }}>
      {state === "missing" ? (
        installState === "error" && installError ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{installError}</span>
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={runInstall}>Retry</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              onClick={runInstall}
              disabled={installState === "installing" || verifying}
            >
              {installState === "installing" || verifying ? (
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              ) : null}
              {installState === "installing" ? "Installing…" : verifying ? "Checking…" : `Install ${label}`}
            </button>
            {verifyTimedOut && (
              <span className="fleet-channel-expand-error" style={{ margin: 0 }}>
                Still not detected — this box heartbeats roughly every 20s. Give it another moment, or confirm the
                install actually succeeded on the machine itself.
              </span>
            )}
          </div>
        )
      ) : loginPhase === "idle" ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={startLogin} disabled={verifying}>
            {verifying ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {verifying ? "Checking…" : "Sign in"}
          </button>
          {verifyTimedOut && (
            <span className="fleet-channel-expand-error" style={{ margin: 0 }}>
              Still not signed in after a couple of checks — try signing in again.
            </span>
          )}
        </div>
      ) : loginPhase === "error" ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{loginError}</span>
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={startLogin}>Retry</button>
        </div>
      ) : loginPhase === "starting" ? (
        <p style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting sign-in…
        </p>
      ) : (
        <>
          {urlText ? (
            <p style={{ margin: "0 0 4px", wordBreak: "break-all" }}>
              Open this link and approve:{" "}
              <a
                href={urlText}
                target="_blank"
                rel="noreferrer noopener"
                className="fleet-md-code"
                style={{ color: "var(--accent)", textDecoration: "underline" }}
              >
                {urlText}
              </a>
            </p>
          ) : (
            <p style={{ margin: "0 0 4px", display: "flex", alignItems: "center", gap: 8 }}>
              <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Waiting for {label} to print a sign-in link…
            </p>
          )}
          {codePromptText && <p style={{ margin: "0 0 4px", color: "var(--text-muted)" }}>{codePromptText}</p>}
          {runtime === "claude_code" && (
            <>
              <div className="gw-pair-panel-row" style={{ marginTop: 6, alignItems: "stretch", gap: 8 }}>
                <input
                  className="fleet-wizard-input"
                  style={{ flex: 1 }}
                  placeholder="Paste the code it gives you"
                  value={codeInput}
                  disabled={loginPhase === "submitting"}
                  maxLength={64}
                  onChange={(e) => setCodeInput(e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void submitCode();
                    }
                  }}
                />
                <button
                  type="button"
                  className="fleet-btn fleet-btn--accent"
                  onClick={submitCode}
                  disabled={loginPhase === "submitting" || !codeInput.trim()}
                >
                  {loginPhase === "submitting" ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
                  Submit
                </button>
              </div>
              {submitError && (
                <span className="fleet-channel-expand-error" style={{ margin: "4px 0 0", display: "block" }}>
                  {submitError}
                </span>
              )}
            </>
          )}
          <p style={{ margin: "10px 0 0", color: "var(--text-muted)" }}>
            Empyralis never sees or stores this credential — the CLI reads its own login directly on this computer.
          </p>
          <div style={{ marginTop: 10 }}>
            <button type="button" className="fleet-btn" onClick={cancelLogin} disabled={loginPhase === "cancelling"}>
              {loginPhase === "cancelling" ? "Cancelling…" : "Cancel"}
            </button>
          </div>
        </>
      )}
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
  const connPresentation = connectionPresentation(gateway);
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
                <CliSetupControl
                  runtime={runtime}
                  state={state}
                  gatewayId={targetGatewayId}
                  workspaceId={workspaceId}
                  refresh={refresh}
                />
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
