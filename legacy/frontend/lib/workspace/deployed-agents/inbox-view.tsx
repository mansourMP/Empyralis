'use client';

import { ListDetailPanel } from '@/lib/ui/list-detail';
import { AppButton } from '@/lib/ui/primitives';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { FormGrid, FormField, FormSelect, FormReadout } from '@/lib/ui/form-controls';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { DataTable, DataTableHeader, DataTableHeaderCell, DataTableRow, DataTableCell, DataBadge } from '@/lib/ui/data-table';
import type {
  DeployedAgentConversationRecord,
  DeployedAgentRecord,
} from '@/lib/workspace/workstation-client';
import type {
  ConversationFilters,
  TimelineEntry,
} from './types';
import {
  readString,
  humanizeToken,
  formatTimestamp,
  conversationCustomerLabel,
  escalationTone,
  outcomeTone,
} from './utils';
import { TranscriptEntryCard } from './components';

export interface AgentInboxViewProps {
  selectedAgent: DeployedAgentRecord | null;
  conversations: DeployedAgentConversationRecord[];
  isLoadingConversations: boolean;
  conversationFilters: ConversationFilters;
  onUpdateFilters: (filters: Partial<ConversationFilters>) => void;
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string | null) => void;
  selectedConversation: DeployedAgentConversationRecord | null;
  isLoadingTranscript: boolean;
  selectedTranscript: DeployedAgentConversationRecord | null;
  onDeleteCustomerData: () => void;
  busyExternalUserId: string | null;
  selectedExternalUserId: string | null;
  selectedExternalUserLabel: string;
  channelFilterOptions: string[];
  escalationFilterOptions: string[];
  outcomeFilterOptions: string[];
  filteredConversations: DeployedAgentConversationRecord[];
}

