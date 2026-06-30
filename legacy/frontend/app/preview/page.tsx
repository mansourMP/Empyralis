import { loadAccountShellSessionSafely } from '@/lib/server/load-account-shell-session';

import { PublicAgentPreviewClient } from './PublicAgentPreviewClient';

export default async function PreviewPage() {
  const session = await loadAccountShellSessionSafely();
  const setupHref = session ? '/onboarding' : '/signup';
  return <PublicAgentPreviewClient setupHref={setupHref} />;
}
