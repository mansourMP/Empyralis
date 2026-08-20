// Live model catalogs for the cli_subscription runtimes that publish one —
// each asked in ITS OWN native way, in ITS OWN output format. There is
// deliberately no unified protocol, no adapter and no shim here: the founder's
// instruction, 2026-08-20, is "we are going to make it work by how THEY
// provide the specific thing, not trying to make another thing for those
// providers... I just want to serve however they provide to me."
//
//   codex        `codex app-server` JSON-RPC `model/list`   → codex-app-server.ts
//                (structured, per-account, carries the reasoning-effort
//                 catalog too — the richest of the four, and the only one
//                 that is machine-readable by design)
//   cursor_cli   `cursor-agent models`                      → here
//                its own help text: "List available models for this account"
//   grok_build   `grok models`                              → here
//                its own help text: "List available models and exit"
//   claude_code  NOTHING                                    → not enumerable
//                `claude` ships as a compiled binary with no models
//                subcommand and no --list-models flag. Reported as
//                unsupported rather than filled in from documentation —
//                CLAUDE.md: "Verifying against DOCS is still transcription."
//
// Both commands here print HUMAN TEXT, not JSON (verified: `cursor-agent
// models --help` and `grok models --help` each expose only `-h`). So the
// parsing below is deliberately shape-tolerant and, more importantly,
// FAILS TO "UNSUPPORTED" RATHER THAN TO AN EMPTY LIST: a probe that returns
// zero models reports supported:false carrying the CLI's own sentence, so
// the customer is told what the CLI actually said instead of being shown an
// empty dropdown. An empty dropdown is a dead control; a relayed sentence is
// a fact they can act on.

import { execFileWithTimeout } from "../shell/exec-file-with-timeout";

const MODEL_LIST_TIMEOUT_MS = 20_000;

export interface CliModelListEntry {
  id: string;
  displayName: string;
  isDefault: boolean;
}

export interface CliModelListOutcome {
  supported: boolean;
  models: CliModelListEntry[];
  /** Present only when supported is false — the CLI's own words where it had
   *  any, so the reason a catalog is empty is never invented by us. */
  reason?: string;
}

export type CliModelListExec = (
  command: string,
  args: string[],
) => Promise<{ exitCode: number | null; stdout: string; stderr: string; error?: NodeJS.ErrnoException }>;

function defaultExec(command: string, args: string[]): ReturnType<CliModelListExec> {
  return execFileWithTimeout(command, args, MODEL_LIST_TIMEOUT_MS).then((r) => ({
    exitCode: r.exitCode,
    stdout: String(r.stdout || ""),
    stderr: String(r.stderr || ""),
    error: r.error,
  }));
}

/** First non-empty line of whatever the CLI said, so a failure is reported in
 *  the CLI's own words. Capped — this reaches a browser. */
function firstMeaningfulLine(...blobs: string[]): string {
  for (const blob of blobs) {
    for (const line of String(blob || "").split("\n")) {
      const trimmed = line.trim();
      if (trimmed) return trimmed.slice(0, 300);
    }
  }
  return "";
}

/** Strip a leading bullet/marker and any trailing "(default)"-style note,
 *  returning the bare id plus whether the note marked it default.
 *
 *  Derived from `grok models`' REAL output, observed live 2026-08-20 against
 *  a real signed-in grok 0.2.112:
 *
 *      You are logged in with grok.com.
 *
 *      Default model: grok-4.6
 *
 *      Available models:
 *        * grok-4.6 (default)
 *
 *  `cursor-agent models` could NOT be observed populated on that machine —
 *  its account answered "No models available for this account." — so the same
 *  tolerant shape is applied there and the zero-model case is reported as
 *  unsupported-with-a-reason rather than as an empty catalog. See this
 *  module's header. */
function parseModelLine(raw: string): { id: string; isDefault: boolean } | null {
  let line = raw.trim();
  if (!line) return null;
  line = line.replace(/^[-*•]\s+/, "").trim();
  let isDefault = false;
  const note = line.match(/\s*\(([^)]*)\)\s*$/);
  if (note) {
    isDefault = /default|current|active/i.test(note[1]);
    line = line.slice(0, note.index).trim();
  }
  // A model id is a single bare token. Anything with whitespace left in it is
  // prose ("You are logged in with grok.com."), not an id — dropping those is
  // what keeps a chatty preamble out of the picker.
  if (!line || /\s/.test(line)) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@/-]*$/.test(line)) return null;
  return { id: line, isDefault };
}

