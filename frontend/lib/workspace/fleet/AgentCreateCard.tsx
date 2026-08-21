"use client";

/**
 * THE AGENT-CREATION SURFACE — a real, sequential setup flow, in FOUR steps.
 *
 * ```
 *  1 Identity & placement   name · what it does · where it runs
 *  2 Brain                  who pays ▸ provider ▸ model
 *      └── "Create agent" ──▶ the agent becomes real here
 *  3 Channels               how people reach it, optional
 *      └── "Next" / "Skip for now"
 *  4 Apps                   what it can reach, optional
 *      └── "Finish" / "Skip for now" ──▶ into the agent
 * ```
 *
 * Channels and Apps were merged into one "Reach" screen for exactly one day
 * and the founder reversed it: *"channels and application must be
 * separated, do you understand?"* The merge's own argument — two optional
 * screens read as ceremony — is answered by the one-press skip on each,
 * never by fusing two different questions. agent-create-wizard.ts's header
 * carries the full reasoning.
 *
 * The step RULES live in agent-create-wizard.ts / agent-create-placement.ts /
 * agent-create-brain.ts — pure and tested. This file renders them.
 *
 * ── What the previous version had lost, measured rather than recalled ────
 * The shipped card asked for a name, an optional prompt and ONE model
 * `<select>`. Grepping it for `platform_credits|byok|cli_subscription`
 * returned zero: the entire "who pays for this model" question was gone, and
 * so were placement and hardware. Those are back, in the order that makes
 * them honest — placement FIRST, because "Your subscription" and "Run
 * locally" both route the brain through a Gateway on a real machine, and
 * offering them to a cloud-only agent is a control that cannot be completed.
 *
 * ── AND WHAT IT MUST NOT BRING BACK: the project question ────────────────
 * The deleted FleetCreateAgentWizard's Placement step carried a "Which
 * project?" `<select>`. An agent belongs to the WORKSPACE (CLAUDE.md,
 * 2026-08-20), so that field does not return. `currentProjectId` is still
 * resolved silently to satisfy a required backend field, and is never
 * rendered and never a choice.
 *
 * ── THE COMMIT, and the one place two facts could collapse into one ──────
 *
 * ```
 * "Create agent"
 *   1  byok with a pasted key ─▶ POST vault credential + provider profile
 *                                BEFORE anything exists. A failure here has
 *                                nothing to explain away: no agent yet.
 *   2  POST /fleet/agents        name · instructions · project · model_choice
 *                                ─▶ THE AGENT IS REAL FROM THIS LINE ON
 *   3  PATCH  (only when needed) placement columns, and/or the machine-bound
 *                                model_config the 3-key create path cannot
 *                                carry (gateway_binding + runtime + a
 *                                real-box check that lives in
 *                                fleet_configure_agent and is REUSED, never
 *                                copied into a thinner second validator)
 * ```
 *
 * The PATCH is a step that can independently fail after a commit — the exact
 * shape CLAUDE.md names as a recurring defect — so the obligation it carries
 * is exact and is honoured in `create()` below: a failure there is reported
 * as ITS OWN fact ("<Name> was created, but …"), the sequence CONTINUES into
 * Channels because the agent genuinely exists, and it is never reported as
 * "couldn't create the agent". A cloud-placed platform/byok agent — the
 * overwhelmingly common path — skips the PATCH entirely and is one atomic
 * POST, exactly as before.
 *
 * ── Steps 3 and 4 are the REAL tabs, not simplified copies ───────────────
 * ChannelsTab and ConnectorsTab are imported from FleetAgentDetail. A second,
 * creation-only channel picker would be a fifth copy of a channel list in a
 * codebase that has already shipped four and drifted on all four.
 *
 * ── Corrections from earlier reviews that still stand ────────────────────
 *
 * ```
 * X CREATED AN AGENT       the head's X is gone from the moment the commit
 *                          lands; the control becomes "Finish later", which
 *                          is what it does. Nothing labelled cancel survives
 *                          the commit.
 * "TOOLS" WAS THE WRONG    Configure has a separate, genuinely different
 * WORD                     "Tools" section. The connector picker is Apps.
 * A MODAL IS PORTALLED     `position: fixed` does not save an element whose
 *                          ANCESTOR is `display: none` — the agents page's
 *                          own responsive split pane hid this surface
 *                          entirely at 375px. It renders into document.body.
 * THE PURPLE               at most ONE accent-filled element is on screen:
 *                          the forward button, and only when it can be
 *                          pressed (`forward.accent`).
 * ```
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, Check, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { composerSubmitButtonClass } from "./create-accent";
import { resolveAgentCreateName } from "./agent-create-card";
import {
  findAgentCreateModelChoice,
  groupAgentCreateModelChoices,
  resolveAgentCreateModelId,
} from "./agent-create-model";
import { useAgentCreateModelCatalog } from "./agent-create-model-catalog";
import {
  AGENT_CREATE_BRAIN_OPTIONS,
  AGENT_CREATE_DEFAULT_BRAIN_MODE,
  agentCreateBrainOptionsFor,
  agentCreateBrainPlacementNote,
  planAgentCreateBrain,
  type AgentCreateBrainMode,
} from "./agent-create-brain";
import {
  AGENT_CREATE_DEFAULT_PLACEMENT,
  AGENT_CREATE_PLACEMENTS,
  hardwareNodeId,
  hardwareNodeLabel,
  hardwareNodeOnline,
  nodesForPlacement,
  planAgentCreatePlacement,
  type AgentCreatePlacement,
  type HardwareNodeLike,
} from "./agent-create-placement";
import {
  AGENT_CREATE_STEPS,
  agentCreateCloseIntent,
  agentCreateNextStep,
  agentCreatePreviousStep,
  agentCreateStepStatus,
  agentCreateSurfaceTitle,
  planAgentCreateFooter,
  type AgentCreateStepId,
} from "./agent-create-wizard";
import { createAgentQuickly } from "./agent-quick-create";
import { ChannelsTab, ConnectorsTab, isChannelConnected } from "./FleetAgentDetail";
import {
  BYOK_PROVIDERS,
  FREEFORM_MODEL_PROVIDERS,
  LOCAL_PROVIDERS,
  SUBSCRIPTION_PROVIDERS,
  defaultModelForProvider,
  modelsForProvider,
  normalizeCliRuntime,
  providerLabel,
  runtimeForProvider,
} from "./fleet-provider-constants";
import { useCodexModelCatalog, visibleCodexModels } from "./fleet-model-config";
import {
  useFleetAgentChannels,
  useFleetAgentConnectors,
  type FleetAgent,
  type FleetProject,
} from "./fleet-data";
import { CloudVpsSetupPanel } from "@/lib/workspace/cloud-vps-setup-panel";
import { SshServerConnectPanel } from "@/lib/workspace/ssh-server-connect-panel";
import { GatewayPairPanel, type GatewayRegistrationRecord } from "@/lib/gateway/GatewayPairPanel";

import "./agent-create-surface.css";

function exitDurationMs(): number {
  if (typeof window === "undefined") return 150;
  const styles = getComputedStyle(document.documentElement);
  const raw = styles.getPropertyValue("--dur-2").trim();
  const ms = raw.endsWith("ms") ? parseFloat(raw) : parseFloat(raw) * 1000;
  return Number.isFinite(ms) && ms > 0 ? ms : 150;
}

/** The workspace's paired machines, from the SAME
 *  `/api/gateway/registrations` endpoint the Hardware page reads. Local and
 *  self-contained rather than gateway-box-picker's own hook because this
 *  needs `hardware_kind`, which that hook's type does not model — the same
 *  reason the deleted wizard had its own copy. */
