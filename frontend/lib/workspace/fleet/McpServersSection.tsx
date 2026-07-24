"use client";

// "Connect an MCP app" — MCP Phase B UI.
//
// Backend contract (verified file:line, server_modules/agent_registry_api.py):
//   GET    /agent-registry/mcp/servers                       list_mcp_servers        :874
//   PUT    /agent-registry/mcp/servers/{server_id}            save_mcp_server         :913
//   POST   /agent-registry/mcp/servers/{server_id}/refresh    refresh_mcp_server      :952
//   GET    /agent-registry/mcp/servers/{server_id}/tools      list_mcp_server_tools   :983
//   DELETE /agent-registry/mcp/servers/{server_id}            delete_mcp_server       :1004
// These routes are registered on `agents_router` (server_modules/routes_agents.py),
// which server.py mounts BOTH bare and under "/api" — so the same paths this file
// calls under /api/agent-registry/... are live and match the existing app-wide
// convention of proxying every fetch through /api/[...path]/route.ts (see
// frontend/lib/workspace/fleet/fleet-data.ts, credit-balance.ts, and this
// directory's settings/page.tsx, all of which use plain fetch() rather than
// frontend/lib/workspace/workstation-client.ts — that client's own
// createWorkstationClient() is never instantiated anywhere live in the app
// (no WorkstationKernelProvider is mounted under frontend/app/), so this file
// mirrors its documented request/response shapes (paths, methods, body field
// names — workstation-client.ts:2544-2597) via the same plain-fetch convention
// every other live page in this directory already uses, instead of standing up
// an unused, orphaned client wrapper for a single new section.
//
// Tool approval/deny routes (added same-day this section was built — they were
// a gap: mcp_registry_service.py had working approve_mcp_tool()/deny_mcp_tool()
// functions with no REST route):
//   POST   /agent-registry/mcp/servers/{server_id}/tools/approve   approve_mcp_server_tool
//   POST   /agent-registry/mcp/servers/{server_id}/tools/deny      deny_mcp_server_tool
// Both take {workspace_id, tool_name} (McpToolApproveRequest) and are owner-gated.
// NOTE: never fake approval changes via the raw PUT/save route — it strips
// "approved" from any non-empty tools[] you send (agent_registry_api.py
// _strip_mcp_tool_approval_flags), a deliberate guard against self-approval;
// per-tool approval MUST go through these dedicated routes. The whole-server
// enable/disable toggle is sent with tools omitted so existing approvals survive
// (mcp_registry_service.py:611, the `tools: normalized_tools or existing tools`
// fallback).

import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Plus, RefreshCw, Server, Trash2, TriangleAlert } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { timeAgo } from "@/lib/workspace/fleet/fleet-presentation";

export type McpServerTool = {
  name: string;
  label: string;
  description: string;
  action_class: string;
  risk_level: string;
  approved: boolean;
  enabled: boolean;
};

export type McpServerRecord = {
  id: string;
  label: string;
  endpoint: string;
  transport: string;
  enabled: boolean;
  tool_count: number;
  tools: McpServerTool[];
  last_synced_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  credential_id: string | null;
};

// ── plain-fetch helpers (matches the convention this whole directory uses —
//    see fleet-data.ts / credit-balance.ts / settings/page.tsx) ─────────────

