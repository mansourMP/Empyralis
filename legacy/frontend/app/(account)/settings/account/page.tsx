import { redirectToWorkspaceSettings } from '@/app/(account)/settings/resolve-settings-route';

export default async function AccountSettingsPage() {
  await redirectToWorkspaceSettings('account');
}
