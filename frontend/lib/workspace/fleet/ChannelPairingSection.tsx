"use client";

// Channel sender pairing — Settings → Connections. Inbound: a channel sender
// (a Slack user DMing the app, a phone number texting a bound Twilio number,
// a WeChat Official Account follower, a Telegram/WhatsApp identity) connecting
// IN to this workspace. Sits alongside McpApiKeysSection ("Inbound: external
// MCP clients connecting IN") — same direction, different protocol.
//
// Backend contract (verified file:line):
//   POST /auth/channel-pairing/intents           create_channel_pairing_intent
//        server_modules/routes_auth.py:487-502 -> channel_pairing_service.
//        create_pairing_intent (also mounted at /api/v1/auth/... and
//        /api/auth/...; this file calls the Next proxy at
//        /api/channel-pairing/intents -> frontend/app/api/channel-pairing/
//        intents/route.ts -> /api/v1/auth/channel-pairing/intents).
//   GET  /auth/channel-pairing/links              list_authenticated_channel_links
//        server_modules/routes_auth.py:505-517
//   POST /auth/channel-pairing/links/{id}/revoke   revoke_authenticated_channel_link
//        server_modules/routes_auth.py:520-533
//
// Providers gated by channel_pairing_service.authorize_channel_message today
// (verified by grepping every call site, server_modules/*.py): telegram,
// whatsapp, slack (DMs only), sms (Twilio), wechat_official. Discord and
// iMessage use their own separate authorization paths, not this service, and
// are deliberately not offered here.
//
// This is a fresh component, not the pre-existing
// workspace-channel-pairing-surface.tsx (WorkspaceChannelPairingSurface).
// That component's only wrapper, <WorkspaceBoundary>, is never mounted
// anywhere under frontend/app — its whole "Workstation Surface" v2 shell
// (workspace-boundary.tsx, workspace-services.tsx,
// workstation-surface-primitives.tsx) is orphaned scaffolding with zero
// other consumers. Standing that framework up was a much bigger, separate
// call than fixing tonight's regression, so this follows the pattern every
// other live Settings → Connections section already uses (see
// McpApiKeysSection.tsx, MembersSection.tsx): plain fetch + fleet-* CSS,
// wired straight to the same backend endpoints.
//
// No approval-prompt UI (standing product law) — a pairing code an owner
// issues once is the whole mechanism, same shape as the Members section's
// invite links just above it.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Copy, Link2, Trash2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { formatDateTime } from "@/lib/workspace/fleet/fleet-presentation";
import { useOwnWorkspaceRole } from "@/lib/workspace/fleet/members-data";

type ChannelProvider = "telegram" | "whatsapp" | "slack" | "sms" | "wechat_official";

type ChannelLink = {
  link_id: string;
  provider: string;
  external_subject_hint?: string | null;
  scopes: string[];
  linked_at?: number | null;
  revoked_at?: number | null;
  revoked_reason?: string | null;
  status: "active" | "revoked";
};

type ChannelPairingIntent = {
  intent_id: string;
  provider: string;
  expires_at: number;
  pairing_code: string;
  connect_url?: string;
  instructions: string;
};

// The real provider list, taken from the backend's own provider handling
// (channel_pairing_service.py's _display_provider map and every
// authorize_channel_message call site) — not the stale ['telegram',
// 'whatsapp'] hardcode the orphaned surface shipped with.
const PROVIDERS: { id: ChannelProvider; label: string }[] = [
  { id: "telegram", label: "Telegram" },
  { id: "whatsapp", label: "WhatsApp" },
  { id: "slack", label: "Slack" },
  { id: "sms", label: "SMS" },
  { id: "wechat_official", label: "WeChat Official" },
];

const PROVIDER_LABEL: Record<string, string> = Object.fromEntries(
  PROVIDERS.map((p) => [p.id, p.label]),
);

function providerLabel(provider: string): string {
  return PROVIDER_LABEL[provider] || provider;
}

