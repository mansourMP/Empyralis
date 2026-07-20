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

/** Multi-line copy-paste shell/config snippet inside the claude_code
 *  long-lived-token guide (CliSetupControl) — .fleet-md-code is chip-sized
 *  for inline spans, so a block variant is defined here rather than adding
 *  a new global CSS class for one component's use. */
const CODE_BLOCK_STYLE: React.CSSProperties = {
  margin: "6px 0 0",
  padding: "8px 10px",
  borderRadius: 6,
  background: "var(--bg-inset)",
  fontFamily: "var(--app-font-mono, ui-monospace, monospace)",
  fontSize: 11,
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

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
type CliLoginPhase = "idle" | "picking" | "starting" | "active" | "submitting" | "cancelling" | "error";

/** Auth methods each runtime exposes to the UI. Mirrors the gateway-side
 *  LOGIN_COMMAND and the backend LOGIN_METHODS map — kept as a static
 *  constant here (not fetched) so the picker renders instantly on click and
 *  works offline for demos. If the gateway rejects an unsupported (runtime,
 *  method) at .start(), the friendly error surfaces the specific "unsupported
 *  method" reason via the shared classifier.
 *  Field `inputKind` tells the picker what to collect after the user picks
 *  a stdin-secret method (null = URL-and-code flow, submits nothing back). */
type CliAuthMethod = {
  key: "device_auth" | "api_key" | "access_token" | "claudeai" | "console";
  label: string;
  description: string;
  inputKind: null | "api_key" | "access_token";
  recommended?: boolean;
};

const CLI_AUTH_METHODS: Record<"claude_code" | "codex", CliAuthMethod[]> = {
  codex: [
    {
      key: "device_auth",
      label: "Your ChatGPT account (device code)",
      description: "Uses your ChatGPT Plus / Pro / Team plan quota. Sign in on any browser — no callback needed.",
      inputKind: null,
      recommended: true,
    },
    {
      key: "api_key",
      label: "An OpenAI API key",
      description: "Bring your own sk-… key. Charged per token to your OpenAI billing.",
      inputKind: "api_key",
    },
    {
      key: "access_token",
      label: "A pre-obtained access token",
      description: "Advanced — paste a token you already hold. Skips the auth handshake entirely.",
      inputKind: "access_token",
    },
  ],
  claude_code: [
    {
      key: "claudeai",
      label: "Your Claude subscription (Pro / Max / Team)",
      description: "Uses your Claude.ai plan quota. Sign in on any browser — device-code flow, works on a headless box.",
      inputKind: null,
      recommended: true,
    },
    {
      key: "console",
      label: "Anthropic Console (API billing)",
      description: "Uses your Anthropic Console account, per-token billing. Same device-code flow, for per-token billing instead.",
      inputKind: null,
    },
    {
      key: "api_key",
      label: "An Anthropic API key",
      description: "Bring your own sk-ant-… key. Charged per token.",
      inputKind: "api_key",
    },
  ],
};

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
  isCloud,
}: {
  runtime: "claude_code" | "codex";
  state: RuntimeState;
  gatewayId: string;
  workspaceId: string;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
  /** Cloud VPS (Ubuntu, systemd) vs this-device (macOS, launchd) — used only
   *  to default the long-lived-token guide's instructions to the right OS.
   *  The guide itself never assumes; it's presented as a toggle so an
   *  operator whose box doesn't match this heuristic can still get correct
   *  copy-paste instructions. */
  isCloud: boolean;
}) {
  const label = RUNTIME_LABELS[runtime];
  const capabilityLabel = runtime === "claude_code" ? "Claude Code" : "Codex";
  const methods = CLI_AUTH_METHODS[runtime];
  const recommendedMethod = methods.find((m) => m.recommended) || methods[0];

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

  // Stale-error auto-clear: if the runtime transitions past "missing" (by
  // any path — a background heartbeat detected it, another operator ran a
  // sudo install, whatever), the persisted "install failed" banner from a
  // prior failed attempt is by definition stale. Without this the banner
  // survives an inventory recovery and the user has no in-UI way to clear
  // it without a hard page reload.
  useEffect(() => {
    if (state !== "missing" && installState === "error") {
      setInstallState("idle");
      setInstallError(null);
    }
  }, [state, installState]);

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
  /** Which method the user picked (or the default if the picker was skipped).
   *  Drives the input-field placement (secret submitted at start vs pasted
   *  back after URL) and the input's `kind` field. */
  const [chosenMethod, setChosenMethod] = useState<CliAuthMethod>(recommendedMethod);
  /** Value collected in the picker for stdin-secret methods (api_key /
   *  access_token). Never rendered as plain text — password-style input. */
  const [secretInput, setSecretInput] = useState("");
  /** claude_code only: the owner-driven CLAUDE_CODE_OAUTH_TOKEN fallback.
   *  This is NOT a cli.login.* flow — nothing is spawned on the Gateway and
   *  no run_id exists. The owner runs `claude setup-token` themselves (on
   *  any machine with a browser) and places the printed token into this
   *  Gateway's own environment file directly; Empyralis never sees the
   *  value at any point. See cli-login-session.ts's module doc comment for
   *  why this exists as a separate, non-spawned path rather than a fourth
   *  cli.login.start method. */
  const [tokenGuideOpen, setTokenGuideOpen] = useState(false);
  /** Which OS's instructions the guide shows — defaults from isCloud (cloud
   *  VPS boxes are always Ubuntu/systemd per scripts/install-agent-
   *  computer.sh; a non-cloud box is the owner's own machine, macOS/
   *  launchd for this product today) but stays a toggle since that
   *  heuristic isn't a hard guarantee for every box shape. */
  const [tokenGuideIsCloud, setTokenGuideIsCloud] = useState(isCloud);
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

  /** Kicks off the actual CLI process on the box with the picked method.
   *  For stdin-secret methods, `secretValue` is written to the session's
   *  stdin immediately after start returns — the CLI is blocked on read()
   *  and would sit there until the session-timeout otherwise. */
  const startLogin = useCallback(
    async (method: CliAuthMethod, secretValue?: string) => {
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
          method: method.key,
          run_id: runId,
          workspace_id: workspaceId,
        });
        setActiveRunId(runId);
        setLoginPhase("active");
        pollLoginEvents(runId);
        // Stdin-secret method: submit the API key / access token
        // immediately. Errors here surface via submitError; the session
        // stays "active" so the user can retry via the input field if the
        // gateway rejects the value.
        if (method.inputKind && secretValue) {
          try {
            await postCliAction(
              `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/${encodeURIComponent(runId)}/input`,
              { value: secretValue, kind: method.inputKind, workspace_id: workspaceId },
            );
          } catch (err) {
            setSubmitError(err instanceof Error ? err.message : "Couldn't submit that value — try again.");
          }
        }
      } catch (err) {
        setLoginPhase("error");
        setLoginError(err instanceof Error ? err.message : "Sign-in failed to start.");
      }
    },
    [gatewayId, runtime, workspaceId, pollLoginEvents],
  );

  const submitCode = useCallback(async () => {
    const code = codeInput.trim();
    if (!activeRunId || !code) return;
    setLoginPhase("submitting");
    setSubmitError(null);
    try {
      await postCliAction(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/cli/login/${encodeURIComponent(activeRunId)}/input`,
        { value: code, kind: "code", workspace_id: workspaceId },
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
      setSecretInput("");
    }
  }, [gatewayId, activeRunId, workspaceId, stopEventsPoll]);

  const openPicker = useCallback(() => {
    setLoginPhase("picking");
    setLoginError(null);
    setChosenMethod(recommendedMethod);
    setSecretInput("");
    setTokenGuideOpen(false);
  }, [recommendedMethod]);

  // Small design-system helpers scoped to this component. Local because
  // they're only meaningful inside the row+expansion pattern below.
  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    width: "100%",
  };
  const rightSlotStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 10 };

  const statusChip = <StatusChip tone={runtimeStateTone(state)} label={runtimeStateLabel(state)} />;

  // Primary action button in the right-side slot — one per row state.
  // Layout: [label · status chip · action button]. Everything else the
  // component might render (chooser, URL/code, input, error) drops into
  // the expansion area below.
  let primaryAction: React.ReactNode = null;
  if (state === "missing") {
    const installBusy = installState === "installing" || verifying;
    primaryAction = (
      <button
        type="button"
        className="fleet-btn fleet-btn--accent"
        onClick={runInstall}
        disabled={installBusy}
      >
        {installBusy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
        {installState === "installing" ? "Installing…" : verifying ? "Checking…" : installState === "error" ? "Retry" : `Install ${label}`}
      </button>
    );
  } else if (state === "unauthenticated" && loginPhase === "idle") {
    primaryAction = (
      <button
        type="button"
        className="fleet-btn fleet-btn--accent"
        onClick={openPicker}
        disabled={verifying}
      >
        {verifying ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
        {verifying ? "Checking…" : "Sign in ▾"}
      </button>
    );
  } else if (state === "unauthenticated" && loginPhase !== "idle") {
    // A run is in flight (picking, starting, active, submitting, cancelling,
    // error). Right-slot action becomes Cancel so it's always one click to
    // stop; primary continuation lives in the expansion area.
    primaryAction = (
      <button
        type="button"
        className="fleet-btn"
        onClick={cancelLogin}
        disabled={loginPhase === "cancelling"}
      >
        {loginPhase === "cancelling" ? "Cancelling…" : "Cancel"}
      </button>
    );
  }

  // Expansion area — everything that doesn't fit in the row goes here.
  // Rendered as a sibling section under the row when non-empty.
  const expansion: React.ReactNode[] = [];

  if (state === "missing" && installState === "error" && installError) {
    expansion.push(
      <span key="install-error" className="fleet-channel-expand-error" style={{ margin: 0 }}>
        {installError}
      </span>,
    );
  }
  if (state === "missing" && verifyTimedOut) {
    expansion.push(
      <span key="verify-timeout" className="fleet-channel-expand-error" style={{ margin: 0 }}>
        Still not detected — this box heartbeats roughly every 20s. Give it another moment, or
        confirm the install actually succeeded on the machine itself.
      </span>,
    );
  }

  if (state === "unauthenticated" && loginPhase === "picking") {
    const needsSecret = Boolean(chosenMethod.inputKind);
    expansion.push(
      <div key="picker" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>
          Sign {capabilityLabel} in with…
        </div>
        {methods.map((m) => {
          const isChosen = chosenMethod.key === m.key;
          return (
            <label
              key={m.key}
              style={{
                display: "flex",
                gap: 12,
                padding: "10px 12px",
                border: `1px solid ${isChosen ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 6,
                cursor: "pointer",
                background: isChosen ? "var(--bg-accent-soft, transparent)" : "transparent",
              }}
            >
              <input
                type="radio"
                name={`cli-method-${runtime}`}
                checked={isChosen}
                onChange={() => setChosenMethod(m)}
                style={{ marginTop: 3 }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 500 }}>
                  {m.label}
                  {m.recommended && (
                    <span
                      style={{
                        fontSize: 10,
                        letterSpacing: 0.08,
                        textTransform: "uppercase",
                        color: "var(--accent)",
                        padding: "1px 6px",
                        borderRadius: 3,
                        border: "1px solid var(--accent)",
                      }}
                    >
                      Recommended
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{m.description}</div>
              </div>
            </label>
          );
        })}
        {needsSecret && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <input
              type="password"
              className="fleet-wizard-input"
              placeholder={chosenMethod.inputKind === "api_key" ? "sk-…" : "Paste your access token"}
              value={secretInput}
              onChange={(e) => setSecretInput(e.currentTarget.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              This value is sent once to the CLI on your computer. Empyralis never stores it.
            </span>
          </div>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent"
            onClick={() => {
              const secret = needsSecret ? secretInput.trim() : undefined;
              if (needsSecret && !secret) return;
              void startLogin(chosenMethod, secret);
            }}
            disabled={needsSecret && !secretInput.trim()}
          >
            Continue
          </button>
          <button type="button" className="fleet-btn" onClick={() => setLoginPhase("idle")}>
            Back
          </button>
        </div>
        {runtime === "claude_code" && (
          <div style={{ marginTop: 4, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
            <button
              type="button"
              onClick={() => setTokenGuideOpen((open) => !open)}
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                textDecoration: "underline",
                textUnderlineOffset: 2,
              }}
            >
              {tokenGuideOpen ? "Hide manual token setup" : "Sign-in not sticking? Set a long-lived token manually"}
            </button>
            {tokenGuideOpen && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10, fontSize: 12 }}>
                <p style={{ margin: 0, color: "var(--text-muted)" }}>
                  This is the same long-lived-token mechanism Anthropic documents for CI and
                  background services. You generate it yourself and place it directly into this
                  Gateway&apos;s own environment — Empyralis never transmits or stores the value.
                </p>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    className={tokenGuideIsCloud ? "fleet-btn fleet-btn--accent" : "fleet-btn"}
                    style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => setTokenGuideIsCloud(true)}
                  >
                    Cloud server (Linux)
                  </button>
                  <button
                    type="button"
                    className={!tokenGuideIsCloud ? "fleet-btn fleet-btn--accent" : "fleet-btn"}
                    style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => setTokenGuideIsCloud(false)}
                  >
                    This Mac
                  </button>
                </div>
                <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
                  <li>
                    On any computer where you&apos;re signed in to Claude Code, run{" "}
                    <code className="fleet-md-code">claude setup-token</code>, approve access in the
                    browser, and copy the token it prints — treat it like a password.
                  </li>
                  {tokenGuideIsCloud ? (
                    <li>
                      SSH into this box and run:
                      <pre style={CODE_BLOCK_STYLE}>
                        {'echo \'CLAUDE_CODE_OAUTH_TOKEN="paste-your-token-here"\' | sudo tee -a /etc/empyralis/agent-computer.env\nsudo systemctl restart empyralis-gateway.service'}
                      </pre>
                    </li>
                  ) : (
                    <li>
                      In the Empyralis folder you installed this in, run:
                      <pre style={CODE_BLOCK_STYLE}>
                        {'echo \'CLAUDE_CODE_OAUTH_TOKEN=paste-your-token-here\' >> .env.local\nscripts/agent_computer.sh stop && scripts/agent_computer.sh start'}
                      </pre>
                      Not sure where that is, or running this as a background service already? Add the
                      same line to{" "}
                      <code className="fleet-md-code">~/Library/LaunchAgents/ai.empyralis.agent-computer.plist</code>
                      {"'"}s <code className="fleet-md-code">EnvironmentVariables</code> dict instead
                      (<code className="fleet-md-code">{"<key>CLAUDE_CODE_OAUTH_TOKEN</key><string>…</string>"}</code>),
                      then run{" "}
                      <code className="fleet-md-code">launchctl kickstart -k gui/$(id -u)/ai.empyralis.agent-computer</code>.
                    </li>
                  )}
                  <li>We&apos;ll pick it up on the next heartbeat, usually within 20 seconds.</li>
                </ol>
                <div>
                  <button
                    type="button"
                    className="fleet-btn"
                    onClick={() => verifyUntil((s) => s === "ready")}
                    disabled={verifying}
                  >
                    {verifying ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
                    {verifying ? "Checking…" : "Check now"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>,
    );
  }

  if (state === "unauthenticated" && loginPhase === "error") {
    expansion.push(
      <div key="login-error" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="fleet-channel-expand-error" style={{ margin: 0 }}>
          {loginError}
        </span>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={openPicker}>
          Try again
        </button>
      </div>,
    );
  }

  if (state === "unauthenticated" && loginPhase === "starting") {
    expansion.push(
      <p key="starting" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting sign-in…
      </p>,
    );
  }

  if (
    state === "unauthenticated"
    && (loginPhase === "active" || loginPhase === "submitting" || loginPhase === "cancelling")
  ) {
    expansion.push(
      <div key="active-flow">
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
        ) : chosenMethod.inputKind ? (
          // Stdin-secret method — no URL phase.
          <p style={{ margin: "0 0 4px", display: "flex", alignItems: "center", gap: 8 }}>
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Verifying with {label}…
          </p>
        ) : (
          <p style={{ margin: "0 0 4px", display: "flex", alignItems: "center", gap: 8 }}>
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Waiting for {label} to print a sign-in link…
          </p>
        )}
        {codePromptText && (
          <p style={{ margin: "0 0 4px", color: "var(--text-muted)" }}>{codePromptText}</p>
        )}
        {/* Claude Code's paste-back completion step: applies to EVERY
            claude_code URL+code method (claudeai, console), not just one of
            them. Any box the owner is reached over the network on (a paired
            remote Gateway is the common case) can't have its own loopback
            callback reached by the owner's local browser, so the CLI falls
            back to printing "Paste code here if prompted" and blocking on
            stdin — see cli-login-session.ts's module doc comment and
            https://code.claude.com/docs/en/authentication. Previously this
            was gated to chosenMethod.key === "subscription" only, which left
            console (the old default!) with no way to actually complete a
            sign-in that hit this fallback. */}
        {runtime === "claude_code" && !chosenMethod.inputKind && (
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
                {loginPhase === "submitting" ? (
                  <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
                ) : null}
                Submit
              </button>
            </div>
            {submitError && (
              <span
                className="fleet-channel-expand-error"
                style={{ margin: "4px 0 0", display: "block" }}
              >
                {submitError}
              </span>
            )}
          </>
        )}
        <p style={{ margin: "10px 0 0", color: "var(--text-muted)" }}>
          Empyralis never sees or stores this credential — the CLI reads its own login directly on this computer.
        </p>
      </div>,
    );
  }

  if (state === "unauthenticated" && loginPhase === "idle" && verifyTimedOut) {
    expansion.push(
      <span key="signin-timeout" className="fleet-channel-expand-error" style={{ margin: 0 }}>
        Still not signed in after a couple of checks — try signing in again.
      </span>,
    );
  }

  if (state === "ready") return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: "100%" }}>
      <div style={rowStyle}>
        <span className="fleet-hw-label">{capabilityLabel}</span>
        <span className="fleet-hw-value" style={rightSlotStyle}>
          {statusChip}
          {primaryAction}
        </span>
      </div>
      {expansion.length > 0 && (
        <div
          className="fleet-hw-note"
          style={{ paddingTop: 10, borderTop: "1px solid var(--border)", width: "100%", display: "flex", flexDirection: "column", gap: 10 }}
        >
          {expansion}
        </div>
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
    <main className="fleet-content fleet-content--wide">
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
            // A CLI row with state==="ready" collapses to nothing extra —
            // the CliSetupControl returns null and we render just the
            // label + status chip like every other row.
            if (state === "ready") {
              return (
                <div className="fleet-hw-row" key={id}>
                  <span className="fleet-hw-label">{CAPABILITY_LABEL[id]}</span>
                  <span className="fleet-hw-value">
                    <StatusChip tone={runtimeStateTone(state)} label={runtimeStateLabel(state)} />
                  </span>
                </div>
              );
            }
            // CliSetupControl now OWNS the row layout for missing /
            // unauthenticated states — label, status chip, primary action
            // button all sit on ONE flex row, with the expansion area
            // (chooser / URL+code / input / error) rendered below only
            // when non-empty. See the memo at
            // https://claude.ai/code/artifact/d3280431-5697-49e7-89bc-cad15f013af2
            return (
              <div
                className="fleet-hw-row"
                key={id}
                style={{ flexDirection: "column", alignItems: "stretch", justifyContent: "flex-start", gap: 4 }}
              >
                <CliSetupControl
                  runtime={runtime}
                  state={state}
                  gatewayId={targetGatewayId}
                  workspaceId={workspaceId}
                  refresh={refresh}
                  isCloud={isCloud}
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