function useWorkspaceHardwareNodes(workspaceId: string) {
  const [nodes, setNodes] = useState<HardwareNodeLike[]>([]);
  // "have we been told yet" — NOT "are there none". The two are different
  // facts and the placement plan refuses to state the second while the first
  // is true (agent-create-placement.ts).
  const [known, setKnown] = useState(false);

  const refresh = useCallback(async (): Promise<HardwareNodeLike[]> => {
    try {
      const res = await fleetAuthorizedFetch(
        `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
      const data = res.ok ? await res.json().catch(() => ({})) : {};
      const list = data?.items || data?.registrations || (Array.isArray(data) ? data : []);
      const arr: HardwareNodeLike[] = Array.isArray(list) ? list : [];
      setNodes(arr);
      return arr;
    } catch {
      setNodes([]);
      return [];
    } finally {
      setKnown(true);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { nodes, known, refresh };
}

export function AgentCreateCard({
  workspaceId,
  currentProjectId,
  projects,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  /** The project this was opened from, if any — used only to silently seed
   *  the create request's required project_id field. Never rendered, never
   *  a choice: agents belong to the workspace. */
  currentProjectId?: string;
  projects: FleetProject[];
  onClose: () => void;
  /** Fired when the sequence finishes (or is closed after the agent is
   *  real) — the caller navigates from here, same as every "New agent"
   *  entry point already did. */
  onCreated: (result: { agentId: string; projectId: string }) => void;
}) {
  const [step, setStep] = useState<AgentCreateStepId>("identity");
  const [typedName, setTypedName] = useState("");
  const [suggestedName, setSuggestedName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  // ── Step 1: placement ───────────────────────────────────────────────────
  const [placement, setPlacement] = useState<AgentCreatePlacement>(AGENT_CREATE_DEFAULT_PLACEMENT);
  const [nodeId, setNodeId] = useState("");
  const { nodes, known: nodesKnown, refresh: refreshNodes } = useWorkspaceHardwareNodes(workspaceId);
  const [vpsPanelOpen, setVpsPanelOpen] = useState(false);
  const [sshPanelOpen, setSshPanelOpen] = useState(false);
  const [pairPanelOpen, setPairPanelOpen] = useState(false);
  const placementNodes = useMemo(() => nodesForPlacement(placement, nodes), [placement, nodes]);

  // ── Step 2: brain ───────────────────────────────────────────────────────
  const [brainMode, setBrainMode] = useState<AgentCreateBrainMode>(AGENT_CREATE_DEFAULT_BRAIN_MODE);
  const [platformModelId, setPlatformModelId] = useState("");
  const [byokProvider, setByokProvider] = useState(BYOK_PROVIDERS[0]?.id || "anthropic");
  const [byokModel, setByokModel] = useState("");
  const [byokKey, setByokKey] = useState("");
  const [subscriptionProvider, setSubscriptionProvider] = useState(
    SUBSCRIPTION_PROVIDERS[0]?.id || "claude_code_cli",
  );
  const [subscriptionModel, setSubscriptionModel] = useState("");
  const [localProvider, setLocalProvider] = useState(LOCAL_PROVIDERS[0]?.id || "ollama");
  const [localModel, setLocalModel] = useState("");

  const [created, setCreated] = useState<{ agentId: string; projectId: string } | null>(null);
  const [createdAgent, setCreatedAgent] = useState<FleetAgent | null>(null);

  const nameRef = useRef<HTMLInputElement | null>(null);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const closeTimer = useRef<number | null>(null);
  // A vault credential a PRIOR attempt already created, when a LATER part of
  // the same commit then failed. Without it, pressing "Create agent" again
  // mints a SECOND encrypted key for the same value with nothing referencing
  // the first. Cleared the moment the profile that references it succeeds.
  const pendingCredentialId = useRef<string | null>(null);

  const name = resolveAgentCreateName(typedName, suggestedName);

  // The platform/BYOK picker opens fully usable on the first frame (the
  // platform tiers are synchronous constants) and folds in any credentialed
  // provider's own live models when they arrive — never a spinner in front
  // of a control that already works. See agent-create-model-catalog.ts.
  const { choices } = useAgentCreateModelCatalog(workspaceId);
  const platformChoices = useMemo(() => choices.filter((c) => c.mode === "platform_credits"), [choices]);
  const byokChoices = useMemo(() => choices.filter((c) => c.mode === "byok_api"), [choices]);
  const selectedPlatformId = resolveAgentCreateModelId(platformChoices, platformModelId);
  const selectedPlatformChoice = findAgentCreateModelChoice(platformChoices, selectedPlatformId);
  const platformGroups = useMemo(
    () => groupAgentCreateModelChoices(platformChoices),
    [platformChoices],
  );

  // Which BYOK providers this workspace already holds a usable credential
  // for — the live sweep's own answer, never a guess. A provider absent from
  // it needs its key pasted here, and its model list falls back to the
  // static per-provider catalog, which is EXACTLY what the server validates
  // the pick against (provider_profiles.model_is_known_for_provider).
  const credentialedProviders = useMemo(
    () => new Set(byokChoices.map((c) => c.provider)),
    [byokChoices],
  );
  const byokProviderHasCredential = credentialedProviders.has(byokProvider);
  const liveByokModels = useMemo(
    () => byokChoices.filter((c) => c.provider === byokProvider).map((c) => c.model),
    [byokChoices, byokProvider],
  );
  const byokModelOptions = byokProviderHasCredential && liveByokModels.length > 0
    ? liveByokModels
    : modelsForProvider(byokProvider);

  // The picked computer's OWN Codex model list — the live catalog, not the
  // hand-typed mirror (which already carries ids OpenAI has retired).
  const codexCatalog = useCodexModelCatalog(
    workspaceId,
    nodeId,
    normalizeCliRuntime(runtimeForProvider(subscriptionProvider)),
  );
  const liveCodexModels = subscriptionProvider === "openai-codex" ? visibleCodexModels(codexCatalog) : null;

  // Keep each mode's model in step with its own provider, and never across
  // modes — a BYOK model id is not a subscription model id.
  useEffect(() => {
    setByokModel(FREEFORM_MODEL_PROVIDERS.has(byokProvider) ? "" : defaultModelForProvider(byokProvider));
  }, [byokProvider]);
  useEffect(() => {
    setSubscriptionModel(
      FREEFORM_MODEL_PROVIDERS.has(subscriptionProvider) ? "" : defaultModelForProvider(subscriptionProvider),
    );
  }, [subscriptionProvider]);
  useEffect(() => {
    setLocalModel(defaultModelForProvider(localProvider || "ollama"));
  }, [localProvider]);
  useEffect(() => {
    if (!liveCodexModels || liveCodexModels.length === 0) return;
    if (liveCodexModels.some((m) => m.id === subscriptionModel)) return;
    setSubscriptionModel(liveCodexModels.find((m) => m.isDefault)?.id || liveCodexModels[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveCodexModels]);

  const placementPlan = planAgentCreatePlacement({
    placement,
    nodeId,
    nodesKnown,
    availableNodeCount: placementNodes.length,
  });

  const activeProvider =
    brainMode === "platform"
      ? selectedPlatformChoice?.provider || ""
      : brainMode === "byok"
        ? byokProvider
        : brainMode === "subscription"
          ? subscriptionProvider
          : localProvider;
  const activeModel =
    brainMode === "platform"
      ? selectedPlatformChoice?.model || ""
      : brainMode === "byok"
        ? byokModel
        : brainMode === "subscription"
          ? subscriptionModel
          : localModel;

  const brainPlan = planAgentCreateBrain({
    placement,
    mode: brainMode,
    provider: activeProvider,
    model: activeModel,
    apiKey: byokKey,
    providerHasCredential: byokProviderHasCredential,
    nodeId,
  });

  // Only asked for once the Channels step is actually open — a creation
  // sequence must not poll an agent's channels through screens that do not
  // show them.
  const {
    channels,
    slackChannelBinding,
    telegramBotConnected,
    loading: channelsLoading,
    refresh: refreshChannels,
  } = useFleetAgentChannels(workspaceId, step === "channels" && created ? created.agentId : null);
  const connectedChannelCount = channels.filter((c) =>
    isChannelConnected(c, slackChannelBinding, telegramBotConnected),
  ).length;

  // …and the same for apps, so the last button can say "Finish" rather than
  // "Skip for now" when something actually got connected. This is a SECOND
  // read of the list ConnectorPicker fetches for itself one component down —
  // `useFleetAgentConnectors` holds no shared cache — and it is accepted
  // rather than plumbed through a callback: one small GET, on one step, buys
  // the footer a fact it otherwise has to guess at, and a `onConnected` prop
  // threaded up from the picker would have to fire on four different success
  // paths inside it (OAuth return, pasted fields, reuse, disconnect) — four
  // places to forget, for the same answer the list already carries.
  const { connectors: agentConnectors } = useFleetAgentConnectors(
    workspaceId,
    step === "apps" && created ? created.agentId : null,
  );
  const connectedAppCount = agentConnectors.filter((c) => c.connected).length;

  const footer = planAgentCreateFooter({
    step,
    created: Boolean(created),
    busy,
    hasName: name.trim().length > 0,
    placementReady: placementPlan.ready,
    placementBlockedReason: placementPlan.blockedReason,
    brainReady: brainPlan.ready,
    brainBlockedReason: brainPlan.blockedReason,
    // `loading` is the honest "we have not been told yet".
    channelsKnown: Boolean(created) && !channelsLoading,
    connectedChannelCount,
    connectedAppCount,
  });

  // Suggested name — fleet_create_agent's own fallback pool, fetched once so
  // the field opens pre-filled rather than blank.
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/suggested-name`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d?.name) return;
        setSuggestedName(String(d.name));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  /* THE CARD NO LONGER FOCUSES THE NAME FIELD. It used to, on every arrival at
     the identity step, and that is why the founder kept seeing a focused
     control before he had touched anything — programmatic .focus() satisfies
     :focus-visible exactly like a real Tab press, the same trap CLAUDE.md
     already records for FleetAgentDetail's tab strip. Removed 2026-08-21; a
     previous pass argued to keep it for typing speed and that argument lost.
     Do not point this at an INTERACTIVE control again.

     What replaced it is deliberately the dialog's own container, and it is not
     decoration: this dialog is role="dialog" aria-modal="true" with NO focus
     trap, so the Name field was also its only focus ENTRY point. Measured
     after removing it — Tab walked straight out of the open dialog into the
     rail behind the backdrop, which makes aria-modal a false claim. Focusing
     the surface (tabIndex={-1}) puts the keyboard inside the dialog with
     nothing painted: the container is not a form control, it has no border of
     its own to step up, and the app draws no focus ring at all. Same
     heading-not-control idiom TaskDetailView/DocumentDetailView already use.
     On MOUNT only — the old effect keyed on `step`, so returning to step 1
     would yank focus out of whatever the person was using. */
  useEffect(() => {
    surfaceRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(
    () => () => {
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    },
    [],
  );

  const finish = useCallback(() => {
    if (created) onCreated(created);
    else onClose();
  }, [created, onClose, onCreated]);

  /** Dismiss. Before the commit that discards; after it, the agent is real
   *  and already saved, so it opens the agent instead — "I stopped early" is
   *  not "nothing happened". */
  const requestClose = useCallback(() => {
    if (closing || busy) return;
    setClosing(true);
    const intent = agentCreateCloseIntent(Boolean(created));
    closeTimer.current = window.setTimeout(
      () => (intent === "open_agent" ? finish() : onClose()),
      exitDurationMs(),
    );
  }, [busy, closing, created, finish, onClose]);

  /** Best-effort — ChannelsTab and ConnectorsTab both tolerate a null agent,
   *  so a failed hydrate degrades to a slightly emptier panel rather than a
   *  broken step. */
  const hydrateCreatedAgent = useCallback(
    async (agentId: string) => {
      try {
        const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
          credentials: "include",
        });
        const data = res.ok ? await res.json().catch(() => ({})) : {};
        const found = (data?.agents || []).find((a: FleetAgent) => a.agent_id === agentId) || null;
        setCreatedAgent(found);
      } catch {
        /* keep null */
      }
    },
    [workspaceId],
  );

  /** Saves the pasted key as a vault credential + an enabled provider
   *  profile. Runs BEFORE the agent is created, so a failure here leaves
   *  nothing behind to explain. Idempotent across retries via
   *  pendingCredentialId. */
  const saveApiKey = useCallback(async () => {
    const label = `${providerLabel(byokProvider)} — ${name.trim() || "agent"}`;
    let credentialId = pendingCredentialId.current;
    if (!credentialId) {
      const res = await fleetAuthorizedFetch("/api/credentials/vault", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider: byokProvider,
          label,
          mode: "byok",
          credentials: { api_key: byokKey.trim() },
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(getErrorMessage(data, `Could not save your API key (HTTP ${res.status})`));
      credentialId = String(data?.id || "");
      pendingCredentialId.current = credentialId;
    }
    const profileRes = await fleetAuthorizedFetch("/api/providers/profiles", {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({
        workspace_id: workspaceId,
        provider: byokProvider,
        label,
        credential_id: credentialId,
        enabled: true,
      }),
    });
    if (!profileRes.ok) {
      const data = await profileRes.json().catch(() => ({}));
      // The key IS saved — say so, rather than a flat failure that implies
      // nothing happened and invites a retry that mints a second one.
      throw new Error(
        `Your API key was saved, but couldn't be assigned yet: ${getErrorMessage(data, `HTTP ${profileRes.status}`)}`,
      );
    }
    pendingCredentialId.current = null;
  }, [byokKey, byokProvider, name, workspaceId]);

  const create = useCallback(async () => {
    if (busy || created || !brainPlan.ready) return;
    setBusy(true);
    setError(null);

    // ── Before anything exists ──────────────────────────────────────────
    if (brainPlan.savesApiKey) {
      try {
        await saveApiKey();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not save your API key.");
        setBusy(false);
        return;
      }
    }

    // ── The commit ──────────────────────────────────────────────────────
    let result: { agentId: string; projectId: string };
    try {
      result = await createAgentQuickly(
        workspaceId,
        currentProjectId,
        projects,
        name.trim(),
        instructions.trim(),
        brainPlan.modelChoice,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
      setBusy(false);
      return;
    }

    // ── After it. THE AGENT EXISTS from here on, whatever happens next ──
    // Any failure below is reported as its own fact and the sequence
    // continues — never as "couldn't create the agent", which would be the
    // outcome-honesty law broken on the product's first screen.
    setCreated(result);
    const patch: Record<string, unknown> = { ...(placementPlan.patch || {}) };
    if (brainPlan.modelConfigPatch) patch.model_config = brainPlan.modelConfigPatch;
    if (Object.keys(patch).length > 0) {
      try {
        const res = await fleetAuthorizedFetch(
          `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(result.agentId)}`,
          {
            method: "PATCH",
            credentials: "include",
            headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
            body: JSON.stringify({ patch }),
          },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      } catch (e) {
        const detail = e instanceof Error ? e.message : "";
        const what = brainPlan.modelConfigPatch
          ? placementPlan.patch
            ? "where it runs and what runs it"
            : "what runs it"
          : "where it runs";
        setError(
          `${name.trim() || "The agent"} was created, but ${what} couldn't be saved — set it in Configure.` +
            (detail ? ` (${detail})` : ""),
        );
      }
    }

    void hydrateCreatedAgent(result.agentId);
    setStep("channels");
    setBusy(false);
  }, [
    brainPlan.modelChoice,
    brainPlan.modelConfigPatch,
    brainPlan.ready,
    brainPlan.savesApiKey,
    busy,
    created,
    currentProjectId,
    hydrateCreatedAgent,
    instructions,
    name,
    placementPlan.patch,
    projects,
    saveApiKey,
    workspaceId,
  ]);

  const goForward = useCallback(() => {
    if (footer.forward.disabled) return;
    if (footer.forward.action === "create") {
      void create();
      return;
    }
    if (footer.forward.action === "finish") {
      finish();
      return;
    }
    const next = agentCreateNextStep(step);
    if (next) setStep(next);
  }, [create, finish, footer.forward.action, footer.forward.disabled, step]);

  const goBack = useCallback(() => {
    if (!footer.back || footer.back.disabled) return;
    if (footer.back.action === "cancel") {
      requestClose();
      return;
    }
    const prev = agentCreatePreviousStep(step);
    if (prev) setStep(prev);
  }, [footer.back, requestClose, step]);

  /** Picking a machine anywhere — the list, a fresh VPS, a just-paired
   *  computer — goes through here, so no path can select a node without the
   *  brain step's binding following it. */
  const selectNode = useCallback((id: string) => {
    setNodeId(id);
  }, []);

  const adoptNewestNode = useCallback(
    async (kind: "cloud_vps" | "gateway") => {
      const before = new Set(nodes.map(hardwareNodeId));
      const after = await refreshNodes();
      const added = after.find(
        (n) =>
          !before.has(hardwareNodeId(n)) &&
          (kind === "cloud_vps"
            ? String(n.hardware_kind || "") === "cloud_vps"
            : String(n.hardware_kind || "") !== "cloud_vps"),
      );
      if (added) selectNode(hardwareNodeId(added));
    },
    [nodes, refreshNodes, selectNode],
  );

  const handleGatewayPaired = useCallback(
    (g: GatewayRegistrationRecord) => {
      setPairPanelOpen(false);
      selectNode(String(g.gateway_id || ""));
      void refreshNodes();
    },
    [refreshNodes, selectNode],
  );

  /** The last two steps each host a whole real tab, whose height swings
   *  widely — a filter press on Channels moves it between 4 cards and 21. */
  const embedsFullTab = step === "channels" || step === "apps";

  const agentHref = created
    ? `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(created.projectId)}/agents/${encodeURIComponent(created.agentId)}/hardware`
    : undefined;

  const brainOptions = agentCreateBrainOptionsFor(placement);
  const placementNote = agentCreateBrainPlacementNote(placement);

  /* An EMPTY list renders no sentence — just the control that fixes it.
     Measured live with the first build: "No computers paired yet." appeared
     twice on one screen, once here and once as the footer's blocked reason.
     The footer owns that fact (it is what explains a Next that will not
     move); here the "Pair this computer" button IS the empty state, per the
     founder's standing rule that a setup control does the work rather than
     explaining it. The LOADING line stays, because "still looking" is a
     different fact the footer deliberately refuses to state. */
  const nodeList = (addControls: React.ReactNode) => (
    <div className="agent-create-nodes">
      {!nodesKnown ? (
        <p className="agent-create-note" aria-busy="true">
          Looking for your machines…
        </p>
      ) : placementNodes.length === 0 ? null : (
        <div className="agent-create-options">
          {placementNodes.map((n) => {
            const id = hardwareNodeId(n);
            return (
              <button
                key={id}
                type="button"
                className={`agent-create-option${nodeId === id ? " is-selected" : ""}`}
                onClick={() => selectNode(id)}
                aria-pressed={nodeId === id}
              >
                <span className="agent-create-option-label">{hardwareNodeLabel(n)}</span>
                <span className="agent-create-option-body">{hardwareNodeOnline(n) ? "Online" : "Offline"}</span>
              </button>
            );
          })}
        </div>
      )}
      <div className="agent-create-node-actions">{addControls}</div>
    </div>
  );

  const surface = (
    <div
      className={`agent-create-backdrop${closing ? " is-closing" : ""}`}
      onMouseDown={requestClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          requestClose();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          goForward();
        }
      }}
    >
      {/* ONE FRAME, EVERY STEP. The founder: *"it stays the same size almost
          — it has a smallest size which you cannot make it smaller, and a
          biggest size you cannot make it bigger, even though it's a longer
          page."* A floor stops a two-control step collapsing into a stub; a
          ceiling stops the whole dialog tracking a filter that swings a grid
          between 4 cards and 25. Head and footer are pinned; the BODY is the
          only thing that scrolls. `--embed` only raises the floor to the
          ceiling for the step that hosts two whole tabs — the frame is the
          same object, never a second layout. */}
      <div
        className={`agent-create-surface${embedsFullTab ? " agent-create-surface--embed" : ""}${closing ? " is-closing" : ""}`}
        ref={surfaceRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-create-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="agent-create-head">
          <h2 id="agent-create-title" className="agent-create-title">
            {agentCreateSurfaceTitle(Boolean(created), name)}
          </h2>
          {/* An X is a cancel gesture, and there is nothing left to cancel
              once the commit has landed — pressing it used to leave a real,
              silently created agent behind. So the control itself changes
              with the fact. The rule is in agent-create-wizard.ts beside
              `agentCreateCloseIntent`, so label and behaviour cannot drift. */}
          {footer.dismiss.kind === "cancel" ? (
            <button
              type="button"
              className="agent-create-close"
              onClick={requestClose}
              aria-label={footer.dismiss.label}
            >
              <X size={15} strokeWidth={2} />
            </button>
          ) : (
            <button type="button" className="fleet-btn agent-create-defer" onClick={requestClose}>
              {footer.dismiss.label}
            </button>
          )}
        </div>

        {/* The sequence, always visible. Not a progress score — it is where
            you are, and where you are going. */}
        <ol className="agent-create-steps" aria-label="Setup steps">
          {AGENT_CREATE_STEPS.map((s, i) => {
            const status = agentCreateStepStatus(s.id, step);
            return (
              <li
                key={s.id}
                className={`agent-create-step is-${status}`}
                aria-current={status === "current" ? "step" : undefined}
              >
                <span className="agent-create-step-index" aria-hidden="true">
                  {status === "done" ? <Check size={11} strokeWidth={2.75} /> : i + 1}
                </span>
                <span className="agent-create-step-label">{s.label}</span>
              </li>
            );
          })}
        </ol>

        <div className="agent-create-body">
          {step === "identity" && (
            <>
              {/* The mark and the name are ONE CONTROL — this is the thing
                  being made, not a title field on a form. Deliberately NOT a
                  preview of the agent's real sigil: AgentSigil hashes the
                  agent's id, which does not exist yet. */}
              <div className="agent-create-identity">
                <label className="agent-create-label" htmlFor="agent-create-name">Name</label>
                <div className="agent-create-name-field">
                  <span className="agent-create-mark" aria-hidden="true">
                    <Bot size={18} strokeWidth={1.75} />
                  </span>
                  <input
                    ref={nameRef}
                    id="agent-create-name"
                    className="agent-create-name"
                    value={name}
                    placeholder="Agent name"
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(e) => setTypedName(e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        goForward();
                      }
                    }}
                  />
                </div>
              </div>

              <div className="agent-create-group">
                <label className="agent-create-label" htmlFor="agent-create-instructions">
                  What this agent does
                </label>
                <textarea
                  id="agent-create-instructions"
                  className="agent-create-input"
                  value={instructions}
                  rows={3}
                  placeholder="Optional. Editable anytime from Configure."
                  onChange={(e) => setInstructions(e.currentTarget.value)}
                />
              </div>

              <div className="agent-create-group">
                <span className="agent-create-label">Where it runs</span>
                <div className="agent-create-options">
                  {AGENT_CREATE_PLACEMENTS.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={`agent-create-option${placement === p.id ? " is-selected" : ""}`}
                      aria-pressed={placement === p.id}
                      onClick={() => {
                        setPlacement(p.id);
                        if (p.id === "cloud") setNodeId("");
                      }}
                    >
                      <span className="agent-create-option-label">{p.label}</span>
                      <span className="agent-create-option-body">{p.body}</span>
                    </button>
                  ))}
                </div>

                {placement === "vps" &&
                  nodeList(
                    <>
                      <button type="button" className="fleet-btn" onClick={() => setVpsPanelOpen(true)}>
                        Start a server
                      </button>
                      <button type="button" className="fleet-btn" onClick={() => setSshPanelOpen(true)}>
                        Connect your own
                      </button>
                    </>,
                  )}

                {placement === "gateway" &&
                  (pairPanelOpen ? (
                    <div className="agent-create-nodes">
                      <GatewayPairPanel workspaceId={workspaceId} compact onPaired={handleGatewayPaired} />
                    </div>
                  ) : (
                    nodeList(
                      <button type="button" className="fleet-btn" onClick={() => setPairPanelOpen(true)}>
                        Pair this computer
                      </button>,
                    )
                  ))}
              </div>
            </>
          )}

          {step === "brain" && (
            <>
              <div className="agent-create-group">
                <span className="agent-create-label">Who pays for the model</span>
                <div className="agent-create-options">
                  {brainOptions.map((o) => (
                    <button
                      key={o.id}
                      type="button"
                      className={`agent-create-option${brainMode === o.id ? " is-selected" : ""}`}
                      aria-pressed={brainMode === o.id}
                      onClick={() => setBrainMode(o.id)}
                    >
                      <span className="agent-create-option-label">{o.label}</span>
                      <span className="agent-create-option-body">{o.body}</span>
                    </button>
                  ))}
                </div>
                {/* Said ONCE, and only where something is genuinely missing.
                    agent-create-brain.ts decides; this renders. */}
                {placementNote ? <p className="agent-create-note">{placementNote}</p> : null}
              </div>

              {brainMode === "platform" && (
                <div className="agent-create-group">
                  <label className="agent-create-label" htmlFor="agent-create-platform-model">Model</label>
                  {/* Options carry what the model actually IS beside what it
                      is called — a model pick is a spend decision, so the
                      second half is never hidden behind a tooltip. */}
                  <select
                    id="agent-create-platform-model"
                    className="agent-create-select"
                    value={selectedPlatformId}
                    onChange={(e) => setPlatformModelId(e.currentTarget.value)}
                  >
                    {platformGroups.map((group) => (
                      <optgroup key={group.group} label={group.group}>
                        {group.choices.map((choice) => (
                          <option key={choice.id} value={choice.id}>
                            {choice.label === choice.detail ? choice.label : `${choice.label} — ${choice.detail}`}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </div>
              )}

              {brainMode === "byok" && (
                <>
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-byok-provider">Provider</label>
                    <select
                      id="agent-create-byok-provider"
                      className="agent-create-select"
                      value={byokProvider}
                      onChange={(e) => setByokProvider(e.currentTarget.value)}
                    >
                      {BYOK_PROVIDERS.map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                  {/* No key field once this provider already has one saved —
                      re-asking for a credential the workspace holds is a
                      control with nothing to do. */}
                  {byokProviderHasCredential ? (
                    <p className="agent-create-note">Using the {providerLabel(byokProvider)} key you already saved.</p>
                  ) : (
                    <div className="agent-create-group">
                      <label className="agent-create-label" htmlFor="agent-create-byok-key">API key</label>
                      <input
                        id="agent-create-byok-key"
                        className="agent-create-select"
                        type="password"
                        autoComplete="off"
                        value={byokKey}
                        placeholder="Paste your key"
                        onChange={(e) => setByokKey(e.currentTarget.value)}
                      />
                    </div>
                  )}
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-byok-model">Model</label>
                    {FREEFORM_MODEL_PROVIDERS.has(byokProvider) ? (
                      <input
                        id="agent-create-byok-model"
                        className="agent-create-select"
                        value={byokModel}
                        placeholder={byokProvider === "azure_openai" ? "Your deployment name" : "Model id"}
                        onChange={(e) => setByokModel(e.currentTarget.value)}
                      />
                    ) : (
                      <select
                        id="agent-create-byok-model"
                        className="agent-create-select"
                        value={byokModel}
                        onChange={(e) => setByokModel(e.currentTarget.value)}
                      >
                        {byokModelOptions.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    )}
                  </div>
                </>
              )}

              {brainMode === "subscription" && (
                <>
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-sub-provider">Subscription</label>
                    <select
                      id="agent-create-sub-provider"
                      className="agent-create-select"
                      value={subscriptionProvider}
                      onChange={(e) => setSubscriptionProvider(e.currentTarget.value)}
                    >
                      {SUBSCRIPTION_PROVIDERS.map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-sub-model">Model</label>
                    {FREEFORM_MODEL_PROVIDERS.has(subscriptionProvider) ? (
                      <input
                        id="agent-create-sub-model"
                        className="agent-create-select"
                        value={subscriptionModel}
                        placeholder="Leave blank for the CLI's own default"
                        onChange={(e) => setSubscriptionModel(e.currentTarget.value)}
                      />
                    ) : (
                      <select
                        id="agent-create-sub-model"
                        className="agent-create-select"
                        value={subscriptionModel}
                        onChange={(e) => setSubscriptionModel(e.currentTarget.value)}
                      >
                        {(liveCodexModels
                          ? liveCodexModels.map((m) => ({ id: m.id, label: m.displayName }))
                          : modelsForProvider(subscriptionProvider).map((m) => ({ id: m, label: m }))
                        ).map((o) => (
                          <option key={o.id} value={o.id}>{o.label}</option>
                        ))}
                      </select>
                    )}
                  </div>
                </>
              )}

              {brainMode === "local" && (
                <>
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-local-provider">Runtime</label>
                    <select
                      id="agent-create-local-provider"
                      className="agent-create-select"
                      value={localProvider}
                      onChange={(e) => setLocalProvider(e.currentTarget.value)}
                    >
                      {LOCAL_PROVIDERS.map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="agent-create-group">
                    <label className="agent-create-label" htmlFor="agent-create-local-model">Model</label>
                    <select
                      id="agent-create-local-model"
                      className="agent-create-select"
                      value={localModel}
                      onChange={(e) => setLocalModel(e.currentTarget.value)}
                    >
                      {modelsForProvider(localProvider || "ollama").map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}
            </>
          )}

          {/* TWO STEPS, one tab each — the founder's own instruction, and
              the reasoning is in agent-create-wizard.ts's header. Each is
              the REAL tab, never a creation-only copy of it. */}
          {step === "channels" && created && (
            <div className="agent-create-embed">
              <ChannelsTab
                workspaceId={workspaceId}
                agentId={created.agentId}
                agent={createdAgent}
                hardwareHref={agentHref}
                onChannelsChanged={refreshChannels}
              />
            </div>
          )}

          {step === "apps" && created && (
            <div className="agent-create-embed">
              <ConnectorsTab workspaceId={workspaceId} agentId={created.agentId} agent={createdAgent} />
            </div>
          )}
        </div>

        {error ? (
          <p className="agent-create-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="agent-create-foot">
          {/* Why the forward button will not move — or, on Reach, what stays
              undone if it is pressed anyway. One short fact, only once we
              actually know it, never on the button's own line. */}
          {footer.blockedReason ? (
            <p className="agent-create-blocked">{footer.blockedReason}</p>
          ) : (
            <span className="agent-create-foot-spacer" aria-hidden="true" />
          )}
          {footer.back ? (
            <button
              type="button"
              className="fleet-btn agent-create-back"
              onClick={goBack}
              disabled={footer.back.disabled}
            >
              {footer.back.label}
            </button>
          ) : null}
          {/* While this surface is open it owns the view's single accent
              fill, and the fill is spent only on a move that is actually
              available: a blocked forward keeps its label (a named disabled
              control is not a dead one) and drops to plain neutral. */}
          <button
            type="button"
            className={footer.forward.accent ? composerSubmitButtonClass() : "fleet-btn"}
            onClick={goForward}
            disabled={footer.forward.disabled}
          >
            {footer.forward.label}
          </button>
        </div>

        {/* Nested INSIDE the surface (not as backdrop siblings) so a click
            anywhere in these — including their own close buttons — stops at
            the surface's own stopPropagation and never bubbles out to the
            backdrop's dismiss, which would kill the whole sequence instead
            of just this sub-panel. */}
        <CloudVpsSetupPanel
          open={vpsPanelOpen}
          workspaceId={workspaceId}
          onClose={() => setVpsPanelOpen(false)}
          onConnected={() => {
            setVpsPanelOpen(false);
            void adoptNewestNode("cloud_vps");
          }}
        />
        <SshServerConnectPanel
          open={sshPanelOpen}
          workspaceId={workspaceId}
          onClose={() => setSshPanelOpen(false)}
          onConnected={() => {
            setSshPanelOpen(false);
            void adoptNewestNode("cloud_vps");
          }}
        />
      </div>
    </div>
  );

  // Rendered into the body, never in place — see the block comment above.
  // `typeof document` guards the server render: this is a "use client"
  // component, but Next still renders it on the server for the first paint,
  // and `document` does not exist there.
  return typeof document === "undefined" ? surface : createPortal(surface, document.body);
}
