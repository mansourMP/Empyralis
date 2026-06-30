export const AUTH_REQUEST_TIMEOUT_MS = 30_000;
export const AUTH_ACCOUNT_SHELL_TIMEOUT_MS = 10_000;
export const ACCOUNT_SHELL_RETRY_DELAYS_MS = [250, 750] as const;

export const AUTH_TIMEOUT_MESSAGE =
  'Authentication is taking longer than expected. Please try again in a moment.';
