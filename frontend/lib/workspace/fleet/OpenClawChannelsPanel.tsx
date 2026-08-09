"use client";

/**
 * Channel setup for the OpenClaw transport — every channel, one screen, from
 * the browser. No terminal, no SSH.
 *
 * THE FORM IS GENERATED, NOT WRITTEN
 * ---------------------------------
 * There is no per-channel component in this file and there must never be one.
 * Every control comes from `channels[].fields`, which the backend derives from
 * OpenClaw's own config schema at manifest-generation time. A channel OpenClaw
 * adds upstream grows its own form here with no change to this file — the same
 * rule that already governs the channel LIST governs the channel FORMS, for
 * the same reason: a hand-maintained per-channel surface is pure curation and
 * pure defect.
 *
 * THREE STATES, NEVER COLLAPSED
 * -----------------------------
 * OpenClaw's own `channels list` is the bar:
 *
 *     - Feishu: not installed, configured, disabled, run openclaw plugins …
 *
 * Three independent facts and the specific next action, on one line. A single
 * "Connected / Not connected" light throws away exactly the information that
 * says which of the three to go fix. So each row reads its three states
 * separately, and each non-working state carries its own remediation — a
 * BUTTON here rather than a command, because the cloud can already drive the
 * device. Where the action genuinely cannot happen in a browser (a QR scan on
 * a phone), the row says so instead of rendering a control that does nothing.
 *
 * OBSERVED, NEVER DECLARED
 * ------------------------
 * No state on this screen comes from "we saved a credential, so it must be
 * connected". `installed` is OpenClaw's registry, `set` is a read-back of the
 * effective config, `enabled` is the config switch. When the box cannot be
 * reached we show that as its own state — never as "not connected", which
 * would be a claim about the channel made from a fact about the network.
 *
 * NOTHING SECRET COMES BACK
 * -------------------------
 * A secret input is always empty on load and after a save. There is no
 * "current value" to show: OpenClaw redacts secret values before they leave
 * the customer's machine, so the browser only ever learns `set: true`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Loader2,
  Plug,
  RefreshCw,
  Smartphone,
} from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

import "./openclaw-channels.css";

export type OpenClawCredentialField = {
  name: string;
  secret: boolean;
  type: string;
  file_alternative?: string | null;
};

export type OpenClawChannelCatalogEntry = {
  channel_key: string;
  channel_id: string;
  label: string;
  connect_method: "credential" | "pairing" | "plugin_absent";
  selection_label: string;
  docs_path: string | null;
  fields: OpenClawCredentialField[];
  requires_plugin: boolean;
  plugin_id: string | null;
};

export type OpenClawObservedField = { name: string; secret: boolean; type: string; set: boolean };

export type OpenClawObservedChannel = {
  channel_id: string;
  channel_key: string;
  installed: boolean;
  requires_plugin: boolean;
  enabled: boolean;
  configured: boolean;
  fields: OpenClawObservedField[];
  accounts: string[];
};

type SetupResponse = {
  openclaw_version?: string;
  channels?: OpenClawChannelCatalogEntry[];
  observed?: { status?: string; channels?: OpenClawObservedChannel[]; refusal?: { detail?: string } | null } | null;
  observed_error?: string | null;
};

/** The one thing this row needs the reader to do next, and why.
 *  Exactly one per row — a row with two calls to action has no call to action. */
type Remediation =
  | { kind: "install"; label: string; detail: string }
  | { kind: "credential"; label: string; detail: string }
  | { kind: "enable"; label: string; detail: string }
  | { kind: "elsewhere"; detail: string }
  | { kind: "unknown"; detail: string }
  | { kind: "ready"; detail: string };

