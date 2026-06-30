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
    const documentTheme = readDocumentTheme();
    if (!documentTheme) {
      setPrefersDark(mediaQuery.matches);
    }
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
