"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { CreditCard, Copy, Check, Trash2, Plus, Play, Square, TriangleAlert } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { resumeFleetWorkspace, stopFleetWorkspace, useFleetWorkspace } from "@/lib/workspace/fleet/fleet-data";
import { timeAgo, formatDate } from "@/lib/workspace/fleet/fleet-presentation";

type McpKey = {
  key_id: string;
  label: string;
  writes_enabled: boolean;
  created_at: string;
};

/** Workspace-wide emergency stop — kill_switch_gate's workspace:{id} key.
 *  Blocks every agent's turns immediately (checked before any LLM call, see
 *  sage_agent_runtime_service._run_sage_action_loop_v3). A confirm step
 *  guards the action itself; a real error path guards against silently
 *  believing it worked. */
function StopAllAgentsSection({ workspaceId }: { workspaceId: string }) {
  const { workspace, loading, refresh } = useFleetWorkspace(workspaceId);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stopped = workspace?.stopped;

  async function handleConfirmStop() {
    setBusy(true);
    setError(null);
    const result = await stopFleetWorkspace(workspaceId, reason);
    setBusy(false);
    if (result.ok) {
      setConfirming(false);
      setReason("");
      await refresh();
    } else {
      setError(result.error || "Could not stop all agents.");
    }
  }

  async function handleResume() {
    setBusy(true);
    setError(null);
    const result = await resumeFleetWorkspace(workspaceId);
    setBusy(false);
    if (result.ok) await refresh();
    else setError(result.error || "Could not resume agents.");
  }

  return (
    <>
      <div className="fleet-detail-section-title">Emergency stop</div>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Immediately stops every agent in this workspace from replying, on every channel. Nothing is deleted — resume
        at any time to pick back up where they left off.
      </p>

      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}

      {loading ? (
        <div className="fleet-list"><div className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} /></div></div>
      ) : stopped?.active ? (
        <div className="fleet-card" style={{ borderColor: "var(--accent)", padding: "var(--space-3)" }}>
          <div className="fleet-stop-chip" style={{ marginBottom: 8 }}>
            <Square size={12} strokeWidth={2} />
            All agents stopped
          </div>
          <div className="fleet-list-row-desc">
            Stopped by {stopped.stopped_by_label || "an owner"}
            {stopped.at ? ` · ${timeAgo(stopped.at)}` : ""}
            {stopped.reason ? ` · "${stopped.reason}"` : ""}
          </div>
          <button type="button" className="fleet-btn" style={{ marginTop: 12 }} disabled={busy} onClick={handleResume}>
            <Play size={14} strokeWidth={1.75} /> Resume all agents
          </button>
        </div>
      ) : confirming ? (
        <div className="fleet-card" style={{ borderColor: "var(--offline-text)", padding: "var(--space-3)" }}>
          <div className="fleet-list-row-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <TriangleAlert size={15} strokeWidth={1.75} />
            Stop every agent in this workspace?
          </div>
          <p className="fleet-list-row-desc" style={{ marginTop: 4 }}>
            No one will get a reply from any agent here — on any channel — until you resume.
          </p>
          <input
            className="fleet-wizard-input"
            placeholder="Reason (optional, visible in the activity log)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ marginTop: 10 }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="fleet-btn" disabled={busy} onClick={() => { setConfirming(false); setReason(""); }}>
              Cancel
            </button>
            <button type="button" className="fleet-btn fleet-btn--accent" disabled={busy} onClick={handleConfirmStop}>
              {busy ? "Stopping…" : "Confirm stop"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="fleet-btn" onClick={() => setConfirming(true)}>
          <Square size={14} strokeWidth={1.75} /> Stop all agents
        </button>
      )}
    </>
  );
}

