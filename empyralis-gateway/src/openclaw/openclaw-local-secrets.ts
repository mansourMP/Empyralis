/**
 * The two loopback secrets an OpenClaw transport box needs, generated on the
 * box when nothing supplied them.
 *
 * THE BUG THIS CLOSES
 * -------------------
 * index.ts constructs the inbound listener, the outbound WS client, the
 * personal-channel runtimes AND `OpenClawProvisioningRuntime` only when BOTH
 * `EMPYRALIS_BRIDGE_TOKEN` and `EMPYRALIS_OPENCLAW_GATEWAY_TOKEN` are set.
 * Nothing ever set them. scripts/install-agent-computer.sh writes a
 * twenty-line env file and neither name appears in it; scripts/
 * agent_computer.sh likewise. So on every box this product has ever
 * provisioned, `openclaw.provision` was not advertised, the cloud could not
 * dispatch it, and the entire channel transport was unreachable — not broken,
 * not erroring, simply never constructed. CLAUDE.md's "built, tested, and
 * never wired", one level up from the code.
 *
 * WHY GENERATING THEM HERE IS CORRECT, NOT A SHORTCUT
 * --------------------------------------------------
 * Neither value is a credential anyone issues us. Both ends of both secrets
 * are written by Empyralis, on one machine, for traffic that never leaves it:
 *
 *   openclawBridgeToken   the bridge plugin (running INSIDE OpenClaw, on this
 *                         box) authenticating to this gateway's loopback
 *                         intake. We write both the plugin's environment (the
 *                         supervised unit) and the listener.
 *   openclawGatewayToken  this gateway authenticating to OpenClaw's own
 *                         loopback gateway. We write both our WS client and
 *                         `gateway.auth.token` in the config provisioning
 *                         generates.
 *
 * A value that both sides get from us has no business being an operator's
 * job. Generating it here also means it reaches boxes that were installed
 * before any of this existed: they never re-run an installer, but they do
 * take gateway self-updates, so the next restart of an old box wires its
 * transport up with no human anywhere. An installer-only fix could not do
 * that, and would have left a silent two-tier fleet.
 *
 * ENV STILL WINS. An explicitly configured value is used verbatim and nothing
 * is generated for it — operators pinning a token (and
 * scripts/install-agent-computer.sh, which needs both values at root time to
 * render the OpenClaw systemd unit) must keep control.
 */

import { randomBytes } from "crypto";
import { promises as fsp } from "fs";
import path from "path";

export interface OpenClawLocalSecrets {
  bridgeToken: string;
  gatewayToken: string;
  /** Which of the two were minted by this call rather than read from the
   *  environment or from disk. Names only — the values are never journaled. */
  generated: Array<"bridge_token" | "gateway_token">;
}

interface StoredSecrets {
  bridgeToken?: string;
  gatewayToken?: string;
}

/** Beside the provisioning record, on the Empyralis side of the box. Mode
 *  0600: it is a secret at rest, even though it only ever authenticates
 *  loopback. */
export function openClawLocalSecretsPath(stateDir: string): string {
  return path.join(stateDir, "openclaw", "local-secrets.json");
}

function mint(): string {
  // 32 bytes, hex. Wide enough that a loopback brute force is not a thing
  // anyone has to reason about, and printable so it survives an env file, a
  // plist and a systemd `Environment=` line unescaped.
  return randomBytes(32).toString("hex");
}

export interface ResolveOpenClawLocalSecretsOptions {
  stateDir: string;
  /** From config.ts — already trimmed, already `undefined` when blank. */
  envBridgeToken?: string;
  envGatewayToken?: string;
  /** Injectable for tests; production uses the real fs. */
  fs?: {
    readFile: (filePath: string) => Promise<string>;
    writeFile: (filePath: string, contents: string) => Promise<void>;
    mkdir: (dirPath: string) => Promise<void>;
  };
}

/**
 * Resolves both secrets, minting and persisting whatever is missing.
 *
 * Order per secret: environment -> the persisted file -> mint. Persisted
 * before it is returned, so the value survives a restart — a gateway that
 * minted a fresh token every boot would invalidate OpenClaw's stored
 * `gateway.auth.token` on every restart and present as a transport that works
 * until the box reboots.
 *
 * Never throws. A state directory this process cannot write (a read-only
 * volume, a wrong owner) still yields usable in-memory secrets for this
 * process's lifetime; the persistence failure is reported through the return
 * value's `generated` list staying non-empty across restarts rather than by
 * taking the gateway down. Channels degrade; nothing else does.
 */
export async function resolveOpenClawLocalSecrets(
  options: ResolveOpenClawLocalSecretsOptions,
): Promise<OpenClawLocalSecrets> {
  const io = options.fs ?? {
    readFile: (filePath: string) => fsp.readFile(filePath, "utf8"),
    writeFile: async (filePath: string, contents: string) => {
      await fsp.writeFile(filePath, contents, { mode: 0o600 });
    },
    mkdir: async (dirPath: string) => {
      await fsp.mkdir(dirPath, { recursive: true, mode: 0o700 });
    },
  };
  const filePath = openClawLocalSecretsPath(options.stateDir);

  let stored: StoredSecrets = {};
  try {
    const parsed = JSON.parse(await io.readFile(filePath)) as StoredSecrets;
    if (parsed && typeof parsed === "object") stored = parsed;
  } catch {
    // Absent or unreadable is simply "nothing stored yet".
  }

  const generated: OpenClawLocalSecrets["generated"] = [];
  const resolveOne = (
    fromEnv: string | undefined,
    fromDisk: string | undefined,
    name: "bridge_token" | "gateway_token",
  ): string => {
    const env = String(fromEnv || "").trim();
    if (env) return env;
    const disk = String(fromDisk || "").trim();
    if (disk) return disk;
    generated.push(name);
    return mint();
  };

  const bridgeToken = resolveOne(options.envBridgeToken, stored.bridgeToken, "bridge_token");
  const gatewayToken = resolveOne(options.envGatewayToken, stored.gatewayToken, "gateway_token");

  // Persist whatever is not env-supplied, so the next boot reads it back
  // instead of minting again. An env-supplied value is deliberately NOT
  // written to disk: it belongs to whoever set it, and copying it here would
  // make a later change to the env file silently ineffective.
  const toPersist: StoredSecrets = {};
  if (!String(options.envBridgeToken || "").trim()) toPersist.bridgeToken = bridgeToken;
  if (!String(options.envGatewayToken || "").trim()) toPersist.gatewayToken = gatewayToken;
  if (Object.keys(toPersist).length > 0 && JSON.stringify(toPersist) !== JSON.stringify(stored)) {
    try {
      await io.mkdir(path.dirname(filePath));
      await io.writeFile(filePath, JSON.stringify(toPersist, null, 2));
    } catch {
      // See the doc comment: an unwritable state dir degrades channels for
      // this process's lifetime, it does not take the gateway down.
    }
  }

  return { bridgeToken, gatewayToken, generated };
}
