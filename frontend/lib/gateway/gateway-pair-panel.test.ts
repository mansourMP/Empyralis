/**
 * pairing-command.ts unit tests — the "connect your own computer" command,
 * per platform.
 *
 * Two live bugs are pinned here.
 *
 * 1. A dead link (2026-08-14): `https://get.empyralis.com/gateway` does not
 *    resolve at all — no such domain was ever provisioned — so every customer
 *    who copied the pairing command got nothing. The working installer is
 *    served from empyralis.ai, the exact same prebuilt-artifact script the
 *    DigitalOcean cloud-init path and the "connect via SSH" path already use.
 *
 * 2. A platform picker that decided nothing (2026-08-15): Settings ->
 *    Connections defaults its Platform picker to macOS, and the command it
 *    emitted was the Ubuntu-only one for every selection. So the DEFAULT
 *    choice handed a Mac customer `curl … | sudo -E bash` against a script
 *    whose second real statement is an /etc/os-release check that refuses
 *    anything but Ubuntu — and changing the picker changed nothing at all.
 *
 * The assertions below are written per platform rather than over one blob,
 * because "every platform gets a command" was exactly the passing state of
 * bug 2 — a test that only checks the command is well-formed cannot see that
 * it is well-formed for the wrong operating system.
 *
 * Run: npx tsx lib/gateway/gateway-pair-panel.test.ts
 */

import {
  normalizePairingPlatform,
  pairingPlatformSupported,
  pairingSetup,
  pairingUnsupportedReason,
  type PairingPlatformId,
} from "./pairing-command";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function commandFor(platform: PairingPlatformId, fullAccess = false): string {
  const setup = pairingSetup("gpair_test_token", "My Mac", "ws_test", fullAccess, platform);
  if (setup.kind !== "command") {
    throw new Error(`expected a command for ${platform}, got ${setup.kind}`);
  }
  return setup.command;
}

// ── Every platform that HAS a path emits a real, well-formed command ────────

for (const platform of ["macos", "linux"] as const) {
  for (const [label, cmd] of [
    [`${platform} default (sandbox)`, commandFor(platform, false)],
    [`${platform} full access`, commandFor(platform, true)],
  ] as const) {
    assert(!cmd.includes("get.empyralis.com"), `${label}: never references the dead get.empyralis.com domain`);
    assert(
      /^curl -fsSL https:\/\/empyralis\.ai\/install\/[a-z0-9.-]+\.sh \| /m.test(cmd),
      `${label}: fetches an installer from the real, live host`,
    );
    assert(
      !/\|\s*sh\b/.test(cmd),
      `${label}: never pipes into a bare "sh" (both installers open with bash's -o pipefail)`,
    );
    assert(
      cmd.includes(`export EMPYRALIS_GATEWAY_PAIRING_TOKEN="gpair_test_token"`),
      `${label}: exports the pairing token`,
    );
    assert(
      cmd.includes(`export EMPYRALIS_GATEWAY_DISPLAY_NAME="My Mac"`),
      `${label}: exports the display name`,
    );
    assert(
      cmd.includes(`export EMPYRALIS_WORKSPACE_ID="ws_test"`),
      `${label}: exports the workspace id`,
    );
  }
}

// ── The picker actually decides — each platform gets ITS OWN installer ──────
//
// This is the assertion bug 2 could not have passed. Both directions are
// checked: macOS must not be handed the Ubuntu script, and Linux must not be
// handed the macOS one.

const macCommand = commandFor("macos");
const linuxCommand = commandFor("linux");

assert(
  macCommand.includes("https://empyralis.ai/install/agent-computer-macos.sh"),
  "macOS: fetches the macOS installer",
);
assert(
  !macCommand.includes("/install/agent-computer.sh"),
  "macOS: is NOT handed the Ubuntu-only installer (its /etc/os-release check refuses a Mac outright)",
);
assert(
  linuxCommand.includes("https://empyralis.ai/install/agent-computer.sh"),
  "linux: fetches the Ubuntu installer",
);
assert(
  !linuxCommand.includes("agent-computer-macos.sh"),
  "linux: is NOT handed the macOS installer",
);
assert(macCommand !== linuxCommand, "macOS and Linux do not emit the same command");

