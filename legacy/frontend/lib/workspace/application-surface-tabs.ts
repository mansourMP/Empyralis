export type ApplicationSurfaceTabId = 'installed' | 'my_apps';

export const APPLICATION_SURFACE_TABS: readonly { id: ApplicationSurfaceTabId; label: string }[] = [
  { id: 'installed', label: 'Apps' },
  { id: 'my_apps', label: 'Create app' },
];

export const APPLICATION_TITLEBAR_TABS: readonly { id: ApplicationSurfaceTabId; label: string }[] = [
  { id: 'installed', label: 'Apps' },
];

export function normalizeApplicationSurfaceTabId(value: string | null | undefined): ApplicationSurfaceTabId {
  return APPLICATION_SURFACE_TABS.some((tab) => tab.id === value)
    ? value as ApplicationSurfaceTabId
    : 'installed';
}

export function buildApplicationTabHref(
  workspaceId: string,
  tabId: ApplicationSurfaceTabId,
  searchParams: { toString: () => string },
): string {
  const params = new URLSearchParams(searchParams.toString());
  if (tabId === 'installed') {
    params.delete('tab');
    params.delete('applicationTab');
  } else {
    params.set('tab', tabId);
    params.delete('applicationTab');
  }
  const query = params.toString();
  const baseHref = `/w/${encodeURIComponent(workspaceId)}/applications`;
  return query ? `${baseHref}?${query}` : baseHref;
}