export function AgentInboxView({
  selectedAgent,
  conversations,
  isLoadingConversations,
  conversationFilters,
  onUpdateFilters,
  selectedSessionId,
  onSelectSession,
  selectedConversation,
  isLoadingTranscript,
  selectedTranscript,
  onDeleteCustomerData,
  busyExternalUserId,
  selectedExternalUserId,
  selectedExternalUserLabel,
  channelFilterOptions,
  escalationFilterOptions,
  outcomeFilterOptions,
  filteredConversations,
}: AgentInboxViewProps) {
  const transcriptEntries: TimelineEntry[] = Array.isArray(selectedTranscript?.entries)
    ? (selectedTranscript.entries as TimelineEntry[])
    : [];

  return (
    <>
      <ListDetailPanel
        className="studio-panel studio-panel--inbox"
        eyebrow="Conversations"
        title="Live conversation inbox"
        subtitle="Open customer sessions for the selected Business Agent, with filters for channel, handoff, and outcome."
      >
        {!selectedAgent ? (
          <EmptyPanel
            title="Select a Business Agent first"
            body="Pick a Business Agent to open its inbox and review customer sessions."
          />
        ) : isLoadingConversations ? (
          <>
            <SkeletonBlock height="3rem" />
            <SkeletonBlock height="3rem" />
          </>
        ) : conversations.length === 0 ? (
          <EmptyPanel
            title="No customer sessions yet"
            body="Connect Telegram and send the first customer message to start this inbox."
          />
        ) : (
          <div data-deployed-agent-conversations="list">
            <div data-deployed-agent-conversations="filters" className="deployed-agents-filter-bar">
              <FormGrid columns="repeat(auto-fit, minmax(10rem, 1fr))">
                <FormField label="Channel filter" hint="Keep the inbox focused on one customer channel at a time.">
                  <FormSelect
                    value={conversationFilters.channel}
                    onChange={(event) => onUpdateFilters({ channel: event.currentTarget.value })}
                  >
                    <option value="all">All channels</option>
                    {channelFilterOptions.map((channel) => (
                      <option key={channel} value={channel}>
                        {humanizeToken(channel, channel)}
                      </option>
                    ))}
                  </FormSelect>
                </FormField>
                <FormField label="Escalation filter" hint="Separate clear sessions from approval or escalation pressure.">
                  <FormSelect
                    value={conversationFilters.escalationState}
                    onChange={(event) => onUpdateFilters({ escalationState: event.currentTarget.value })}
                  >
                    <option value="all">All escalation states</option>
                    {escalationFilterOptions.map((state) => (
                      <option key={state} value={state}>
                        {humanizeToken(state, state)}
                      </option>
                    ))}
                  </FormSelect>
                </FormField>
                <FormField label="Outcome filter" hint="Focus on open work versus completed customer sessions.">
                  <FormSelect
                    value={conversationFilters.outcome}
                    onChange={(event) => onUpdateFilters({ outcome: event.currentTarget.value })}
                  >
                    <option value="all">All outcomes</option>
                    {outcomeFilterOptions.map((outcome) => (
                      <option key={outcome} value={outcome}>
                        {humanizeToken(outcome, outcome)}
                      </option>
                    ))}
                  </FormSelect>
                </FormField>
              </FormGrid>
              <div className="deployed-agents-filter-summary">
                <div className="app-data-table__hint">
                  Showing {filteredConversations.length} of {conversations.length} customer sessions.
                </div>
                <AppButton
                  type="button"
                  tone="secondary"
                  className="app-button--compact"
                  onClick={() => onUpdateFilters({ channel: 'all', escalationState: 'all', outcome: 'all' })}
                >
                  Clear filters
                </AppButton>
              </div>
            </div>
            {filteredConversations.length === 0 ? (
              <EmptyPanel
                title="No sessions match the active filters"
                body="No sessions match these filters. Clear them to return to the full inbox."
              />
            ) : (
              <DataTable>
                <DataTableHeader columns="minmax(0, 1fr) auto auto">
                  <DataTableHeaderCell>Customer</DataTableHeaderCell>
                  <DataTableHeaderCell>State</DataTableHeaderCell>
                  <DataTableHeaderCell align="end">Updated</DataTableHeaderCell>
                </DataTableHeader>
                {filteredConversations.map((conversation) => {
                  const sessionId = readString(conversation.session_id);
                  const selected = sessionId === selectedSessionId;
                  return (
                    <DataTableRow
                      key={sessionId}
                      columns="minmax(0, 1fr) auto auto"
                      selected={selected}
                      onClick={() => onSelectSession(sessionId)}
                    >
                      <DataTableCell
                        primary={conversationCustomerLabel(conversation)}
                        secondary={readString(conversation.last_message, 'No last message preview')}
                        meta={humanizeToken(conversation.channel, 'Channel')}
                      />
                      <DataTableCell
                        primary={(
                          <div className="deployed-agents-badge-row">
                            <DataBadge tone={escalationTone(conversation.escalation_state)}>
                              {humanizeToken(conversation.escalation_state, 'Clear')}
                            </DataBadge>
                            <DataBadge tone={outcomeTone(conversation.outcome)}>
                              {humanizeToken(conversation.outcome, 'Open')}
                            </DataBadge>
                          </div>
                        )}
                        secondary={readString(conversation.latest_run_id, 'No run linked')}
                      />
                      <DataTableCell align="end" primary={formatTimestamp(conversation.last_message_at)} />
                    </DataTableRow>
                  );
                })}
              </DataTable>
            )}
          </div>
        )}
      </ListDetailPanel>

      <ListDetailPanel
        className="studio-panel studio-panel--transcript"
        eyebrow="Transcript"
        title={selectedConversation ? conversationCustomerLabel(selectedConversation) : 'Transcript detail'}
        subtitle="Message history, runs, and escalation events for the selected customer session."
        actions={selectedConversation && selectedExternalUserId ? (
          <AppButton
            type="button"
            tone="danger"
            onClick={onDeleteCustomerData}
            disabled={busyExternalUserId === selectedExternalUserId}
          >
            Delete Customer Data
          </AppButton>
        ) : null}
      >
        {!selectedConversation ? (
          <EmptyPanel
            title="Select a session"
            body="Choose a customer session to inspect the full transcript and linked runs."
          />
        ) : isLoadingTranscript ? (
          <>
            <SkeletonBlock height="4rem" />
            <SkeletonBlock height="4rem" />
            <SkeletonBlock height="4rem" />
          </>
        ) : !selectedTranscript ? (
          <EmptyPanel
            title="Transcript unavailable"
            body="This session exists, but the transcript could not be loaded right now. Refresh and try again."
          />
        ) : (
          <div data-deployed-agent-transcript="detail" className="app-stack-3">
            <FormGrid columns="repeat(auto-fit, minmax(10rem, 1fr))">
              <FormReadout label="Channel" value={humanizeToken(selectedTranscript.channel, 'Telegram')} />
              <FormReadout label="Outcome" value={humanizeToken(selectedTranscript.outcome, 'Open')} />
              <FormReadout label="Thread" value={readString(selectedTranscript.thread_id, 'not linked')} />
            </FormGrid>
            <FormReadout
              label="Run ids"
              value={Array.isArray(selectedTranscript.run_ids) && selectedTranscript.run_ids.length > 0 ? selectedTranscript.run_ids.join(', ') : 'No run ids logged'}
            />
            <div className="deployed-agents-transcript">
              {transcriptEntries.length === 0 ? (
                <div className="deployed-agents-transcript__empty">No messages or events in this transcript.</div>
              ) : (
                transcriptEntries.map((entry, index) => (
                  <TranscriptEntryCard
                    key={`${entry.type}-${entry.timestamp}-${index}`}
                    entry={entry}
                  />
                ))
              )}
            </div>
          </div>
        )}
      </ListDetailPanel>
    </>
  );
}
