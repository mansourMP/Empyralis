import type { AccountShellSnapshot } from '@/lib/shell/account-shell-store';

export const ACCOUNT_SHELL_STORAGE_KEY = 'empyralis.account-shell.v2';

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function readStringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) {
    return {};
  }

  return Object.entries(value).reduce<Record<string, string>>((accumulator, [key, entry]) => {
    if (typeof entry === 'string' && key) {
      accumulator[key] = entry;
    }
    return accumulator;
  }, {});
}

export function readAccountShellSnapshot(): AccountShellSnapshot | null {
  if (!canUseStorage()) {
    return null;
  }

  const rawValue = window.localStorage.getItem(ACCOUNT_SHELL_STORAGE_KEY);
  if (!rawValue) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawValue) as unknown;
    if (!isRecord(parsed)) {
      throw new Error('Account shell snapshot must be an object.');
    }
    return {
      accountId: typeof parsed.accountId === 'string' ? parsed.accountId : null,
      selectedWorkspaceId:
        typeof parsed.selectedWorkspaceId === 'string' ? parsed.selectedWorkspaceId : null,
      lastVisitedWorkspaceRouteById: readStringMap(parsed.lastVisitedWorkspaceRouteById),
      workspaceRouteStateById: readStringMap(parsed.workspaceRouteStateById),
      globalTheme:
        parsed.globalTheme === 'light' || parsed.globalTheme === 'dark' || parsed.globalTheme === 'system'
          ? parsed.globalTheme
          : 'light',
      globalChromePreferences: {
        tenantSwitcherCollapsed: Boolean(
          isRecord(parsed.globalChromePreferences)
            ? parsed.globalChromePreferences.tenantSwitcherCollapsed
            : false,
        ),
      },
    };
  } catch {
    window.localStorage.removeItem(ACCOUNT_SHELL_STORAGE_KEY);
    return null;
  }
}

export function writeAccountShellSnapshot(snapshot: AccountShellSnapshot): void {
  if (!canUseStorage()) {
    return;
  }

  window.localStorage.setItem(ACCOUNT_SHELL_STORAGE_KEY, JSON.stringify(snapshot));
}

export function clearAccountShellSnapshot(): void {
  if (!canUseStorage()) {
    return;
  }

  window.localStorage.removeItem(ACCOUNT_SHELL_STORAGE_KEY);
}