// Timestamps here are epoch SECONDS (channel_pairing_service.py stores
// linked_at/revoked_at/expires_at as SQLite INTEGER seconds) — same
// seconds-vs-milliseconds trap members-data.ts's own header warns about for
// invite created_at, so every read multiplies by 1000 before formatDateTime.
function formatEpochSeconds(value: number | null | undefined): string {
  if (!value) return "—";
  return formatDateTime(value * 1000, { dateStyle: "medium", timeStyle: "short" });
}

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
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

export function ChannelPairingSection({ workspaceId }: { workspaceId: string }) {
  // Role comes from the server-rendered account-shell payload, not a client
  // fetch — the members round trip this used to do was the same redundant
  // call that delayed every project control by ~3s until it was removed.
  const ownRole = useOwnWorkspaceRole(workspaceId);

  const [links, setLinks] = useState<ChannelLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [provider, setProvider] = useState<ChannelProvider>("telegram");
  const [creating, setCreating] = useState(false);
  const [freshIntent, setFreshIntent] = useState<ChannelPairingIntent | null>(null);
  const [copied, setCopied] = useState<"code" | "link" | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getJson(
        `/api/channel-pairing/links?workspace_id=${encodeURIComponent(workspaceId)}&include_revoked=true`,
      );
      setLinks(Array.isArray(data?.links) ? data.links : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load channel pairings.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const createPairing = useCallback(async () => {
    setCreating(true);
    setError(null);
    setFreshIntent(null);
    try {
      const data = await mutateJson("/api/channel-pairing/intents", "POST", {
        provider,
        workspace_id: workspaceId,
        allow_relink: false,
        metadata: { source: "settings_connections" },
      });
      setFreshIntent((data?.intent as ChannelPairingIntent) ?? null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create a pairing code.");
    } finally {
      setCreating(false);
    }
  }, [provider, workspaceId, load]);

  const revokeLink = useCallback(
    async (linkId: string) => {
      setRevokingId(linkId);
      setError(null);
      try {
        await mutateJson(`/api/channel-pairing/links/${encodeURIComponent(linkId)}/revoke`, "POST", {
          confirm: true,
          reason: "Revoked from workspace Settings → Connections.",
        });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not revoke this pairing.");
      } finally {
        setRevokingId(null);
      }
    },
    [load],
  );

  function copyValue(value: string, which: "code" | "link") {
    navigator.clipboard
      ?.writeText(value)
      .then(() => {
        setCopied(which);
        setTimeout(() => setCopied(null), 1500);
      })
      .catch(() => {});
  }

  const activeLinks = useMemo(() => links.filter((l) => l.status === "active"), [links]);
  const revokedLinks = useMemo(() => links.filter((l) => l.status === "revoked"), [links]);

  // No dead controls: the backend floor is the same one this section would
  // otherwise just bounce off of (channel_pairing_enabled requires role
  // member/owner/admin — see workspace_bootstrap_service.py's
  // _build_workspace_bootstrap_payload). A viewer would only ever get a 403,
  // so the whole section stays unrendered rather than showing a button that
  // fails every time. Also unrendered while ownRole is still resolving
  // (useOwnRole's own doc comment: avoids flashing controls on then off).
  if (ownRole === "viewer" || ownRole === null) {
    return null;
  }

  return (
    <>
      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Channel pairing</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Slack DMs, SMS, WeChat Official, Telegram, and WhatsApp all deny an unrecognized sender before any
        reply. Pair a sender here to let them through — the code is one-time, no per-message approval.
      </p>

      {error ? (
        <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>
          {error}
        </div>
      ) : null}

      {/* Neutral tone, not accent: the accent in this section belongs to
          "Create pairing code" below, the actual state-changing action. This
          is just the toggle that reveals it (craft doctrine: one accent per
          view). */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--space-3)" }}>
        <button
          type="button"
          className="fleet-btn"
          onClick={() => {
            setFormOpen((v) => !v);
            setError(null);
          }}
        >
          <Link2 size={14} strokeWidth={1.75} /> Pair a sender
        </button>
      </div>

      {formOpen ? (
        <div className="fleet-card" style={{ padding: "var(--space-3)", marginBottom: "var(--space-4)" }}>
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <select
              className="fleet-wizard-input"
              value={provider}
              onChange={(e) => setProvider(e.target.value as ChannelProvider)}
              style={{ flex: "1 1 160px" }}
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={createPairing} disabled={creating}>
              {creating ? "Creating…" : "Create pairing code"}
            </button>
          </div>

          {freshIntent ? (
            <div className="fleet-card" style={{ borderColor: "var(--accent)", marginTop: 10, padding: "var(--space-3)" }}>
              <div className="fleet-list-row-title">
                {providerLabel(freshIntent.provider)} pairing code — expires {formatEpochSeconds(freshIntent.expires_at)}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
                <code
                  style={{
                    flex: 1,
                    overflow: "auto",
                    fontSize: "var(--text-sm)",
                    background: "var(--bg-inset)",
                    padding: "var(--space-2)",
                    borderRadius: "var(--radius-control)",
                  }}
                >
                  {freshIntent.pairing_code}
                </code>
                <button type="button" className="fleet-btn" onClick={() => copyValue(freshIntent.pairing_code, "code")}>
                  {copied === "code" ? <Check size={14} /> : <Copy size={14} />} {copied === "code" ? "Copied" : "Copy code"}
                </button>
              </div>
              {freshIntent.connect_url ? (
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
                  <code
                    style={{
                      flex: 1,
                      overflow: "auto",
                      fontSize: "var(--text-sm)",
                      background: "var(--bg-inset)",
                      padding: "var(--space-2)",
                      borderRadius: "var(--radius-control)",
                    }}
                  >
                    {freshIntent.connect_url}
                  </code>
                  <button type="button" className="fleet-btn" onClick={() => copyValue(freshIntent.connect_url as string, "link")}>
                    {copied === "link" ? <Check size={14} /> : <Copy size={14} />} {copied === "link" ? "Copied" : "Copy link"}
                  </button>
                </div>
              ) : null}
              <p className="fleet-list-row-desc" style={{ marginTop: 6 }}>{freshIntent.instructions}</p>
            </div>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className="fleet-list">
          <div className="fleet-list-row">
            <div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} />
          </div>
        </div>
      ) : activeLinks.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-icon">
            <Link2 size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-empty-title">No paired senders yet</div>
          <div className="fleet-empty-desc">
            Create a pairing code above, then have the sender open the channel and use it.
          </div>
        </div>
      ) : (
        <div className="fleet-list">
          {activeLinks.map((link) => (
            <div key={link.link_id} className="fleet-list-row" style={{ cursor: "default" }}>
              <span className="fleet-list-row-main">
                <span className="fleet-list-row-title">
                  {link.external_subject_hint || `Linked sender ${link.link_id.slice(0, 8)}`}
                </span>
                <span className="fleet-list-row-desc">
                  linked {formatEpochSeconds(link.linked_at)}
                </span>
              </span>
              <span className="fleet-badge" style={{ marginLeft: 0 }}>{providerLabel(link.provider)}</span>
              <button
                type="button"
                className="fleet-btn"
                onClick={() => revokeLink(link.link_id)}
                disabled={revokingId === link.link_id}
              >
                <Trash2 size={14} /> {revokingId === link.link_id ? "Revoking…" : "Revoke"}
              </button>
            </div>
          ))}
        </div>
      )}

      {revokedLinks.length > 0 ? (
        <>
          <h3 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)", fontSize: "var(--text-sm)" }}>
            Revoked
          </h3>
          <div className="fleet-list">
            {revokedLinks.map((link) => (
              <div key={link.link_id} className="fleet-list-row" style={{ cursor: "default", opacity: 0.65 }}>
                <span className="fleet-list-row-main">
                  <span className="fleet-list-row-title">
                    {link.external_subject_hint || `Linked sender ${link.link_id.slice(0, 8)}`}
                  </span>
                  <span className="fleet-list-row-desc">
                    {providerLabel(link.provider)} · revoked {formatEpochSeconds(link.revoked_at)}
                    {link.revoked_reason ? ` · ${link.revoked_reason}` : ""}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}
