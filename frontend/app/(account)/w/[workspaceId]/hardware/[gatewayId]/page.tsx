"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Cpu,
  Loader2,
  LogIn,
  MemoryStick,
  Server,
  ServerOff,
  TriangleAlert,
} from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { safeExternalHref } from "@/lib/workspace/safe-render-url";
import { ConfirmDialog } from "@/lib/ui/confirm-dialog";
import { StatusChip, StatusDot, TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { CHANNEL_LABELS, channelIconSrc } from "@/lib/workspace/fleet/fleet-icons";
import { deriveStatus, formatDate, timeAgo, type AgentStatusTone } from "@/lib/workspace/fleet/fleet-presentation";
import {
  planChannelTransportState,
  type ProbeStatus,
} from "@/lib/workspace/fleet/box-capability-state";
import {
  boxHasProblem,
  planBoxHealth,
  planBoxProblems,
  planChannelCapabilityRow,
  planCliCapabilityRow,
  planLocalModelCapabilityRow,
  planSandboxCapabilityRow,
  type CapabilityRow,
  type CapabilityTone,
} from "@/lib/workspace/fleet/hardware-detail-shape";
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
  type GatewayResources,
  type ServiceInventoryItem,
  type RuntimeState,
} from "@/lib/workspace/fleet/gateway-box-picker";
import { type CliSubscriptionRuntime } from "@/lib/workspace/fleet/fleet-provider-constants";

/** The four coding CLIs, in the order they are offered. Docker, Ollama and
 *  the channel transport are ALSO capabilities and render in the same list —
 *  they simply have no install/sign-in action behind them, which
 *  hardware-detail-shape.ts decides once rather than each row guessing.
 *
 *  This used to be two separate lists on this page: "AI coding tools" with
 *  real buttons, and "Detected on this machine" as a read-only readout. That
 *  split put "Not detected" next to Install buttons (reading as a broken
 *  control) and, worse, mixed real signal with noise — the founder's own
 *  local PostgreSQL showed up as "Not responding" on a page about an Agent
 *  Computer, where it means nothing at all. PostgreSQL and GPU are gone from
 *  this page for that reason; they are facts about a machine, not about what
 *  an agent can do on it. */
const INSTALLABLE_TOOL_ORDER = ["claude_cli", "codex_cli", "grok_cli", "cursor_cli"] as const;

const CAPABILITY_LABEL: Record<string, string> = {
  claude_cli: "Claude Code",
  codex_cli: "Codex",
  grok_cli: "Grok Build",
  cursor_cli: "Cursor CLI",
};

/** Maps a service_inventory row id to its cli_subscription runtime key —
 *  the one place this mapping lives, so INSTALLABLE_TOOL_ORDER's rows below
 *  and CliSetupControl's runtime prop never drift apart. */
const CLI_ROW_RUNTIME: Record<string, CliSubscriptionRuntime> = {
  claude_cli: "claude_code",
  codex_cli: "codex",
  grok_cli: "grok_build",
  cursor_cli: "cursor_cli",
};

/** A capability row's tone, mapped onto the shared StatusChip vocabulary. */
function capabilityChipTone(tone: CapabilityTone): AgentStatusTone {
  switch (tone) {
    case "ready":
      return "ready";
    case "degraded":
      return "degraded";
    case "error":
      return "offline";
    default:
      return "unknown";
  }
}

type ShellAccessPresentation = { tone: AgentStatusTone; label: string; note: string | null };

/** Shell access row presentation — the authorized-vs-locally-enabled honesty
 *  fix. runtime_access_mode/runtime_access_label is only ever what the
 *  SERVER authorized for this gateway at pairing time; shell_full_access_
 *  locally_enabled is the box operator's own live opt-in
 *  (EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED), reported on every
 *  heartbeat. Full Access only actually runs a call when BOTH are true
 *  (empyralis-gateway/src/shell/runtime.ts's resolveExecutionMode()) —
 *  showing only the server half, as this row used to, let a customer
 *  believe full_access was live when the local half was never turned on,
 *  or the reverse. Default/Custom modes don't carry this ambiguity (the
 *  local flag is irrelevant unless the server has authorized full_access in
 *  the first place), so they keep the plain label with no extra note. */
function shellAccessPresentation(gateway: FleetGateway): ShellAccessPresentation {
  const label = gateway.runtime_access_label || "Default";
  if (gateway.runtime_access_mode !== "full_access") {
    return { tone: "ready", label, note: null };
  }
  const locallyEnabled = gateway.shell_full_access_locally_enabled;
  if (locallyEnabled === true) {
    return { tone: "ready", label: "Full Access", note: null };
  }
  if (locallyEnabled === false) {
    return {
      tone: "degraded",
      label: "Full Access — not enabled on this box",
      note: "This box is authorized for Full Access, but it hasn't been turned on locally yet — calls fall back to sandboxed execution.",
    };
  }
  // null/undefined: this gateway hasn't heartbeated the field yet (older
  // build, or hasn't connected since it shipped) — say "unknown", never
  // guess which way it actually is.
  return {
    tone: "unknown",
    label: "Full Access (authorized)",
    note: "This computer hasn't reported whether Full Access is turned on locally yet.",
  };
}

/** Resource-gauge bar color tier — "" (default/green) below 60%, amber at
 *  60-84%, red at 85%+. Shared by CPU/GPU/Memory; Temp uses its own
 *  Celsius-scale thresholds below since 34% CPU and 34°C don't mean the
 *  same thing. */
type GaugeTone = "" | "warn" | "danger";

function toneForPct(pct: number): GaugeTone {
  if (pct >= 85) return "danger";
  if (pct >= 60) return "warn";
  return "";
}

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
}

function formatGB(bytes: number): string {
  return (bytes / 1_000_000_000).toFixed(1);
}

type GaugeCardDef = {
  key: string;
  icon: ReactNode;
  label: string;
  value: string;
  pct: number;
  tone: GaugeTone;
};

/** CPU and memory ONLY, and that is a measurement result rather than a
 *  design preference.
 *
 *  Both were verified against their real source before being kept:
 *    · cpu_pct is the idle/total delta of os.cpus() over a 500ms window
 *      (empyralis-gateway/src/health/resource-metrics.ts). Driven to a
 *      genuine full-core load it reads 100.0%, and at rest it matches
 *      `top`'s own busy figure.
 *    · memory on Linux — which every cloud Agent Computer is — comes out as
 *      total minus os.freemem(), and on the Node this gateway runs
 *      os.freemem() IS MemAvailable, so that figure equals the true
 *      "in use" number exactly (checked inside a real Linux container, not
 *      inferred from documentation). On macOS it is vm_stat's
 *      active+wired+compressed, which measured ~3 percentage points below
 *      Activity Monitor's own formula — a real reading, slightly
 *      conservative, never fabricated.
 *
 *  GPU and temperature were REMOVED. Not because they are wrong — they are
 *  best-effort by their own contract (nvidia-smi only, thermal zones only,
 *  both null on macOS) — but because nothing here could prove them against
 *  a real source, and the standing bar for a number on this page is that it
 *  is provably accurate. A gauge nobody has checked is worse than a missing
 *  one. */