function remediationFor(
  entry: OpenClawChannelCatalogEntry,
  observed: OpenClawObservedChannel | undefined,
  reachable: boolean,
): Remediation {
  if (!reachable || !observed) {
    return {
      kind: "unknown",
      detail: "This computer could not be reached, so its channel state is unknown.",
    };
  }
  if (entry.requires_plugin && !observed.installed) {
    return {
      kind: "install",
      // Deliberately not "not connected": the plugin is the thing that is
      // missing, and installing it is a button, not a support ticket.
      label: "Install plugin",
      detail: "The channel's plugin is not on this computer yet.",
    };
  }
  if (entry.connect_method === "plugin_absent") {
    return {
      kind: "unknown",
      detail: "OpenClaw declares this channel's connection fields only once its plugin is installed.",
    };
  }
  if (entry.connect_method === "pairing") {
    return {
      kind: "elsewhere",
      // The honest version of a dead control. Their own selection label says
      // how it links; we do not invent a form that would submit nothing.
      detail: `${entry.selection_label} — there is no token to paste. Link it from the OpenClaw session on this computer.`,
    };
  }
  const missing = observed.fields.filter((field) => !field.set);
  if (missing.length > 0) {
    return {
      kind: "credential",
      label: observed.fields.some((field) => field.set) ? "Finish credential" : "Add credential",
      detail: `Waiting on ${missing.map((field) => field.name).join(", ")}.`,
    };
  }
  if (!observed.enabled) {
    return {
      kind: "enable",
      label: "Turn on",
      detail: "Credential is in place, but the channel is switched off on this computer.",
    };
  }
  return {
    kind: "ready",
    // "Ready", never "Connected". A connection is proven by a real message
    // arriving, and nothing on this screen has seen one.
    detail: "Plugin installed, credential in place, channel on.",
  };
}

function StateChip({ ok, on, off }: { ok: boolean; on: string; off: string }) {
  return (
    <span className={`openclaw-chip ${ok ? "openclaw-chip--ok" : "openclaw-chip--off"}`}>
      {ok ? on : off}
    </span>
  );
}

