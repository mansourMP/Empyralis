import type { Metadata } from 'next';
import localFont from 'next/font/local';
import type { ReactNode } from 'react';

import './globals.css';
import '../lib/ui/chrome.css';
import { AccountShellProvider } from '@/lib/shell/account-shell-context';
import { ACCOUNT_SHELL_STORAGE_KEY } from '@/lib/shell/account-shell-storage';
import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';

export const metadata: Metadata = {
  title: 'Empyralis',
  description: 'Empyralis browser shell',
};

const dmSans = localFont({
  src: [
    {
      path: './fonts/DMSans-Regular.ttf',
      weight: '400',
      style: 'normal',
    },
    {
      path: './fonts/DMSans-Medium.ttf',
      weight: '500',
      style: 'normal',
    },
    {
      path: './fonts/DMSans-Bold.ttf',
      weight: '700',
      style: 'normal',
    },
  ],
  variable: '--font-dm-sans',
  display: 'swap',
});

const fraunces = localFont({
  src: [
    {
      path: './fonts/Fraunces-Regular.ttf',
      weight: '400',
      style: 'normal',
    },
    {
      path: './fonts/Fraunces-Bold.ttf',
      weight: '700',
      style: 'normal',
    },
  ],
  variable: '--font-fraunces',
  display: 'swap',
});

function buildThemeBootstrapScript(storageKey: string): string {
  return `
    (function () {
      try {
        var raw = window.localStorage.getItem(${JSON.stringify(storageKey)});
        var preference = 'light';
        if (raw) {
          var parsed = JSON.parse(raw);
          var candidate = parsed && typeof parsed === 'object' ? parsed.globalTheme : null;
          if (candidate === 'light' || candidate === 'dark' || candidate === 'system') {
            preference = candidate;
          }
        }

        var resolved = preference;
        if (resolved !== 'light' && resolved !== 'dark') {
          var prefersDark = typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-color-scheme: dark)').matches;
          resolved = prefersDark ? 'dark' : 'light';
        }

        var root = document.documentElement;
        var body = document.body;
        root.setAttribute('data-emp-theme', resolved);
        root.style.colorScheme = resolved;
        if (body) {
          body.setAttribute('data-emp-theme', resolved);
          body.style.colorScheme = resolved;
        }
      } catch (_error) {
      }
    })();
  `;
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const initialSession = await loadAccountShellSessionSafely();
  const themeBootstrapScript = buildThemeBootstrapScript(ACCOUNT_SHELL_STORAGE_KEY);

  return (
    <html
      lang="en"
      data-emp-theme="light"
      suppressHydrationWarning
      className={`${dmSans.variable} ${fraunces.variable}`}
    >
      <body data-emp-theme="light" suppressHydrationWarning>
        <script
          // Keep document theme in sync with persisted preference before hydration.
          dangerouslySetInnerHTML={{ __html: themeBootstrapScript }}
        />
        <AccountShellProvider initialSession={initialSession}>{children}</AccountShellProvider>
      </body>
    </html>
  );
}
