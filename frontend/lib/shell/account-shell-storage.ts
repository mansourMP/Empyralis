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

  // `typeof window.localStorage` only proves the object EXISTS — in Safari
  // with "Block All Cookies", certain ITP states, and some private-browsing
  // configurations, every actual access (get/set/remove) throws
  // SecurityError even though the property is present. This whole function
  // body has to be one try, including the read itself, or that throw
  // escapes readAccountShellSnapshot and crashes the account shell on boot.
  try {
    const rawValue = window.localStorage.getItem(ACCOUNT_SHELL_STORAGE_KEY);
    if (!rawValue) {
      return null;
    }

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
    // A parse failure and a storage-access SecurityError land here alike —
    // both mean "no usable snapshot". The removeItem is itself a storage
    // access and can throw too (a corrupt blob under a throwing storage
    // object), so it gets its own try rather than escaping this catch.
    try {
      window.localStorage.removeItem(ACCOUNT_SHELL_STORAGE_KEY);
    } catch {
      /* storage refused the write too — nothing left to clean up */
    }
    return null;
  }
}

export function writeAccountShellSnapshot(snapshot: AccountShellSnapshot): void {
  if (!canUseStorage()) {
    return;
  }

  try {
    window.localStorage.setItem(ACCOUNT_SHELL_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // Private browsing (QuotaExceededError) and storage-blocked browsers
    // (SecurityError) both land here. This cache is a convenience, never a
    // requirement — losing a write must never break the shell.
  }
}

export function clearAccountShellSnapshot(): void {
  if (!canUseStorage()) {
    return;
  }

  try {
    window.localStorage.removeItem(ACCOUNT_SHELL_STORAGE_KEY);
  } catch {
    /* storage refused the removal — nothing left to clean up */
  }
}
