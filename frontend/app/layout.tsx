import type { Metadata } from 'next';
import localFont from 'next/font/local';
import type { ReactNode } from 'react';

import './globals.css';
import '../lib/ui/theme-tokens.css';
import '../lib/ui/chrome.css';
import { AccountShellProvider } from '@/lib/shell/account-shell-context';
import { ACCOUNT_SHELL_STORAGE_KEY } from '@/lib/shell/account-shell-storage';
import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';

// Declared through the Metadata `icons` object rather than Next's app/icon.*
// file convention, because the file convention emits ONE asset for every size
// and the mark needs two: empyralis-mark.svg carries 50% padding by design, so
// at 16px it draws the artwork into 8px and its dot into ~1px. The compact
// variant (same bars, same hex values, cropped to the artwork, dot enlarged to
// 1.33x the bar height) is what a tab actually renders. Only the `icons` object
// can say "this asset at 16, that one at 32".
//
// public/favicon.ico exists alongside these for the bare /favicon.ico request
// browsers, bookmark managers and crawlers make without reading any <link>.
// It lives in public/ and NOT app/favicon.ico on purpose: the file convention
// would inject its own competing <link rel="icon">.
export const metadata: Metadata = {
  title: 'Empyralis',
  description: 'Empyralis browser shell',
  icons: {
    icon: [
      { url: '/brand-assets/empyralis/empyralis-mark-compact.svg', type: 'image/svg+xml' },
      { url: '/brand-assets/empyralis/empyralis-favicon-32.png', sizes: '32x32', type: 'image/png' },
      { url: '/brand-assets/empyralis/empyralis-favicon-16.png', sizes: '16x16', type: 'image/png' },
    ],
    apple: [
      { url: '/brand-assets/empyralis/empyralis-apple-touch-180.png', sizes: '180x180', type: 'image/png' },
    ],
    shortcut: ['/favicon.ico'],
  },
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
