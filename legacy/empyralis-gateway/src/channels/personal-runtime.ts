import type {
  GatewayChannelInboundPayload,
  GatewayChannelOutboundPayload,
  GatewayRequestEnvelope,
  GatewayScope,
  GatewayToolInvokePayload,
} from "../protocol/types";

export interface PersonalChannelGatewayPublisher {
  publishEvent: (type: "channel.inbound", payload: GatewayChannelInboundPayload) => Promise<void>;
  publishStateUpdate: (payload: Record<string, unknown>) => Promise<void>;
}

export type PersonalChannelStage = "live" | "next" | "later" | "reserved";
export type PersonalChannelStatus =
  | "live"
  | "not_configured"
  | "connecting"
  | "connected"
  | "disconnected"
  | "unavailable"
  | "reserved";

export interface PersonalChannelCapabilityManifest {
  channelKey: string;
  label: string;
  provider: string;
  runtimeLane: "personal_gateway";
  stage: PersonalChannelStage;
  status: PersonalChannelStatus;
  liveCapable: boolean;
  requiresAgentComputer: true;
  sessionOwner: "paired_gateway";
  setupKind: "phone_login" | "qr_pairing" | "local_bridge" | "mac_bridge";
  capabilities: string[];
  chatTypes: string[];
  media: {
    text: boolean;
    images?: boolean;
    files?: boolean;
    reactions?: boolean;
    voice?: boolean;
  };
  safety: {
    ownerPairingRequired: boolean;
    allowlistRequired: boolean;
    studioBusinessAllowed: false;
    customerPublicSendAllowed: false;
  };
  notes?: string[];
}

export interface PersonalChannelHealthSnapshot {
  channelKey: string;
  provider: string;
  status: PersonalChannelStatus | string;
  running: boolean;
  connected: boolean;
  reconnectAttempts?: number;
  lastEventAt?: string;
  lastError?: string;
  issues?: string[];
}

export interface PersonalChannelRuntime {
  requestedCapabilities: () => string[];
  supportsCapability: (capabilityId: string) => boolean;
  handleCapabilityInvoke: (
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ) => Promise<Record<string, unknown>>;
  supportsChannel: (channelKey: string) => boolean;
  handleChannelOutbound: (
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
  ) => Promise<Record<string, unknown>>;
  handleGatewayConnected: (scope: GatewayScope) => Promise<void>;
  handleGatewayDisconnected: (reason: string) => Promise<void>;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  setPublisher?: (publisher: PersonalChannelGatewayPublisher) => void;
  getManifest?: () => PersonalChannelCapabilityManifest;
  getHealthSnapshot?: () => Promise<PersonalChannelHealthSnapshot> | PersonalChannelHealthSnapshot;
}

export function personalChannelManifestToStatePayload(
  manifest: PersonalChannelCapabilityManifest,
): Record<string, unknown> {
  return {
    channel_key: manifest.channelKey,
    label: manifest.label,
    provider: manifest.provider,
    runtime_lane: manifest.runtimeLane,
    stage: manifest.stage,
    status: manifest.status,
    live_capable: manifest.liveCapable,
    requires_agent_computer: manifest.requiresAgentComputer,
    session_owner: manifest.sessionOwner,
    setup_kind: manifest.setupKind,
    capabilities: manifest.capabilities,
    chat_types: manifest.chatTypes,
    media: manifest.media,
    safety: {
      owner_pairing_required: manifest.safety.ownerPairingRequired,
      allowlist_required: manifest.safety.allowlistRequired,
      studio_business_allowed: manifest.safety.studioBusinessAllowed,
      customer_public_send_allowed: manifest.safety.customerPublicSendAllowed,
    },
    notes: manifest.notes ?? [],
  };
}

export function personalChannelHealthToStatePayload(
  snapshot: PersonalChannelHealthSnapshot,
): Record<string, unknown> {
  return {
    channel_key: snapshot.channelKey,
    provider: snapshot.provider,
    status: snapshot.status,
    running: snapshot.running,
    connected: snapshot.connected,
    reconnect_attempts: snapshot.reconnectAttempts ?? 0,
    last_event_at: snapshot.lastEventAt,
    last_error: snapshot.lastError,
    issues: snapshot.issues ?? [],
  };
}

export class PersonalChannelRuntimeRegistry {
  private readonly runtimes: PersonalChannelRuntime[];
  private publisher?: PersonalChannelGatewayPublisher;

  constructor(runtimes: PersonalChannelRuntime[] = []) {
    this.runtimes = [...runtimes];
  }

  all(): PersonalChannelRuntime[] {
    return [...this.runtimes];
  }

  requestedCapabilities(): string[] {
    return this.runtimes.flatMap((runtime) =>
      runtime.requestedCapabilities().filter((capability) => runtime.supportsCapability(capability)),
    );
  }

  runtimeForCapability(capabilityId: string): PersonalChannelRuntime | undefined {
    return this.runtimes.find((runtime) => runtime.supportsCapability(capabilityId));
  }

  runtimeForChannel(channelKey: string): PersonalChannelRuntime | undefined {
    return this.runtimes.find((runtime) => runtime.supportsChannel(channelKey));
  }

  channelManifests(): PersonalChannelCapabilityManifest[] {
    return this.runtimes.flatMap((runtime) => {
      const manifest = runtime.getManifest?.();
      return manifest ? [manifest] : [];
    });
  }

  async healthSnapshots(): Promise<PersonalChannelHealthSnapshot[]> {
    const snapshots: PersonalChannelHealthSnapshot[] = [];
    for (const runtime of this.runtimes) {
      const snapshot = await runtime.getHealthSnapshot?.();
      if (snapshot) {
        snapshots.push(snapshot);
      }
    }
    return snapshots;
  }

  setPublisher(publisher: PersonalChannelGatewayPublisher): void {
    this.publisher = publisher;
    for (const runtime of this.runtimes) {
      runtime.setPublisher?.(publisher);
    }
  }

  async startAll(): Promise<void> {
    for (const runtime of this.runtimes) {
      await runtime.start();
    }
  }

  async stopAll(): Promise<void> {
    for (const runtime of [...this.runtimes].reverse()) {
      await runtime.stop();
    }
  }

  async handleGatewayConnected(scope: GatewayScope): Promise<void> {
    await this.publishRegistryState().catch(() => undefined);
    for (const runtime of this.runtimes) {
      await runtime.handleGatewayConnected(scope);
    }
    await this.publishRegistryState().catch(() => undefined);
  }

  async handleGatewayDisconnected(reason: string): Promise<void> {
    for (const runtime of this.runtimes) {
      await runtime.handleGatewayDisconnected(reason);
    }
    await this.publishRegistryState().catch(() => undefined);
  }

  private async publishRegistryState(): Promise<void> {
    if (!this.publisher) {
      return;
    }
    const manifests = this.channelManifests().map(personalChannelManifestToStatePayload);
    const health = (await this.healthSnapshots()).map(personalChannelHealthToStatePayload);
    await this.publisher.publishStateUpdate({
      personal_channel_manifests: manifests,
      personal_channel_health: health,
    });
  }
}