// ── sudo belongs to exactly one of them ─────────────────────────────────────
//
// The Linux installer requires root and `-E` is what carries the export lines
// across the sudo boundary. The macOS installer refuses to run as root: a
// LaunchAgent belongs to the logged-in user, and under sudo $HOME resolves to
// /var/root, so a root-run install would land the plist, the state and the
// credentials somewhere that session never loads and then report success.

assert(
  /\|\s*sudo\s+-E\s+bash\s*$/.test(linuxCommand),
  "linux: pipes into `sudo -E bash` (root required; -E carries the exports across the sudo boundary)",
);
assert(
  /\|\s*bash\s*$/.test(macCommand) && !macCommand.includes("sudo"),
  "macOS: pipes into a bare `bash` and never asks for sudo",
);

// ── full_access only ever ADDS a line ──────────────────────────────────────

for (const platform of ["macos", "linux"] as const) {
  const plain = commandFor(platform, false);
  const full = commandFor(platform, true);
  assert(
    full.includes("export EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED=true"),
    `${platform} full access: adds the full-access export`,
  );
  assert(
    !plain.includes("EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED"),
    `${platform} default (sandbox): does not add the full-access export`,
  );
  assert(
    full.split("\n").filter((line) => !line.includes("FULL_ACCESS")).join("\n") === plain,
    `${platform}: full access changes no other line`,
  );
}

// ── A platform with no path never emits something runnable ─────────────────
//
// Windows has no installer anywhere in this repo, and the gateway's own
// supervisor module returns null for win32 — there is not even a service
// definition to install. The honest answer is a sentence, and specifically
// NOT a command: silently leaving it on the Linux one is what shipped.

const windows = pairingSetup("gpair_test_token", "My PC", "ws_test", false, "windows");
assert(windows.kind === "unsupported", "windows: reports itself unsupported rather than emitting a command");
assert(!pairingPlatformSupported("windows"), "windows: pairingPlatformSupported is false");
assert(pairingPlatformSupported("macos"), "macOS: pairingPlatformSupported is true");
assert(pairingPlatformSupported("linux"), "linux: pairingPlatformSupported is true");

const windowsText = windows.kind === "unsupported" ? windows.reason : "";
for (const forbidden of ["curl", "bash", "sudo", "sh ", "|"]) {
  assert(
    !windowsText.includes(forbidden),
    `windows: its message carries nothing runnable (found ${JSON.stringify(forbidden)})`,
  );
}
assert(windowsText.trim().length > 0, "windows: says something rather than rendering an empty box");
assert(
  pairingUnsupportedReason("windows") === windowsText,
  "windows: the standalone reason and the setup's reason are the same sentence",
);
assert(
  pairingUnsupportedReason("macos") === "" && pairingUnsupportedReason("linux") === "",
  "a supported platform has no unsupported-reason sentence to show",
);

// An unsupported platform stays unsupported even WITH a valid token — the
// missing thing is the installer, not the token, and reporting it as a token
// problem would send someone to retry a button that can never help.
const windowsBlankToken = pairingSetup("", "My PC", "ws_test", false, "windows");
assert(
  windowsBlankToken.kind === "unsupported",
  "windows: an empty token does not turn 'no installer exists' into 'token unavailable'",
);

// ── A missing token is its own third fact ──────────────────────────────────

for (const platform of ["macos", "linux"] as const) {
  const setup = pairingSetup("", "My Mac", "ws_test", false, platform);
  assert(
    setup.kind === "token_unavailable",
    `${platform}: an empty token reports token_unavailable, not a broken command`,
  );
}

// ── Platform normalization ─────────────────────────────────────────────────

assert(normalizePairingPlatform("windows") === "windows", "normalize: windows");
assert(normalizePairingPlatform("win32") === "windows", "normalize: win32 -> windows");
assert(normalizePairingPlatform("linux") === "linux", "normalize: linux");
assert(normalizePairingPlatform("macos") === "macos", "normalize: macos");
assert(normalizePairingPlatform("MacOS") === "macos", "normalize: case-insensitive");
assert(normalizePairingPlatform(undefined) === "macos", "normalize: absent defaults to macOS");
assert(
  normalizePairingPlatform("plan9") === "macos",
  "normalize: an unknown value falls back to a platform that HAS an installer",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
