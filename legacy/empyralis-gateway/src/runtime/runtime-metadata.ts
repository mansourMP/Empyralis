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