export default function SettingsPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");

  const [keys, setKeys] = useState<McpKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newLabel, setNewLabel] = useState("");
  const [newWrites, setNewWrites] = useState(false);
  const [creating, setCreating] = useState(false);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/connections/mcp-keys?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" });
      if (!res.ok) throw new Error(`Could not load API keys (HTTP ${res.status})`);
      const data = await res.json();
      setKeys(Array.isArray(data?.keys) ? data.keys : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load API keys");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => { void load(); }, [load]);

  const createKey = useCallback(async () => {
    setCreating(true);
    setError(null);
    setFreshKey(null);
    try {
      const res = await fetch("/api/connections/mcp-keys", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId, label: newLabel.trim(), writes_enabled: newWrites }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok) throw new Error(data?.detail || data?.error || `Could not create key (HTTP ${res.status})`);
      setFreshKey(String(data.key || ""));
      setNewLabel("");
      setNewWrites(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create key");
    } finally {
      setCreating(false);
    }
  }, [workspaceId, newLabel, newWrites, load]);

  const revokeKey = useCallback(async (keyId: string) => {
    setError(null);
    try {
      const res = await fetch(`/api/connections/mcp-keys/${encodeURIComponent(keyId)}`, {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE"),
      });
      if (!res.ok) throw new Error(`Could not revoke key (HTTP ${res.status})`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not revoke key");
    }
  }, [load]);

  const copyFresh = useCallback(() => {
    if (!freshKey) return;
    navigator.clipboard?.writeText(freshKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }, [freshKey]);

  return (
    <main className="fleet-content">
      {/* No page-title header — the breadcrumb already says "Settings". */}
      <StopAllAgentsSection workspaceId={workspaceId} />

      {/* Billing */}
      <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Billing</div>
      <Link href={`/w/${workspaceId}/billing`} className="fleet-list-row" style={{ textDecoration: "none" }}>
        <span className="fleet-list-row-icon"><CreditCard size={16} strokeWidth={1.75} /></span>
        <span className="fleet-list-row-main">
          <span className="fleet-list-row-title">Billing &amp; usage</span>
          <span className="fleet-list-row-desc">Plans, credits, and per-agent spend.</span>
        </span>
        <span className="fleet-list-row-meta">Open →</span>
      </Link>

      {/* MCP API keys */}
      <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>MCP API keys</div>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Give external MCP clients scoped access to this workspace. Keys are shown once at creation.
      </p>

      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}

      {freshKey ? (
        <div className="fleet-card" style={{ borderColor: "var(--accent)", marginBottom: "var(--space-4)", padding: "var(--space-3)" }}>
          <div className="fleet-list-row-title">Copy your new key now — it won't be shown again.</div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
            <code style={{ flex: 1, overflow: "auto", fontSize: "var(--text-sm)", background: "var(--bg-inset)", padding: "var(--space-2)", borderRadius: "var(--radius-control)" }}>{freshKey}</code>
            <button type="button" className="fleet-btn" onClick={copyFresh}>
              {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      ) : null}

      {/* Create form */}
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", marginBottom: "var(--space-4)", flexWrap: "wrap" }}>
        <input
          className="fleet-wizard-input"
          placeholder="Key label (e.g. Claude Desktop)"
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          style={{ flex: "1 1 220px" }}
        />
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
          <input type="checkbox" checked={newWrites} onChange={(e) => setNewWrites(e.target.checked)} />
          Allow writes
        </label>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={createKey} disabled={creating}>
          <Plus size={14} /> {creating ? "Creating…" : "Create key"}
        </button>
      </div>

      {/* Key list */}
      {loading ? (
        <div className="fleet-list"><div className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "30%", height: 12 }} /></div></div>
      ) : keys.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No API keys yet</div>
          <div className="fleet-empty-desc">Create one above to connect an external MCP client.</div>
        </div>
      ) : (
        <div className="fleet-list">
          {keys.map((k) => (
            <div key={k.key_id} className="fleet-list-row" style={{ cursor: "default" }}>
              <span className="fleet-list-row-main">
                <span className="fleet-list-row-title">{k.label || k.key_id}</span>
                <span className="fleet-list-row-desc">
                  {k.writes_enabled ? "Read + write" : "Read only"}
                  {k.created_at ? ` · created ${formatDate(k.created_at)}` : ""}
                </span>
              </span>
              <button type="button" className="fleet-btn" onClick={() => revokeKey(k.key_id)} title="Revoke key">
                <Trash2 size={14} /> Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
