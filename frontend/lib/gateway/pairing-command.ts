/**
 * What a customer is actually told to run to connect their own computer —
 * per platform, as pure data and pure functions.
 *
 * Its own module, not a const inside GatewayPairPanel.tsx, for the same
 * reason channel-doors.ts and agent-count-shape.ts are: the test imports THE
 * REAL rule rather than a literal copied beside it, so the expected set and
 * the actual set can never be the same hand-written string.
 *
 * ── The bug this exists to close (found live 2026-08-15) ──────────────────
 * Settings -> Connections -> "add your own computer" has a Platform picker
 * whose DEFAULT is macOS, and the command it emitted ignored the picker
 * entirely: every platform got
 *
 *     curl -fsSL https://empyralis.ai/install/agent-computer.sh | sudo -E bash
 *
 * scripts/install-agent-computer.sh is Ubuntu-only — it opens with a
 * /etc/os-release check that refuses anything but Ubuntu 22.04/24.04, then
 * apt-get, a system service user and a unit in /etc/systemd/system. So the
 * DEFAULT selection handed a Mac customer a command that cannot succeed, and
 * changing the picker changed nothing.
 *
 * ── Three platforms, three different facts ───────────────────────────────
 *   macOS    a real installer: scripts/install-agent-computer-macos.sh.
 *            No sudo — it installs a LaunchAgent into the user's own account,
 *            which is why the macOS line is a bare `| bash`. Piping a script
 *            that opens with `set -Eeuo pipefail` into `sh` is the mistake
 *            this repo already paid for once, so it is `bash` on both.
 *   Linux    unchanged, byte for byte, including `sudo -E` (the installer
 *            requires root, and -E carries the export lines across the sudo
 *            boundary).
 *   Windows  no path exists, and this says so rather than emitting something.
 *            Not an oversight being hidden: the gateway's own supervisor
 *            module (gateway-supervisor-install.ts's
 *            resolveExpectedSupervisorUnit) returns null for win32 — there is
 *            no service definition to install, no installer, and nothing in
 *            the repo that has ever run there. CLAUDE.md's "no dead controls"
 *            law applies to the COMMAND, not to the option: the option stays
 *            visible so a Windows customer learns the answer instead of
 *            wondering, and it renders a sentence where a command would be,
 *            exactly like channel-doors.ts renders an inert door rather than
 *            a setup form that could only fail.
 */

export type PairingPlatformId = "macos" | "windows" | "linux";

/**
 * Three outcomes, never collapsed into a string. A caller has to be able to
 * tell "here is what to run" from "there is nothing to run on this platform"
 * from "we could not mint a token" — they are three different screens, and
 * this codebase has already been bitten several times by two of them sharing
 * one message.
 */
export type PairingSetup =
  | { kind: "command"; platform: PairingPlatformId; command: string }
  | { kind: "unsupported"; platform: PairingPlatformId; reason: string }
  | { kind: "token_unavailable" };

/** How each platform is installed. `installer: null` means there is no path,
 *  and the reason is the sentence a customer reads. */
const PAIRING_PLATFORMS: Record<
  PairingPlatformId,
  { installerUrl: string; runner: string } | { installerUrl: null; reason: string }
> = {
  macos: {
    // Served by frontend/app/install/agent-computer-macos.sh (which proxies
    // the backend's own on-disk copy), the same shape the Linux script is
    // served with.
    installerUrl: "https://empyralis.ai/install/agent-computer-macos.sh",
    // No sudo: a LaunchAgent belongs to the logged-in user, and a root-run
    // install would resolve $HOME to /var/root and load nothing into the
    // user's session.
    runner: "bash",
  },
  linux: {
    installerUrl: "https://empyralis.ai/install/agent-computer.sh",
    runner: "sudo -E bash",
  },
  windows: {
    installerUrl: null,
    reason:
      "Windows isn't supported yet — an Agent Computer runs on macOS or Linux. " +
      "Use one of those, or add a cloud computer instead.",
  },
};

export function normalizePairingPlatform(raw: string | null | undefined): PairingPlatformId {
  const value = String(raw || "").trim().toLowerCase();
  if (value === "windows" || value === "win32" || value === "win") return "windows";
  if (value === "linux") return "linux";
  return "macos";
}

/** True only when this platform has an installer that can actually run. */
export function pairingPlatformSupported(platform: PairingPlatformId): boolean {
  return PAIRING_PLATFORMS[platform].installerUrl !== null;
}

/** The sentence shown where a command would be, for a platform with no path.
 *  Empty for a supported platform — there is nothing to explain. */
export function pairingUnsupportedReason(platform: PairingPlatformId): string {
  const spec = PAIRING_PLATFORMS[platform];
  return spec.installerUrl === null ? spec.reason : "";
}

/**
 * fullAccess only ever adds a line — never changes any line above it — so a
 * default (sandbox) pairing's command is byte-for-byte what it always was.
 * The exported var is the box operator's LOCAL half of the full_access opt-in
 * (see empyralis-gateway/src/config.ts's shellFullAccessLocallyEnabled doc
 * comment); the pairing intent request carries the other, server half, and
 * both are required before any call actually runs unsandboxed.
 */
export function pairingSetup(
  token: string,
  displayName: string,
  workspaceId: string,
  fullAccess: boolean,
  platform: PairingPlatformId,
): PairingSetup {
  const spec = PAIRING_PLATFORMS[platform];
  if (spec.installerUrl === null) {
    return { kind: "unsupported", platform, reason: spec.reason };
  }
  if (!token) {
    return { kind: "token_unavailable" };
  }
  const name = displayName.trim() || "My device";
  const lines = [
    `export EMPYRALIS_GATEWAY_PAIRING_TOKEN=${JSON.stringify(token)}`,
    `export EMPYRALIS_GATEWAY_DISPLAY_NAME=${JSON.stringify(name)}`,
    `export EMPYRALIS_WORKSPACE_ID=${JSON.stringify(workspaceId)}`,
  ];
  if (fullAccess) {
    lines.push(`export EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED=true`);
  }
  lines.push(`curl -fsSL ${spec.installerUrl} | ${spec.runner}`);
  return { kind: "command", platform, command: lines.join("\n") };
}
