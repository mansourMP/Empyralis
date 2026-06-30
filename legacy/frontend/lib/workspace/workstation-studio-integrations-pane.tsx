'use client';

import { ListDetailPanel, ListDetailShell } from '@/lib/ui/list-detail';
import { WorkstationSageConnectorsPane } from '@/lib/workspace/workstation-sage-connectors-pane';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';

export function WorkstationStudioIntegrationsPane() {
  return (
    <WorkstationSurfaceRoot surface="studio-integrations">
      <ListDetailShell
        className="app-studio-shell app-studio-shell--integrations"
        title="Build · Integrations"
        subtitle="Business messaging and workflow app connections for deployed specialists."
      >
        <ListDetailPanel
          className="studio-panel studio-panel--integrations"
          eyebrow="Integrations"
          title="Business messaging and workflow connections"
          subtitle="Keep customer-facing channels in Studio. Personal Telegram and personal WhatsApp stay on the paired computer inside Sage."
        >
          <WorkstationSageConnectorsPane
            surface="studio"
            showProviders={false}
            showTools={false}
            connectorIds={['telegram_bot', 'whatsapp_twilio', 'gmail', 'webhook', 'slack']}
            className="studio-integrations-pane"
          />
        </ListDetailPanel>
      </ListDetailShell>
    </WorkstationSurfaceRoot>
  );
}
