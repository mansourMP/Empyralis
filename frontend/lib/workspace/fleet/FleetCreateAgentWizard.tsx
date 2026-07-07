"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Lock, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import {
  BYOK_PROVIDERS,
  SUBSCRIPTION_PROVIDERS,
  LOCAL_PROVIDERS,
  COMING_SOON_NOTE,
  FREEFORM_MODEL_PROVIDERS,
  providerLabel,
  modelsForProvider,
  defaultModelForProvider,
} from "./fleet-provider-constants";
import { useFleetProjects } from "./fleet-data";
import { GatewayBoxPicker } from "./gateway-box-picker";

type CapabilityPreset = "standard" | "knowledge";
type WizardProviderMode = "platform" | "byok" | "subscription" | "local";
type HardwareChoice = "none" | "gateway";
type ChannelChoice = "none" | "byo";

// Fixed order per the UI contract: name → project → capability preset →
// hardware → AI brain → model → channel. Hardware comes before the brain
// because the chosen box determines which brains are actually available
// (a box with no local Ollama can't run "Run locally").
const STEP_LABELS = ["Name", "Project", "Capability preset", "Hardware", "AI brain", "Model", "Channel"];

const PRESET_OPTIONS: { id: CapabilityPreset; label: string; body: string; note?: string }[] = [
  {
    id: "standard",
    label: "Standard agent",
    body: "A full agent. Can use tools, connect apps, and (if you grant it) a computer.",
  },
  {
    id: "knowledge",
    label: "Knowledge agent",
    body: "Answers from documents and connected apps only. Cheaper, and simpler to trust.",
    note: "No hardware access — locked off for safety.",
  },
];

