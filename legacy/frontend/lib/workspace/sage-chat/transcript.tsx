'use client';

import React, { memo } from 'react';
import { CodexChatCell, type CodexApprovalAction } from '@/lib/workspace/codex-chat/cell-components';
import type { CodexTranscriptCell } from '@/lib/workspace/codex-chat/cells';
import { ScrollRegion } from '@/lib/ui/scroll-region';

export type SageRecentRunRow = {
  runId: string | null;
  threadId: string | null;
  createdAt: string | null;
  preview: string;
  title: string;
};

export type SageThreadSeed = {
  title: string;
  sourceRunId: string | null;
  sourceThreadId: string | null;
};

export interface SageTranscriptProps {
  visibleTranscriptCells: CodexTranscriptCell[];
  pinnedTimelineCells: CodexTranscriptCell[];
  resolvingApprovalId: string | null;
  onResolveApproval: (approvalId: string, action: CodexApprovalAction) => void;
  showBlankTranscript: boolean;
  recentRunRows: SageRecentRunRow[];
  onStartNewThread: (seed: SageThreadSeed) => void | Promise<void>;
  formatRelativeTime: (time: string | null) => string;
}

export const SageTranscript = memo(function SageTranscript({
  visibleTranscriptCells,
  pinnedTimelineCells,
  resolvingApprovalId,
  onResolveApproval,
  showBlankTranscript,
  recentRunRows: _recentRunRows,
  onStartNewThread: _onStartNewThread,
  formatRelativeTime: _formatRelativeTime,
}: SageTranscriptProps) {
  return (
    <ScrollRegion className="app-chat-thread__scroll">
      <div className="app-chat-thread__body">
        {showBlankTranscript && (
          <div className="app-chat-empty-state" aria-hidden="true" />
        )}

        {visibleTranscriptCells.map((cell, index) => (
          <CodexChatCell
            key={`${cell.kind}:${cell.id}:${index}`}
            cell={cell}
            resolvingApprovalId={resolvingApprovalId}
            onResolveApproval={onResolveApproval}
          />
        ))}

        {pinnedTimelineCells.map((cell, index) => (
          <CodexChatCell
            key={`pinned:${cell.kind}:${cell.id}:${index}`}
            cell={cell}
            resolvingApprovalId={resolvingApprovalId}
            onResolveApproval={onResolveApproval}
          />
        ))}
      </div>
    </ScrollRegion>
  );
});
