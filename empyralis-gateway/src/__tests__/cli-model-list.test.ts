import test from "node:test";
import assert from "node:assert/strict";

import {
  listModelsViaCli,
  TEXT_MODEL_LIST_RUNTIMES,
  __parseTextModelListForTests as parseTextModelList,
} from "../llm/cli-model-list";

// ── Fixtures are REAL, not invented ─────────────────────────────────────────
// Both blobs below were captured live on 2026-08-20 by running the CLI's own
// subcommand on a real machine against a real account. CLAUDE.md: "a fixture
// that invents its own input cannot notice the real input is shaped
// differently."

/** `grok models` — grok 0.2.112, signed in with grok.com, verbatim. */
const REAL_GROK_MODELS_STDOUT = [
  "You are logged in with grok.com.",
  "",
  "Default model: grok-4.6",
  "",
  "Available models:",
  "  * grok-4.6 (default)",
  "",
].join("\n");

/** `cursor-agent models` — cursor-agent 2026.06.04, verbatim. This account
 *  could not be observed with a populated catalog, so the POPULATED cursor
 *  shape is genuinely unverified; the zero-model path below is what protects
 *  a customer from that gap (it reports Cursor's own sentence rather than an
 *  empty dropdown). */
const REAL_CURSOR_EMPTY_STDOUT = "No models available for this account.\n";

test("grok's real `grok models` output parses to its real catalog, with its own declared default", () => {
  const models = parseTextModelList(REAL_GROK_MODELS_STDOUT);
  assert.deepEqual(models, [{ id: "grok-4.6", displayName: "grok-4.6", isDefault: true }]);
});

test("a chatty preamble never becomes a model id", () => {
  // "You are logged in with grok.com." is prose; a model id is one bare
  // token. This is the assertion that keeps a sentence out of the picker.
  const models = parseTextModelList(REAL_GROK_MODELS_STDOUT);
  assert.ok(!models.some((m) => /\s/.test(m.id)));
  assert.ok(!models.some((m) => m.id.toLowerCase().includes("logged")));
});

test("a `Default model:` line marks the matching entry rather than becoming an entry of its own", () => {
  const models = parseTextModelList([
    "Default model: b",
    "Available models:",
    "  * a",
    "  * b",
  ].join("\n"));
  assert.deepEqual(models.map((m) => m.id), ["a", "b"]);
  assert.equal(models.find((m) => m.id === "b")?.isDefault, true);
  assert.equal(models.find((m) => m.id === "a")?.isDefault, false);
});

test("zero models is reported as unsupported carrying the CLI's OWN sentence, never as an empty catalog", async () => {
  const outcome = await listModelsViaCli("cursor_cli", {}, async () => ({
    exitCode: 0,
    stdout: REAL_CURSOR_EMPTY_STDOUT,
    stderr: "",
  }));
  assert.equal(outcome.supported, false);
  assert.deepEqual(outcome.models, []);
  assert.equal(outcome.reason, "No models available for this account.");
});

test("a populated answer is supported:true and reports no reason", async () => {
  const outcome = await listModelsViaCli("grok_build", {}, async () => ({
    exitCode: 0,
    stdout: REAL_GROK_MODELS_STDOUT,
    stderr: "",
  }));
  assert.equal(outcome.supported, true);
  assert.deepEqual(outcome.models.map((m) => m.id), ["grok-4.6"]);
  assert.equal(outcome.reason, undefined);
});

test("each runtime is asked with ITS OWN native command — never a shared or invented one", async () => {
  const invoked: Array<{ command: string; args: string[] }> = [];
  const exec = async (command: string, args: string[]) => {
    invoked.push({ command, args });
    return { exitCode: 0, stdout: "", stderr: "" };
  };
  await listModelsViaCli("cursor_cli", {}, exec);
  await listModelsViaCli("grok_build", {}, exec);
  assert.deepEqual(invoked, [
    { command: "cursor-agent", args: ["models"] },
    { command: "grok", args: ["models"] },
  ]);
});

test("the binary path env override each CLI already uses is honoured", async () => {
  const invoked: string[] = [];
  const exec = async (command: string) => {
    invoked.push(command);
    return { exitCode: 0, stdout: "", stderr: "" };
  };
  await listModelsViaCli("cursor_cli", { CURSOR_CLI_PATH: "/opt/cursor-agent" }, exec);
  await listModelsViaCli("grok_build", { GROK_CLI_PATH: "/opt/grok" }, exec);
  assert.deepEqual(invoked, ["/opt/cursor-agent", "/opt/grok"]);
});

test("a missing binary is reported in plain language, never as an empty catalog", async () => {
  const enoent = Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" });
  const outcome = await listModelsViaCli("grok_build", {}, async () => ({
    exitCode: null,
    stdout: "",
    stderr: "",
    error: enoent as NodeJS.ErrnoException,
  }));
  assert.equal(outcome.supported, false);
  assert.match(String(outcome.reason), /isn’t installed/);
});

test("claude_code and codex are NOT text-listed here — one has nothing to ask, the other has a real RPC", () => {
  assert.equal(TEXT_MODEL_LIST_RUNTIMES.has("claude_code"), false);
  assert.equal(TEXT_MODEL_LIST_RUNTIMES.has("codex"), false);
  assert.deepEqual([...TEXT_MODEL_LIST_RUNTIMES].sort(), ["cursor_cli", "grok_build"]);
});

test("an unknown runtime is refused rather than probed with a guessed command", async () => {
  let spawned = false;
  const outcome = await listModelsViaCli("some_new_cli", {}, async () => {
    spawned = true;
    return { exitCode: 0, stdout: "", stderr: "" };
  });
  assert.equal(outcome.supported, false);
  assert.equal(spawned, false);
});
