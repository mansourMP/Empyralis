"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader2, LogOut } from "lucide-react";

import { GatewayPairPanel, type GatewayRegistrationRecord } from "@/lib/gateway/GatewayPairPanel";
import { useWorkspaceGateways } from "./gateway-box-picker";
import {
  disconnectPersonalChannel,
  friendlyPersonalChannelError,
  setupTelegramPersonalChannel,
  setupWhatsAppPersonalChannel,
  usePersonalChannelStatus,
  type PersonalChannelKey,
} from "./personal-channel-pairing";

// Loaded lazily — qrcode's toDataURL touches document/Image internals that
// don't exist during SSR.
let qrcodeModulePromise: Promise<typeof import("qrcode")> | null = null;
function loadQrcode() {
  if (!qrcodeModulePromise) qrcodeModulePromise = import("qrcode");
  return qrcodeModulePromise;
}

function maskedIdentity(state: { linked_phone?: string | null; linked_username?: string | null; linked_jid?: string | null; linked_name?: string | null } | null | undefined): string {
  if (!state) return "";
  return state.linked_name || state.linked_username || state.linked_phone || state.linked_jid || "your account";
}

/**
 * Personal-channel (telegram_personal/whatsapp_personal) pairing wizard.
 * Drives the EXISTING /personal-channels/{channel}/gateways/{id} setup,
 * status, and disconnect endpoints — no new backend routes. Every status
 * badge here is the real enum value from those endpoints; there is no
 * client-invented "connected" state.
 */
export function PersonalChannelConnectPanel({
  workspaceId,
  channelKey,
  label,
}: {
  workspaceId: string;
  channelKey: PersonalChannelKey;
  label: string;
}) {
  const { gateways, loading: gatewaysLoading } = useWorkspaceGateways(workspaceId);
  const [gatewayId, setGatewayId] = useState<string | null>(null);

  useEffect(() => {
    if (gatewayId && gateways.some((g) => String(g.gateway_id) === gatewayId)) return;
    const first = gateways[0];
    setGatewayId(first ? String(first.gateway_id || "") || null : null);
  }, [gateways, gatewayId]);

  const { view, loading: statusLoading, refresh } = usePersonalChannelStatus(workspaceId, channelKey, gatewayId);

  if (!gatewaysLoading && gateways.length === 0) {
    return (
      <div className="pc-connect-panel">
        <p className="fleet-channel-expand-hint">
          Pair a computer first — {label} runs through your own Gateway, never Empyralis's servers.
        </p>
        <GatewayPairPanel
          workspaceId={workspaceId}
          compact
          onPaired={(g: GatewayRegistrationRecord) => setGatewayId(String(g.gateway_id || "") || null)}
        />
      </div>
    );
  }

  if (gatewaysLoading || !gatewayId || statusLoading) {
    return (
      <div className="pc-connect-panel pc-connect-panel--loading">
        <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} />
      </div>
    );
  }

  const status = view?.state?.status || "idle";

  return (
    <div className="pc-connect-panel">
      {channelKey === "telegram_personal" ? (
        <TelegramConnectBody
          label={label}
          gatewayId={gatewayId}
          status={status}
          view={view}
          onRefresh={refresh}
        />
      ) : (
        <WhatsAppConnectBody
          label={label}
          gatewayId={gatewayId}
          status={status}
          view={view}
          onRefresh={refresh}
        />
      )}
    </div>
  );
}

function DisconnectControl({
  channelKey,
  gatewayId,
  onDone,
}: {
  channelKey: PersonalChannelKey;
  gatewayId: string;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDisconnect = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await disconnectPersonalChannel(channelKey, gatewayId);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not disconnect.");
    } finally {
      setBusy(false);
    }
  }, [channelKey, gatewayId, onDone]);

  return (
    <div className="pc-connect-disconnect">
      <button type="button" className="fleet-btn pc-connect-disconnect-btn" onClick={handleDisconnect} disabled={busy}>
        {busy ? <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> : <LogOut size={13} strokeWidth={2} />}
        {busy ? "Disconnecting…" : "Disconnect & start over"}
      </button>
      {error && <p className="fleet-channel-expand-error">{error}</p>}
    </div>
  );
}

