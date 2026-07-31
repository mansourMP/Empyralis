"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { formatDate } from "@/lib/workspace/fleet/fleet-presentation";

type McpKey = {
  key_id: string;
  label: string;
  writes_enabled: boolean;
  created_at: string;
};

/** Inbound: external MCP clients (Claude Desktop, etc.) connecting IN to
 *  this workspace — the opposite direction from McpServersSection (this
 *  workspace's agents connecting OUT). Promoted out of settings/page.tsx's
 *  inline block into its own named section when Settings moved to a
 *  grouped rail, per the founder's ask that it read as a real section, not
 *  a block tacked onto the end of the old single-scroll page. Renders
 *  inside the "Connections" section, alongside Hardware and MCP servers. */
export function McpApiKeysSection({ workspaceId }: { workspaceId: string }) {
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
    <>
      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>MCP API keys</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        The other direction — give external MCP clients (Claude Desktop, etc.) scoped access to this workspace.
        Keys are shown once at creation.
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
          <div className="fleet-empty-icon">
            <KeyRound size={20} strokeWidth={1.75} />
          </div>
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
    </>
  );
}
