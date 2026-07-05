"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Lock, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, providerLabel } from "./fleet-provider-constants";
import { useFleetProjects } from "./fleet-data";

type CapabilityPreset = "standard" | "knowledge";
type WizardProviderMode = "platform" | "byok" | "subscription";
type ChannelChoice = "none" | "telegram_pool" | "byo";

const STEP_LABELS = ["Name", "Project", "Type", "Model", "Channel"];

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
 * Create-agent wizard aligned to the real model:
 * Name → Project → Type (capability preset) → Model → Channel.
 * The agent is created at the Type step (name + preset + project); Model and
 * Channel are incremental patches after. Plain-language throughout — no jargon.
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
  const [providerMode, setProviderMode] = useState<WizardProviderMode>("platform");
  const [byokProvider, setByokProvider] = useState("anthropic");
  const [byokKey, setByokKey] = useState("");
  const [subscriptionProvider, setSubscriptionProvider] = useState("claude_code_cli");
  const [channel, setChannel] = useState<ChannelChoice>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Default the project selection to the pre-selected one or the first project.
  useEffect(() => {
    if (!projectId && projects.length > 0) {
      setProjectId(initialProjectId || projects[0].id);
    }
  }, [projects, projectId, initialProjectId]);

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

  // Step 3 → create the agent with its preset + project.
  async function createAgent() {
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

  // Step 4 → set the model/provider.
  async function submitProvider() {
    setBusy(true);
    setError(null);
    try {
      if (providerMode === "byok") {
        if (byokKey.trim()) {
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
        await patchAgent({ model_config: { mode: "byok_api", provider: byokProvider } });
      } else if (providerMode === "subscription") {
        await patchAgent({ model_config: { mode: "cli_subscription", provider: subscriptionProvider } });
      }
      // platform: nothing to patch — it's the default.
      setStep(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the provider.");
    } finally {
      setBusy(false);
    }
  }

  // Step 5 → finish. Channel BYO/pool setup continues in the Channels tab.
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
              <input className="fleet-wizard-input" value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder="e.g. Support Bot" autoFocus />
              <label className="fleet-wizard-label">What should it do? (one line)</label>
              <input className="fleet-wizard-input" value={description} onChange={(e) => setDescription(e.currentTarget.value)} placeholder="e.g. Answer customer questions about orders" />
            </div>
          )}

          {step === 2 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Which project?</div>
              <div className="fleet-wizard-options">
                {projects.map((p) => (
                  <button key={p.id} type="button" className={`fleet-wizard-option${projectId === p.id ? " is-selected" : ""}`} onClick={() => setProjectId(p.id)}>
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
              <div className="fleet-detail-section-title">What kind of agent?</div>
              <div className="fleet-wizard-options">
                {PRESET_OPTIONS.map((opt) => (
                  <button key={opt.id} type="button" className={`fleet-wizard-option${preset === opt.id ? " is-selected" : ""}`} onClick={() => setPreset(opt.id)}>
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
                <button type="button" className={`fleet-wizard-option${providerMode === "subscription" ? " is-selected" : ""}`} onClick={() => setProviderMode("subscription")}>
                  <span className="fleet-wizard-option-label">Your subscription</span>
                  <span className="fleet-wizard-option-body">Route through your Claude Code or Codex plan. Needs the Gateway.</span>
                </button>
              </div>
              {providerMode === "byok" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Provider</label>
                  <select className="fleet-wizard-input" value={byokProvider} onChange={(e) => setByokProvider(e.currentTarget.value)}>
                    {BYOK_PROVIDERS.map((p) => <option key={p} value={p}>{providerLabel(p)}</option>)}
                  </select>
                  <label className="fleet-wizard-label">API key</label>
                  <input className="fleet-wizard-input" type="password" value={byokKey} onChange={(e) => setByokKey(e.currentTarget.value)} placeholder="Paste your key" />
                </div>
              )}
              {providerMode === "subscription" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Subscription</label>
                  <select className="fleet-wizard-input" value={subscriptionProvider} onChange={(e) => setSubscriptionProvider(e.currentTarget.value)}>
                    {SUBSCRIPTION_PROVIDERS.map((p) => <option key={p} value={p}>{providerLabel(p)}</option>)}
                  </select>
                </div>
              )}
            </div>
          )}

          {step === 5 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">How do people reach it?</div>
              <div className="fleet-wizard-options">
                <button type="button" className={`fleet-wizard-option${channel === "none" ? " is-selected" : ""}`} onClick={() => setChannel("none")}>
                  <span className="fleet-wizard-option-label">Not yet <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">Create it now, connect a channel later from the Channels tab.</span>
                </button>
                <button type="button" className={`fleet-wizard-option${channel === "telegram_pool" ? " is-selected" : ""}`} onClick={() => setChannel("telegram_pool")}>
                  <span className="fleet-wizard-option-label">Telegram — hosted bot</span>
                  <span className="fleet-wizard-option-body">Get a ready-made bot from the shared pool. You’ll pick one in the Channels tab.</span>
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
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Create"}
            </button>
          )}
          {step === 4 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitProvider} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 5 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={finish} disabled={busy}>Done</button>
          )}
        </div>
      </div>
    </div>
  );
}