function TelegramConnectBody({
  label,
  gatewayId,
  status,
  view,
  onRefresh,
}: {
  label: string;
  gatewayId: string;
  status: string;
  view: ReturnType<typeof usePersonalChannelStatus>["view"];
  onRefresh: () => void;
}) {
  const [phoneNumber, setPhoneNumber] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submitPhone = useCallback(async () => {
    if (!phoneNumber.trim()) {
      setError("Enter a phone number, including the country code.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setupTelegramPersonalChannel(gatewayId, { phone_number: phoneNumber.trim() });
      onRefresh();
    } catch (e) {
      setError(friendlyPersonalChannelError(e instanceof Error ? e.message : String(e), label));
    } finally {
      setBusy(false);
    }
  }, [gatewayId, phoneNumber, label, onRefresh]);

  const submitCode = useCallback(async () => {
    if (!code.trim()) {
      setError("Enter the code Telegram sent you.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setupTelegramPersonalChannel(gatewayId, { login_code: code.trim() });
      setCode("");
      onRefresh();
    } catch (e) {
      setError(friendlyPersonalChannelError(e instanceof Error ? e.message : String(e), label));
    } finally {
      setBusy(false);
    }
  }, [gatewayId, code, label, onRefresh]);

  const submitPassword = useCallback(async () => {
    if (!password.trim()) {
      setError("Enter your two-factor password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setupTelegramPersonalChannel(gatewayId, { password: password.trim() });
      setPassword("");
      onRefresh();
    } catch (e) {
      setError(friendlyPersonalChannelError(e instanceof Error ? e.message : String(e), label));
    } finally {
      setBusy(false);
    }
  }, [gatewayId, password, label, onRefresh]);

  if (status === "connected") {
    return (
      <div className="pc-connect-connected">
        <div className="fleet-channel-expand-success">
          <Check size={16} strokeWidth={2} /> Connected as {maskedIdentity(view?.state)}
        </div>
        <DisconnectControl channelKey="telegram_personal" gatewayId={gatewayId} onDone={onRefresh} />
      </div>
    );
  }

  if (status === "code_required") {
    return (
      <div className="pc-connect-step">
        <p className="fleet-channel-expand-hint">
          Enter the code Telegram sent to {view?.state?.login_hint || "your phone"}.
        </p>
        <label className="gw-pair-panel-field">
          <span>Code</span>
          <input type="text" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => { setCode(e.currentTarget.value); setError(null); }} />
        </label>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitCode} disabled={busy}>
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {busy ? "Verifying…" : "Verify code"}
        </button>
        {error && <p className="fleet-channel-expand-error">{error}</p>}
        <DisconnectControl channelKey="telegram_personal" gatewayId={gatewayId} onDone={onRefresh} />
      </div>
    );
  }

  if (status === "password_required") {
    return (
      <div className="pc-connect-step">
        <p className="fleet-channel-expand-hint">This account has two-factor authentication enabled.</p>
        <label className="gw-pair-panel-field">
          <span>Password</span>
          <input type="password" autoComplete="off" value={password} onChange={(e) => { setPassword(e.currentTarget.value); setError(null); }} />
        </label>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitPassword} disabled={busy}>
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {busy ? "Verifying…" : "Verify password"}
        </button>
        {error && <p className="fleet-channel-expand-error">{error}</p>}
        <DisconnectControl channelKey="telegram_personal" gatewayId={gatewayId} onDone={onRefresh} />
      </div>
    );
  }

  if (status === "connecting") {
    return (
      <div className="pc-connect-step pc-connect-step--waiting">
        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        <span>Connecting…</span>
      </div>
    );
  }

  // idle / disconnected / logged_out / authorization_required / disabled
  return (
    <div className="pc-connect-step">
      <p className="fleet-channel-expand-hint">Enter your Telegram phone number, including the country code.</p>
      <label className="gw-pair-panel-field">
        <span>Phone number</span>
        <input type="tel" autoComplete="tel" placeholder="+1 555 0100" value={phoneNumber} onChange={(e) => { setPhoneNumber(e.currentTarget.value); setError(null); }} />
      </label>
      <button type="button" className="fleet-btn fleet-btn--accent" onClick={submitPhone} disabled={busy}>
        {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
        {busy ? "Sending…" : "Send code"}
      </button>
      {error && <p className="fleet-channel-expand-error">{error}</p>}
    </div>
  );
}

function WhatsAppConnectBody({
  label,
  gatewayId,
  status,
  view,
  onRefresh,
}: {
  label: string;
  gatewayId: string;
  status: string;
  view: ReturnType<typeof usePersonalChannelStatus>["view"];
  onRefresh: () => void;
}) {
  const [usePhone, setUsePhone] = useState(false);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const autoStartedFor = useRef<string | null>(null);

  const qrCode = view?.state?.qr_code || null;
  const pairingCode = view?.state?.metadata?.pairing_code || null;
  const isRestIdle = status === "idle" || status === "disconnected" || status === "logged_out" || status === "authorization_required";

  const beginOrRetry = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await setupWhatsAppPersonalChannel(gatewayId, {});
      onRefresh();
    } catch (e) {
      setError(friendlyPersonalChannelError(e instanceof Error ? e.message : String(e), label));
    } finally {
      setBusy(false);
    }
  }, [gatewayId, label, onRefresh]);

  // The gap this closes: after disconnect(), the runtime sits in idle
  // forever -- previously a QR only ever appeared because the whole Gateway
  // process happened to auto-attempt a connection on its own boot. Fire the
  // same begin/retry call the "Generate QR code" button below uses, once per
  // idle state entered (not once per poll), so opening the wizard (including
  // right after disconnecting) reaches a QR without a restart.
  useEffect(() => {
    if (!isRestIdle || usePhone || qrCode || pairingCode) return;
    if (autoStartedFor.current === gatewayId) return;
    autoStartedFor.current = gatewayId;
    void beginOrRetry();
  }, [isRestIdle, usePhone, qrCode, pairingCode, gatewayId, beginOrRetry]);

  useEffect(() => {
    if (!isRestIdle) autoStartedFor.current = null;
  }, [isRestIdle]);

  useEffect(() => {
    if (!qrCode) {
      setQrDataUrl(null);
      return;
    }
    let cancelled = false;
    void loadQrcode().then((QRCode) =>
      QRCode.toDataURL(qrCode, { margin: 1, width: 220 }).then((url) => {
        if (!cancelled) setQrDataUrl(url);
      }),
    );
    return () => {
      cancelled = true;
    };
  }, [qrCode]);

  const startWithPhone = useCallback(async () => {
    if (!phoneNumber.trim()) {
      setError("Enter a phone number, including the country code.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setupWhatsAppPersonalChannel(gatewayId, { phone_number: phoneNumber.trim() });
      onRefresh();
    } catch (e) {
      setError(friendlyPersonalChannelError(e instanceof Error ? e.message : String(e), label));
    } finally {
      setBusy(false);
    }
  }, [gatewayId, phoneNumber, label, onRefresh]);

  if (status === "connected") {
    return (
      <div className="pc-connect-connected">
        <div className="fleet-channel-expand-success">
          <Check size={16} strokeWidth={2} /> Connected as {maskedIdentity(view?.state)}
        </div>
        <DisconnectControl channelKey="whatsapp_personal" gatewayId={gatewayId} onDone={onRefresh} />
      </div>
    );
  }

  if (status === "pairing_code_required" && view?.state?.metadata?.pairing_code) {
    return (
      <div className="pc-connect-step">
        <p className="fleet-channel-expand-hint">Open WhatsApp on your phone → Linked Devices → Link with phone number, then enter:</p>
        <div className="pc-connect-pairing-code">{view.state.metadata.pairing_code}</div>
        <DisconnectControl channelKey="whatsapp_personal" gatewayId={gatewayId} onDone={onRefresh} />
      </div>
    );
  }

  if (status === "connecting") {
    return (
      <div className="pc-connect-step pc-connect-step--waiting">
        <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        <span>Connecting…</span>
      </div>
    );
  }

  if (usePhone) {
    return (
      <div className="pc-connect-step">
        <p className="fleet-channel-expand-hint">Enter your WhatsApp phone number, including the country code.</p>
        <label className="gw-pair-panel-field">
          <span>Phone number</span>
          <input type="tel" autoComplete="tel" placeholder="+1 555 0100" value={phoneNumber} onChange={(e) => { setPhoneNumber(e.currentTarget.value); setError(null); }} />
        </label>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={startWithPhone} disabled={busy}>
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {busy ? "Requesting…" : "Get pairing code"}
        </button>
        {error && <p className="fleet-channel-expand-error">{error}</p>}
        <button type="button" className="pc-connect-alt-link" onClick={() => { setUsePhone(false); setError(null); }}>
          Use QR code instead
        </button>
      </div>
    );
  }

  // idle / disconnected / logged_out / authorization_required, or qr_required
  // once a QR has actually been issued.
  return (
    <div className="pc-connect-step">
      <p className="fleet-channel-expand-hint">Open WhatsApp on your phone → Linked Devices → Link a device, then scan:</p>
      {qrDataUrl ? (
        <img className="pc-connect-qr" src={qrDataUrl} alt="WhatsApp pairing QR code" width={220} height={220} />
      ) : (
        <div className="pc-connect-qr pc-connect-qr--pending">
          <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} />
        </div>
      )}
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      {isRestIdle && !qrCode && (
        <button type="button" className="fleet-btn" onClick={() => void beginOrRetry()} disabled={busy}>
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {busy ? "Requesting…" : error ? "Try again" : "Generate QR code"}
        </button>
      )}
      <button type="button" className="pc-connect-alt-link" onClick={() => { setUsePhone(true); setError(null); }}>
        Use phone number instead
      </button>
    </div>
  );
}
