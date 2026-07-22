"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import {
  BYOK_PROVIDERS,
  SUBSCRIPTION_PROVIDERS,
  LOCAL_PROVIDERS,
  FREEFORM_MODEL_PROVIDERS,
  providerLabel,
  modelsForProvider,
  defaultModelForProvider,
  runtimeForProvider,
} from "./fleet-provider-constants";
import { useFleetAgentChannels, type FleetAgent } from "./fleet-data";
import { GatewayBoxPicker, RUNTIME_LABELS } from "./gateway-box-picker";
import { ChannelsTab } from "./FleetAgentDetail";
import { ConnectorPicker } from "./ConnectorPicker";
import { GatewayPairPanel, type GatewayRegistrationRecord } from "@/lib/gateway/GatewayPairPanel";
import { CloudVpsSetupPanel } from "@/lib/workspace/cloud-vps-setup-panel";
import { SshServerConnectPanel } from "@/lib/workspace/ssh-server-connect-panel";

type WizardProviderMode = "platform" | "byok" | "subscription" | "local";
type Placement = "cloud" | "vps" | "gateway";

const STEP_LABELS = ["Placement", "Brain", "Channels", "Connections"];

// Server field: purpose_preset (customer_facing | internal_assistant |
// operator) — seeds this agent's DEFAULT instructions only (skipped
// entirely if the owner later types their own). It is not a security mode:
// nothing in authority enforcement reads it. Who may do what is decided
// per-message by sender identity (see the Tools tab / Properties "Customer
// access" hint), the same way for every agent regardless of this pick.
//
// "Operator" is intentionally not offered here — the owner is the
// operator, and Sage/the platform's own operator role still exists
// internally (fleet_tools.OPERATOR_ROLE), just never as a pick for a new
// agent. The remaining two picks ARE an architectural distinction: each
// carries a separate `audience` field (owner | external, see below) that a
// later backend task uses to gate the owner's connectors/credentials/
// memory — kept distinct from purpose_preset so that non-security,
// instruction-seeding field never has to double as the security signal.
const PURPOSE_PRESETS: { value: string; label: string; body: string; audience: "owner" | "external" }[] = [
  { value: "customer_facing", label: "Customer Support", audience: "external", body: "Talks to your customers — kept separate from your private accounts." },
  { value: "internal_assistant", label: "Personal Assistant", audience: "owner", body: "Works for you — trusted with your connected accounts and memory." },
];

function audienceForPreset(preset: string): "owner" | "external" {
  return PURPOSE_PRESETS.find((p) => p.value === preset)?.audience || "owner";
}

// ── Hardware nodes (VPS + paired Gateways) — same /api/gateway/registrations
// endpoint the Hardware page reads, partitioned by hardware_kind. A local,
// self-contained fetch (not gateway-box-picker's useWorkspaceGateways) because
// this needs the hardware_kind field that hook's type doesn't model. ─────────

type HardwareNode = {
  gateway_id?: string | null;
  id?: string | null;
  display_name?: string | null;
  platform?: string | null;
  status?: string | null;
  connection_status?: string | null;
  hardware_kind?: string | null;
  hardware_label?: string | null;
};

function nodeId(n: HardwareNode): string {
  return String(n.gateway_id || n.id || "").trim();
}
function nodeLabel(n: HardwareNode): string {
  return (
    String(n.hardware_label || "").trim() ||
    String(n.display_name || "").trim() ||
    String(n.platform || "").trim() ||
    nodeId(n) ||
    "Computer"
  );
}
function nodeOnline(n: HardwareNode): boolean {
  return `${n.connection_status || ""} ${n.status || ""}`.toLowerCase().includes("online");
}