/** Shared across both text-printing CLIs because the SHAPE is shared (a
 *  preamble, an optional "Default model: <id>" line, then one id per line) —
 *  not because their protocols were normalised. Neither publishes a machine
 *  format; if either ever does, it gets its own parser, not a flag here. */
function parseTextModelList(stdout: string): CliModelListEntry[] {
  const lines = String(stdout || "").split("\n");
  let declaredDefault = "";
  const seen = new Set<string>();
  const models: CliModelListEntry[] = [];
  for (const line of lines) {
    const defaultMatch = line.match(/^\s*Default model:\s*(\S+)\s*$/i);
    if (defaultMatch) {
      declaredDefault = defaultMatch[1];
      continue;
    }
    // A heading ends in ':' and is not itself a model.
    if (/:\s*$/.test(line)) continue;
    const parsed = parseModelLine(line);
    if (!parsed || seen.has(parsed.id)) continue;
    seen.add(parsed.id);
    models.push({ id: parsed.id, displayName: parsed.id, isDefault: parsed.isDefault });
  }
  if (declaredDefault && !models.some((m) => m.isDefault)) {
    for (const m of models) {
      if (m.id === declaredDefault) m.isDefault = true;
    }
  }
  return models;
}

const NATIVE_MODEL_LIST_COMMAND: Record<string, { binaryEnvVar: string; defaultBinary: string; args: string[] }> = {
  // cursor-agent's own subcommand — `agent models`, "List available models
  // for this account" (its own --help, read live 2026-08-20 on 2026.06.04).
  cursor_cli: { binaryEnvVar: "CURSOR_CLI_PATH", defaultBinary: "cursor-agent", args: ["models"] },
  // grok's own subcommand — `grok models`, "List available models and exit"
  // (its own --help, read live 2026-08-20 on 0.2.112).
  grok_build: { binaryEnvVar: "GROK_CLI_PATH", defaultBinary: "grok", args: ["models"] },
};

/** Runtimes this module can ask. codex is absent on purpose — it has a real
 *  JSON-RPC catalog and is served by codex-app-server.ts, which is strictly
 *  better than parsing text. claude_code is absent because nothing to ask
 *  exists. */
export const TEXT_MODEL_LIST_RUNTIMES: ReadonlySet<string> = new Set(Object.keys(NATIVE_MODEL_LIST_COMMAND));

export async function listModelsViaCli(
  runtime: string,
  env: NodeJS.ProcessEnv = process.env,
  exec: CliModelListExec = defaultExec,
): Promise<CliModelListOutcome> {
  const spec = NATIVE_MODEL_LIST_COMMAND[String(runtime || "").trim().toLowerCase()];
  if (!spec) {
    return { supported: false, models: [], reason: `No live model catalog exists for runtime "${runtime}".` };
  }
  const command = String(env[spec.binaryEnvVar] || "").trim() || spec.defaultBinary;
  const result = await exec(command, spec.args);
  if (result.error) {
    const code = String((result.error as NodeJS.ErrnoException).code || "");
    return {
      supported: false,
      models: [],
      reason: code === "ENOENT"
        ? `${command} isn’t installed on this computer.`
        : `Couldn’t run ${command}: ${result.error.message}`,
    };
  }
  const models = parseTextModelList(result.stdout);
  if (models.length === 0) {
    // Relay what the CLI said. On a signed-out or entitlement-less account
    // this is the actual, actionable sentence ("No models available for this
    // account.") — never our own guess at why.
    return {
      supported: false,
      models: [],
      reason: firstMeaningfulLine(result.stdout, result.stderr)
        || `${command} listed no models.`,
    };
  }
  return { supported: true, models };
}

// Exported for tests only — the parser is the part worth pinning against real
// observed output.
export const __parseTextModelListForTests = parseTextModelList;
