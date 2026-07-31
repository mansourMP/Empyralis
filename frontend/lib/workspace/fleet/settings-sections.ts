/**
 * The three Settings groups — plain data, deliberately in its own module
 * with NO "use client" directive. It's imported from both a server
 * component (settings/page.tsx's redirect, which runs on the server) and
 * client components (SettingsShell.tsx, settings/[section]/page.tsx).
 *
 * That split is load-bearing, not incidental: a "use client" file's exports
 * become opaque client references once a server component imports them —
 * fine for a component, but a plain constant like DEFAULT_SETTINGS_SECTION
 * turns into a call to a function that only exists on the client, and
 * `redirect()` on the server ends up building a URL out of that function's
 * *serialized reference* instead of its value (confirmed live: the redirect
 * landed on `/settings/function()%20%7B%20throw%20new%20Error(...)%7D`).
 * Keeping this file client-free avoids the boundary entirely.
 */

export const SETTINGS_SECTIONS = ["account", "workspace", "connections"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];
export const DEFAULT_SETTINGS_SECTION: SettingsSection = "workspace";

export function isSettingsSection(value: string): value is SettingsSection {
  return (SETTINGS_SECTIONS as readonly string[]).includes(value);
}