export function OpenClawChannelsPanel({
  workspaceId,
  gatewayId,
  agentId,
}: {
  workspaceId: string;
  gatewayId: string;
  agentId: string;
}) {
  const [data, setData] = useState<SetupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<OpenClawChannelCatalogEntry | null>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!opts?.silent) setLoading(true);
      try {
        const res = await fetch(
          `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/setup`,
          { credentials: "include" },
        );
        if (!res.ok) {
          setError(`Could not load channels (${res.status}).`);
          return;
        }
        setError(null);
        setData((await res.json()) as SetupResponse);
      } catch {
        setError("Could not load channels.");
      } finally {
        setLoading(false);
      }
    },
    [gatewayId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  /** The `doctor --fix` equivalent: provisioning. It installs the plugins it
   *  was asked for and re-asserts policy — and it HIDES NOTHING, because every
   *  row re-reads its own three states straight afterwards. */
  const provision = useCallback(
    async (installChannels: string[]) => {
      setBusy(installChannels[0] ?? "__all__");
      try {
        const res = await fetch(
          `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/provision?agent_id=${encodeURIComponent(agentId)}`,
          {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({ install_channels: installChannels }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(String(body?.detail || `Setup failed (${res.status}).`));
        } else {
          const refusal = body?.openclaw_provisioning?.refusal;
          setError(refusal ? `${refusal.code}: ${refusal.detail}` : null);
        }
      } catch {
        setError("Could not reach this computer.");
      } finally {
        setBusy(null);
        await load({ silent: true });
      }
    },
    [agentId, gatewayId, load],
  );

  const catalog = data?.channels ?? [];
  const observedList = data?.observed?.channels ?? [];
  const reachable = Boolean(data?.observed) && !data?.observed_error;
  const observedById = useMemo(
    () => new Map(observedList.map((entry) => [entry.channel_id, entry])),
    [observedList],
  );

  const rows = useMemo(
    () =>
      catalog.map((entry) => {
        const observed = observedById.get(entry.channel_id);
        return { entry, observed, remediation: remediationFor(entry, observed, reachable) };
      }),
    [catalog, observedById, reachable],
  );

  const repairable = rows
    .filter((row) => row.remediation.kind === "install" || row.remediation.kind === "enable")
    .map((row) => row.entry.channel_key);

  return (
    <section aria-labelledby="openclaw-channels-heading">
      <h2 className="fleet-detail-section-title" id="openclaw-channels-heading">
        Channels
      </h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        {data?.openclaw_version
          ? `Carried by the OpenClaw transport on this computer (v${data.openclaw_version}).`
          : "Carried by the OpenClaw transport on this computer."}
      </p>

      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {data?.observed_error ? (
        <div className="openclaw-banner" role="status">
          <AlertTriangle size={14} aria-hidden />
          <span>
            This computer could not be reached, so the states below are unknown. The setup fields are
            still correct for OpenClaw {data.openclaw_version ?? ""}.
          </span>
        </div>
      ) : null}

      <div className="openclaw-toolbar">
        {/* The single accent action in this view. Everything in a row is
            neutral, so there is exactly one primary action on screen. */}
        <button
          type="button"
          className={`fleet-btn ${repairable.length > 0 ? "fleet-btn--accent-fill" : ""}`}
          onClick={() => void provision(repairable)}
          disabled={busy !== null || loading}
        >
          {busy === "__all__" ? <Loader2 size={14} className="openclaw-spin" /> : <Plug size={14} />}
          {repairable.length > 0 ? `Set up ${repairable.length} channel${repairable.length === 1 ? "" : "s"}` : "Re-check this computer"}
        </button>
        <button
          type="button"
          className="fleet-btn"
          onClick={() => void load()}
          disabled={busy !== null || loading}
          aria-label="Refresh channel state"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <p className="fleet-subtitle">Reading this computer…</p>
      ) : rows.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No channels</div>
          <div className="fleet-empty-desc">
            This build of the transport carries no channels, which should be impossible — regenerate
            the OpenClaw channel manifest.
          </div>
        </div>
      ) : (
        <ul className="openclaw-channel-list">
          {rows.map(({ entry, observed, remediation }) => (
            <li key={entry.channel_key} className="openclaw-channel-row">
              <div className="openclaw-channel-main">
                <div className="openclaw-channel-title">{entry.label}</div>
                <div className="openclaw-channel-selection">{entry.selection_label}</div>
                <div className="openclaw-chips">
                  {entry.requires_plugin ? (
                    <StateChip ok={Boolean(observed?.installed)} on="installed" off="not installed" />
                  ) : (
                    <span className="openclaw-chip openclaw-chip--ok">bundled</span>
                  )}
                  {entry.connect_method === "credential" ? (
                    <StateChip ok={Boolean(observed?.configured)} on="credential set" off="no credential" />
                  ) : null}
                  <StateChip ok={Boolean(observed?.enabled)} on="on" off="off" />
                </div>
                <div className="openclaw-channel-detail">{remediation.detail}</div>
              </div>
              <div className="openclaw-channel-actions">
                {remediation.kind === "install" || remediation.kind === "enable" ? (
                  <button
                    type="button"
                    className="fleet-btn"
                    onClick={() => void provision([entry.channel_key])}
                    disabled={busy !== null}
                  >
                    {busy === entry.channel_key ? (
                      <Loader2 size={14} className="openclaw-spin" />
                    ) : null}
                    {remediation.label}
                  </button>
                ) : null}
                {remediation.kind === "credential" ? (
                  <button
                    type="button"
                    className="fleet-btn"
                    onClick={() => setOpen(entry)}
                    disabled={busy !== null}
                  >
                    {remediation.label}
                  </button>
                ) : null}
                {remediation.kind === "ready" ? (
                  <>
                    <span className="openclaw-ready">
                      <Check size={14} aria-hidden /> Ready
                    </span>
                    <button type="button" className="fleet-btn" onClick={() => setOpen(entry)}>
                      Replace credential
                    </button>
                  </>
                ) : null}
                {remediation.kind === "elsewhere" ? (
                  <span className="openclaw-ready openclaw-ready--muted">
                    <Smartphone size={14} aria-hidden /> Links on the device
                  </span>
                ) : null}
                {entry.docs_path ? (
                  <a
                    className="openclaw-docs"
                    href={`https://docs.openclaw.ai${entry.docs_path}`}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Docs <ExternalLink size={12} aria-hidden />
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {open ? (
        <CredentialDialog
          gatewayId={gatewayId}
          entry={open}
          observed={observedById.get(open.channel_id)}
          onClose={() => setOpen(null)}
          onSaved={async () => {
            setOpen(null);
            await load({ silent: true });
          }}
        />
      ) : null}
    </section>
  );
}

/** The generated form. Every control below is driven by `entry.fields`; there
 *  is no branch on a channel id anywhere in it. */
function CredentialDialog({
  gatewayId,
  entry,
  observed,
  onClose,
  onSaved,
}: {
  gatewayId: string;
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setFields = useMemo(
    () => new Set((observed?.fields ?? []).filter((field) => field.set).map((field) => field.name)),
    [observed],
  );
  const filled = Object.values(values).filter((value) => value.trim().length > 0).length;

  const save = async () => {
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim().length > 0),
    );
    if (Object.keys(payload).length === 0) {
      setError("Fill in at least one field.");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(
        `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/channels/${encodeURIComponent(entry.channel_key)}/credential`,
        {
          method: "PUT",
          credentials: "include",
          headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
          body: JSON.stringify({ values: payload }),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(String(body?.detail || `Save failed (${res.status}).`));
        return;
      }
      const refusal = body?.openclaw_channel_setup?.refusal;
      if (refusal) {
        setError(`${refusal.code}: ${refusal.detail}`);
        return;
      }
      // Cleared rather than retained: nothing typed here is kept in the tab
      // any longer than the request needs it.
      setValues({});
      await onSaved();
    } catch {
      setError("Could not reach this computer.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div
        className="openclaw-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="openclaw-dialog-heading"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 className="fleet-detail-section-title" id="openclaw-dialog-heading" style={{ marginTop: 0 }}>
          {entry.label}
        </h3>
        <p className="fleet-subtitle" style={{ marginTop: 0 }}>
          {entry.selection_label}. Fields are the ones OpenClaw declares for this channel.
        </p>

        {error ? (
          <div className="openclaw-banner" role="alert">
            <AlertTriangle size={14} aria-hidden />
            <span>{error}</span>
          </div>
        ) : null}

        <div className="openclaw-form">
          {entry.fields.map((field) => {
            const inputId = `openclaw-${entry.channel_id}-${field.name}`;
            const already = setFields.has(field.name);
            return (
              <div key={field.name} className="openclaw-form-field">
                <label htmlFor={inputId}>
                  <span className="openclaw-form-name">{field.name}</span>
                  {field.secret ? <span className="openclaw-chip openclaw-chip--secret">secret</span> : null}
                  {already ? <span className="openclaw-chip openclaw-chip--ok">set</span> : null}
                </label>
                <input
                  id={inputId}
                  className="fleet-wizard-input"
                  /* A secret is a password input and is ALWAYS empty on open:
                     there is no stored value to prefill, because OpenClaw
                     redacts it before it ever leaves the customer's machine. */
                  type={field.secret ? "password" : "text"}
                  autoComplete="off"
                  spellCheck={false}
                  value={values[field.name] ?? ""}
                  placeholder={already ? "Set — type to replace" : ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [field.name]: event.target.value }))
                  }
                />
              </div>
            );
          })}
        </div>

        <p className="openclaw-form-note">
          Leave a field blank to keep what is already on the computer. Values are sent straight to it
          and are not stored here.
        </p>

        <div className="openclaw-dialog-actions">
          <button type="button" className="fleet-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            onClick={() => void save()}
            disabled={saving || filled === 0}
          >
            {saving ? <Loader2 size={14} className="openclaw-spin" /> : null}
            {saving ? "Saving…" : "Save credential"}
          </button>
        </div>
      </div>
    </div>
  );
}