function useWorkspaceHardwareNodes(workspaceId: string) {
  const [nodes, setNodes] = useState<HardwareNode[]>([]);
  const [loading, setLoading] = useState(true);

  async function refresh(): Promise<HardwareNode[]> {
    setLoading(true);
    try {
      const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" });
      const data = res.ok ? await res.json() : {};
      const list = data?.items || data?.registrations || (Array.isArray(data) ? data : []);
      const arr = Array.isArray(list) ? list : [];
      setNodes(arr);
      return arr;
    } catch {
      setNodes([]);
      return [];
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  return { nodes, loading, refresh };
}

/**
 * Create-agent wizard v2 — Placement → Brain → Channels → Connections.
 * The agent is created the moment Placement is committed (auto pool name,
 * project "General" unless opened from a specific project, capability_preset
 * standard) — everything after that is a PATCH, so closing early still
 * leaves a real, usable agent behind. Name/Project/Capability preset are no
 * longer steps: they're sane defaults, editable later from the Overview and
 * Model tabs.
 */
export function FleetCreateAgentWizard({
  workspaceId,
  onClose,
  onCreated,
  initialProjectId,
}: {
  workspaceId: string;
  onClose: () => void;
  onCreated: (agentId: string) => void;
  initialProjectId?: string;
}) {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [resolvedProjectId, setResolvedProjectId] = useState("");
  const [createdAgent, setCreatedAgent] = useState<FleetAgent | null>(null);
  // Hardware-aware "recommended" reuse (founder ruling, §29): set from the
  // Placement PATCH response (fleet_configure_agent's recommended_model_
  // config — computed server-side off the SAME llm_runtimes signal the box
  // picker already reads, see fleet_tools.recommended_model_config_for_
  // gateway) whenever a real gateway got bound. Non-null only when that box
  // already has an authenticated Claude Code or Codex CLI ready to reuse.
  const [recommendedModelConfig, setRecommendedModelConfig] = useState<{
    mode: string; provider: string; runtime: "claude_code" | "codex"; gateway_binding: string;
  } | null>(null);

  // Step 1 — Placement (+ purpose preset, sent in the same create call)
  const [purposePreset, setPurposePreset] = useState<string>("internal_assistant");
  const [placement, setPlacement] = useState<Placement>("cloud");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const { nodes, loading: nodesLoading, refresh: refreshNodes } = useWorkspaceHardwareNodes(workspaceId);
  const [vpsPanelOpen, setVpsPanelOpen] = useState(false);
  const [sshPanelOpen, setSshPanelOpen] = useState(false);
  const [showGatewayPair, setShowGatewayPair] = useState(false);
  const vpsNodes = nodes.filter((n) => n.hardware_kind === "cloud_vps");
  const gatewayNodes = nodes.filter((n) => n.hardware_kind !== "cloud_vps");

  // Step 2 — Brain
  const [providerMode, setProviderMode] = useState<WizardProviderMode>("platform");
  const [platformProvider, setPlatformProvider] = useState("");
  const [byokProvider, setByokProvider] = useState("anthropic");
  const [byokKey, setByokKey] = useState("");
  const [subscriptionProvider, setSubscriptionProvider] = useState("claude_code_cli");
  const [localProvider, setLocalProvider] = useState("ollama");
  const [gatewayBinding, setGatewayBinding] = useState("");
  const [selectedModel, setSelectedModel] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The provider whose model catalog the Brain step's model picker should
  // show — keep the selected model in sync with it (freeform providers get
  // "" so their text field starts empty rather than carrying a stale id).
  // "platform" only joins this once the owner picks an explicit provider —
  // an empty platformProvider ("Platform default") must NOT re-default
  // selectedModel, or every plain create would silently overwrite the
  // seeded model default (seed_specialist_metadata's "deepseek-reasoner").
  const activeModelProvider = providerMode === "byok" ? byokProvider : providerMode === "local" ? (localProvider || "ollama") : providerMode === "platform" ? platformProvider : "";
  useEffect(() => {
    if (!activeModelProvider) return;
    setSelectedModel(FREEFORM_MODEL_PROVIDERS.has(activeModelProvider) ? "" : defaultModelForProvider(activeModelProvider));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModelProvider]);

  // How many channels are already live — decides whether step 3's forward
  // button reads "Next" (something real happened) or "Skip for now".
  const { channels } = useFleetAgentChannels(workspaceId, step === 3 ? agentId : null);
  const channelsConnected = channels.filter((c: any) => c?.connected).length;

  async function patchAgent(id: string, patch: Record<string, any>) {
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(id)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
    return data;
  }

  async function hydrateCreatedAgent(id: string) {
    try {
      const res = await fetch(`/api/w/${workspaceId}/fleet/agents`, { credentials: "include" });
      const data = res.ok ? await res.json() : {};
      const found = (data?.agents || []).find((a: FleetAgent) => a.agent_id === id) || null;
      setCreatedAgent(found);
    } catch {
      // Best-effort — ChannelsTab tolerates a null agent.
    }
  }

  // Step 1 (Placement) → creates the agent on first commit (auto name, the
  // caller's project or "General", capability_preset standard), then always
  // (re)patches hardware_access/preferred_gateway_id to match the current
  // choice — placement is changeable anytime, including by coming back here
  // after already advancing.
  async function submitPlacement() {
    if (placement !== "cloud" && !selectedNodeId.trim()) {
      setError(placement === "vps" ? "Pick a cloud server, or start provisioning one." : "Pick a computer, or pair one.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let id = agentId;
      let projId = resolvedProjectId;
      if (!id) {
        const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            capability_preset: "standard",
            project_id: initialProjectId || "",
            purpose_preset: purposePreset,
            audience: audienceForPreset(purposePreset),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
        id = String(data.agent_id || "");
        projId = String(data.project_id || initialProjectId || "");
        setAgentId(id);
        setResolvedProjectId(projId);
      }
      const patchResult = await patchAgent(id, {
        hardware_access: placement === "cloud" ? "none" : placement,
        preferred_gateway_id: placement === "cloud" ? "" : selectedNodeId.trim(),
      });
      setRecommendedModelConfig(patchResult?.recommended_model_config || null);
      await hydrateCreatedAgent(id);
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save placement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleVpsConnected() {
    setVpsPanelOpen(false);
    const before = new Set(nodes.map(nodeId));
    const after = await refreshNodes();
    const added = after.find((n) => n.hardware_kind === "cloud_vps" && !before.has(nodeId(n)));
    if (added) setSelectedNodeId(nodeId(added));
  }

  async function handleSshConnected() {
    setSshPanelOpen(false);
    const before = new Set(nodes.map(nodeId));
    const after = await refreshNodes();
    const added = after.find((n) => !before.has(nodeId(n)));
    if (added) setSelectedNodeId(nodeId(added));
  }

  function handleGatewayPaired(g: GatewayRegistrationRecord) {
    setShowGatewayPair(false);
    setSelectedNodeId(String(g.gateway_id || ""));
    void refreshNodes();
  }

  // "You already have Codex on this box — use it" (founder ruling, §29):
  // one click adopts the hardware-recommended subscription instead of
  // making the owner re-navigate the box picker and reselect a runtime
  // they've already signed in on this exact machine.
  function applyRecommendedSubscription() {
    if (!recommendedModelConfig) return;
    setProviderMode("subscription");
    setSubscriptionProvider(recommendedModelConfig.provider);
    setGatewayBinding(recommendedModelConfig.gateway_binding);
  }

  // Step 2 (Brain) → who pays / which brain / which model, in one commit.
  // Same logic as the old wizard's steps 5-6, unchanged: BYOK saves a vault
  // key + provider profile before the model_config patch (which replaces
  // model_config wholesale, so both fields are written together here).
  async function submitBrain() {
    if (providerMode === "subscription" && !gatewayBinding.trim()) {
      setError("Pick a computer to run this agent’s subscription CLI.");
      return;
    }
    if (providerMode === "local" && !gatewayBinding.trim()) {
      setError("Pick a computer (with Ollama) to run this agent’s local model.");
      return;
    }
    if (!agentId) return;
    setBusy(true);
    setError(null);
    try {
      if (providerMode === "byok" && byokKey.trim()) {
        const label = `${providerLabel(byokProvider)} — ${createdAgent?.label || "agent"}`;
        const credRes = await fetch("/api/credentials/vault", {
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
        if (!credRes.ok) {
          const cd = await credRes.json().catch(() => ({}));
          throw new Error(cd?.detail || cd?.error || `HTTP ${credRes.status}`);
        }
        const credentialId = (await credRes.json())?.id;

        const profileRes = await fetch("/api/providers/profiles", {
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
          const pd = await profileRes.json().catch(() => ({}));
          throw new Error(pd?.detail || pd?.error || `HTTP ${profileRes.status}`);
        }
      }
      if (providerMode === "byok") {
        await patchAgent(agentId, { model_config: { mode: "byok_api", provider: byokProvider, model: selectedModel.trim() || undefined } });
      } else if (providerMode === "platform" && platformProvider) {
        // Only patch when the owner explicitly locked this agent to a
        // provider — the seeded default (mode: platform_credits, no
        // provider) already matches "platform" with nothing picked, so
        // skip the round-trip rather than clobbering the seeded model
        // default (seed_specialist_metadata's "deepseek-reasoner") for
        // every plain create. An explicit pick here is isolated per-agent
        // from birth — the workspace-wide default can change later without
        // ever touching it (see sage_agent_runtime_service.
        // _resolve_agent_cloud_provider's platform_credits branch).
        await patchAgent(agentId, {
          model_config: { mode: "platform_credits", provider: platformProvider, model: selectedModel.trim() || undefined },
        });
      } else if (providerMode === "local") {
        await patchAgent(agentId, {
          model_config: {
            mode: "local",
            provider: localProvider || "ollama",
            gateway_binding: gatewayBinding.trim(),
            runtime: "ollama",
            model: selectedModel.trim() || undefined,
          },
        });
      } else if (providerMode === "subscription") {
        // Previously this branch fell through with no PATCH — the wizard
        // silently accepted the user's subscription choice and left the
        // agent on the platform-credits default, forcing users to redo
        // the entire selection on the Model tab afterward. Every BYO-brain
        // agent created via the wizard hit this bug.
        await patchAgent(agentId, {
          model_config: {
            mode: "cli_subscription",
            provider: subscriptionProvider,
            runtime: runtimeForProvider(subscriptionProvider),
            gateway_binding: gatewayBinding.trim(),
          },
        });
      }
      setStep(3);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the provider.");
    } finally {
      setBusy(false);
    }
  }

  function finish() {
    if (!agentId) return;
    onCreated(agentId);
    // Placement always resolves a real project (explicit pick, or "General"
    // — see the doc comment above), so projSeg should never actually be
    // blank here. But .../projects/{projSeg}/agents/{id}/overview 404s (via
    // the global not-found page) if it ever is — the empty segment collapses
    // the path so "agents" gets consumed as the [projectId] value, stranding
    // the real agent id — so guard it anyway: land on the flat, always-valid
    // agents list rather than a link known to be broken.
    const projSeg = resolvedProjectId || initialProjectId || "";
    const base = `/w/${encodeURIComponent(workspaceId)}`;
    router.push(
      projSeg
        ? `${base}/projects/${encodeURIComponent(projSeg)}/agents/${encodeURIComponent(agentId)}/overview`
        : `${base}/agents`,
    );
  }

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div className="fleet-wizard" role="dialog" aria-modal="true" aria-label="Create agent" onClick={(e) => e.stopPropagation()}>
        <div className="fleet-wizard-header">
          <div className="fleet-wizard-steps">
            {STEP_LABELS.map((label, i) => {
              const n = i + 1;
              return (
                <div key={label} className={`fleet-wizard-step-dot${n === step ? " is-active" : ""}${n < step ? " is-done" : ""}`}>
                  <span className="fleet-wizard-step-index">{n < step ? <Check size={11} strokeWidth={2.5} /> : n}</span>
                  <span className="fleet-wizard-step-label">{label}</span>
                </div>
              );
            })}
          </div>
          <button type="button" className="fleet-detail-close" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>

        <div className="fleet-wizard-body">
          {step === 1 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">What's it for?</div>
              <div className="fleet-wizard-options">
                {PURPOSE_PRESETS.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    className={`fleet-wizard-option${purposePreset === p.value ? " is-selected" : ""}`}
                    onClick={() => setPurposePreset(p.value)}
                  >
                    <span className="fleet-wizard-option-label">{p.label}</span>
                    <span className="fleet-wizard-option-body">{p.body}</span>
                  </button>
                ))}
              </div>
              <p className="fleet-wizard-hint" style={{ marginTop: 8 }}>
                A starting point for its instructions — edit them anytime from Overview. It also marks
                this agent as yours or your customers' to talk to, so Customer Support agents stay off
                your private accounts. What it's allowed to do during a conversation is still decided
                per-message, the same way for every agent, not by this pick.
              </p>

              {/* Same three placements as the Hardware tab's own picker
                  (none/gateway/vps as "Cloud only"/"Paired computer"/"Cloud
                  VPS") — no "full access" concept here either: once this
                  agent has hardware, it has full run of that box by
                  default. Labels below are wizard-flavored ("Paired
                  computer" vs onboarding straight into pairing) but map
                  1:1 onto HardwareTab.tsx's ACCESS_OPTIONS values. */}
              <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Where does it work?</div>
              <div className="fleet-wizard-options">
                <button type="button" className={`fleet-wizard-option${placement === "cloud" ? " is-selected" : ""}`} onClick={() => { setPlacement("cloud"); if (providerMode === "subscription" || providerMode === "local") setProviderMode("platform"); }}>
                  <span className="fleet-wizard-option-label">Cloud only <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">No hardware. Runs entirely on Empyralis’ infrastructure.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${placement === "vps" ? " is-selected" : ""}`} onClick={() => setPlacement("vps")}>
                  <span className="fleet-wizard-option-label">Cloud VPS</span>
                  <span className="fleet-wizard-option-body">A cloud server, provisioned by Empyralis or connected over SSH.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${placement === "gateway" ? " is-selected" : ""}`} onClick={() => setPlacement("gateway")}>
                  <span className="fleet-wizard-option-label">Paired computer</span>
                  <span className="fleet-wizard-option-body">A computer you’ve paired as a Gateway.</span>
                </button>
              </div>

              {placement === "vps" && (
                <div className="fleet-channel-expand">
                  {nodesLoading ? (
                    <p className="fleet-channel-expand-hint">Loading your cloud servers…</p>
                  ) : vpsNodes.length > 0 ? (
                    <>
                      <label className="fleet-wizard-label">Which server?</label>
                      <div className="fleet-wizard-options">
                        {vpsNodes.map((n) => (
                          <button
                            key={nodeId(n)}
                            type="button"
                            className={`fleet-wizard-option${selectedNodeId === nodeId(n) ? " is-selected" : ""}`}
                            onClick={() => setSelectedNodeId(nodeId(n))}
                          >
                            <span className="fleet-wizard-option-label">{nodeLabel(n)}</span>
                            <span className="fleet-wizard-option-body">{nodeOnline(n) ? "Online" : "Offline"}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="fleet-channel-expand-hint">No cloud servers connected yet.</p>
                  )}
                  <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                    <button type="button" className="fleet-btn" onClick={() => setVpsPanelOpen(true)}>Start provisioning</button>
                    <button type="button" className="fleet-btn" onClick={() => setSshPanelOpen(true)}>Connect your own server</button>
                  </div>
                </div>
              )}

              {placement === "gateway" && (
                <div className="fleet-channel-expand">
                  {nodesLoading ? (
                    <p className="fleet-channel-expand-hint">Loading your paired computers…</p>
                  ) : gatewayNodes.length > 0 ? (
                    <>
                      <label className="fleet-wizard-label">Which computer?</label>
                      <div className="fleet-wizard-options">
                        {gatewayNodes.map((n) => (
                          <button
                            key={nodeId(n)}
                            type="button"
                            className={`fleet-wizard-option${selectedNodeId === nodeId(n) ? " is-selected" : ""}`}
                            onClick={() => setSelectedNodeId(nodeId(n))}
                          >
                            <span className="fleet-wizard-option-label">{nodeLabel(n)}</span>
                            <span className="fleet-wizard-option-body">{nodeOnline(n) ? "Online" : "Offline"}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="fleet-channel-expand-hint">No computers paired yet.</p>
                  )}
                  {showGatewayPair ? (
                    <div style={{ marginTop: 10 }}>
                      <GatewayPairPanel
                        workspaceId={workspaceId}
                        compact
                        onPaired={handleGatewayPaired}
                        renderPostPairNext={(g) => {
                          // Wizard-in-flow next-step CTA: keep the user in
                          // the wizard rather than sending them out to the
                          // Hardware detail page. On click we advance
                          // straight to step 2 with this new gateway
                          // pre-selected as the subscription's brain box.
                          const gid = String(g.gateway_id || "").trim();
                          if (!gid) return null;
                          return (
                            <button
                              type="button"
                              className="fleet-btn fleet-btn--accent"
                              onClick={() => {
                                setSelectedNodeId(gid);
                                setGatewayBinding(gid);
                                setProviderMode("subscription");
                                setShowGatewayPair(false);
                                setStep(2);
                              }}
                            >
                              Use it as this agent&apos;s brain →
                            </button>
                          );
                        }}
                      />
                    </div>
                  ) : (
                    <button type="button" className="fleet-btn" style={{ marginTop: 10 }} onClick={() => setShowGatewayPair(true)}>
                      Pair this computer
                    </button>
                  )}
                </div>
              )}

              <p className="fleet-wizard-hint" style={{ marginTop: 16 }}>
                Placement is where the agent works. What it may do there is set by you, the owner —
                people who message it can request, not command.
              </p>
            </div>
          )}

          {step === 2 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Who pays for the model?</div>
              {recommendedModelConfig && providerMode !== "subscription" && (
                <div
                  className="fleet-channel-expand"
                  style={{ borderColor: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
                >
                  <p className="fleet-channel-expand-hint" style={{ margin: 0 }}>
                    You already have {RUNTIME_LABELS[recommendedModelConfig.runtime]} signed in on this computer —
                    use it instead of Empyralis credits?
                  </p>
                  <button type="button" className="fleet-btn fleet-btn--accent" onClick={applyRecommendedSubscription}>
                    Use it
                  </button>
                </div>
              )}
              <div className="fleet-wizard-options">
                <button type="button" className={`fleet-wizard-option${providerMode === "platform" ? " is-selected" : ""}`} onClick={() => setProviderMode("platform")}>
                  <span className="fleet-wizard-option-label">Empyralis credits {!recommendedModelConfig && <span className="fleet-wizard-option-tag">Recommended</span>}</span>
                  <span className="fleet-wizard-option-body">Runs on your plan’s credits. Nothing to set up.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${providerMode === "byok" ? " is-selected" : ""}`} onClick={() => setProviderMode("byok")}>
                  <span className="fleet-wizard-option-label">Your own API key</span>
                  <span className="fleet-wizard-option-body">Use your key for any provider. You pay them directly.</span>
                </button>
                {/* "Your subscription" and "Run locally" both route the brain
                    through a Gateway on a real machine — impossible for a
                    Cloud-only agent, which has none. Offering them on cloud is
                    the exact dead-end that surfaces later as an unrunnable
                    "Needs sign-in" agent, so they only appear once a computer
                    or VPS is the placement. */}
                {placement !== "cloud" && (
                  <>
                    <button type="button" className={`fleet-wizard-option${providerMode === "subscription" ? " is-selected" : ""}`} onClick={() => setProviderMode("subscription")}>
                      <span className="fleet-wizard-option-label">Your subscription {recommendedModelConfig && <span className="fleet-wizard-option-tag">Recommended</span>}</span>
                      <span className="fleet-wizard-option-body">Route through your Claude Code or Codex plan. Needs the Gateway.</span>
                    </button>
                    <button type="button" className={`fleet-wizard-option${providerMode === "local" ? " is-selected" : ""}`} onClick={() => setProviderMode("local")}>
                      <span className="fleet-wizard-option-label">Run locally</span>
                      <span className="fleet-wizard-option-body">Ollama on your own machine, via the Gateway.</span>
                    </button>
                  </>
                )}
              </div>
              {placement === "cloud" && (
                <p className="fleet-wizard-hint" style={{ marginTop: 6 }}>
                  Want to run on your own Claude/Codex subscription or a local model? Put this agent on a paired computer or a VPS in step 1 — those need a machine to run on.
                </p>
              )}
              {providerMode === "platform" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Provider</label>
                  <select className="fleet-wizard-input" value={platformProvider} onChange={(e) => setPlatformProvider(e.currentTarget.value)}>
                    <option value="">Platform default</option>
                    {BYOK_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  <p className="fleet-wizard-hint">
                    {platformProvider
                      ? `This agent always uses ${providerLabel(platformProvider)}, billed to your Empyralis credits — independent of any other agent or a workspace-wide setting change.`
                      : "Tracks your workspace's shared default provider (DeepSeek, unless changed workspace-wide). Pick a specific provider above to lock this agent to it permanently, independent of every other agent."}
                  </p>
                </div>
              )}
              {providerMode === "byok" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Provider</label>
                  <select className="fleet-wizard-input" value={byokProvider} onChange={(e) => setByokProvider(e.currentTarget.value)}>
                    {BYOK_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  <label className="fleet-wizard-label">API key</label>
                  <input className="fleet-wizard-input" type="password" value={byokKey} onChange={(e) => setByokKey(e.currentTarget.value)} placeholder="Paste your key" />
                </div>
              )}
              {providerMode === "subscription" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Subscription</label>
                  <select className="fleet-wizard-input" value={subscriptionProvider} onChange={(e) => setSubscriptionProvider(e.currentTarget.value)}>
                    {SUBSCRIPTION_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  <GatewayBoxPicker
                    workspaceId={workspaceId}
                    value={gatewayBinding}
                    onChange={setGatewayBinding}
                    requireRuntime={runtimeForProvider(subscriptionProvider) === "codex" ? "codex" : "claude_code"}
                  />
                </div>
              )}
              {providerMode === "local" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Runtime</label>
                  <select className="fleet-wizard-input" value={localProvider} onChange={(e) => setLocalProvider(e.currentTarget.value)}>
                    {LOCAL_PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={setGatewayBinding} requireLocalModel />
                </div>
              )}

              <div className="fleet-detail-section-title" style={{ marginTop: 20 }}>Which model?</div>
              {providerMode === "platform" && (
                <p className="fleet-wizard-hint">
                  {platformProvider
                    ? `Empyralis manages the exact ${providerLabel(platformProvider)} model for you.`
                    : "Empyralis picks and maintains the model for you."}
                </p>
              )}
              {providerMode === "byok" && (
                FREEFORM_MODEL_PROVIDERS.has(byokProvider) ? (
                  <>
                    <label className="fleet-wizard-label">Model ID</label>
                    <input
                      className="fleet-wizard-input"
                      value={selectedModel}
                      onChange={(e) => setSelectedModel(e.currentTarget.value)}
                      placeholder={byokProvider === "azure_openai" ? "e.g. my-gpt4-deployment" : "e.g. llama-3-70b"}
                    />
                    <p className="fleet-wizard-hint">
                      {byokProvider === "azure_openai"
                        ? "Azure OpenAI is deployment-scoped — enter your deployment's name, not a model family."
                        : "Custom OpenAI-compatible endpoints don't have a fixed catalog — enter the model id your endpoint expects."}
                    </p>
                  </>
                ) : (
                  <>
                    <label className="fleet-wizard-label">Model</label>
                    <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => setSelectedModel(e.currentTarget.value)}>
                      {modelsForProvider(byokProvider).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </>
                )
              )}
              {providerMode === "local" && (
                <>
                  <label className="fleet-wizard-label">Ollama model</label>
                  <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => setSelectedModel(e.currentTarget.value)}>
                    {modelsForProvider("ollama").map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <p className="fleet-wizard-hint">
                    This is a suggested list — the turn will only work if this model is actually pulled on
                    the computer you chose. Not sure? Run <code>ollama pull {selectedModel || "llama3.2"}</code> on
                    that box first.
                  </p>
                </>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">How do people reach it?</div>
              {agentId && <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={createdAgent} />}
              <p className="fleet-wizard-hint" style={{ marginTop: 12 }}>
                Optional — connect a channel now, or skip and add one later from the Channels tab.
              </p>
            </div>
          )}

          {step === 4 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Connect its apps</div>
              {agentId && resolvedProjectId && <ConnectorPicker workspaceId={workspaceId} projectId={resolvedProjectId} agentId={agentId} />}
              <p className="fleet-wizard-hint" style={{ marginTop: 12 }}>
                Optional — connect the apps this agent needs, or skip and add them later from the Connectors tab.
              </p>
            </div>
          )}

          {error && <p className="fleet-channel-expand-error">{error}</p>}
        </div>

        <div className="fleet-wizard-footer">
          <button type="button" className="fleet-btn" onClick={() => (step === 1 ? onClose() : setStep((s) => s - 1))} disabled={busy}>
            {step === 1 ? "Cancel" : "Back"}
          </button>
          {step === 1 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitPlacement} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 2 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitBrain} disabled={busy || (providerMode === "subscription" && !gatewayBinding.trim()) || (providerMode === "local" && !gatewayBinding.trim())}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 3 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setStep(4)} disabled={busy}>
              {channelsConnected > 0 ? "Next" : "Skip for now"}
            </button>
          )}
          {step === 4 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={finish} disabled={busy}>
              Finish
            </button>
          )}
        </div>

        {/* Nested INSIDE .fleet-wizard (not as backdrop siblings) so a click
            anywhere in these — including their own close buttons — stops at
            .fleet-wizard's own stopPropagation and never bubbles to the
            outer backdrop's onClose, which would otherwise kill the whole
            wizard instead of just this sub-panel. */}
        <CloudVpsSetupPanel
          open={vpsPanelOpen}
          workspaceId={workspaceId}
          onClose={() => setVpsPanelOpen(false)}
          onConnected={handleVpsConnected}
        />
        <SshServerConnectPanel
          open={sshPanelOpen}
          workspaceId={workspaceId}
          onClose={() => setSshPanelOpen(false)}
          onConnected={handleSshConnected}
        />
      </div>
    </div>
  );
}