function buildGaugeCards(resources: GatewayResources | null | undefined): GaugeCardDef[] {
  if (!resources) return [];
  const cards: GaugeCardDef[] = [];
  if (typeof resources.cpu_pct === "number" && Number.isFinite(resources.cpu_pct)) {
    const pct = clampPct(resources.cpu_pct);
    cards.push({
      key: "cpu",
      icon: <Cpu size={13} strokeWidth={1.75} />,
      label: "CPU",
      value: `${Math.round(pct)}%`,
      pct,
      tone: toneForPct(pct),
    });
  }
  if (
    typeof resources.memory_used_bytes === "number" && Number.isFinite(resources.memory_used_bytes)
    && typeof resources.memory_total_bytes === "number" && resources.memory_total_bytes > 0
  ) {
    const pct = clampPct((resources.memory_used_bytes / resources.memory_total_bytes) * 100);
    cards.push({
      key: "memory",
      icon: <MemoryStick size={13} strokeWidth={1.75} />,
      label: "Memory",
      value: `${formatGB(resources.memory_used_bytes)} / ${formatGB(resources.memory_total_bytes)} GB`,
      pct,
      tone: toneForPct(pct),
    });
  }
  return cards;
}

/** Compact elapsed-duration string ("3h 42m", "2d 6h") for the Gateway
 *  card's Uptime row. Measures time since latest_connected_at — this
 *  session's own connected duration (the one uptime signal the backend
 *  actually reports; no host-OS process uptime is transmitted) — not
 *  "ago" phrasing like timeAgo(), since a duration reads oddly as "X ago". */
function formatUptime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "—";
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours < 24) return remMins > 0 ? `${hours}h ${remMins}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
}

const VERIFY_POLL_MS = 5_000;
const VERIFY_TIMEOUT_MS = 90_000;
const LOGIN_EVENTS_POLL_MS = 2_000;

function createRunId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `run_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

