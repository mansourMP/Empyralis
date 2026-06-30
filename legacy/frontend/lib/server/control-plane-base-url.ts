import 'server-only';

type ControlPlaneEnv = Partial<Record<
  | 'EMPYRALIS_API_URL'
  | 'ORION_API_URL'
  | 'NEXT_PUBLIC_ORION_API_URL'
  | 'NEXT_PUBLIC_API_URL'
  | 'EMPYRALIS_DEPLOY_ENV'
  | 'NODE_ENV',
  string | undefined
>>;

function isCloudEnvironment(env: ControlPlaneEnv): boolean {
  const value = String(env.EMPYRALIS_DEPLOY_ENV ?? env.NODE_ENV ?? '').trim().toLowerCase();
  return value === 'production' || value === 'prod' || value === 'staging';
}

function validateControlPlaneBaseUrl(rawValue: string, env: ControlPlaneEnv): string {
  const normalized = rawValue.replace(/\/+$/, '');
  if (!isCloudEnvironment(env)) {
    return normalized;
  }
  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new Error('Control-plane base URL must be an absolute HTTPS URL in staging/production.');
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('Control-plane base URL must use HTTPS in staging/production.');
  }
  if (['127.0.0.1', 'localhost', '0.0.0.0', '::1'].includes(parsed.hostname)) {
    throw new Error('Control-plane base URL cannot point at localhost in staging/production.');
  }
  return normalized;
}

export function resolveControlPlaneBaseUrl(
  env: ControlPlaneEnv = process.env as ControlPlaneEnv,
): string {
  const rawValue =
    env.EMPYRALIS_API_URL
    ?? env.ORION_API_URL
    ?? env.NEXT_PUBLIC_ORION_API_URL
    ?? env.NEXT_PUBLIC_API_URL
    ?? (process.env.NODE_ENV === 'production' ? undefined : 'http://127.0.0.1:8001');

  if (!rawValue || !rawValue.trim()) {
    throw new Error(
      'Control-plane base URL is not configured. Set EMPYRALIS_API_URL or ORION_API_URL.',
    );
  }

  return validateControlPlaneBaseUrl(rawValue, env);
}

export function controlPlaneBaseUrl(): string {
  return resolveControlPlaneBaseUrl(process.env as ControlPlaneEnv);
}
