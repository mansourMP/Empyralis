'use client';

import {
  type PropsWithChildren,
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from 'react';

import {
  APP_DEFAULT_THEME,
  APP_THEME_ATTRIBUTE,
  type AppResolvedTheme,
  type AppThemePreference,
  resolveAppThemeCssVariables,
  resolveAppThemePreference,
} from '@/lib/ui/tokens';

type AppThemeContextValue = {
  preference: AppThemePreference;
  resolvedTheme: AppResolvedTheme;
};

const AppThemeContext = createContext<AppThemeContextValue>({
  preference: 'light',
  resolvedTheme: APP_DEFAULT_THEME,
});

function readSystemDarkPreference(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function readDocumentTheme(): AppResolvedTheme | null {
  if (typeof document === 'undefined') {
    return null;
  }
  const attributeValue =
    document.documentElement.getAttribute(APP_THEME_ATTRIBUTE)
    ?? document.body.getAttribute(APP_THEME_ATTRIBUTE);
  return attributeValue === 'light' || attributeValue === 'dark' ? attributeValue : null;
}

export function AppThemeProvider({
  preference,
  children,
}: PropsWithChildren<{
  preference: AppThemePreference;
}>) {
  const [prefersDark, setPrefersDark] = useState<boolean>(() => {
    if (preference === 'dark') {
      return true;
    }
    if (preference === 'light') {
      return false;
    }
    const documentTheme = readDocumentTheme();
    if (documentTheme) {
      return documentTheme === 'dark';
    }
    return readSystemDarkPreference();
  });

  useEffect(() => {
    if (preference === 'dark') {
      setPrefersDark(true);
      return undefined;
    }
    if (preference === 'light') {
      setPrefersDark(false);
      return undefined;
    }
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => {
      setPrefersDark(event.matches);
    };
    // UNDER 'system' THE DEVICE IS THE AUTHORITY, ALWAYS — read it here
    // rather than deferring to whatever is currently painted.
    //
    // This line used to be `if (!readDocumentTheme()) { … }`, guarding
    // against overriding the pre-hydration paint. At first load that guard
    // is harmless, because layout.tsx's bootstrap script resolves 'system'
    // from this same prefers-color-scheme query, so the value it skipped
    // reading was one it already agreed with. At RUNTIME it was a silent
    // bug: this provider stamps data-theme on <html> and <body> itself, so
    // readDocumentTheme() can never be null once it has rendered once —
    // switching the preference TO 'system' therefore adopted whatever the
    // PREVIOUS preference had painted and sat there until the OS scheme
    // happened to change.
    //
    // It was unreachable until 2026-08-29 because nothing in the product
    // could write 'system': the only writer was the rail account-popover's
    // binary light/dark toggle. Settings ▸ Account's three-way picker makes
    // it reachable, and measured it directly — device light, app showing
    // dark, pick System, app stays dark. Fresh loads were always correct,
    // which is what kept this hidden.
    setPrefersDark(mediaQuery.matches);
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', onChange);
      return () => mediaQuery.removeEventListener('change', onChange);
    }
    mediaQuery.addListener(onChange);
    return () => mediaQuery.removeListener(onChange);
  }, [preference]);

  const resolvedTheme = resolveAppThemePreference(preference, prefersDark);

  useLayoutEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }
    const cssVariables = resolveAppThemeCssVariables(resolvedTheme);
    document.documentElement.setAttribute(APP_THEME_ATTRIBUTE, resolvedTheme);
    document.documentElement.style.colorScheme = resolvedTheme;
    for (const [name, value] of Object.entries(cssVariables)) {
      document.documentElement.style.setProperty(name, value);
    }
    document.body.setAttribute(APP_THEME_ATTRIBUTE, resolvedTheme);
    document.body.style.colorScheme = resolvedTheme;
  }, [resolvedTheme]);

  const value = useMemo<AppThemeContextValue>(
    () => ({
      preference,
      resolvedTheme,
    }),
    [preference, resolvedTheme],
  );

  return (
    <AppThemeContext.Provider value={value}>
      <div className="app-root">
        {children}
      </div>
    </AppThemeContext.Provider>
  );
}

export function useAppTheme(): AppThemeContextValue {
  return useContext(AppThemeContext);
}