async function postCliAction(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fleetAuthorizedFetch(path, {
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
  key: "device_auth" | "api_key" | "access_token" | "claudeai" | "console" | "login";
  label: string;
  description: string;
  /** Fallback/edge-case caveat — shown as a tooltip on the option, not in the visible description. */
  note?: string;
  inputKind: null | "api_key" | "access_token";
  recommended?: boolean;
};

const CLI_AUTH_METHODS: Record<CliSubscriptionRuntime, CliAuthMethod[]> = {
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
  // xAI Grok Build — docs.x.ai/build, verified live 2026-07-24. `grok login
  // --device-auth` is the only login-session method wired on the Gateway
  // side (see cli-login-session.ts's grok_build comment for why an api_key
  // method isn't — XAI_API_KEY is an env-var fallback with no persisting
  // login subcommand, so it's set directly in the Gateway's own environment
  // instead of through this picker).
  grok_build: [
    {
      key: "device_auth",
      label: "Your SuperGrok / X Premium+ subscription (device code)",
      description: "Uses your SuperGrok/X Premium+ plan quota. Sign in on any browser — no callback needed.",
      inputKind: null,
      recommended: true,
    },
  ],
  // Cursor CLI — cursor.com/docs/cli, verified live 2026-07-24. `agent login`
  // is the only login-session method wired here; see cli-login-session.ts's
  // cursor_cli comment for the real, documented SSH/headless reliability
  // caveat and why CURSOR_API_KEY (like XAI_API_KEY above) is set directly
  // in the Gateway's own environment instead of through this picker.
  cursor_cli: [
    {
      key: "login",
      label: "Your Cursor subscription (Pro / Pro+ / Ultra)",
      description: "Uses your Cursor plan quota. Sign in on any browser.",
      note: "If this doesn't complete over a remote connection, set CURSOR_API_KEY on the Gateway's own machine instead.",
      inputKind: null,
      recommended: true,
    },
  ],
};

/** Frontend-only serialization for CLI installs. Before this, clicking
 *  Install on all four tools fired four concurrent installer processes on
 *  the SAME box — on a small machine that pins it at 100% CPU and everything
 *  hangs, including the very heartbeat this page polls for progress. This
 *  queues the install *request*, not any backend state — the Gateway's
 *  cli.install endpoint is untouched; this only decides WHEN the frontend is
 *  allowed to call it. One runtime installs at a time; the rest read
 *  "Queued" until it's their turn. A runtime's turn ends (see the release
 *  effect inside CliSetupControl) when its install either genuinely
 *  succeeds (gatewayRuntimeState moves off "missing"), fails outright, or
 *  its own verify-poll times out — any of which means nothing is still
 *  running on the box for it, so the next queued runtime is safe to start. */
type InstallQueueController = {
  activeRuntime: CliSubscriptionRuntime | null;
  queuedRuntimes: CliSubscriptionRuntime[];
  requestTurn: (runtime: CliSubscriptionRuntime) => void;
  release: (runtime: CliSubscriptionRuntime) => void;
};

function useInstallQueue(): InstallQueueController {
  const [state, setState] = useState<{ active: CliSubscriptionRuntime | null; queue: CliSubscriptionRuntime[] }>({
    active: null,
    queue: [],
  });

  const requestTurn = useCallback((runtime: CliSubscriptionRuntime) => {
    setState((prev) => {
      if (prev.active === runtime || prev.queue.includes(runtime)) return prev;
      if (prev.active === null) return { active: runtime, queue: prev.queue };
      return { active: prev.active, queue: [...prev.queue, runtime] };
    });
  }, []);

  const release = useCallback((runtime: CliSubscriptionRuntime) => {
    setState((prev) => {
      if (prev.active !== runtime) {
        // Not the active runtime (already released, or was only ever
        // queued) — defensively drop it from the queue so a stale request
        // can never get stuck waiting for a turn nothing will grant.
        return prev.queue.includes(runtime)
          ? { active: prev.active, queue: prev.queue.filter((r) => r !== runtime) }
          : prev;
      }
      const [next, ...rest] = prev.queue;
      return { active: next ?? null, queue: rest };
    });
  }, []);

  return { activeRuntime: state.active, queuedRuntimes: state.queue, requestTurn, release };
}

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
  installQueue,
}: {
  runtime: CliSubscriptionRuntime;
  state: RuntimeState;
  gatewayId: string;
  workspaceId: string;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
  /** Shared across all four CliSetupControl instances on this page — see
   *  useInstallQueue's own doc comment for why installs must be serialized
   *  here instead of each row firing independently. */
  installQueue: InstallQueueController;
  /** Cloud VPS (Ubuntu, systemd) vs this-device (macOS, launchd) — used only
   *  to default the long-lived-token guide's instructions to the right OS.
   *  The guide itself never assumes; it's presented as a toggle so an
   *  operator whose box doesn't match this heuristic can still get correct
   *  copy-paste instructions. */
  isCloud: boolean;
}) {
  const label = RUNTIME_LABELS[runtime];
  const capabilityLabel = label;
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

  // ---- install queue wiring ----
  // Install button clicks never call runInstall() directly — they call
  // installQueue.requestTurn(), which either makes this runtime the active
  // one immediately (queue was empty) or parks it in "Queued" state. Actual
  // install only fires here, the instant this runtime BECOMES the active
  // one (edge-triggered, not level-triggered, so a re-render while already
  // active never re-fires it).
  const isActiveInstallTurn = installQueue.activeRuntime === runtime;
  const isQueuedForInstall = installQueue.queuedRuntimes.includes(runtime);
  const wasActiveInstallTurnRef = useRef(false);
  useEffect(() => {
    if (isActiveInstallTurn && !wasActiveInstallTurnRef.current) {
      void runInstall();
    }
    wasActiveInstallTurnRef.current = isActiveInstallTurn;
  }, [isActiveInstallTurn, runInstall]);

  // Release this runtime's turn the moment nothing is still running on the
  // box for it: the real state moved off "missing" (success — verifyUntil
  // caught the change), the install call itself failed outright, or the
  // verify-poll gave up waiting. Whichever it is, the next queued runtime
  // (if any) is safe to start.
  useEffect(() => {
    if (!isActiveInstallTurn) return;
    if (state !== "missing" || installState === "error" || verifyTimedOut) {
      installQueue.release(runtime);
    }
  }, [isActiveInstallTurn, state, installState, verifyTimedOut, installQueue, runtime]);

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
  /** Copy-the-setting-name affordance for the long-lived-token path above.
   *  A NAME, never a command: what a person has to add is one setting, and
   *  a shell line telling them how to add it on one particular OS is
   *  mechanism this page does not owe anybody. */
  const [settingCopied, setSettingCopied] = useState(false);
  const settingCopiedRef = useRef<number | null>(null);
  const copySettingName = useCallback(async () => {
    try {
      await navigator.clipboard.writeText("CLAUDE_CODE_OAUTH_TOKEN");
      setSettingCopied(true);
      if (settingCopiedRef.current) window.clearTimeout(settingCopiedRef.current);
      settingCopiedRef.current = window.setTimeout(() => setSettingCopied(false), 2_000);
    } catch {
      // Clipboard denied (an insecure origin, or a browser prompt refused).
      // The name is already on screen in full, so nothing is lost — never
      // report a failure the reader cannot act on and does not need to.
    }
  }, []);
  useEffect(() => {
    return () => {
      if (settingCopiedRef.current) window.clearTimeout(settingCopiedRef.current);
    };
  }, []);
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
          const res = await fleetAuthorizedFetch(
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
    // Busy only counts while THIS runtime actually holds the install turn —
    // a queued runtime is never "busy" (nothing is running for it yet), it's
    // just waiting. See useInstallQueue's doc comment above.
    const installBusy = isActiveInstallTurn && (installState === "installing" || verifying);
    const label2 = isQueuedForInstall
      ? "Queued"
      : installState === "installing"
        ? "Installing…"
        : installBusy
          ? "Checking…"
          : installState === "error"
            ? "Retry"
            // "Install", not "Install Codex" — the tool's name is already
            // the left half of this same row, and repeating it made four
            // buttons in one list four different widths. The accessible
            // name below still carries it.
            : "Install";
    primaryAction = (
      <button
        type="button"
        // NOT accent-filled. Four coding-CLI rows sit in this list and each
        // can carry an action, so an accent fill here paints up to four
        // "the primary action" buttons in one view — the founder called
        // exactly this out on Cursor CLI's Sign in. fleet-btn--accent is
        // the neutral-but-emphatic variant for precisely this case: a row
        // action in a view whose primary action is something else.
        className="fleet-btn fleet-btn--accent"
        aria-label={`Install ${label} on this computer`}
        onClick={() => installQueue.requestTurn(runtime)}
        disabled={installBusy || isQueuedForInstall}
      >
        {installBusy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
        {label2}
      </button>
    );
  } else if (state === "unauthenticated" && loginPhase === "idle") {
    // Emphatic by WEIGHT, never by hue — see the install button above for
    // why an accent fill cannot live on a row that repeats four times.
    primaryAction = (
      <button
        type="button"
        className="fleet-btn fleet-btn--accent"
        aria-label={`Sign in to ${label} on this computer`}
        onClick={openPicker}
        disabled={verifying}
      >
        {verifying ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <LogIn size={14} strokeWidth={2} />}
        {verifying ? "Checking…" : "Sign in"}
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
              title={m.note}
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
              {tokenGuideOpen ? "Hide" : "Sign-in not sticking?"}
            </button>
            {/* This used to be three shell commands, a plist path, an
                EnvironmentVariables dict and a `launchctl kickstart` line —
                a wall of mechanism handed to a customer who asked why their
                sign-in did not stick. What replaces it says the same thing
                as a fact and an action: the setting to add, one button to
                copy it, and an honest sentence about who has to apply it.
                Nobody reads a command off a screen and retypes it; anyone
                who can act on this can paste it. */}
            {tokenGuideOpen && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10, fontSize: 12 }}>
                <p style={{ margin: 0, color: "var(--text-muted)" }}>
                  Claude Code can also hold a long-lived token instead of a sign-in session. You
                  create it yourself and put it on that computer — Empyralis never sees the value.
                </p>
                <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
                  <li>
                    On any computer where you are already signed in to Claude Code, create a
                    long-lived token and copy it. Treat it like a password.
                  </li>
                  <li>
                    Add it to this computer as the setting{" "}
                    <code className="fleet-md-code">CLAUDE_CODE_OAUTH_TOKEN</code>, then restart it.
                    {isCloud
                      ? " On a cloud server that needs someone with administrator access to it."
                      : " On your own Mac you can do this yourself."}
                    <div style={{ marginTop: 6 }}>
                      <button
                        type="button"
                        className="fleet-btn"
                        style={{ padding: "4px 10px", fontSize: 12 }}
                        onClick={() => void copySettingName()}
                      >
                        {settingCopied ? "Copied" : "Copy setting name"}
                      </button>
                    </div>
                  </li>
                  <li>We pick it up on the next check-in, usually within 20 seconds.</li>
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
            {(() => {
              // safeExternalHref: `urlText` is a "url"-kind line read off the
              // CLI login flow's own stdout on the owner's box (see
              // setUrlText above) — text the app never authored, so it goes
              // through the same allowlist as every other data-to-href seam
              // rather than straight into href. A refused value still shows
              // (the operator may need to read/copy it), just not as a live
              // link.
              const safeUrl = safeExternalHref(urlText);
              return safeUrl ? (
                <a
                  href={safeUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="fleet-md-code"
                  style={{ color: "var(--accent)", textDecoration: "underline" }}
                >
                  {urlText}
                </a>
              ) : (
                <span className="fleet-md-code">{urlText}</span>
              );
            })()}
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

/** Platform-triggered gateway self-update — one button, no SSH. Dispatches
 *  through routes_gateway.py's POST .../self-update (member-gated), which
 *  routes the SAME tool-invoke transport CliSetupControl's install button
 *  above uses, to the gateway.self_update capability
 *  (empyralis-gateway/src/update/gateway-self-update-runtime.ts). Unlike
 *  CliSetupControl there is no "probe" to poll — the one fact that changes
 *  on success is gateway_version itself, so this polls for THAT to move
 *  past its pre-update value, reusing the same verify-poll shape (refresh()
 *  on an interval, bounded by VERIFY_TIMEOUT_MS) rather than inventing a
 *  second one. */
function GatewaySelfUpdateControl({
  gateway,
  gatewayId,
  workspaceId,
  refresh,
}: {
  gateway: FleetGateway;
  gatewayId: string;
  workspaceId: string;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
}) {
  const currentVersion = gateway.gateway_version || null;
  const latestVersion = gateway.latest_gateway_version || null;
  const updateAvailable = Boolean(gateway.gateway_update_available);
  // "Nothing newer exists" and "this computer cannot receive updates at all"
  // were both rendering as "Up to date" — the backend has carried a refusal
  // code since the build fingerprint shipped and nothing on this page ever
  // read it. That is this codebase's own outcome-honesty law broken on the
  // one screen where a stuck box is visible.
  const cannotReceiveUpdates = gateway.gateway_update_refusal_code === "launch_path_not_updatable";

  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyTimedOut, setVerifyTimedOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const runUpdate = useCallback(async () => {
    setBusy(true);
    setError(null);
    setVerifyTimedOut(false);
    const versionBeforeUpdate = currentVersion;
    try {
      await postCliAction(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/self-update`, {
        workspace_id: workspaceId,
      });
      setBusy(false);
      setVerifying(true);
      const startedAt = Date.now();
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        if (Date.now() - startedAt > VERIFY_TIMEOUT_MS) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setVerifying(false);
          setVerifyTimedOut(true);
          return;
        }
        const list = await refresh({ silent: true });
        const match = list.find((g) => idOf(g) === gatewayId);
        const nowVersion = match?.gateway_version || null;
        if (match && nowVersion && nowVersion !== versionBeforeUpdate) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setVerifying(false);
        }
      }, VERIFY_POLL_MS);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Update failed.");
    }
  }, [gatewayId, workspaceId, currentVersion, refresh]);

  // Nothing meaningful to show for a box whose gateway_version this backend
  // has never recorded (a gateway build old enough to predate self-update
  // reports no version at all) AND has no known newer build to offer either.
  // A box that reports itself un-updatable is the exception: that is the one
  // state a person most needs to see, whatever its version says.
  if (!currentVersion && !updateAvailable && !cannotReceiveUpdates) {
    return null;
  }

  return (
    <div className="fleet-hw-row">
      <span className="fleet-hw-label">Gateway version</span>
      <span className="fleet-hw-value" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span>{currentVersion ? `v${currentVersion}` : "Unknown"}</span>
        {verifying ? (
          <span className="fleet-list-row-desc" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Updating…
          </span>
        ) : updateAvailable ? (
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void runUpdate()} disabled={busy}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {busy ? "Starting…" : `Update to v${latestVersion}`}
          </button>
        ) : cannotReceiveUpdates ? (
          // The reason, the blockers and the repair itself all live in
          // "What's wrong" (GatewayLaunchRepairRow) — this row only has to
          // stop claiming "Up to date", which is what it used to do for a
          // box that could not be updated at all.
          <span className="fleet-list-row-desc">Can&apos;t receive updates</span>
        ) : (
          <span className="fleet-list-row-desc">Up to date</span>
        )}
      </span>
      {error && (
        <span className="fleet-channel-expand-error" style={{ margin: 0, width: "100%" }}>
          {error}
        </span>
      )}
      {verifyTimedOut && (
        <span className="fleet-channel-expand-error" style={{ margin: 0, width: "100%" }}>
          The update was triggered but this computer hasn&apos;t reported a new version yet — it may still be downloading, or it may need a manual check.
        </span>
      )}
    </div>
  );
}

/** THE REPAIR — a sentence and a button, never a wall of shell.
 *
 *  This is the one repair on the page Empyralis genuinely cannot perform:
 *  the gateway runs unprivileged inside a read-only mount namespace with
 *  NoNewPrivileges set (all three measured on production), so it cannot
 *  rewrite the supervisor entry that starts it. A button that ran it would
 *  be a dead control.
 *
 *  What it used to do was print four `plutil`/`launchctl` lines at the
 *  customer. What it does now is state the fact — this needs someone with
 *  administrator access — and hand that person the exact commands through
 *  the clipboard. Nobody reads shell off a screen and retypes it; anyone
 *  who can act on this can paste it. The commands come BACK FROM THE
 *  BACKEND for this specific box (gateway_launch_repair) and are never
 *  literals in this file, which is why the surface scan bans the literals
 *  and permits this.
 *
 *  Three states, kept apart because they send a person to three different
 *  places: `ready` (here is the fix), `unverified` (this computer could not
 *  prepare its own repair — the reason is named and NO instruction is
 *  offered, since a wrong ExecStart turns a stale box into a dead one), and
 *  nothing at all. */
function GatewayLaunchRepairRow({ gateway }: { gateway: FleetGateway }) {
  const repair = gateway.gateway_launch_repair ?? null;
  const commands = repair?.state === "ready" ? (repair.commands ?? []) : [];
  const [copied, setCopied] = useState(false);
  const copiedRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (copiedRef.current) window.clearTimeout(copiedRef.current);
    };
  }, []);
  const copyCommands = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(commands.join("\n"));
      setCopied(true);
      if (copiedRef.current) window.clearTimeout(copiedRef.current);
      copiedRef.current = window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      // Clipboard denied. Deliberately silent rather than reporting a
      // failure whose only fix is a browser permission the reader did not
      // come here to think about — the sentence above still says who has to
      // act and what for.
    }
  }, [commands]);
  return (
    <div className="fleet-hw-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      {(repair?.blockers ?? []).map((blocker) => (
        <span key={String(blocker?.code)} className="fleet-list-row-desc" style={{ whiteSpace: "normal" }}>
          {blocker?.detail}
        </span>
      ))}
      {/* The backend's own sentence, passed through rather than replaced —
          it already names who has to act and that it is a one-time change.
          Writing a second sentence beside it said the same thing twice. */}
      {repair?.detail ? (
        <span className="fleet-list-row-desc" style={{ whiteSpace: "normal" }}>{repair.detail}</span>
      ) : null}
      {commands.length > 0 ? (
        <div>
          <button type="button" className="fleet-btn" onClick={() => void copyCommands()}>
            {copied ? "Copied" : "Copy the commands"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** CHANNELS, as one capability row — the same shape as every other row on
 *  this page rather than a banner of its own.
 *
 *  THE GATEWAY AND THE CHANNEL TRANSPORT ARE TWO DIFFERENT THINGS, and this
 *  page used to blur them. The gateway is this computer's own connection to
 *  Empyralis — one process, always required, the thing that makes the box
 *  reachable at all. The channel transport (OpenClaw) is a SEPARATE program
 *  running beside it that carries Telegram/WhatsApp/Signal traffic, and a
 *  computer with a perfectly healthy connection can have no channel
 *  transport at all. They are named apart everywhere on this page now:
 *  "Connection to Empyralis" for the first, "Channels" for the second.
 *
 *  The state comes from box-capability-state.ts's planChannelTransportState
 *  composed by hardware-detail-shape.ts's planChannelCapabilityRow — one
 *  derivation, so this row and the health line can never disagree. */
function ChannelCapabilityRow({
  openclawStatus,
  pluginsStatus,
  gatewayId,
  workspaceId,
  setupAgentId,
  refresh,
}: {
  openclawStatus: ProbeStatus;
  pluginsStatus: ProbeStatus;
  gatewayId: string;
  workspaceId: string;
  /** The agent whose channel policy a "Set up" click provisions — the
   *  transport is provisioned FOR an agent's own settings, so there is
   *  nothing this button can do without one. `null` renders no button at
   *  all and says why instead (CLAUDE.md: "no dead controls"). */
  setupAgentId: string | null;
  refresh: (opts?: { silent?: boolean }) => Promise<FleetGateway[]>;
}) {
  const state = planChannelTransportState({ transportStatus: openclawStatus, pluginsStatus });
  const row = planChannelCapabilityRow(state, Boolean(setupAgentId));

  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const runSetup = useCallback(async () => {
    if (!setupAgentId) return;
    setBusy(true);
    setError(null);
    const stateBefore = state;
    try {
      const result = await postCliAction(
        `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/provision?agent_id=${encodeURIComponent(setupAgentId)}`,
        { install_channels: [] },
      );
      // A "refused" result is a SUCCESSFUL round trip carrying a reason from
      // the box itself (wrong version, a failed lockdown read-back, an
      // unclean security audit) — surfaced immediately rather than left to
      // the verify-poll to time out and say nothing useful.
      const provisioning = result?.openclaw_provisioning as { refusal?: { code?: string; detail?: string } } | undefined;
      if (provisioning?.refusal) {
        setBusy(false);
        setError(provisioning.refusal.detail || provisioning.refusal.code || "This computer refused the setup request.");
        return;
      }
      setBusy(false);
      setVerifying(true);
      const startedAt = Date.now();
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        if (Date.now() - startedAt > VERIFY_TIMEOUT_MS) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setVerifying(false);
          return;
        }
        const list = await refresh({ silent: true });
        const match = list.find((g) => idOf(g) === gatewayId);
        const items: ServiceInventoryItem[] = match?.metadata?.service_inventory || [];
        const nowById = new Map(items.map((item) => [String(item.id || ""), item]));
        const nowState = planChannelTransportState({
          transportStatus: nowById.get("openclaw")?.status as ProbeStatus,
          pluginsStatus: nowById.get("openclaw_channel_plugins")?.status as ProbeStatus,
        });
        if (nowState !== stateBefore) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setVerifying(false);
        }
      }, VERIFY_POLL_MS);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Could not reach this computer.");
    }
  }, [gatewayId, setupAgentId, state, refresh]);

  return (
    <CapabilityRowView
      row={row}
      action={
        row.action === "set_up" ? (
          <button type="button" className="fleet-btn" disabled={busy || verifying} onClick={() => void runSetup()}>
            {(busy || verifying) ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {busy ? "Starting…" : verifying ? "Setting up…" : "Set up"}
          </button>
        ) : null
      }
      error={error}
    />
  );
}

/** One capability, one line: name, honest state, and at most ONE action.
 *  Healthy collapses to exactly that line — no note, no expansion — which
 *  is the whole "healthy collapses, broken expands" rule expressed in one
 *  place instead of at each call site. */
function CapabilityRowView({
  row,
  action,
  error,
}: {
  row: CapabilityRow;
  action?: ReactNode;
  error?: string | null;
}) {
  const expanded = Boolean(row.note) || Boolean(error);
  return (
    <div
      className="fleet-hw-row"
      style={expanded ? { flexDirection: "column", alignItems: "stretch", gap: 6 } : undefined}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, width: "100%" }}>
        <span className="fleet-hw-label">{row.label}</span>
        <span className="fleet-hw-value" style={{ gap: 10 }}>
          <StatusChip tone={capabilityChipTone(row.tone)} label={row.stateLabel} />
          {action}
        </span>
      </div>
      {row.note ? (
        <span
          className="fleet-list-row-desc"
          style={{ whiteSpace: "normal", overflow: "visible", textOverflow: "clip" }}
        >
          {row.note}
        </span>
      ) : null}
      {error ? (
        <span className="fleet-channel-expand-error" style={{ margin: 0 }}>
          {error}
        </span>
      ) : null}
    </div>
  );
}

/** In-product "restart the gateway" — gap-hardware-gateway.md Part 1 item 2.
 *  Before this, the only way to restart a stuck gateway process without
 *  destroying the whole VPS was SSH + `systemctl restart` (see the manual
 *  token-setup guide above, which still shows that raw command for the
 *  narrower "apply a new env var" case). This dispatches through
 *  routes_gateway.py's POST .../restart (member-gated) to the
 *  gateway.restart capability (empyralis-gateway/src/update/gateway-
 *  restart-runtime.ts) — same tool-invoke transport as self-update/doctor,
 *  no artifact download, re-execs the build already on disk. Confirm-first:
 *  restarting briefly disconnects the box and interrupts anything running
 *  on it right now. */
function GatewayRestartControl({ gatewayId, workspaceId }: { gatewayId: string; workspaceId: string }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justRestarted, setJustRestarted] = useState(false);
  const clearJustRestartedRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (clearJustRestartedRef.current) window.clearTimeout(clearJustRestartedRef.current);
    };
  }, []);

  const runRestart = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await postCliAction(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/restart`, {
        workspace_id: workspaceId,
      });
      setConfirmOpen(false);
      setJustRestarted(true);
      if (clearJustRestartedRef.current) window.clearTimeout(clearJustRestartedRef.current);
      clearJustRestartedRef.current = window.setTimeout(() => setJustRestarted(false), 30_000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restart failed.");
    } finally {
      setBusy(false);
    }
  }, [gatewayId, workspaceId]);

  return (
    <div className="fleet-hw-row">
      {/* Named for the ACTION, not for the process. This section is
          already titled "Connection to Empyralis"; repeating "Gateway" in
          the row label was the same mechanism-naming this page has been
          swept for everywhere else. */}
      <span className="fleet-hw-label">Restart</span>
      <span className="fleet-hw-value" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {justRestarted ? (
          <span className="fleet-list-row-desc" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Restarting — should reconnect within a few
            seconds.
          </span>
        ) : (
          <button type="button" className="fleet-btn" onClick={() => setConfirmOpen(true)}>
            Restart
          </button>
        )}
      </span>
      {error && (
        <span className="fleet-channel-expand-error" style={{ margin: 0, width: "100%" }}>
          {error}
        </span>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title="Restart this computer's connection?"
        body="This briefly disconnects the computer and interrupts anything running on it right now. It reconnects on its own within a few seconds — nothing is lost and it stays paired."
        confirmLabel="Restart"
        confirmTone="primary"
        busy={busy}
        onConfirm={() => void runRestart()}
        onCancel={() => {
          setConfirmOpen(false);
          setError(null);
        }}
      />
    </div>
  );
}

/** One check result from the gateway's own in-process doctor pass — see
 *  empyralis-gateway/src/health/gateway-doctor.ts's GatewayDoctorCheckResult.
 *  `status` mirrors the exact same detect->repair->re-validate contract the
 *  gateway ran; this UI never re-interprets it. */
type GatewayDoctorCheckResult = {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "skip";
  detail: string;
  repairable: boolean;
  repaired?: boolean;
  repair_detail?: string;
};

type GatewayDoctorRunResponse = {
  checked_at?: string;
  repair_requested?: boolean;
  results?: GatewayDoctorCheckResult[];
};

function doctorStatusTone(status: GatewayDoctorCheckResult["status"]): AgentStatusTone {
  switch (status) {
    case "pass":
      return "ready";
    case "warn":
      return "degraded";
    case "fail":
      return "offline";
    default:
      return "unknown";
  }
}

function doctorStatusLabel(status: GatewayDoctorCheckResult["status"]): string {
  switch (status) {
    case "pass":
      return "OK";
    case "warn":
      return "Attention";
    case "fail":
      return "Issue";
    default:
      return "Skipped";
  }
}

/** Runs the live in-gateway doctor (detect -> safe repair -> re-validate,
 *  see gateway-doctor.ts) and renders one compact row per check — label,
 *  status chip, and a single plain-language line. Deliberately terse: every
 *  check is ONE row, never a multi-line breakdown, so this never turns into
 *  the kind of dense diagnostic wall of text the iMessage setup panel's
 *  staged probe shows (that panel's audience is "debug my iMessage bridge";
 *  this one is "is my computer healthy," answered at a glance). */
function GatewayDoctorControl({ gatewayId, workspaceId }: { gatewayId: string; workspaceId: string }) {
  const [result, setResult] = useState<GatewayDoctorRunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (repair: boolean) => {
      if (repair) setRepairing(true);
      else setRunning(true);
      setError(null);
      try {
        const data = await postCliAction(`/api/gateway/registrations/${encodeURIComponent(gatewayId)}/doctor/run`, {
          workspace_id: workspaceId,
          repair,
        });
        setResult(data as unknown as GatewayDoctorRunResponse);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Diagnostics couldn't run.");
      } finally {
        setRunning(false);
        setRepairing(false);
      }
    },
    [gatewayId, workspaceId],
  );

  const results = result?.results ?? [];
  const hasRepairableIssue = results.some((r) => r.repairable && (r.status === "fail" || r.status === "warn"));
  const busy = running || repairing;

  return (
    <>
      <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Diagnostics</div>
      <div className="fleet-hw-card">
        {results.length === 0 ? (
          <div className="fleet-hw-row">
            <span className="fleet-list-row-desc">
              Check this computer's connection, features, and sign-ins in one pass.
            </span>
          </div>
        ) : (
          results.map((r) => (
            <div
              className="fleet-hw-row"
              key={r.id}
              style={{ flexDirection: "column", alignItems: "stretch", justifyContent: "flex-start", gap: 4 }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                <span className="fleet-hw-label">{r.label}</span>
                <StatusChip tone={doctorStatusTone(r.status)} label={r.repaired ? "Fixed" : doctorStatusLabel(r.status)} />
              </div>
              <span className="fleet-list-row-desc">{r.detail}</span>
            </div>
          ))
        )}
        <div className="fleet-hw-row" style={{ justifyContent: "flex-end", gap: 8 }}>
          {hasRepairableIssue && (
            <button type="button" className="fleet-btn" onClick={() => void run(true)} disabled={busy}>
              {repairing ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
              {repairing ? "Fixing…" : "Fix what's safe to fix"}
            </button>
          )}
          <button type="button" className="fleet-btn" onClick={() => void run(false)} disabled={busy}>
            {running ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {running ? "Checking…" : result ? "Run again" : "Run diagnostics"}
          </button>
        </div>
        {result?.checked_at && (
          <div className="fleet-hw-row">
            <span className="fleet-list-row-desc">Checked {timeAgo(result.checked_at)}</span>
          </div>
        )}
        {error && (
          <span className="fleet-channel-expand-error" style={{ margin: 0, width: "100%" }}>
            {error}
          </span>
        )}
      </div>
    </>
  );
}

export default function GatewayDetailPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const targetGatewayId = String(params?.gatewayId || "");
  const { gateways, loading, refresh } = useWorkspaceGateways(workspaceId);
  const { agents, loading: agentsLoading } = useFleetAgents(workspaceId);
  const router = useRouter();

  const [destroyOpen, setDestroyOpen] = useState(false);
  const [destroying, setDestroying] = useState(false);
  const [destroyError, setDestroyError] = useState<string | null>(null);
  // Shared across every CliSetupControl row below (one queue per page, not
  // per tool) so an install click on tool B while tool A is still installing
  // enqueues instead of firing a second concurrent install on the same box.
  const installQueue = useInstallQueue();

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
    // Reuses the real dashboard's own classNames (.fleet-hw-dash-header/
    // -identity/-gauges/-band/-panel, fleet-theme.css) so the identity
    // block, the resource-gauge row and both two-panel bands land at the
    // exact spot the real gateway dashboard renders into — was previously
    // one bare 12px bar inside a single `.fleet-list-row`, standing in for
    // this whole multi-section dashboard.
    return (
      <main className="fleet-hw-dashboard" aria-busy="true" aria-label="Loading">
        {backLink}
        <header className="fleet-hw-dash-header">
          <div className="fleet-hw-dash-identity">
            <div className="fleet-skeleton-bar" style={{ width: 40, height: 40, borderRadius: 8 }} />
            <div className="fleet-hw-dash-identity-text">
              <div className="fleet-skeleton-bar" style={{ width: 150, height: 15 }} />
              <div className="fleet-skeleton-bar" style={{ width: 100, height: 11, opacity: 0.7 }} />
            </div>
          </div>
          <div className="fleet-skeleton-bar" style={{ width: 74, height: 20, borderRadius: 999 }} />
        </header>

        {/* The health line — the real page's first answer, so the skeleton
            stands in at the same spot rather than at a gauge grid that no
            longer exists. */}
        <div className="fleet-hw-dash-health">
          <div className="fleet-skeleton-bar" style={{ width: 240, height: 12 }} />
        </div>

        <div className="fleet-hw-dash-band">
          <div className="fleet-hw-dash-panel">
            <div className="fleet-skeleton-bar" style={{ width: 170, height: 12, marginBottom: 14 }} />
            <div className="fleet-hw-card">
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "55%", height: 12 }} /></div>
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} /></div>
            </div>
          </div>
          <div className="fleet-hw-dash-panel">
            <div className="fleet-skeleton-bar" style={{ width: 80, height: 12, marginBottom: 14 }} />
            <div className="fleet-hw-card">
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "65%", height: 12 }} /></div>
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "48%", height: 12 }} /></div>
            </div>
          </div>
        </div>

        <div className="fleet-hw-dash-band">
          <div className="fleet-hw-dash-panel">
            <div className="fleet-skeleton-bar" style={{ width: 190, height: 12, marginBottom: 14 }} />
            <div className="fleet-hw-card">
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "50%", height: 12 }} /></div>
            </div>
          </div>
          <div className="fleet-hw-dash-panel">
            <div className="fleet-skeleton-bar" style={{ width: 110, height: 12, marginBottom: 14 }} />
            <div className="fleet-hw-card">
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "60%", height: 12 }} /></div>
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "45%", height: 12 }} /></div>
              <div className="fleet-hw-row"><div className="fleet-skeleton-bar" style={{ width: "52%", height: 12 }} /></div>
            </div>
          </div>
        </div>
      </main>
    );
  }

  if (!gateway) {
    return (
      <main className="fleet-hw-dashboard">
        {backLink}
        <div className="fleet-empty" style={{ marginTop: 20 }}>
          <div className="fleet-empty-icon">
            <ServerOff size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-empty-title">This computer isn't in your workspace</div>
          <div className="fleet-empty-desc">It may have been removed, or the link is stale.</div>
        </div>
      </main>
    );
  }

  const isCloud = gateway.hardware_kind === "cloud_vps";
  const vpsId = String((gateway.metadata as Record<string, unknown> | undefined)?.vps_id || "").trim();
  const canDestroy = isCloud && vpsId.length > 0;

  const destroyServer = async () => {
    if (!vpsId) return;
    setDestroying(true);
    setDestroyError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/hardware/vps/${encodeURIComponent(vpsId)}`, {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE", {}),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`;
        throw new Error(detail);
      }
      router.push(backHref);
    } catch (e) {
      setDestroyError(e instanceof Error ? e.message : "Could not destroy this server.");
      setDestroying(false);
    }
  };
  const connPresentation = connectionPresentation(gateway);
  const heartbeatAge = gateway.heartbeat_age_seconds;
  const serviceInventory: ServiceInventoryItem[] = gateway.metadata?.service_inventory || [];
  const byId = new Map(serviceInventory.map((item) => [String(item.id || ""), item]));
  const shellAccess = shellAccessPresentation(gateway);

  // Header sub-line: "darwin-arm64 · Local computer · paired Jul 21, 2026" —
  // platform · location/type · paired date, each segment omitted when this
  // box genuinely has no value for it rather than printing a placeholder.
  const platformSegment = (gateway.platform || "").trim() || "unknown platform";
  const locationSegment = isCloud ? (gateway.hardware_label || "Cloud server") : "Local computer";
  const pairedSegment = gateway.created_at
    ? `paired ${formatDate(gateway.created_at, { month: "short", day: "numeric", year: "numeric" })}`
    : null;
  const headerSubline = [platformSegment, locationSegment, pairedSegment].filter(Boolean).join(" · ");

  // Real resource snapshot this box last heartbeated — feeds both the gauge
  // cards and the local-model memory gate below, so the two never quote a
  // different number for the same box.
  const resources = gateway.metadata?.resources ?? gateway.resources;
  const gaugeCards = buildGaugeCards(resources);

  // ── QUESTION 1: is this computer working? ──────────────────────────────
  // One line. The state, plus the ONE fact that could break it — chosen by
  // priority in hardware-detail-shape.ts, never a list glued together.
  const dockerStatus = byId.get("docker")?.status as ProbeStatus;
  const launchRepairBlocked = gateway.gateway_update_refusal_code === "launch_path_not_updatable";
  const fullAccessAuthorizedButOff =
    gateway.runtime_access_mode === "full_access"
      ? gateway.shell_full_access_locally_enabled === false
        ? true
        : gateway.shell_full_access_locally_enabled === true
          ? false
          : null
      : false;
  const healthFacts = {
    connectionTone: connPresentation.tone as Parameters<typeof planBoxHealth>[0]["connectionTone"],
    connectionLabel: connPresentation.label,
    dockerStatus,
    fullAccessAuthorizedButOff,
    cannotReceiveUpdates: launchRepairBlocked,
    executionBlocked: String(gateway.connection_status || "").toLowerCase() === "execution_blocked",
  };
  const health = planBoxHealth(healthFacts);

  // ── QUESTION 2: what can it do? ────────────────────────────────────────
  // The four coding CLIs keep their own control (CliSetupControl owns its
  // row: install/sign-in are real multi-step flows with their own state).
  // Everything else is a plain CapabilityRowView. Both go through the same
  // rule module, so "ready" means the same thing in every row.
  const cliRows = INSTALLABLE_TOOL_ORDER.map((id) => ({
    id,
    runtime: CLI_ROW_RUNTIME[id],
    row: planCliCapabilityRow(id, CAPABILITY_LABEL[id], gatewayRuntimeState(gateway, CLI_ROW_RUNTIME[id])),
  }));
  const sandboxRow = planSandboxCapabilityRow(dockerStatus);
  const localModelRow = planLocalModelCapabilityRow(
    byId.get("ollama")?.status as ProbeStatus,
    resources?.memory_total_bytes ?? null,
  );
  const channelRowState = planChannelTransportState({
    transportStatus: byId.get("openclaw")?.status as ProbeStatus,
    pluginsStatus: byId.get("openclaw_channel_plugins")?.status as ProbeStatus,
  });

  // Channels through this box — derived from the SAME boundAgents this
  // page already resolves for "Agents running on this computer" (see
  // boundAgents above: agent.preferred_gateway_id === this gateway).
  // fleet_list_agents only returns each agent's PRIMARY enabled channel
  // (key, plus a "+N" suffix when more are enabled) — a real, known gap:
  // an agent with 2+ channels only contributes its first one here.
  const channelKeys = Array.from(
    new Set(
      boundAgents
        .map((a) => (a.channel || "").split(" ")[0].trim())
        .filter((key) => key.length > 0),
    ),
  );
  // Which agent a "Set up" click provisions for — the transport is
  // provisioned against a specific agent's channel policy, so the first
  // agent pinned to this box is as good a choice as any (provisioning is
  // additive/idempotent, never destructive to another agent's setup).
  const setupAgentId = boundAgents[0]?.agent_id || null;
  const channelRow = planChannelCapabilityRow(channelRowState, Boolean(setupAgentId));

  // ── QUESTION 3: what's wrong? ──────────────────────────────────────────
  // Empty on a healthy box, and the whole section is then ABSENT — not
  // rendered as an "all clear" panel nobody needs. A capability that
  // already offers its own control is a next step, not a problem, and is
  // deliberately not repeated here.
  const problems = planBoxProblems({
    ...healthFacts,
    updateRefusalReason: gateway.gateway_update_refusal_reason || null,
  });
  const somethingIsWrong = boxHasProblem(problems, [
    ...cliRows.map((c) => c.row),
    sandboxRow,
    localModelRow,
    channelRow,
  ]);

  return (
    <main className="fleet-hw-dashboard">
      {backLink}

      <header className="fleet-hw-dash-header">
        <div className="fleet-hw-dash-identity">
          <TintTile accent size={40}>
            {isCloud ? <Server size={20} strokeWidth={1.75} /> : <Cpu size={20} strokeWidth={1.75} />}
          </TintTile>
          <div className="fleet-hw-dash-identity-text">
            <HardwareRenameField
              gatewayId={targetGatewayId}
              displayName={gatewayLabel(gateway)}
              onRenamed={() => void refresh()}
            />
            <span className="fleet-hw-dash-subline">{headerSubline}</span>
          </div>
        </div>
        <span className="fleet-hw-dash-status">
          <StatusChip tone={connPresentation.tone} label={health.headline} />
        </span>
      </header>

      {/* ── 1. IS THIS COMPUTER WORKING? ────────────────────────────────
          One line. On a healthy box the caveat is null and this is the
          whole answer — the pill above plus, at most, live usage. The
          status pill used to carry a glued-on suffix ("Online · gateway
          healthy") which said the same word twice; the caveat is now the
          only thing that adds a fact. */}
      <div className="fleet-hw-dash-health">
        {health.caveat ? (
          <span className="fleet-hw-dash-health-caveat">
            <TriangleAlert size={14} strokeWidth={1.75} aria-hidden="true" />
            {health.caveat}
          </span>
        ) : (
          <span className="fleet-hw-dash-health-ok">Nothing needs your attention on this computer.</span>
        )}
        {gaugeCards.length > 0 ? (
          <span className="fleet-hw-dash-health-metrics">
            {gaugeCards.map((g) => (
              <span className="fleet-hw-list-resource-item" key={g.key} title={`${g.label} — ${g.value}`}>
                {g.icon}
                {g.key === "memory" ? `${Math.round(g.pct)}%` : g.value}
              </span>
            ))}
          </span>
        ) : null}
      </div>

      {/* ── 2. WHAT CAN IT DO? ───────────────────────────────────────────
          One row per capability: name, honest state, at most ONE action.
          This replaced two lists that used to sit side by side — "AI
          coding tools" with buttons and "Detected on this machine" as a
          read-only readout. That split put "Not detected" next to Install
          buttons, and the read-only half mixed real signal with noise: the
          founder's own local PostgreSQL reported "Not responding" on a
          page about an Agent Computer, where it means nothing. PostgreSQL
          and GPU are gone; Docker and Ollama are here, named for what they
          let an agent DO rather than for the software they are. */}
      <div className="fleet-detail-section-title">What this computer can do</div>
      <div className="fleet-hw-card">
        {cliRows.map(({ id, runtime, row }) =>
          row.healthy ? (
            <CapabilityRowView key={id} row={row} />
          ) : (
            <div
              className="fleet-hw-row"
              key={id}
              style={{ flexDirection: "column", alignItems: "stretch", justifyContent: "flex-start", gap: 4 }}
            >
              {/* CliSetupControl owns its own row layout for the states that
                  have a real multi-step flow behind them (install, then the
                  method picker, then the device-code exchange). */}
              <CliSetupControl
                runtime={runtime}
                state={gatewayRuntimeState(gateway, runtime)}
                gatewayId={targetGatewayId}
                workspaceId={workspaceId}
                refresh={refresh}
                isCloud={isCloud}
                installQueue={installQueue}
              />
            </div>
          ),
        )}
        <CapabilityRowView row={sandboxRow} />
        <CapabilityRowView row={localModelRow} />
        <ChannelCapabilityRow
          openclawStatus={byId.get("openclaw")?.status as ProbeStatus}
          pluginsStatus={byId.get("openclaw_channel_plugins")?.status as ProbeStatus}
          gatewayId={targetGatewayId}
          workspaceId={workspaceId}
          setupAgentId={setupAgentId}
          refresh={refresh}
        />
      </div>

      {/* ── 3. WHAT'S WRONG? ─────────────────────────────────────────────
          Rendered only when something IS. On a healthy computer this whole
          section — problems, repair instructions and diagnostics — is
          absent from the page rather than sitting there empty. */}
      {somethingIsWrong ? (
        <>
          {/* The HEADING renders only when there is something to list under
              it. A box whose only fault is Docker being down has already
              said so twice — in the health line at the top and in Docker's
              own row — so a "What's wrong" heading over nothing but a
              Diagnostics button would be a third empty restatement. What
              that box still needs from this section is the diagnostics and
              repair below, and it gets exactly that. */}
          {problems.length > 0 || launchRepairBlocked ? (
          <>
          <div className="fleet-detail-section-title">What&apos;s wrong</div>
          <div className="fleet-hw-card">
            {problems.map((problem) => (
              <div
                className="fleet-hw-row"
                key={problem.key}
                style={{ flexDirection: "column", alignItems: "stretch", gap: 4 }}
              >
                <span className="fleet-hw-label" style={{ color: "var(--text-primary)" }}>{problem.title}</span>
                {problem.detail ? (
                  <span
                    className="fleet-list-row-desc"
                    style={{ whiteSpace: "normal", overflow: "visible", textOverflow: "clip" }}
                  >
                    {problem.detail}
                  </span>
                ) : null}
              </div>
            ))}
            {launchRepairBlocked ? (
              <GatewayLaunchRepairRow gateway={gateway} />
            ) : null}
          </div>
          </>
          ) : null}
          <GatewayDoctorControl gatewayId={targetGatewayId} workspaceId={workspaceId} />
        </>
      ) : null}

      {/* Reference, below the answers. Everything here is a fact about the
          box that a person occasionally needs and never scans for. */}
      <div className="fleet-hw-dash-band">
        <div className="fleet-hw-dash-panel">
          <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>
            Agents on this computer{boundAgents.length > 0 ? ` · ${boundAgents.length}` : ""}
          </div>
          {agentsLoading ? (
            <div className="fleet-hw-card">
              <div className="fleet-hw-row">
                <div className="fleet-skeleton-bar" style={{ width: "50%", height: 12 }} />
              </div>
            </div>
          ) : boundAgents.length === 0 ? (
            <div className="fleet-hw-dash-empty">
              <div className="fleet-hw-dash-empty-title">No agents are pinned to this computer</div>
              <div className="fleet-hw-dash-empty-desc">
                Pin one from that agent&apos;s own Hardware tab.
              </div>
            </div>
          ) : (
            <div className="fleet-hw-card">
              {boundAgents.map((a) => {
                const status = deriveStatus(a.hardware_status, a.stopped?.active, Boolean(a.current_run_id));
                const primaryChannelKey = (a.channel || "").split(" ")[0].trim();
                const channelLabel = primaryChannelKey ? CHANNEL_LABELS[primaryChannelKey] || primaryChannelKey : "";
                const subText = channelLabel ? `${status.label} · ${channelLabel}` : status.label;
                return (
                  <div key={a.agent_id} className="fleet-hw-dash-agent-row">
                    <StatusDot tone={status.tone} />
                    <span className="fleet-hw-dash-agent-text">
                      <span className="fleet-hw-dash-agent-name">{a.label || "Unnamed agent"}</span>
                      <span className="fleet-hw-dash-agent-sub">{subText}</span>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {channelKeys.length > 0 ? (
            <div className="fleet-hw-dash-channels">
              {channelKeys.map((key) => (
                <span className="fleet-hw-dash-channel-pill" key={key}>
                  {channelIconSrc(key) && <img src={channelIconSrc(key)} alt="" />}
                  {CHANNEL_LABELS[key] || key}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="fleet-hw-dash-panel">
          {/* NAMED APART FROM CHANNELS, DELIBERATELY. This is the one
              process that makes the computer reachable by Empyralis at all.
              The channel transport is a different program running beside
              it, and it lives up in the capability list under "Channels" —
              the two used to blur into one another here. */}
          <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>Connection to Empyralis</div>
          <div className="fleet-hw-card">
            <GatewaySelfUpdateControl gateway={gateway} gatewayId={targetGatewayId} workspaceId={workspaceId} refresh={refresh} />
            <GatewayRestartControl gatewayId={targetGatewayId} workspaceId={workspaceId} />
            <div className="fleet-hw-row">
              <span className="fleet-hw-label">Last check-in</span>
              <span className="fleet-hw-value">
                {typeof heartbeatAge === "number"
                  ? heartbeatAge < 60 ? `${heartbeatAge}s ago` : timeAgo(gateway.last_heartbeat_at)
                  : gateway.last_heartbeat_at ? timeAgo(gateway.last_heartbeat_at) : "—"}
              </span>
            </div>
            <div className="fleet-hw-row">
              <span className="fleet-hw-label">Connected for</span>
              <span className="fleet-hw-value">{formatUptime(gateway.latest_connected_at)}</span>
            </div>
            {gateway.runtime_access_label && (
              <div className="fleet-hw-row">
                <span className="fleet-hw-label">What agents may touch</span>
                <span className="fleet-hw-value">{shellAccess.label}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {canDestroy && (
        <>
          <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Danger zone</div>
          <div className="fleet-hw-card">
            <div className="fleet-hw-row">
              <span className="fleet-hw-label">Destroy server</span>
              <span className="fleet-hw-value">
                <button type="button" className="fleet-btn fleet-btn--danger" onClick={() => setDestroyOpen(true)}>
                  Destroy server
                </button>
              </span>
            </div>
            <div className="fleet-hw-note" style={{ paddingTop: 10, borderTop: "1px solid var(--border)" }}>
              Permanently deletes the DigitalOcean droplet backing this computer and stops billing for it.
              Agents pinned here lose hardware access immediately. This can&apos;t be undone.
            </div>
            {destroyError && (
              <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{destroyError}</span>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={destroyOpen}
        title="Destroy this server?"
        body={`This deletes the underlying DigitalOcean droplet for "${gatewayLabel(gateway)}" and stops billing for it. Agents pinned to this computer lose hardware access immediately — this can't be undone.`}
        confirmLabel="Destroy server"
        confirmTone="danger"
        busy={destroying}
        onConfirm={() => void destroyServer()}
        onCancel={() => {
          setDestroyOpen(false);
          setDestroyError(null);
        }}
      />
    </main>
  );
}
