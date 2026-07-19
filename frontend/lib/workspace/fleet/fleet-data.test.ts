/**
 * fleet-data unit tests — currently covers friendlyChannelOwnershipError,
 * the client-side defense-in-depth mapper for the channel-ownership-
 * conflict error surfaced by every channel-bind path in FleetAgentDetail.tsx
 * (saveByoBotToken for Telegram/Discord, saveSlackChannelBinding for
 * Slack). The backend (routes_fleet.py, hosted_bot_provisioning_service.py,
 * discord_bot_provisioning_service.py, agent_specialist_repository.py) is
 * responsible for translating the DB-level uq_agent_channel_bindings_
 * inbound_owner_v2 violation into specific, friendly copy before it ever
 * reaches the frontend -- this mapper exists only to catch the rare case
 * that translation is missing (a future channel-bind path not yet wired
 * with it) so a raw Postgres constraint-violation string never renders in
 * front of a user.
 *
 * Run: npx tsx lib/workspace/fleet/fleet-data.test.ts
 */

import { friendlyChannelOwnershipError } from './fleet-data';

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

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (actual === expected) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// --- Raw DB errors get replaced with clear, specific copy ---

const rawPgError =
  'duplicate key value violates unique constraint "uq_agent_channel_bindings_inbound_owner_v2"';
assertEqual(
  friendlyChannelOwnershipError(rawPgError),
  'This channel is already connected to another agent. A channel can only be owned by one agent at a time.',
  'raw duplicate-key error is replaced with friendly copy',
);

const rawConstraintNameOnly = 'insert or update on table "agent_channel_bindings" violates inbound_owner check';
assertEqual(
  friendlyChannelOwnershipError(rawConstraintNameOnly),
  'This channel is already connected to another agent. A channel can only be owned by one agent at a time.',
  'any message mentioning inbound_owner is treated as a raw DB leak',
);

// --- Already-friendly backend copy passes through unchanged (including
//     agent-name enrichment from get_agent_install_label) ---

assertEqual(
  friendlyChannelOwnershipError('This Slack channel is already connected to "Support Bot". A channel can only be owned by one agent at a time.'),
  'This Slack channel is already connected to "Support Bot". A channel can only be owned by one agent at a time.',
  'friendly backend copy with an agent name is preserved verbatim',
);

assertEqual(
  friendlyChannelOwnershipError('Discord bot @sagebot is already bound to "Sales Agent" in this workspace.'),
  'Discord bot @sagebot is already bound to "Sales Agent" in this workspace.',
  'Discord friendly copy with an agent name is preserved verbatim',
);

assertEqual(
  friendlyChannelOwnershipError('Telegram bot @mybot is already bound to another agent in this workspace.'),
  'Telegram bot @mybot is already bound to another agent in this workspace.',
  'Telegram friendly copy without a resolvable name is preserved verbatim',
);

// --- Unrelated errors are untouched ---

assertEqual(
  friendlyChannelOwnershipError('Paste the bot token from Discord\'s developer portal.'),
  'Paste the bot token from Discord\'s developer portal.',
  'unrelated validation errors pass through unchanged',
);

assertEqual(friendlyChannelOwnershipError(''), '', 'empty string passes through unchanged');

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
