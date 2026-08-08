/**
 * The only place in Empyralis that executes the `openclaw` binary.
 *
 * Three rules, all enforced here rather than remembered at each call site:
 *
 *   --profile ALWAYS. Every invocation is scoped to `~/.openclaw-<profile>`.
 *     An `openclaw` command with no profile operates on `~/.openclaw`, the
 *     operator's own real instance. One isolated instance per customer is not
 *     a preference — it is the entire reason adopting their gateway is safe
 *     for a multi-tenant product, given their own documented trust model
 *     ("not a hostile multi-tenant security boundary… one trusted operator
 *     boundary per gateway"). A missing/blank profile throws.
 *
 *   SANITIZED ENV. The child never inherits a model provider credential
 *     (../provisioning/openclaw-config-plan.ts's sanitizeOpenClawChildEnv).
 *     The deliberate "brain off" hack depends on every agent turn failing
 *     fast; one inherited ANTHROPIC_API_KEY silently turns a transport into a
 *     second, unmanaged agent answering the customer's contacts.
 *
 *   BOUNDED. Every call has a timeout and a stdout cap, so a wedged CLI
 *     cannot hang a capability invocation forever — the same failure the
 *     gateway supervisor installer had to guard against (update/gateway-
 *     supervisor-install.ts's withTimeout).
 *
 * Secrets are never passed as argv (argv is world-readable in `ps`): the
 * gateway token reaches OpenClaw through the config file it writes, and the
 * audit's `--token` is only ever used for a deep probe we do not run.
 */

import { execFile } from "child_process";
import os from "os";
import path from "path";

import { sanitizeOpenClawChildEnv } from "./openclaw-config-plan";

const DEFAULT_TIMEOUT_MS = 60_000;
/** `config schema` alone is ~2.5MB, so this cannot be small. */
const MAX_BUFFER_BYTES = 16 * 1024 * 1024;

export interface OpenClawCliResult {
  code: number;
  stdout: string;
  stderr: string;
}

export type OpenClawCliRunner = (args: string[], options?: { timeoutMs?: number }) => Promise<OpenClawCliResult>;

export interface OpenClawCliOptions {
  profile: string;
  /** Path to the `openclaw` binary. Defaults to resolving it on PATH. */
  binaryPath?: string;
  env?: NodeJS.ProcessEnv;
  /** Test seam — replaces the real child_process call entirely. */
  exec?: OpenClawCliRunner;
}

const PROFILE_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** Profiles become a directory name (`~/.openclaw-<profile>`) and a CLI
 *  argument. Validated, never sanitized into something "close enough". */
export function assertValidOpenClawProfile(profile: string): string {
  const normalized = String(profile || "").trim();
  if (!PROFILE_PATTERN.test(normalized)) {
    throw new Error(
      `Invalid OpenClaw profile "${profile}". A profile must match ${PROFILE_PATTERN} — it names a state directory ` +
        "and must never be able to escape it or collide with the operator's own ~/.openclaw.",
    );
  }
  if (normalized === "dev") {
    // `--profile dev` and `--dev` are not the same directory, but the name is
    // close enough to the operator's real dev profile to be a footgun.
    throw new Error('The OpenClaw profile "dev" is reserved; choose a customer-specific name.');
  }
  return normalized;
}

export function openClawProfileStateDir(profile: string, homeDir: string = os.homedir()): string {
  return path.join(homeDir, `.openclaw-${assertValidOpenClawProfile(profile)}`);
}

export class OpenClawCli {
  readonly profile: string;
  private readonly binaryPath: string;
  private readonly env: NodeJS.ProcessEnv;
  private readonly exec: OpenClawCliRunner;

  constructor(options: OpenClawCliOptions) {
    this.profile = assertValidOpenClawProfile(options.profile);
    this.binaryPath = String(options.binaryPath || "").trim() || "openclaw";
    this.env = sanitizeOpenClawChildEnv(options.env ?? process.env);
    this.exec = options.exec ?? this.defaultExec.bind(this);
  }

  /** The environment a supervised OpenClaw process must be launched into —
   *  exposed so the supervisor unit writer uses the SAME sanitized set the
   *  CLI does, rather than a second, drifting idea of what is safe. */
  childEnv(): NodeJS.ProcessEnv {
    return { ...this.env };
  }

  private defaultExec(args: string[], options?: { timeoutMs?: number }): Promise<OpenClawCliResult> {
    return new Promise((resolve) => {
      execFile(
        this.binaryPath,
        args,
        {
          env: this.env,
          timeout: options?.timeoutMs ?? DEFAULT_TIMEOUT_MS,
          maxBuffer: MAX_BUFFER_BYTES,
          encoding: "utf8" as const,
        },
        (error, stdout, stderr) => {
          if (error && error.code === "ENOENT") {
            // Not installed at all — distinguished from "wrong version" so
            // openclaw-version.ts can say the right thing.
            resolve({ code: 127, stdout: "", stderr: `openclaw binary not found at "${this.binaryPath}"` });
            return;
          }
          const code = typeof error?.code === "number" ? error.code : error ? 1 : 0;
          resolve({ code, stdout: String(stdout ?? ""), stderr: String(stderr ?? "") });
        },
      );
    });
  }

  /** Every subcommand goes through here, so `--profile` cannot be forgotten. */
  run(args: string[], options?: { timeoutMs?: number }): Promise<OpenClawCliResult> {
    return this.exec(["--profile", this.profile, ...args], options);
  }

  /** `undefined` when the binary is not installed at all — the caller
   *  distinguishes "not installed" from "wrong version" (openclaw-version.ts). */
  async version(): Promise<string | undefined> {
    const result = await this.run(["--version"], { timeoutMs: 20_000 });
    if (result.code === 127) return undefined;
    const output = `${result.stdout}\n${result.stderr}`.trim();
    return output.length > 0 ? output : undefined;
  }

  async configSchema(): Promise<unknown> {
    const result = await this.run(["config", "schema"], { timeoutMs: 60_000 });
    try {
      return JSON.parse(result.stdout);
    } catch {
      return undefined;
    }
  }

  /** One validated write. Objects merge recursively, arrays/scalars replace,
   *  null deletes — their own documented semantics. */
  configPatch(filePath: string, options?: { dryRun?: boolean }): Promise<OpenClawCliResult> {
    const args = ["config", "patch", "--file", filePath];
    if (options?.dryRun) args.push("--dry-run");
    return this.run(args, { timeoutMs: 60_000 });
  }

  /** The EFFECTIVE config as OpenClaw itself resolves it — not the file we
   *  wrote. Read-back verification is the only thing that proves the lockdown
   *  is in force rather than merely requested. */
  async effectiveConfig(): Promise<unknown> {
    const result = await this.run(["config", "get", "--json"], { timeoutMs: 30_000 });
    try {
      return JSON.parse(result.stdout);
    } catch {
      return undefined;
    }
  }

  async securityAuditJson(): Promise<{ result: OpenClawCliResult; report: unknown }> {
    const result = await this.run(["security", "audit", "--json"], { timeoutMs: 120_000 });
    let report: unknown;
    try {
      report = JSON.parse(result.stdout);
    } catch {
      report = undefined;
    }
    return { result, report };
  }
}
