import { test } from "node:test";
import assert from "node:assert/strict";

import { isSuppressedCredentiallessTurnReply } from "../cancel-predicate.js";

const REAL_FAILOVER_TEXT =
  'FailoverError: No API key found for provider "openai". Auth store: /Users/mansur/.openclaw-radiopoc/agents/main/agent/openclaw-agent.sqlite (agentDir: /Users/mansur/.openclaw-radiopoc/agents/main/agent). Configure auth for this agent (openclaw --profile radiopoc agents add <id>) or copy only portable static auth profiles from the main agentDir.';

test("matches the exact synthetic no-credentials reply captured live from openclaw@2026.6.10", () => {
  assert.equal(isSuppressedCredentiallessTurnReply({ content: REAL_FAILOVER_TEXT }), true);
});

test("does not match a real assistant reply", () => {
  assert.equal(
    isSuppressedCredentiallessTurnReply({ content: "Sure — here's the summary you asked for." }),
    false,
  );
});

test("does not match empty content", () => {
  assert.equal(isSuppressedCredentiallessTurnReply({ content: "" }), false);
});

test("does not match a FailoverError for a different reason (e.g. rate limit)", () => {
  assert.equal(
    isSuppressedCredentiallessTurnReply({
      content: "FailoverError: rate limit exceeded, please retry in 30s",
    }),
    false,
  );
});

test("does not match a plain mention of a missing API key without the FailoverError marker", () => {
  assert.equal(
    isSuppressedCredentiallessTurnReply({
      content: "No API key found for provider — please check your billing dashboard.",
    }),
    false,
  );
});

test("known accepted false-positive edge: a real reply that happens to contain both exact marker strings", () => {
  // Documents the known, accepted risk of a content match (see
  // src/cancel-predicate.ts's header comment) rather than hiding it. This
  // is a deliberately contrived message containing both exact substrings
  // ("FailoverError" and "No API key found for provider") without being
  // OpenClaw's synthetic reply — the predicate cannot and does not try to
  // tell these apart, because message_sending's real shape gives it
  // nothing structural to tell them apart with.
  assert.equal(
    isSuppressedCredentiallessTurnReply({
      content:
        "Quoting the incident report verbatim: 'FailoverError: No API key found for provider \"openai\"' is what the on-call saw at 2am.",
    }),
    true,
  );
});
