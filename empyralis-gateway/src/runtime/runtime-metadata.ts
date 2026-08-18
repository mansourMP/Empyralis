import os from "os";

import {
  agentComputerDesktopSession,
  agentComputerSystemServiceModeEnabled,
  type AgentComputerDesktopSession,
} from "./service-mode";

export interface GatewayNativeRuntimeMetadata {
  os: NodeJS.Platform;
  arch: string;
  release: string;
  hostname: string;
  desktop_session: AgentComputerDesktopSession;
  system_service_mode: boolean;
}

export interface GatewayRuntimeMetadata {
  gatewayVersion: string;
  /** Content-derived identity of the build actually running — see
   *  update/gateway-build-fingerprint.ts for why `gatewayVersion` alone
   *  cannot answer "which build is this" (it has been the literal string
   *  "0.1.0" on every box since the constant was introduced, and the release
   *  channel publishes under "latest" rather than a number).
   *
   *  Null when it could not be computed. That is a degradation and never an
   *  error: a box that cannot fingerprint itself must still connect and serve
   *  every capability it has. The backend treats null as "unknown build",
   *  which is what makes it refuse to advertise an unobservable update rather
   *  than guess. */
  buildFingerprint: string | null;
  hostname: string;
  platform: string;
  pid: number;
  startedAt: string;
  requestedCapabilities: string[];
  nativeRuntime: GatewayNativeRuntimeMetadata;
  deviceMetadata: Record<string, unknown>;
}

export function buildRuntimeMetadata(
  gatewayVersion: string,
  requestedCapabilities: string[] = [],
  buildFingerprint: string | null = null,
): GatewayRuntimeMetadata {
  const nativeRuntime: GatewayNativeRuntimeMetadata = {
    os: process.platform,
    arch: process.arch,
    release: os.release(),
    hostname: os.hostname(),
    desktop_session: agentComputerDesktopSession(),
    system_service_mode: agentComputerSystemServiceModeEnabled(),
  };
  return {
    gatewayVersion,
    buildFingerprint,
    hostname: nativeRuntime.hostname,
    platform: `${process.platform}-${process.arch}`,
    pid: process.pid,
    startedAt: new Date().toISOString(),
    requestedCapabilities,
    nativeRuntime,
    deviceMetadata: {
      hostname: nativeRuntime.hostname,
      platform: process.platform,
      arch: process.arch,
      release: nativeRuntime.release,
      native_runtime: nativeRuntime,
    },
  };
}
