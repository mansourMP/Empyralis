/**
 * Platform-brand unit tests — verify the neutral brand layer never leaks
 * underlying provider names or logos for the platform/hosted path.
 *
 * Run: npx tsx lib/workspace/platform-brand.test.ts
 */

import { existsSync } from 'node:fs';
import { join } from 'node:path';

import {
  isPlatformProvider,
  isPlatformBillingSource,
  platformSafeProviderLabel,
  platformSafeProviderImage,
  platformSafeImage,
  platformSafeLabel,
  platformTierLabel,
  platformSafeModelLabel,
  PLATFORM_AI_LABEL,
  PLATFORM_AI_LOGO,
} from './platform-brand';

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

// --- isPlatformProvider ---

assert(isPlatformProvider({ credential_plane: 'platform_runtime' }), 'detects platform_runtime');
assert(isPlatformProvider({ credentialPlane: 'platform_runtime' }), 'detects credentialPlane camelCase');
assert(!isPlatformProvider({ credential_plane: 'workspace_connection' }), 'rejects workspace_connection');
assert(!isPlatformProvider({ credential_plane: 'local_runtime' }), 'rejects local_runtime');
assert(!isPlatformProvider(null), 'null is not platform');
assert(!isPlatformProvider(undefined), 'undefined is not platform');
assert(!isPlatformProvider({}), 'empty object is not platform');
assert(!isPlatformProvider('platform_runtime'), 'string is not platform');

// --- isPlatformBillingSource ---

assert(isPlatformBillingSource('empyralis_credits'), 'detects empyralis_credits');
assert(!isPlatformBillingSource('workspace_api_key'), 'rejects workspace_api_key');
assert(!isPlatformBillingSource(''), 'rejects empty string');
assert(!isPlatformBillingSource(null), 'rejects null');

// --- platformSafeImage (direct credential plane) ---

assertEqual(
  platformSafeImage('platform_runtime', '/brand-assets/providers/deepseek.svg'),
  PLATFORM_AI_LOGO,
  'platform path → Empyralis mark, regardless of fallback',
);
assertEqual(
  platformSafeImage('workspace_connection', '/brand-assets/providers/openai.svg'),
  '/brand-assets/providers/openai.svg',
  'BYOK path → real image unchanged',
);
assertEqual(
  platformSafeImage(null, '/brand-assets/providers/deepseek.svg'),
  '/brand-assets/providers/deepseek.svg',
  'null credential plane → fallback unchanged',
);
assertEqual(
  platformSafeImage(undefined, ''),
  '',
  'undefined credential plane → empty fallback',
);

// --- platformSafeLabel (direct credential plane) ---

assertEqual(
  platformSafeLabel('platform_runtime', 'DeepSeek'),
  PLATFORM_AI_LABEL,
  'platform path → Platform AI, ignoring raw label',
);
assertEqual(
  platformSafeLabel('platform_runtime', 'DeepSeek', 'Workspace AI'),
  'Workspace AI',
  'platform path → custom fallback when provided',
);
assertEqual(
  platformSafeLabel('workspace_connection', 'OpenAI'),
  'OpenAI',
  'BYOK path → raw label unchanged',
);

// --- platformSafeProviderImage (object form) ---

assertEqual(
  platformSafeProviderImage({ credential_plane: 'platform_runtime', image: '/brand-assets/providers/deepseek.svg' }),
  PLATFORM_AI_LOGO,
  'object form: platform → Empyralis mark',
);
assertEqual(
  platformSafeProviderImage({ credential_plane: 'workspace_connection', image: '/brand-assets/providers/openai.svg' }),
  '/brand-assets/providers/openai.svg',
  'object form: BYOK → real image',
);

// --- platformSafeProviderLabel (object form) ---

assertEqual(
  platformSafeProviderLabel({ credential_plane: 'platform_runtime', label: 'DeepSeek' }),
  PLATFORM_AI_LABEL,
  'object form: platform → Platform AI',
);
assertEqual(
  platformSafeProviderLabel({ credential_plane: 'workspace_connection', label: 'OpenAI' }),
  'OpenAI',
  'object form: BYOK → real label',
);

// --- platformTierLabel ---

assertEqual(platformTierLabel('light'), 'Light AI', 'light tier');
assertEqual(platformTierLabel('pro'), 'Pro AI', 'pro tier');
assertEqual(platformTierLabel('max'), 'Max AI', 'max tier');
assertEqual(platformTierLabel(null), PLATFORM_AI_LABEL, 'null tier → Platform AI');
assertEqual(platformTierLabel('unknown'), PLATFORM_AI_LABEL, 'unknown tier → Platform AI');

// --- platformSafeModelLabel: NEVER returns raw model ID ---

const modelLabel = platformSafeModelLabel('deepseek-v4-pro', 'pro');
assert(!modelLabel.includes('deepseek'), `model label must not contain provider name: "${modelLabel}"`);
assert(!modelLabel.includes('v4'), `model label must not contain version: "${modelLabel}"`);
assertEqual(modelLabel, 'Platform Pro', 'pro tier → Platform Pro');

assertEqual(platformSafeModelLabel('deepseek-v4-flash', 'light'), 'Platform Fast', 'light tier → Platform Fast');
assertEqual(platformSafeModelLabel('deepseek-v4-pro', 'max'), 'Platform Max', 'max tier → Platform Max');
assertEqual(platformSafeModelLabel('deepseek-chat', 'pro'), 'Platform Pro', 'chat model + pro → Platform Pro');
assertEqual(platformSafeModelLabel('deepseek-v4-flash', 'pro'), 'Platform Fast', 'flash model + pro → Platform Fast');

// --- PLATFORM_AI_LOGO and PLATFORM_AI_LABEL are exported ---

assert(typeof PLATFORM_AI_LOGO === 'string' && PLATFORM_AI_LOGO.length > 0, 'PLATFORM_AI_LOGO is a non-empty string');
assert(typeof PLATFORM_AI_LABEL === 'string' && PLATFORM_AI_LABEL.length > 0, 'PLATFORM_AI_LABEL is a non-empty string');

// --- The logo path names a file that actually exists ---
//
// Every other assertion in this file compares PLATFORM_AI_LOGO against
// PLATFORM_AI_LOGO, so the whole suite stays green when the constant points
// at a deleted or misspelt asset -- a check that derives its expectation from
// the thing it checks (CLAUDE.md). The expected side comes from the source
// constant; the actual side comes from the filesystem, which is a different
// source. A missing brand asset renders as an empty box with no error.

assert(
  existsSync(join(__dirname, '..', '..', 'public', PLATFORM_AI_LOGO.replace(/^\//, ''))),
  `PLATFORM_AI_LOGO resolves to a real file under public/ (${PLATFORM_AI_LOGO})`,
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
