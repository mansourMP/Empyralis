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
// Tool approval/deny routes:
//   POST   /agent-registry/mcp/servers/{server_id}/tools/approve   approve_mcp_server_tool
//   POST   /agent-registry/mcp/servers/{server_id}/tools/deny      deny_mcp_server_tool
// Both take {workspace_id, tool_name} (McpToolApproveRequest) and are owner-gated.
// NOTE: never fake approval changes via the raw PUT/save route — it strips
// "approved" from any non-empty tools[] you send (agent_registry_api.py
// _strip_mcp_tool_approval_flags), a deliberate guard against self-approval;
// per-tool approval MUST go through these dedicated routes. The whole-server
// enable/disable toggle is sent with tools omitted so existing approvals survive
// (mcp_registry_service.py's `tools: normalized_tools or existing tools` fallback).
//
// MAN-92 / MAN-104 (tiered autonomy — no per-tool toggle wall unless a tool is
// genuinely high-stakes): server_modules/mcp_registry_service.py now
// auto-approves a discovered tool unless it's flagged `requires_approval`
// (money movement, irreversible deletes, or third-party messaging on the
// owner's behalf — the exact category MAN-68's doctrine work already teaches
// the model to pause on; see mcp_registry_service._looks_high_stakes). So this
// screen no longer needs to show every tool as its own approve/deny row —
// only the ones still gated. Below, tools are bucketed client-side into
// "auto-enabled" (just a count, no control), "needs your approval" (the real
// gate, kept front and center), "approved high-risk" (revoke option), and
// "turned off by you" (re-enable option) — see bucketizeTools().
//
// Also per MAN-104's literal example ("Google Calendar MCP / Google Drive MCP
// / Google Gmail MCP as three separate entries"): connecting one OAuth app
// (e.g. Google Workspace) registers multiple MCP server rows under
// APP_MCP_SERVER_MAP (connection_oauth_service.py) — one per remote endpoint —
// but they share the SAME credential_id (the OAuth grant that produced them).
// groupServers() below uses that existing, unchanged signal to fold same-app
// server rows into one entry instead of adding a new backend "provider" field
// just for this. Any provider with only one MCP server (nearly all of them)
// renders exactly as before.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, Plus, RefreshCw, Server, Trash2, TriangleAlert } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { timeAgo } from "@/lib/workspace/fleet/fleet-presentation";

