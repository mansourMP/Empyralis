import type { Metadata } from 'next';
import localFont from 'next/font/local';
import type { ReactNode } from 'react';

import './globals.css';
import '../lib/ui/theme-tokens.css';
import '../lib/ui/chrome.css';
import { AccountShellProvider } from '@/lib/shell/account-shell-context';
import { ACCOUNT_SHELL_STORAGE_KEY } from '@/lib/shell/account-shell-storage';
import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';

export const metadata: Metadata = {
  title: 'Empyralis',
  description: 'Empyralis browser shell',
};

// Fleet UI font: self-hosted Inter (Linear's typeface). next/font/google was
// removed because it fetches from Google at build time and fails in offline
// builds. To activate real Inter: drop InterVariable.woff2 into app/fonts, then
// uncomment the block below AND add `${inter.variable}` to the <html> className.
// No CSS change is needed — fleet-theme.css uses var(--font-inter, <system
// stack>), so until the file is present the UI renders in the native system font.
// const inter = localFont({
//   src: [{ path: './fonts/InterVariable.woff2', weight: '100 900', style: 'normal' }],
//   variable: '--font-inter',
//   display: 'swap',
// });

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
        root.setAttribute('data-theme', resolved);
        root.style.colorScheme = resolved;
        if (body) {
          body.setAttribute('data-theme', resolved);
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
      data-theme="light"
      suppressHydrationWarning
      className={dmSans.variable /* append ` ${inter.variable}` when InterVariable.woff2 is added */}
    >
      <body data-theme="light" suppressHydrationWarning>
        <script
          // Keep document theme in sync with persisted preference before hydration.
          dangerouslySetInnerHTML={{ __html: themeBootstrapScript }}
        />
        <AccountShellProvider initialSession={initialSession}>{children}</AccountShellProvider>
      </body>
    </html>
  );
}