/**
 * Create-agent wizard — fixed order per the UI contract:
 * Name → Project → Capability preset → Hardware → AI brain → Model → Channel.
 * The agent row is created behind the scenes at the Preset step (so later
 * steps have an agent_id to patch), but the button there reads "Next" — the
 * user only sees "Create" on the final (Channel) step, once everything is
 * actually configured. Plain-language throughout — no jargon.
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
  const { projects } = useFleetProjects(workspaceId);
  const [step, setStep] = useState(1);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [projectId, setProjectId] = useState<string>(initialProjectId || "");
  const [preset, setPreset] = useState<CapabilityPreset>("standard");
  const [hardwareChoice, setHardwareChoice] = useState<HardwareChoice>("none");
  const [hardwareGatewayId, setHardwareGatewayId] = useState("");
  const [providerMode, setProviderMode] = useState<WizardProviderMode>("platform");
  const [byokProvider, setByokProvider] = useState("anthropic");
  const [byokKey, setByokKey] = useState("");
  const [subscriptionProvider, setSubscriptionProvider] = useState("claude_code_cli");
  const [localProvider, setLocalProvider] = useState("ollama");
  const [gatewayBinding, setGatewayBinding] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [channel, setChannel] = useState<ChannelChoice>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Default the project selection to the pre-selected one or the first project.
  useEffect(() => {
    if (!projectId && projects.length > 0) {
      setProjectId(initialProjectId || projects[0].id);
    }
  }, [projects, projectId, initialProjectId]);

  // The provider whose model catalog the Model step should show — keep the
  // selected model in sync with it (freeform providers get "" so their text
  // field starts empty rather than carrying over a stale id from another mode).
  const activeModelProvider = providerMode === "byok" ? byokProvider : providerMode === "local" ? (localProvider || "ollama") : "";
  useEffect(() => {
    if (!activeModelProvider) return;
    setSelectedModel(FREEFORM_MODEL_PROVIDERS.has(activeModelProvider) ? "" : defaultModelForProvider(activeModelProvider));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModelProvider]);

  async function patchAgent(patch: Record<string, any>) {
    if (!agentId) return;
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
  }

  // Step 3 (Capability preset) → create the agent with its preset + project.
  // The button here reads "Next", not "Create" — more steps follow. If the
  // agent was already created (the user went Back and returned), don't
  // create a second one; just advance.
  async function createAgent() {
    if (agentId) {
      setStep(4);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          name: name.trim(),
          instructions: description.trim(),
          capability_preset: preset,
          project_id: projectId,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      setAgentId(String(data.agent_id || ""));
      setStep(4);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
    } finally {
      setBusy(false);
    }
  }

  // Step 4 (Hardware) → no server call — this step's box choice seeds the AI
  // brain step's default box (hardware before brain: the box determines which
  // brains are available). Knowledge agents are hardware-locked to none.
  function submitHardware() {
    if (preset !== "knowledge" && hardwareChoice === "gateway" && !hardwareGatewayId.trim()) {
      setError("Pick a computer, or choose “No dedicated hardware” to continue.");
      return;
    }
    setError(null);
    if (hardwareChoice === "gateway" && hardwareGatewayId && !gatewayBinding) {
      setGatewayBinding(hardwareGatewayId);
    }
    setStep(5);
  }

  // Step 5 (AI brain) → who pays / which brain. Saves a BYOK vault key if one
  // was entered; the actual model_config patch is deferred to the Model step
  // (step 6), once the concrete model is also known, so it's written once.
  async function submitBrain() {
    // cli_subscription isn't dispatchable yet (Phase 3) — persisting it resolves
    // to a guaranteed "not yet available" turn error. The Next button is disabled
    // for it; this guard is defence in depth.
    if (providerMode === "subscription") {
      setError(`${COMING_SOON_NOTE}. Pick Empyralis credits or your own API key to continue.`);
      return;
    }
    // BYO-brain Phase 2: "local" is live but a local agent MUST name its box.
    if (providerMode === "local" && !gatewayBinding.trim()) {
      setError("Pick a computer (with Ollama) to run this agent’s local model.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (providerMode === "byok" && byokKey.trim()) {
        const vaultRes = await fetch("/api/connectors/vault", {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            workspace_id: workspaceId,
            connector: byokProvider,
            label: providerLabel(byokProvider),
            credentials: { api_key: byokKey.trim() },
          }),
        });
        if (!vaultRes.ok) {
          const vd = await vaultRes.json().catch(() => ({}));
          throw new Error(vd?.detail || vd?.error || `HTTP ${vaultRes.status}`);
        }
      }
      setStep(6);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the provider.");
    } finally {
      setBusy(false);
    }
  }

  // Step 6 (Model) → the real model picker for the chosen brain. Writes the
  // FULL model_config in one shot (mode/provider/gateway_binding/runtime were
  // already known; model is the new field) — the PATCH endpoint replaces
  // model_config wholesale, so a partial write here would erase the brain
  // step's choices. Platform mode has nothing to pick (fixed platform model)
  // and nothing to patch — same as before.
  async function submitModel() {
    setBusy(true);
    setError(null);
    try {
      if (providerMode === "byok") {
        await patchAgent({ model_config: { mode: "byok_api", provider: byokProvider, model: selectedModel.trim() || undefined } });
      } else if (providerMode === "local") {
        await patchAgent({
          model_config: {
            mode: "local",
            provider: localProvider || "ollama",
            gateway_binding: gatewayBinding.trim(),
            runtime: "ollama",
            model: selectedModel.trim() || undefined,
          },
        });
      }
      setStep(7);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the model.");
    } finally {
      setBusy(false);
    }
  }

  // Step 7 (Channel) → Create. Neither remaining choice makes a server call
  // here — "byo" is just a signpost ("you'll paste the token in the Channels
  // tab next"); the actual bot assignment happens there.
  function submitChannelAndCreate() {
    finish();
  }

  function finish() {
    if (agentId) onCreated(agentId);
  }

  const activeProject = projects.find((p) => p.id === projectId);

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
              <div className="fleet-detail-section-title">Name your agent</div>
              <label className="fleet-wizard-label">Name</label>
              <input className="fleet-wizard-input" value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder="e.g. Support Bot" autoFocus disabled={Boolean(agentId)} />
              <label className="fleet-wizard-label">What should it do? (one line)</label>
              <input className="fleet-wizard-input" value={description} onChange={(e) => setDescription(e.currentTarget.value)} placeholder="e.g. Answer customer questions about orders" disabled={Boolean(agentId)} />
              {agentId && <p className="fleet-wizard-hint">Already created — these fields are locked in. Rename it from the agent’s Overview tab after finishing.</p>}
            </div>
          )}

          {step === 2 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Which project?</div>
              <div className="fleet-wizard-options">
                {projects.map((p) => (
                  <button key={p.id} type="button" className={`fleet-wizard-option${projectId === p.id ? " is-selected" : ""}`} onClick={() => setProjectId(p.id)} disabled={Boolean(agentId)}>
                    <span className="fleet-wizard-option-label">{p.name || p.id}</span>
                    <span className="fleet-wizard-option-body">{p.description || `${p.agent_count ?? 0} agents`}</span>
                  </button>
                ))}
                {projects.length === 0 && <p className="fleet-wizard-hint">Loading projects…</p>}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Capability preset</div>
              <div className="fleet-wizard-options">
                {PRESET_OPTIONS.map((opt) => (
                  <button key={opt.id} type="button" className={`fleet-wizard-option${preset === opt.id ? " is-selected" : ""}`} onClick={() => setPreset(opt.id)} disabled={Boolean(agentId)}>
                    <span className="fleet-wizard-option-label">{opt.label}</span>
                    <span className="fleet-wizard-option-body">{opt.body}</span>
                    {opt.note && (
                      <span className="fleet-wizard-option-note"><Lock size={11} strokeWidth={2} /> {opt.note}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Which computer runs it?</div>
              {preset === "knowledge" ? (
                <p className="fleet-wizard-hint">
                  Knowledge agents run cloud-only — hardware access is off and locked for safety.
                  Change the capability preset above to grant hardware.
                </p>
              ) : (
                <>
                  <div className="fleet-wizard-options">
                    <button type="button" className={`fleet-wizard-option${hardwareChoice === "none" ? " is-selected" : ""}`} onClick={() => setHardwareChoice("none")}>
                      <span className="fleet-wizard-option-label">No dedicated hardware <span className="fleet-wizard-option-tag">Recommended</span></span>
                      <span className="fleet-wizard-option-body">Runs in the cloud. Pair a computer for it anytime from the Hardware tab.</span>
                    </button>
                    <button type="button" className={`fleet-wizard-option${hardwareChoice === "gateway" ? " is-selected" : ""}`} onClick={() => setHardwareChoice("gateway")}>
                      <span className="fleet-wizard-option-label">A paired computer</span>
                      <span className="fleet-wizard-option-body">Give it a computer — for browser/shell access, or to run its AI brain locally next.</span>
                    </button>
                  </div>
                  {hardwareChoice === "gateway" && (
                    <GatewayBoxPicker workspaceId={workspaceId} value={hardwareGatewayId} onChange={setHardwareGatewayId} />
                  )}
                </>
              )}
            </div>
          )}

          {step === 5 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Who pays for the model?</div>
              <div className="fleet-wizard-options">
                <button type="button" className={`fleet-wizard-option${providerMode === "platform" ? " is-selected" : ""}`} onClick={() => setProviderMode("platform")}>
                  <span className="fleet-wizard-option-label">Empyralis credits <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">Runs on your plan’s credits. Nothing to set up.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${providerMode === "byok" ? " is-selected" : ""}`} onClick={() => setProviderMode("byok")}>
                  <span className="fleet-wizard-option-label">Your own API key</span>
                  <span className="fleet-wizard-option-body">Use your key for any provider. You pay them directly.</span>
                </button>
                <button type="button" className={`fleet-wizard-option fleet-wizard-option--soon${providerMode === "subscription" ? " is-selected" : ""}`} onClick={() => setProviderMode("subscription")}>
                  <span className="fleet-wizard-option-label">Your subscription</span>
                  <span className="fleet-wizard-option-body">Route through your Claude Code or Codex plan. Needs the Gateway.</span>
                  <span className="fleet-wizard-option-note"><Lock size={11} strokeWidth={2} /> {COMING_SOON_NOTE}</span>
                </button>
                <button type="button" className={`fleet-wizard-option${providerMode === "local" ? " is-selected" : ""}`} onClick={() => setProviderMode("local")}>
                  <span className="fleet-wizard-option-label">Run locally</span>
                  <span className="fleet-wizard-option-body">Ollama on your own machine, via the Gateway.</span>
                </button>
              </div>
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
                  <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={setGatewayBinding} />
                  <p className="fleet-channel-expand-hint">{COMING_SOON_NOTE}. You’ll be able to save this once your box can run it.</p>
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
            </div>
          )}

          {step === 6 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Which model?</div>
              {providerMode === "platform" && (
                <p className="fleet-wizard-hint">
                  This agent uses Empyralis’ managed model (DeepSeek). Nothing to configure — Empyralis
                  picks and maintains it for you.
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

          {step === 7 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">How do people reach it?</div>
              <div className="fleet-wizard-options">
                <button type="button" className={`fleet-wizard-option${channel === "none" ? " is-selected" : ""}`} onClick={() => setChannel("none")}>
                  <span className="fleet-wizard-option-label">Not yet <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">Create it now, connect a channel later from the Channels tab.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${channel === "byo" ? " is-selected" : ""}`} onClick={() => setChannel("byo")}>
                  <span className="fleet-wizard-option-label">Bring your own bot</span>
                  <span className="fleet-wizard-option-body">Use your own Telegram or Discord bot token. You’ll add it in the Channels tab.</span>
                </button>
              </div>
              <p className="fleet-wizard-hint">
                Creating <strong>{name || "this agent"}</strong> in <strong>{activeProject?.name || "General"}</strong> as a{" "}
                <strong>{preset === "knowledge" ? "Knowledge" : "Standard"}</strong> agent.
              </p>
            </div>
          )}

          {error && <p className="fleet-channel-expand-error">{error}</p>}
        </div>

        <div className="fleet-wizard-footer">
          <button type="button" className="fleet-btn" onClick={() => (step === 1 ? onClose() : setStep((s) => s - 1))} disabled={busy}>
            {step === 1 ? "Cancel" : "Back"}
          </button>
          {step === 1 && <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setStep(2)} disabled={!name.trim()}>Next</button>}
          {step === 2 && <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setStep(3)} disabled={!projectId}>Next</button>}
          {step === 3 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={createAgent} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 4 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitHardware} disabled={busy || (preset !== "knowledge" && hardwareChoice === "gateway" && !hardwareGatewayId.trim())}>
              Next
            </button>
          )}
          {step === 5 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitBrain} disabled={busy || providerMode === "subscription" || (providerMode === "local" && !gatewayBinding.trim())}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 6 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitModel} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 7 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitChannelAndCreate} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Create"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