async function getJson(path: string): Promise<any> {
  const res = await fetch(path, { credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

async function mutateJson(path: string, method: string, body?: Record<string, unknown>): Promise<any> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 404 && /tools\/(approve|deny)$/.test(path)) {
      throw new Error(
        "This backend deployment doesn't expose the tool approval routes yet — deploy the backend with this frontend (version skew, not a network issue).",
      );
    }
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

function hostFromUrl(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

// Mirrors server_modules/mcp_registry_service.py:_normalize_server_id exactly
// (lowercase, alnum + dash/underscore, collapsed dashes) so the id we mint
// client-side round-trips through the backend's own normalization unchanged.
function slugify(input: string): string {
  const raw = (input || "").trim().toLowerCase();
  const cleaned = raw.replace(/[^a-z0-9_-]+/g, "-").replace(/-{2,}/g, "-");
  return cleaned.replace(/^[-_]+|[-_]+$/g, "");
}

function baseIdFromInputs(label: string, endpoint: string): string {
  const fromLabel = slugify(label);
  if (fromLabel) return fromLabel;
  try {
    const host = slugify(new URL(endpoint).hostname);
    if (host) return host;
  } catch {
    // fall through
  }
  return "mcp-server";
}

function uniqueServerId(base: string, existingIds: Set<string>): string {
  const root = base || "mcp-server";
  if (!existingIds.has(root)) return root;
  let n = 2;
  while (existingIds.has(`${root}-${n}`)) n += 1;
  return `${root}-${n}`;
}

function statusText(server: McpServerRecord): string {
  // Backend flips status="reauth_required" when a live 401 + failed token
  // refresh proves the grant is dead (mcp_registry_service auth-durability
  // seam); it auto-resets to "ok" when a new credential is attached.
  if ((server as { status?: string }).status === "reauth_required") return "Reconnect needed — sign-in expired";
  if (!server.enabled) return "Disabled";
  if (server.tool_count === 0) return server.last_synced_at ? "No tools discovered" : "Pending discovery";
  const approved = server.tools.filter((t) => t.approved).length;
  const noun = server.tool_count === 1 ? "tool" : "tools";
  if (approved === 0) return `${server.tool_count} ${noun} — none approved`;
  if (approved < server.tool_count) return `${approved}/${server.tool_count} tools approved`;
  return `${server.tool_count} ${noun} approved`;
}

// ── data hook ────────────────────────────────────────────────────────────

function useMcpServers(workspaceId: string) {
  const [servers, setServers] = useState<McpServerRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getJson(`/api/agent-registry/mcp/servers?workspace_id=${encodeURIComponent(workspaceId)}`);
      setServers(Array.isArray(data?.items) ? (data.items as McpServerRecord[]) : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load MCP servers.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { servers, loading, error, refresh };
}

// ── section ──────────────────────────────────────────────────────────────

export function McpServersSection({ workspaceId }: { workspaceId: string }) {
  const { servers, loading, error, refresh } = useMcpServers(workspaceId);

  const [endpointInput, setEndpointInput] = useState("");
  const [labelInput, setLabelInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const setRowErr = (id: string, message: string) => setRowError((prev) => ({ ...prev, [id]: message }));
  const clearRowErr = (id: string) =>
    setRowError((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });

  const attemptDiscovery = useCallback(
    async (id: string) => {
      setBusyKey(`${id}:refresh`);
      try {
        await mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(id)}/refresh`, "POST", {
          workspace_id: workspaceId,
        });
        clearRowErr(id);
        await refresh();
      } catch (e) {
        setRowErr(
          id,
          e instanceof Error
            ? `Discovery failed: ${e.message}`
            : "Discovery failed — the server may require authorization Empyralis doesn't have a connect-flow for yet on custom URLs.",
        );
      } finally {
        setBusyKey((k) => (k === `${id}:refresh` ? null : k));
      }
    },
    [workspaceId, refresh],
  );

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const endpoint = endpointInput.trim();
    if (!endpoint || adding) return;
    setAdding(true);
    setAddError(null);
    const existingIds = new Set(servers.map((s) => s.id));
    const id = uniqueServerId(baseIdFromInputs(labelInput, endpoint), existingIds);
    try {
      await mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(id)}`, "PUT", {
        workspace_id: workspaceId,
        label: labelInput.trim() || undefined,
        endpoint,
        transport: "streamable_http",
        enabled: true,
        discover_tools: false,
      });
      setEndpointInput("");
      setLabelInput("");
      await refresh();
      setExpandedId(id);
      void attemptDiscovery(id);
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Could not connect to that MCP server.");
    } finally {
      setAdding(false);
    }
  }

  async function handleToggleEnabled(server: McpServerRecord) {
    const key = `${server.id}:enable`;
    setBusyKey(key);
    try {
      // tools intentionally omitted here — see the file-header note on why
      // sending a non-empty tools[] through this route would wipe approvals.
      await mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}`, "PUT", {
        workspace_id: workspaceId,
        label: server.label,
        endpoint: server.endpoint,
        transport: "streamable_http",
        enabled: !server.enabled,
        discover_tools: false,
      });
      clearRowErr(server.id);
      await refresh();
    } catch (e) {
      setRowErr(server.id, e instanceof Error ? e.message : "Could not update this server.");
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  async function handleDelete(server: McpServerRecord) {
    const key = `${server.id}:delete`;
    setBusyKey(key);
    try {
      await mutateJson(
        `/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        "DELETE",
      );
      setConfirmDeleteId(null);
      if (expandedId === server.id) setExpandedId(null);
      await refresh();
    } catch (e) {
      setRowErr(server.id, e instanceof Error ? e.message : "Could not disconnect this server.");
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  async function handleToolApproval(server: McpServerRecord, toolName: string, action: "approve" | "deny") {
    const key = `${server.id}:${action}:${toolName}`;
    setBusyKey(key);
    try {
      await mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}/tools/${action}`, "POST", {
        workspace_id: workspaceId,
        tool_name: toolName,
      });
      clearRowErr(server.id);
      await refresh();
    } catch (e) {
      const verb = action === "approve" ? "approve" : "revoke";
      setRowErr(server.id, e instanceof Error ? e.message : `Could not ${verb} "${toolName}".`);
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  return (
    <>
      <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>MCP servers</div>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Connect a remote MCP server by URL. Discovered tools stay unapproved until you approve them individually —
        so a workspace can expose <code>create_issue</code> without <code>delete_repo</code>.
      </p>

      {error ? (
        <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div>
      ) : null}

      <form onSubmit={handleAdd} style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-2)" }}>
        <input
          className="fleet-wizard-input"
          placeholder="https://mcp.example.com"
          value={endpointInput}
          onChange={(e) => setEndpointInput(e.target.value)}
          style={{ flex: "2 1 280px" }}
          inputMode="url"
        />
        <input
          className="fleet-wizard-input"
          placeholder="Label (optional)"
          value={labelInput}
          onChange={(e) => setLabelInput(e.target.value)}
          style={{ flex: "1 1 160px" }}
        />
        <button type="submit" className="fleet-btn fleet-btn--accent" disabled={adding || !endpointInput.trim()}>
          <Plus size={14} strokeWidth={1.75} /> {adding ? "Connecting…" : "Connect"}
        </button>
      </form>
      {addError ? (
        <p className="fleet-list-row-desc" style={{ color: "var(--offline-text)", marginBottom: "var(--space-4)" }}>
          {addError}
        </p>
      ) : (
        <p className="fleet-list-row-desc" style={{ marginBottom: "var(--space-4)" }}>
          Public and API-key-authenticated servers work out of the box. OAuth-protected custom servers aren&apos;t
          fully wired up yet — if discovery fails right after connecting, that&apos;s why.
        </p>
      )}

      {loading ? (
        <div className="fleet-list"><div className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} /></div></div>
      ) : servers.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No MCP servers connected</div>
          <div className="fleet-empty-desc">Paste a server URL above to give this workspace's agents new tools.</div>
        </div>
      ) : (
        <div className="fleet-list">
          {servers.map((server) => {
            const expanded = expandedId === server.id;
            const err = rowError[server.id];
            const refreshBusy = busyKey === `${server.id}:refresh`;
            const enableBusy = busyKey === `${server.id}:enable`;
            const deleteBusy = busyKey === `${server.id}:delete`;
            return (
              <div key={server.id} className="fleet-list-row" style={{ flexDirection: "column", alignItems: "stretch", cursor: "default" }}>
                <button
                  type="button"
                  onClick={() => setExpandedId(expanded ? null : server.id)}
                  style={{
                    display: "flex", alignItems: "center", gap: 10, width: "100%",
                    background: "none", border: "none", padding: 0, textAlign: "left",
                    cursor: "pointer", color: "inherit", font: "inherit",
                  }}
                  aria-expanded={expanded}
                >
                  <ChevronRight
                    size={13} strokeWidth={2}
                    style={{ flexShrink: 0, transition: "transform 0.15s ease-out", transform: expanded ? "rotate(90deg)" : "none", color: "var(--text-secondary)" }}
                  />
                  <span className="fleet-list-row-icon"><Server size={16} strokeWidth={1.75} /></span>
                  <span className="fleet-list-row-main">
                    <span className="fleet-list-row-title">{server.label || server.id}</span>
                    <span className="fleet-list-row-desc">{hostFromUrl(server.endpoint)}</span>
                  </span>
                  <span className="fleet-list-row-meta">{statusText(server)}</span>
                </button>

                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="fleet-btn"
                    disabled={refreshBusy}
                    onClick={() => attemptDiscovery(server.id)}
                  >
                    <RefreshCw size={13} strokeWidth={1.75} /> {refreshBusy ? "Refreshing…" : "Refresh"}
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={server.enabled}
                    aria-label={`${server.enabled ? "Disable" : "Enable"} ${server.label || server.id}`}
                    className={`fleet-toggle${server.enabled ? " is-on" : ""}`}
                    disabled={enableBusy}
                    onClick={() => handleToggleEnabled(server)}
                  />
                  <span className="fleet-toggle-row-desc" style={{ marginTop: 0 }}>{server.enabled ? "Enabled" : "Disabled"}</span>
                  <div style={{ flex: 1 }} />
                  {confirmDeleteId === server.id ? (
                    <>
                      <span className="fleet-list-row-desc">Disconnect this server?</span>
                      <button type="button" className="fleet-btn" disabled={deleteBusy} onClick={() => setConfirmDeleteId(null)}>
                        Cancel
                      </button>
                      <button type="button" className="fleet-btn fleet-btn--danger" disabled={deleteBusy} onClick={() => handleDelete(server)}>
                        {deleteBusy ? "Disconnecting…" : "Confirm"}
                      </button>
                    </>
                  ) : (
                    <button type="button" className="fleet-btn" onClick={() => setConfirmDeleteId(server.id)} title="Disconnect server">
                      <Trash2 size={13} strokeWidth={1.75} /> Disconnect
                    </button>
                  )}
                </div>

                {err ? (
                  <p className="fleet-list-row-desc" style={{ color: "var(--offline-text)", display: "flex", alignItems: "flex-start", gap: 6, marginTop: 8 }}>
                    <TriangleAlert size={13} strokeWidth={1.75} style={{ flexShrink: 0, marginTop: 1 }} /> {err}
                  </p>
                ) : null}

                {server.last_synced_at ? (
                  <span className="fleet-list-row-desc" style={{ marginTop: 4 }}>Last discovered {timeAgo(server.last_synced_at)}</span>
                ) : null}

                {expanded ? (
                  <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    {server.tools.length === 0 ? (
                      <p className="fleet-list-row-desc">
                        {refreshBusy ? "Discovering tools…" : "No tools discovered yet."}
                      </p>
                    ) : (
                      server.tools.map((tool) => {
                        const approveBusy = busyKey === `${server.id}:approve:${tool.name}`;
                        const denyBusy = busyKey === `${server.id}:deny:${tool.name}`;
                        return (
                          <div key={tool.name} className="fleet-toggle-row">
                            <div style={{ minWidth: 0 }}>
                              <div className="fleet-toggle-row-label">{tool.label || tool.name}</div>
                              {tool.description ? <div className="fleet-toggle-row-desc">{tool.description}</div> : null}
                              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                                <span className="fleet-badge" style={{ marginLeft: 0 }}>{tool.action_class}</span>
                                <span className="fleet-badge" style={{ marginLeft: 0 }}>{tool.risk_level} risk</span>
                              </div>
                            </div>
                            {tool.approved ? (
                              <button
                                type="button"
                                className="fleet-badge fleet-badge--lock"
                                style={{ marginLeft: 0, flexShrink: 0 }}
                                disabled={denyBusy}
                                title="Revoke approval — the agent loses access to this tool"
                                onClick={() => handleToolApproval(server, tool.name, "deny")}
                              >
                                {denyBusy ? "Revoking…" : "Approved ✕"}
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="fleet-badge fleet-badge--action"
                                style={{ marginLeft: 0, flexShrink: 0 }}
                                disabled={approveBusy}
                                onClick={() => handleToolApproval(server, tool.name, "approve")}
                              >
                                {approveBusy ? "Approving…" : "Approve"}
                              </button>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