export type McpServerTool = {
  name: string;
  label: string;
  description: string;
  action_class: string;
  risk_level: string;
  requires_approval: boolean;
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
  status?: string;
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

// ── grouping (MAN-104): fold server rows that came from the same OAuth
//    connect (same credential_id) into one visual entry ───────────────────

type ServiceGroup = {
  key: string;
  title: string;
  subtitle: string;
  servers: McpServerRecord[];
};

function stripMcpSuffix(label: string): string {
  return label.replace(/\(mcp\)/gi, "").replace(/\bmcp\b/gi, "").trim().replace(/\s+/g, " ");
}

function commonWordPrefixLength(wordLists: string[][]): number {
  const first = wordLists[0] || [];
  let n = 0;
  while (
    n < first.length &&
    wordLists.every((words) => (words[n] || "").toLowerCase() === first[n].toLowerCase())
  ) {
    n += 1;
  }
  return n;
}

function groupServers(servers: McpServerRecord[]): ServiceGroup[] {
  const byCredential = new Map<string, McpServerRecord[]>();
  const singles: McpServerRecord[] = [];
  for (const server of servers) {
    const credential = (server.credential_id || "").trim();
    if (!credential) {
      singles.push(server);
      continue;
    }
    const bucket = byCredential.get(credential) || [];
    bucket.push(server);
    byCredential.set(credential, bucket);
  }

  const groups: ServiceGroup[] = [];
  for (const [credential, bucket] of byCredential) {
    if (bucket.length < 2) {
      singles.push(...bucket);
      continue;
    }
    const cleanLabels = bucket.map((s) => stripMcpSuffix(s.label || s.id));
    const wordLists = cleanLabels.map((l) => l.split(" ").filter(Boolean));
    const prefixLen = commonWordPrefixLength(wordLists);
    const prefixWords = (wordLists[0] || []).slice(0, prefixLen);
    const subNames = wordLists.map((words, i) => words.slice(prefixLen).join(" ") || cleanLabels[i]);
    groups.push({
      key: credential,
      title: prefixWords.join(" ") || subNames.join(" / "),
      subtitle: subNames.join(", "),
      servers: bucket,
    });
  }
  for (const server of singles) {
    groups.push({
      key: server.id,
      title: server.label || server.id,
      subtitle: hostFromUrl(server.endpoint),
      servers: [server],
    });
  }
  // Keep a stable order (by first server's id) so groups don't jump around
  // as unrelated rows in the list refresh.
  groups.sort((a, b) => a.servers[0].id.localeCompare(b.servers[0].id));
  return groups;
}

function groupStatusText(group: ServiceGroup): string {
  if (group.servers.some((s) => s.status === "reauth_required")) {
    return "Reconnect needed — sign-in expired";
  }
  if (group.servers.every((s) => !s.enabled)) return "Disabled";
  const allTools = group.servers.flatMap((s) => s.tools);
  if (allTools.length === 0) {
    return group.servers.some((s) => s.last_synced_at) ? "No tools discovered" : "Pending discovery";
  }
  const pending = allTools.filter((t) => t.requires_approval && !t.approved).length;
  if (pending > 0) {
    return `Connected — ${pending} ${pending === 1 ? "action needs" : "actions need"} your approval`;
  }
  return `Connected — ${allTools.length} ${allTools.length === 1 ? "tool" : "tools"} active`;
}

// A tool paired with the specific server record it lives on — a group can
// span more than one server row, and approve/deny/refresh all need to know
// which underlying server_id a given tool belongs to.
type FlatTool = { tool: McpServerTool; server: McpServerRecord };

function bucketizeTools(group: ServiceGroup) {
  const flat: FlatTool[] = group.servers.flatMap((server) => server.tools.map((tool) => ({ tool, server })));
  return {
    // The whole point of MAN-92/104: these need zero UI — just a count.
    autoActive: flat.filter((x) => !x.tool.requires_approval && x.tool.approved),
    // An owner explicitly turned a non-high-stakes tool off — rare, but give
    // a way back rather than silently hiding it forever.
    turnedOff: flat.filter((x) => !x.tool.requires_approval && !x.tool.approved),
    // The real gate this redesign keeps: money/delete/third-party-send tools
    // still waiting on an explicit yes.
    needsApproval: flat.filter((x) => x.tool.requires_approval && !x.tool.approved),
    // High-stakes tools the owner already approved — shown compactly with a
    // revoke option, not folded into the invisible "auto" bucket.
    approvedHighStakes: flat.filter((x) => x.tool.requires_approval && x.tool.approved),
  };
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

  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [confirmDeleteKey, setConfirmDeleteKey] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const groups = useMemo(() => groupServers(servers), [servers]);

  const setRowErr = (key: string, message: string) => setRowError((prev) => ({ ...prev, [key]: message }));
  const clearRowErr = (key: string) =>
    setRowError((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });

  const attemptDiscovery = useCallback(
    async (group: ServiceGroup) => {
      setBusyKey(`${group.key}:refresh`);
      try {
        await Promise.all(
          group.servers.map((server) =>
            mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}/refresh`, "POST", {
              workspace_id: workspaceId,
            }),
          ),
        );
        clearRowErr(group.key);
        await refresh();
      } catch (e) {
        setRowErr(
          group.key,
          e instanceof Error
            ? `Discovery failed: ${e.message}`
            : "Discovery failed — the server may require authorization Empyralis doesn't have a connect-flow for yet on custom URLs.",
        );
      } finally {
        setBusyKey((k) => (k === `${group.key}:refresh` ? null : k));
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
      setExpandedKey(id);
      void attemptDiscovery({ key: id, title: "", subtitle: "", servers: [{ id } as McpServerRecord] });
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Could not connect to that MCP server.");
    } finally {
      setAdding(false);
    }
  }

  async function handleToggleEnabled(group: ServiceGroup) {
    const key = `${group.key}:enable`;
    setBusyKey(key);
    const nextEnabled = !group.servers.some((s) => s.enabled);
    try {
      // tools intentionally omitted here — see the file-header note on why
      // sending a non-empty tools[] through this route would wipe approvals.
      await Promise.all(
        group.servers.map((server) =>
          mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}`, "PUT", {
            workspace_id: workspaceId,
            label: server.label,
            endpoint: server.endpoint,
            transport: "streamable_http",
            enabled: nextEnabled,
            discover_tools: false,
          }),
        ),
      );
      clearRowErr(group.key);
      await refresh();
    } catch (e) {
      setRowErr(group.key, e instanceof Error ? e.message : "Could not update this connection.");
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  async function handleDelete(group: ServiceGroup) {
    const key = `${group.key}:delete`;
    setBusyKey(key);
    try {
      await Promise.all(
        group.servers.map((server) =>
          mutateJson(
            `/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}?workspace_id=${encodeURIComponent(workspaceId)}`,
            "DELETE",
          ),
        ),
      );
      setConfirmDeleteKey(null);
      if (expandedKey === group.key) setExpandedKey(null);
      await refresh();
    } catch (e) {
      setRowErr(group.key, e instanceof Error ? e.message : "Could not disconnect this app.");
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  async function handleToolApproval(groupKey: string, server: McpServerRecord, toolName: string, action: "approve" | "deny") {
    const key = `${groupKey}:${action}:${server.id}:${toolName}`;
    setBusyKey(key);
    try {
      await mutateJson(`/api/agent-registry/mcp/servers/${encodeURIComponent(server.id)}/tools/${action}`, "POST", {
        workspace_id: workspaceId,
        tool_name: toolName,
      });
      clearRowErr(groupKey);
      await refresh();
    } catch (e) {
      const verb = action === "approve" ? "approve" : "revoke";
      setRowErr(groupKey, e instanceof Error ? e.message : `Could not ${verb} "${toolName}".`);
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  return (
    <>
      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>MCP servers</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Connect a remote MCP server by URL. Tools are enabled automatically once a server connects — only
        money-moving, destructive, or third-party-messaging actions need your explicit approval first.
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
      ) : groups.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-icon">
            <Server size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-empty-title">No MCP servers connected</div>
          <div className="fleet-empty-desc">Paste a server URL above to give this workspace's agents new tools.</div>
        </div>
      ) : (
        <div className="fleet-list">
          {groups.map((group) => {
            const expanded = expandedKey === group.key;
            const err = rowError[group.key];
            const refreshBusy = busyKey === `${group.key}:refresh`;
            const enableBusy = busyKey === `${group.key}:enable`;
            const deleteBusy = busyKey === `${group.key}:delete`;
            const anyEnabled = group.servers.some((s) => s.enabled);
            const totalTools = group.servers.reduce((n, s) => n + s.tool_count, 0);
            const lastSynced = group.servers
              .map((s) => s.last_synced_at)
              .filter((v): v is string => Boolean(v))
              .sort()
              .pop();
            const buckets = bucketizeTools(group);
            const primaryEndpoint = group.servers.length === 1 ? group.servers[0].endpoint : "";

            return (
              <div key={group.key} className="fleet-list-row" style={{ flexDirection: "column", alignItems: "stretch", cursor: "default" }}>
                <button
                  type="button"
                  onClick={() => setExpandedKey(expanded ? null : group.key)}
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
                    <span className="fleet-list-row-title">{group.title}</span>
                    <span className="fleet-list-row-desc">
                      {group.servers.length > 1 ? `${group.subtitle} · ${totalTools} tools` : (primaryEndpoint ? hostFromUrl(primaryEndpoint) : group.subtitle)}
                    </span>
                  </span>
                  <span className="fleet-list-row-meta">{groupStatusText(group)}</span>
                </button>

                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="fleet-btn"
                    disabled={refreshBusy}
                    onClick={() => attemptDiscovery(group)}
                  >
                    <RefreshCw size={13} strokeWidth={1.75} /> {refreshBusy ? "Refreshing…" : "Refresh"}
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={anyEnabled}
                    aria-label={`${anyEnabled ? "Disable" : "Enable"} ${group.title}`}
                    className={`fleet-toggle${anyEnabled ? " is-on" : ""}`}
                    disabled={enableBusy}
                    onClick={() => handleToggleEnabled(group)}
                  />
                  <span className="fleet-toggle-row-desc" style={{ marginTop: 0 }}>{anyEnabled ? "Enabled" : "Disabled"}</span>
                  <div style={{ flex: 1 }} />
                  {confirmDeleteKey === group.key ? (
                    <>
                      <span className="fleet-list-row-desc">Disconnect {group.title}?</span>
                      <button type="button" className="fleet-btn" disabled={deleteBusy} onClick={() => setConfirmDeleteKey(null)}>
                        Cancel
                      </button>
                      <button type="button" className="fleet-btn fleet-btn--danger" disabled={deleteBusy} onClick={() => handleDelete(group)}>
                        {deleteBusy ? "Disconnecting…" : "Confirm"}
                      </button>
                    </>
                  ) : (
                    <button type="button" className="fleet-btn" onClick={() => setConfirmDeleteKey(group.key)} title="Disconnect">
                      <Trash2 size={13} strokeWidth={1.75} /> Disconnect
                    </button>
                  )}
                </div>

                {err ? (
                  <p className="fleet-list-row-desc" style={{ color: "var(--offline-text)", display: "flex", alignItems: "flex-start", gap: 6, marginTop: 8 }}>
                    <TriangleAlert size={13} strokeWidth={1.75} style={{ flexShrink: 0, marginTop: 1 }} /> {err}
                  </p>
                ) : null}

                {lastSynced ? (
                  <span className="fleet-list-row-desc" style={{ marginTop: 4 }}>Last discovered {timeAgo(lastSynced)}</span>
                ) : null}

                {expanded ? (
                  <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    {totalTools === 0 ? (
                      <p className="fleet-list-row-desc">
                        {refreshBusy ? "Discovering tools…" : "No tools discovered yet."}
                      </p>
                    ) : (
                      <>
                        {buckets.autoActive.length > 0 && (
                          <p className="fleet-list-row-desc" style={{ marginTop: 0 }}>
                            {buckets.autoActive.length} {buckets.autoActive.length === 1 ? "tool" : "tools"} enabled automatically —
                            no action needed.
                          </p>
                        )}

                        {buckets.needsApproval.length > 0 && (
                          <div style={{ marginBottom: 10 }}>
                            <div className="fleet-toggle-row-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                              <TriangleAlert size={13} strokeWidth={1.75} style={{ color: "var(--offline-text)" }} />
                              Needs your approval ({buckets.needsApproval.length})
                            </div>
                            {buckets.needsApproval.map(({ tool, server }) => {
                              const approveBusy = busyKey === `${group.key}:approve:${server.id}:${tool.name}`;
                              const denyBusy = busyKey === `${group.key}:deny:${server.id}:${tool.name}`;
                              return (
                                <div key={`${server.id}:${tool.name}`} className="fleet-toggle-row">
                                  <div style={{ minWidth: 0 }}>
                                    <div className="fleet-toggle-row-label">{tool.label || tool.name}</div>
                                    {tool.description ? <div className="fleet-toggle-row-desc">{tool.description}</div> : null}
                                    <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                                      <span className="fleet-badge" style={{ marginLeft: 0 }}>{tool.risk_level} risk</span>
                                      {group.servers.length > 1 ? (
                                        <span className="fleet-badge" style={{ marginLeft: 0 }}>{server.label}</span>
                                      ) : null}
                                    </div>
                                  </div>
                                  <button
                                    type="button"
                                    className="fleet-badge fleet-badge--action"
                                    style={{ marginLeft: 0, flexShrink: 0 }}
                                    disabled={approveBusy || denyBusy}
                                    onClick={() => handleToolApproval(group.key, server, tool.name, "approve")}
                                  >
                                    {approveBusy ? "Approving…" : "Approve"}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        )}

                        {buckets.approvedHighStakes.length > 0 && (
                          <div style={{ marginBottom: 10 }}>
                            <div className="fleet-toggle-row-label">Approved high-risk actions ({buckets.approvedHighStakes.length})</div>
                            {buckets.approvedHighStakes.map(({ tool, server }) => {
                              const denyBusy = busyKey === `${group.key}:deny:${server.id}:${tool.name}`;
                              return (
                                <div key={`${server.id}:${tool.name}`} className="fleet-toggle-row">
                                  <div style={{ minWidth: 0 }}>
                                    <div className="fleet-toggle-row-label">{tool.label || tool.name}</div>
                                    {tool.description ? <div className="fleet-toggle-row-desc">{tool.description}</div> : null}
                                  </div>
                                  <button
                                    type="button"
                                    className="fleet-badge fleet-badge--lock"
                                    style={{ marginLeft: 0, flexShrink: 0 }}
                                    disabled={denyBusy}
                                    title="Revoke — the agent loses access to this tool"
                                    onClick={() => handleToolApproval(group.key, server, tool.name, "deny")}
                                  >
                                    {denyBusy ? "Revoking…" : "Approved ✕"}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        )}

                        {buckets.turnedOff.length > 0 && (
                          <div>
                            <div className="fleet-toggle-row-label">Turned off ({buckets.turnedOff.length})</div>
                            {buckets.turnedOff.map(({ tool, server }) => {
                              const approveBusy = busyKey === `${group.key}:approve:${server.id}:${tool.name}`;
                              return (
                                <div key={`${server.id}:${tool.name}`} className="fleet-toggle-row">
                                  <div style={{ minWidth: 0 }}>
                                    <div className="fleet-toggle-row-label">{tool.label || tool.name}</div>
                                  </div>
                                  <button
                                    type="button"
                                    className="fleet-badge fleet-badge--action"
                                    style={{ marginLeft: 0, flexShrink: 0 }}
                                    disabled={approveBusy}
                                    onClick={() => handleToolApproval(group.key, server, tool.name, "approve")}
                                  >
                                    {approveBusy ? "Enabling…" : "Enable"}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </>
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
