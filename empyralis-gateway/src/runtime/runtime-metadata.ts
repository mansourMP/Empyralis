import os from "os";

import {
  agentComputerDesktopSession,
  agentComputerSystemServiceModeEnabled,
  type AgentComputerDesktopSession,
} from "./service-mode";

import type { GatewayLaunchRepairPlan } from "../update/gateway-launch-repair";
import type { GatewayLaunchUpdatability } from "../update/gateway-launch-updatability";

/** The classification plus the half of the repair the gateway was able to
 *  prepare for itself, carried together because a report of "stuck" without
 *  the fix beside it is a fact nobody can act on. */
export type GatewayLaunchUpdatabilityReport = GatewayLaunchUpdatability & {
  repair: GatewayLaunchRepairPlan | null;
};

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
  /** Whether a self-update on this box could ever take effect — a property
   *  of the supervisor unit that starts the NEXT process, not of this one.
   *  See update/gateway-launch-updatability.ts.
   *
   *  Null when this build predates the check or it could not be computed.
   *  The backend must treat null exactly like "unknown": keep today's
   *  behaviour. Only an explicit "not_updatable" ever takes an update
   *  away. */
  launchUpdatability: GatewayLaunchUpdatabilityReport | null;
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
  launchUpdatability: GatewayLaunchUpdatabilityReport | null = null,
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
    launchUpdatability,
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
