"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ChannelsTab } from "./FleetAgentDetail";

import { BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS, providerLabel } from "./fleet-provider-constants";

type PurposePreset = "customer_facing" | "internal_assistant" | "operator";
type WizardProviderMode = "platform" | "byok" | "subscription" | "local";
type GatewayItem = { gateway_id: string; display_name: string | null; platform: string | null; status: string };

const STEP_LABELS = ["Name", "Purpose", "Provider", "Channels", "Hardware"];

const PURPOSE_OPTIONS: { id: PurposePreset; label: string; body: string }[] = [
  { id: "customer_facing", label: "Talks to your customers", body: "Talks directly to your customers — support, sales, bookings." },
  { id: "internal_assistant", label: "Works with just your team", body: "Helps your team — internal ops, research, drafting." },
  { id: "operator", label: "Manages your other agents", body: "Coordinates and configures your other agents." },
];

/**
 * 5-step create-agent wizard. Each step persists immediately (there is no
 * atomic "submit everything at the end" — fleet_create_agent and
 * fleet_configure_agent are separate calls, so the agent exists as soon as
 * step 2 finishes and every later step is an incremental patch). Closing
 * early leaves a real, partially-configured agent rather than losing work.
 */
export function FleetCreateAgentWizard({
  workspaceId,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  onClose: () => void;
  onCreated: (agentId: string) => void;
}) {
  const [step, setStep] = useState(1);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [purpose, setPurpose] = useState<PurposePreset>("internal_assistant");
  const [providerMode, setProviderMode] = useState<WizardProviderMode>("platform");
  const [byokProvider, setByokProvider] = useState("anthropic");
  const [byokKey, setByokKey] = useState("");
  const [subscriptionProvider, setSubscriptionProvider] = useState("claude_code_cli");
  const [localProvider, setLocalProvider] = useState("ollama");
  const [hardwareChoice, setHardwareChoice] = useState<string>("cloud");
  const [gateways, setGateways] = useState<GatewayItem[]>([]);
  const [gatewaysLoaded, setGatewaysLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (step !== 5 || gatewaysLoaded) return;
    (async () => {
      try {
        const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" });
        if (res.ok) {
          const data = await res.json();
          setGateways(Array.isArray(data.items) ? data.items : []);
        }
      } catch {
        /* leave empty — cloud default still works */
      } finally {
        setGatewaysLoaded(true);
      }
    })();
  }, [step, gatewaysLoaded, workspaceId]);

  const patchAgent = useCallback(async (patch: Record<string, unknown>) => {
    if (!agentId) return;
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
  }, [workspaceId, agentId]);

  const createAgent = useCallback(async () => {
    if (!name.trim()) {
      setError("Give the agent a name first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: name.trim(), instructions: description.trim(), purpose_preset: purpose }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setAgentId(data.agent_id);
      setStep(3);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create agent.");
    } finally {
      setBusy(false);
    }
  }, [workspaceId, name, description, purpose]);

  const submitProvider = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      if (providerMode === "byok") {
        if (!byokKey.trim()) throw new Error("Enter an API key, or switch to platform default.");
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
        const vaultData = await vaultRes.json().catch(() => ({}));
        if (!vaultRes.ok) throw new Error(vaultData?.detail || vaultData?.error || `HTTP ${vaultRes.status}`);
        await patchAgent({ model_config: { mode: "byok_api", provider: byokProvider } });
      } else if (providerMode === "subscription") {
        await patchAgent({ model_config: { mode: "cli_subscription", provider: subscriptionProvider } });
      } else if (providerMode === "local") {
        await patchAgent({ model_config: { mode: "local", provider: localProvider } });
      } else {
        await patchAgent({ model_config: { mode: "platform_credits" } });
      }
      setStep(4);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the provider.");
    } finally {
      setBusy(false);
    }
  }, [providerMode, byokProvider, byokKey, subscriptionProvider, localProvider, workspaceId, patchAgent]);

  const finish = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await patchAgent({ hardware_access: hardwareChoice !== "cloud" });
      if (agentId) onCreated(agentId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not finish setup.");
    } finally {
      setBusy(false);
    }
  }, [patchAgent, hardwareChoice, agentId, onCreated]);

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div
        className="fleet-wizard"
        role="dialog"
        aria-modal="true"
        aria-label="Create agent"
        onClick={(e) => e.stopPropagation()}
      >
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
              <input
                className="fleet-wizard-input"
                value={name}
                onChange={(e) => setName(e.currentTarget.value)}
                placeholder="e.g. Support Bot"
                autoFocus
              />
              <label className="fleet-wizard-label">One-line description</label>
              <input
                className="fleet-wizard-input"
                value={description}
                onChange={(e) => setDescription(e.currentTarget.value)}
                placeholder="What does this agent do?"
              />
            </div>
          )}

          {step === 2 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">What is it for?</div>
              <div className="fleet-wizard-options">
                {PURPOSE_OPTIONS.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    className={`fleet-wizard-option${purpose === opt.id ? " is-selected" : ""}`}
                    onClick={() => setPurpose(opt.id)}
                  >
                    <span className="fleet-wizard-option-label">{opt.label}</span>
                    <span className="fleet-wizard-option-body">{opt.body}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Who pays for the brain?</div>
              <div className="fleet-wizard-options">
                <button
                  type="button"
                  className={`fleet-wizard-option${providerMode === "platform" ? " is-selected" : ""}`}
                  onClick={() => setProviderMode("platform")}
                >
                  <span className="fleet-wizard-option-label">Platform credits <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">Runs on Empyralis credits. DeepSeek. No setup.</span>
                </button>
                <button
                  type="button"
                  className={`fleet-wizard-option${providerMode === "byok" ? " is-selected" : ""}`}
                  onClick={() => setProviderMode("byok")}
                >
                  <span className="fleet-wizard-option-label">Your own API key</span>
                  <span className="fleet-wizard-option-body">Use your key for any provider. You pay them directly.</span>
                </button>
                <button
                  type="button"
                  className={`fleet-wizard-option${providerMode === "subscription" ? " is-selected" : ""}`}
                  onClick={() => setProviderMode("subscription")}
                >
                  <span className="fleet-wizard-option-label">Your subscription</span>
                  <span className="fleet-wizard-option-body">Route through your local Claude Code or Codex subscription. Requires Gateway.</span>
                </button>
                <button
                  type="button"
                  className={`fleet-wizard-option${providerMode === "local" ? " is-selected" : ""}`}
                  onClick={() => setProviderMode("local")}
                >
                  <span className="fleet-wizard-option-label">Run locally</span>
                  <span className="fleet-wizard-option-body">Run models on your own machine via Ollama. Requires Gateway.</span>
                </button>
              </div>

              {providerMode === "byok" && (
                <div className="fleet-channel-expand">
                  <label className="fleet-wizard-label">Provider</label>
                  <select
                    className="fleet-wizard-input"
                    value={byokProvider}
                    onChange={(e) => setByokProvider(e.currentTarget.value)}
                  >
                    {BYOK_PROVIDERS.map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                  <label className="fleet-wizard-label">API key</label>
                  <input
                    className="fleet-wizard-input"
                    type="password"
                    autoComplete="off"
                    value={byokKey}
                    onChange={(e) => setByokKey(e.currentTarget.value)}
                    placeholder="sk-..."
                  />
                  <p className="fleet-channel-expand-hint">Stored in this workspace's vault. Never leaves it.</p>
                </div>
              )}

              {providerMode === "subscription" && (
                <div className="fleet-channel-expand">
                  <p className="fleet-channel-expand-hint">This agent's turns will be routed through the Gateway on your paired machine, which invokes your local CLI. Empyralis never sees your subscription token.</p>
                  <label className="fleet-wizard-label">Subscription</label>
                  <select
                    className="fleet-wizard-input"
                    value={subscriptionProvider}
                    onChange={(e) => setSubscriptionProvider(e.currentTarget.value)}
                  >
                    {SUBSCRIPTION_PROVIDERS.map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                  <p className="fleet-channel-expand-hint">{SUBSCRIPTION_PROVIDERS.find((p) => p.id === subscriptionProvider)?.detail}</p>
                </div>
              )}

              {providerMode === "local" && (
                <div className="fleet-channel-expand">
                  <p className="fleet-channel-expand-hint">This agent runs models on your own machine. No data leaves your hardware. Requires the Gateway paired and Ollama installed.</p>
                  <label className="fleet-wizard-label">Runtime</label>
                  <select
                    className="fleet-wizard-input"
                    value={localProvider}
                    onChange={(e) => setLocalProvider(e.currentTarget.value)}
                  >
                    {LOCAL_PROVIDERS.map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                  <p className="fleet-channel-expand-hint">{LOCAL_PROVIDERS.find((p) => p.id === localProvider)?.detail}</p>
                </div>
              )}
            </div>
          )}

          {step === 4 && agentId && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Connect channels</div>
              <p className="fleet-wizard-hint">Optional — you can always add these later from the agent's Channels tab.</p>
              <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={null} />
            </div>
          )}

          {step === 5 && (
            <div className="fleet-wizard-panel">
              <div className="fleet-detail-section-title">Where does it run?</div>
              <div className="fleet-wizard-options">
                <button
                  type="button"
                  className={`fleet-wizard-option${hardwareChoice === "cloud" ? " is-selected" : ""}`}
                  onClick={() => setHardwareChoice("cloud")}
                >
                  <span className="fleet-wizard-option-label">Cloud <span className="fleet-wizard-option-tag">Recommended</span></span>
                  <span className="fleet-wizard-option-body">No hardware needed. Works immediately.</span>
                </button>
                {gateways.map((g) => (
                  <button
                    key={g.gateway_id}
                    type="button"
                    className={`fleet-wizard-option${hardwareChoice === g.gateway_id ? " is-selected" : ""}`}
                    onClick={() => setHardwareChoice(g.gateway_id)}
                  >
                    <span className="fleet-wizard-option-label">{g.display_name || g.platform || "Paired machine"}</span>
                    <span className="fleet-wizard-option-body">
                      {String(g.status || "").toLowerCase() === "active" ? "Online" : "Paired"} · {g.platform || "unknown platform"}
                    </span>
                  </button>
                ))}
                {gatewaysLoaded && gateways.length === 0 && (
                  <p className="fleet-wizard-hint">No paired machines yet — pick a machine from Hardware once you've paired one.</p>
                )}
              </div>
            </div>
          )}

          {error && <p className="fleet-channel-expand-error">{error}</p>}
        </div>

        <div className="fleet-wizard-footer">
          <button
            type="button"
            className="fleet-btn"
            onClick={() => (step === 1 ? onClose() : setStep((s) => s - 1))}
            disabled={busy}
          >
            {step === 1 ? "Cancel" : "Back"}
          </button>
          {step === 1 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setStep(2)} disabled={!name.trim()}>
              Next
            </button>
          )}
          {step === 2 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={createAgent} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 3 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitProvider} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Next"}
            </button>
          )}
          {step === 4 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setStep(5)} disabled={busy}>
              Next
            </button>
          )}
          {step === 5 && (
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={finish} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Finish"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
