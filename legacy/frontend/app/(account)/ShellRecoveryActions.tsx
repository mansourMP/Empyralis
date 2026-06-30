'use client';

import { useCallback, useTransition } from 'react';

type ShellRecoveryActionsProps = {
  label: string;
};

export function ShellRecoveryActions({ label }: ShellRecoveryActionsProps) {
  const [isPending, startTransition] = useTransition();
  const reload = useCallback(() => {
    startTransition(() => {
      window.location.reload();
    });
  }, []);

  return (
    <div className="app-page-message__actions" aria-label={label}>
      <button
        type="button"
        className="app-page-message__button"
        onClick={reload}
        disabled={isPending}
      >
        {isPending ? 'Reloading...' : 'Reload'}
      </button>
      <a className="app-page-message__button app-page-message__button--secondary" href="/login">
        Sign in again
      </a>
    </div>
  );
}
