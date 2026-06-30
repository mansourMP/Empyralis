'use client';

import {
  DeployedAgentTestTurnPane,
  type DeployedAgentTestChatSessionState,
} from '@/lib/workspace/workstation-deployed-agent-test-turn-pane';
import type { Dispatch, SetStateAction } from 'react';

export function AgentPlaygroundPanel({
  deployedAgentId,
  workspaceId,
  client,
  runtimeMode,
  session,
  onSessionChange,
  onResetSession,
}: {
  deployedAgentId: string;
  workspaceId: string;
  client: { testTurnDeployedAgent: (params: { deployedAgentId: string; body: Record<string, unknown> }) => Promise<Record<string, unknown> | null> };
  runtimeMode?: string;
  session: DeployedAgentTestChatSessionState;
  onSessionChange: Dispatch<SetStateAction<DeployedAgentTestChatSessionState>>;
  onResetSession: () => void;
}) {
  return (
    <div className="studio-agent-chat">
      <DeployedAgentTestTurnPane
        deployedAgentId={deployedAgentId}
        workspaceId={workspaceId}
        client={client}
        runtimeMode={runtimeMode}
        session={session}
        onSessionChange={onSessionChange}
        onResetSession={onResetSession}
      />
    </div>
  );
}
