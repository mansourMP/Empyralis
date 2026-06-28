'use client';

import { type ClipboardEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Check, X } from 'lucide-react';

import { FormField, FormInput, FormSelect } from '@/lib/ui/form-controls';
import { MotionSlidePanel } from '@/lib/ui/motion';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { AppButton, AppNotice, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { ConnectorSetupModalShell } from '@/lib/workspace/connector-setup-modal-shell';
import { WorkstationSplitWorkbench } from '@/lib/workspace/workstation-split-workbench';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { emitWorkstationProviderChanged } from '@/lib/workspace/workstation-provider-events';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

import { platformSafeImage, platformSafeLabel } from '@/lib/workspace/platform-brand';
import { normalizeHostedCreditStateForChat } from '@/lib/workspace/workstation-chat-pane-model';
import type { ChatHostedCreditState } from '@/lib/workspace/workstation-chat-pane-model';
import type {
  ProviderCatalogModelRecord,
  ConnectionStatusItem,
  ProviderCatalogRecord,
  ProviderProfileRecord,
  VaultCredentialRecord,
  WorkspaceAiRouteKind,
  WorkspaceAiRoutePayload,
} from '@/lib/workspace/workstation-client';

type IntegrationStatus = 'connected' | 'not_connected';

type ProviderAuthModeSnapshot = {
  id: string;
  label: string;
  secretRequired: boolean;
};

type ProviderSnapshot = {
  id: string;
  label: string;
  state: string;
  usable: boolean;
  active: boolean;
  hidden: boolean;
  sageVisible: boolean;
  studioVisible: boolean;
  localOnly: boolean;
  defaultModel: string | null;
  defaultAuthMode: string | null;
  authModes: ProviderAuthModeSnapshot[];
  providerScopes: string[];
  activeSource: string | null;
  credentialPlane: string | null;
  credentialPlaneLabel: string | null;
  credentialOwnerKind: string | null;
  workspaceConnected: boolean;
  hostedSageAiPolicy: string | null;
  stateDetail: string | null;
  modelsSyncedAt: string | null;
  modelsError: string | null;
  models: ProviderCatalogModelRecord[];
};

type ProviderCardRecord = {
  kind: 'provider';
  id: string;
  label: string;
  image: string;
  status: IntegrationStatus;
  provider: ProviderSnapshot;
  credential: VaultCredentialRecord | null;
  profile: ProviderProfileRecord | null;
  connected: boolean;
  keyTail: string | null;
};

type HostedSageAiSnapshot = {
  allowed: boolean;
  planAllowsHostedAi: boolean;
  policy: string;
  reason: string | null;
  message: string | null;
  monthlyCapUsd: number;
  monthlyCostUsd: number;
  monthlyRemainingUsd: number;
  creditsPerUsd: number;
  monthlyCreditCap: number;
  monthlyCreditsUsed: number;
  monthlyCreditsRemaining: number;
};

type ProviderPickerSection = {
  id: 'byok' | 'hosted' | 'local';
  label: string;
  items: ProviderCardRecord[];
};

type AiProviderSummary = {
  activeLabel: string;
  activeDetail: string;
  creditsLabel: string;
  creditsDetail: string;
  backupLabel: string;
  backupDetail: string;
  configLabel: string;
  configDetail: string;
};

type ConnectorCardDefinition = {
  id: string;
  label: string;
  image: string;
  connectorIds?: string[];
  capabilityTags: string[];
  summary: string;
  setupHint: string;
  surfaceScope: 'all' | 'studio_only';
};

type ConnectorCardRecord = {
  kind: 'connector';
  id: string;
  label: string;
  image: string;
  status: IntegrationStatus;
  connected: boolean;
  credential: VaultCredentialRecord | null;
  definition: ConnectorCardDefinition;
};

type GatewayRegistrationRecord = Record<string, unknown> & {
  gateway_id?: string | null;
  display_name?: string | null;
  status?: string | null;
  connection_status?: string | null;
  device_trust_state?: string | null;
  last_seen_at?: string | null;
};

type GatewayDoctorPayload = Record<string, unknown> & {
  status?: string | null;
  browser?: Record<string, unknown> | null;
  browser_attach?: Record<string, unknown> | null;
  personal_channels?: {
    status?: string | null;
    summary?: string | null;
    count?: number | null;
    connected_count?: number | null;
    running_count?: number | null;
    items?: Array<Record<string, unknown>>;
  } | null;
};

type GatewaySelectionSummary = {
  selectedGatewayMissing: boolean;
  selectedGatewayOffline: boolean;
  repairGateway: GatewayRegistrationRecord | null;
};

type PersonalChannelStateRecord = Record<string, unknown> & {
  status?: string | null;
  qr_code?: string | null;
  login_hint?: string | null;
  linked_name?: string | null;
  linked_jid?: string | null;
  linked_phone?: string | null;
  linked_username?: string | null;
  connected_at?: string | null;
  metadata?: Record<string, unknown> | null;
};

type PersonalChannelViewPayload = Record<string, unknown> & {
  state?: PersonalChannelStateRecord | null;
  recent_messages?: Array<Record<string, unknown>>;
};

type PersonalChannelSurfaceRecord = Record<string, unknown> & {
  channel_key?: string | null;
  label?: string | null;
  provider?: string | null;
  stage?: string | null;
  status?: string | null;
  status_label?: string | null;
  live_capable?: boolean | null;
  connected?: boolean | null;
  running?: boolean | null;
  connected_identity?: string | null;
  detail?: string | null;
  next_step?: string | null;
};

type PersonalChannelSurfacesPayload = Record<string, unknown> & {
  items?: PersonalChannelSurfaceRecord[];
};

type PersonalCardStatusTone = 'neutral' | 'connected' | 'warning' | 'danger';
type PersonalCommunicationChannel = 'telegram' | 'whatsapp';
type ChannelRouteMode = 'cloud' | 'hardware';
type GatewayRuntimeDependency = 'cloud_only' | 'gateway_optional' | 'gateway_required';
type ConsumerSetupState = 'oauth_ready' | 'oauth_not_wired' | 'advanced_only';

type PersonalCardRecord = {
  id:
    | 'device'
    | 'browser'
    | 'telegram_personal'
    | 'whatsapp_personal'
    | 'signal_personal'
    | 'imessage_personal'
    | 'wechat_personal';
  label: string;
  image: string;
  detail: string;
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  summary: string;
  nextStep: string | null;
  connectedIdentity?: string | null;
  lastActivityLabel?: string | null;
  ownershipLabel?: string | null;
  channel?: PersonalCommunicationChannel | null;
};

type PersonalChannelDraft = {
  phoneNumber: string;
  apiId: string;
  apiHash: string;
  loginCode: string;
  password: string;
  recipient: string;
  text: string;
};

type TelegramSetupStep = 'phone' | 'code' | 'password' | 'connected';

function asConnectorPayload(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function readTelegramStatePayload(value: unknown): Record<string, unknown> | null {
  const payload = asConnectorPayload(value);
  return asConnectorPayload(payload?.['state']);
}

function resolveTelegramSetupStep(value: unknown, fallback: TelegramSetupStep): TelegramSetupStep {
  const state = readTelegramStatePayload(value) ?? asConnectorPayload(value);
  const metadata = asConnectorPayload(state?.['metadata']);
  const status = String(state?.['status'] ?? '').trim();
  const loginHint = String(state?.['login_hint'] ?? state?.['loginHint'] ?? '').trim();
  const codeRequestedAt = String(
    state?.['code_requested_at']
    ?? state?.['codeRequestedAt']
    ?? metadata?.['code_requested_at']
    ?? metadata?.['codeRequestedAt']
    ?? '',
  ).trim();
  if (status === 'connected') {
    return 'connected';
  }
  if (status === 'password_required' || loginHint === 'password_required') {
    return 'password';
  }
  if (status === 'code_required' || loginHint === 'login_code_required') {
    return codeRequestedAt ? 'code' : 'phone';
  }
  if (
    status === 'authorization_required'
    && (loginHint === 'api_credentials_required' || loginHint === 'phone_number_required')
  ) {
    return 'phone';
  }
  return fallback;
}

function readTelegramConnectedPhone(value: unknown, fallback: string): string {
  const state = readTelegramStatePayload(value) ?? asConnectorPayload(value);
  return String(
    state?.['linked_phone']
    ?? state?.['linkedPhone']
    ?? state?.['phone_number']
    ?? state?.['phoneNumber']
    ?? fallback
    ?? '',
  ).trim();
}

type McpToolRecord = Record<string, unknown> & {
  name?: string | null;
  label?: string | null;
  description?: string | null;
  action_class?: string | null;
  risk_level?: string | null;
  requires_approval?: boolean | null;
  approved?: boolean | null;
  enabled?: boolean | null;
};

type McpServerRecord = Record<string, unknown> & {
  id?: string | null;
  label?: string | null;
  endpoint?: string | null;
  enabled?: boolean | null;
  tool_count?: number | null;
  tools?: McpToolRecord[];
  last_synced_at?: string | null;
};

type McpServerDraft = {
  serverId: string;
  label: string;
  endpoint: string;
};

type ExternalIntegrationCardRecord = {
  id: string;
  label: string;
  image: string;
  detail: string;
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  summary: string;
  nextStep: string | null;
  actionLabel?: string | null;
  secondaryActionLabel?: string | null;
  actionTarget?: 'gateway' | 'computer' | 'ai' | 'connection' | 'advanced' | 'close';
  connectionId?: string | null;
  connectionProvider?: string | null;
  connectionAuthFields?: string[];
  connectorCardId?: string | null;
  channel?: PersonalCommunicationChannel | null;
  locked?: boolean;
  runtimeDependency?: GatewayRuntimeDependency | null;
  consumerSetupState?: ConsumerSetupState | null;
  consumerSetupMessage?: string | null;
};

type IntegrationWorkbenchCategoryId = 'ai_runtime' | 'apps' | 'recipes' | 'channels' | 'advanced';

type SageRecipeRouteMode = 'chat_only' | 'connector_api' | 'cloud_browser' | 'cloud_computer' | 'gateway_required';

type SageRecipeDefinition = {
  id: string;
  title: string;
  summary: string;
  requiredConnections: string[];
  preferredRoute: SageRecipeRouteMode;
  approvalProfile: 'read_only' | 'external_write' | 'computer_action';
  scheduleSupported: boolean;
  starterPrompt: string;
};

type IntegrationWorkbenchGroup = {
  id: IntegrationWorkbenchCategoryId;
  label: string;
  description: string;
  detail: string;
  countLabel: string;
  statusTone: PersonalCardStatusTone;
};

type SageConnectorsPaneCache = {
  providers: ProviderSnapshot[];
  profiles: ProviderProfileRecord[];
  credentials: VaultCredentialRecord[];
  connectorVault: VaultCredentialRecord[];
  gateways: GatewayRegistrationRecord[];
  selectedGatewayId: string | null;
  doctor: GatewayDoctorPayload | null;
  whatsappPersonal: PersonalChannelViewPayload | null;
  telegramPersonal: PersonalChannelViewPayload | null;
  personalChannelSurfaces: PersonalChannelSurfaceRecord[];
  hostedSageAi: HostedSageAiSnapshot;
  workspaceAiRoute: WorkspaceAiRoutePayload | null;
  billingCredits: ChatHostedCreditState | null;
  mcpServers: McpServerRecord[];
  connectionStatusItems: ConnectionStatusItem[];
};

const sageConnectorsPaneCache = new Map<string, SageConnectorsPaneCache>();

const SAGE_RECIPE_DEFINITIONS: SageRecipeDefinition[] = [
  {
    id: 'morning_brief',
    title: 'Morning brief',
    summary: 'Summarize today, open loops, priority messages, calendar, and tasks.',
    requiredConnections: ['Gmail', 'Calendar'],
    preferredRoute: 'connector_api',
    approvalProfile: 'read_only',
    scheduleSupported: true,
    starterPrompt: 'Create my morning brief from connected email, calendar, tasks, and memory. Show priorities, risks, and follow-ups.',
  },
  {
    id: 'email_triage',
    title: 'Email triage',
    summary: 'Find important email, group by urgency, and draft next actions.',
    requiredConnections: ['Gmail'],
    preferredRoute: 'connector_api',
    approvalProfile: 'external_write',
    scheduleSupported: true,
    starterPrompt: 'Triage my important email. Summarize what matters, identify urgent replies, and draft responses for approval.',
  },
  {
    id: 'meeting_prep',
    title: 'Meeting prep',
    summary: 'Prepare notes, context, agenda, and questions before meetings.',
    requiredConnections: ['Calendar', 'Drive'],
    preferredRoute: 'connector_api',
    approvalProfile: 'read_only',
    scheduleSupported: true,
    starterPrompt: 'Prepare me for my next meeting using calendar, connected files, and memory. Give me context, agenda, questions, and risks.',
  },
  {
    id: 'follow_up',
    title: 'Follow up',
    summary: 'Track people to follow up with and draft short messages.',
    requiredConnections: ['Gmail', 'Calendar'],
    preferredRoute: 'connector_api',
    approvalProfile: 'external_write',
    scheduleSupported: true,
    starterPrompt: 'Find people I should follow up with and draft concise messages for approval.',
  },
  {
    id: 'weekly_report',
    title: 'Weekly report',
    summary: 'Turn recent work into a clean weekly summary with next steps.',
    requiredConnections: ['Gmail', 'Calendar', 'Drive'],
    preferredRoute: 'connector_api',
    approvalProfile: 'read_only',
    scheduleSupported: true,
    starterPrompt: 'Create a weekly report from connected work context. Include progress, blockers, decisions, and next steps.',
  },
  {
    id: 'monitor_website',
    title: 'Monitor website',
    summary: 'Check a public site or dashboard and summarize meaningful changes.',
    requiredConnections: ['Website'],
    preferredRoute: 'cloud_browser',
    approvalProfile: 'read_only',
    scheduleSupported: true,
    starterPrompt: 'Monitor this website for meaningful changes and summarize what changed. Ask me for the URL if needed.',
  },
];

const DEFAULT_HOSTED_SAGE_AI: HostedSageAiSnapshot = {
  allowed: false,
  planAllowsHostedAi: false,
  policy: 'disabled',
  reason: 'policy_disabled',
  message: 'Workspace AI is not active yet. Configure workspace AI or connect your own AI account.',
  monthlyCapUsd: 0,
  monthlyCostUsd: 0,
  monthlyRemainingUsd: 0,
  creditsPerUsd: 1000,
  monthlyCreditCap: 0,
  monthlyCreditsUsed: 0,
  monthlyCreditsRemaining: 0,
};

const DEFAULT_MCP_SERVER_DRAFT: McpServerDraft = {
  serverId: '',
  label: '',
  endpoint: '',
};


const SAMPLE_PROVIDER_IDS = [
  'deepseek',
  'gemini',
  'openai',
  'anthropic',
  'openrouter',
  'groq',
  'xai',
  'ollama_cloud',
  'openai-codex',
  'vertex',
  'azure_openai',
  'bedrock',
  'mistral',
  'qwen',
  'custom_openai_compatible',
  'ollama',
] as const;

const SAMPLE_PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  'openai-codex': 'OpenAI Codex',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  groq: 'Groq',
  xai: 'xAI',
  gemini: 'Google Gemini',
  vertex: 'Google Vertex AI',
  azure_openai: 'Azure OpenAI',
  bedrock: 'Amazon Bedrock',
  deepseek: 'DeepSeek',
  ollama_cloud: 'Ollama Cloud',
  mistral: 'Mistral',
  qwen: 'Qwen',
  custom_openai_compatible: 'Custom OpenAI-compatible',
  ollama: 'Ollama',
};

const PROVIDER_IMAGE_BY_ID: Record<string, string> = {
  openai: '/brand-assets/providers/openai.svg?v=3',
  'openai-codex': '/brand-assets/providers/openai.svg?v=3',
  anthropic: '/brand-assets/providers/anthropic.svg?v=3',
  gemini: '/brand-assets/providers/gemini.svg?v=3',
  vertex: '/brand-assets/providers/gemini.svg?v=3',
  openrouter: '/brand-assets/providers/openrouter.svg?v=3',
  groq: '/brand-assets/generic/api.svg?v=3',
  xai: '/brand-assets/providers/xai.svg?v=3',
  azure_openai: '/brand-assets/providers/openai.svg?v=3',
  bedrock: '/brand-assets/providers/bedrock.svg?v=3',
  mistral: '/brand-assets/providers/mistral.svg?v=3',
  deepseek: '/brand-assets/providers/deepseek.svg?v=3',
  ollama_cloud: '/brand-assets/providers/ollama.svg?v=3',
  qwen: '/brand-assets/providers/qwen.svg?v=3',
  custom_openai_compatible: '/brand-assets/generic/api.svg?v=3',
  ollama: '/brand-assets/providers/ollama.svg?v=3',
};

const CONNECTOR_DEFINITIONS: ConnectorCardDefinition[] = [
  {
    id: 'gmail',
    label: 'Gmail',
    image: '/brand-assets/apps/gmail.svg?v=3',
    connectorIds: ['google_workspace'],
    capabilityTags: ['Send email', 'Read inbox'],
    summary: 'Use one Google sign-in when Sage should work with Gmail and Google Calendar.',
    setupHint: 'Connect Google Workspace to unlock Gmail and Calendar in one place.',
    surfaceScope: 'all',
  },
  {
    id: 'google_calendar',
    label: 'Google Calendar',
    image: '/brand-assets/apps/google-calendar.svg?v=3',
    connectorIds: ['google_workspace'],
    capabilityTags: ['Calendar', 'Events'],
    summary: 'Use Google Calendar when Sage should schedule, review, or update events for you.',
    setupHint: 'Connect Google Workspace first, then Sage can use your Google Calendar.',
    surfaceScope: 'all',
  },
  {
    id: 'email',
    label: 'Email',
    image: '/brand-assets/generic/email.svg?v=3',
    connectorIds: ['google_workspace', 'microsoft_365', 'smtp'],
    capabilityTags: ['Inbox', 'Send email'],
    summary: 'Email is where Sage can reach people across Gmail, Outlook, or a custom mailbox.',
    setupHint: 'Connect Gmail or Microsoft 365 for the easiest setup. Use a custom mailbox only when needed.',
    surfaceScope: 'all',
  },
  {
    id: 'sage_telegram_hosted',
    label: 'Sage on Telegram',
    image: '/brand-assets/channels/telegram.svg?v=3',
    connectorIds: ['sage_telegram_hosted'],
    capabilityTags: ['Hosted channel', 'Bot replies'],
    summary: 'Sage on Telegram is the official Empyralis Telegram bot. Open Telegram, send the pairing code from your settings, and start messaging Sage instantly.',
    setupHint: 'Open the official Empyralis bot on Telegram and send your pairing code to connect.',
    surfaceScope: 'all',
  },
  {
    id: 'telegram_bot',
    label: 'Telegram Bot',
    image: '/brand-assets/channels/telegram.svg?v=3',
    connectorIds: ['telegram_bot'],
    capabilityTags: ['Custom bot', 'Advanced'],
    summary: 'Telegram Bot is the custom Studio bot-token path for teams that bring their own Telegram bot.',
    setupHint: 'Use hosted Sage on Telegram for the normal path. Custom Telegram bot setup belongs in Studio or Advanced.',
    surfaceScope: 'all',
  },
  {
    id: 'whatsapp_twilio',
    label: 'WhatsApp',
    image: '/brand-assets/channels/whatsapp.svg?v=3',
    connectorIds: ['whatsapp_twilio'],
    capabilityTags: ['Messaging', 'Customer chat'],
    summary: 'Connect WhatsApp through your Gateway to use your own phone number, or through Twilio for a cloud business number.',
    setupHint: 'Use Gateway for your personal WhatsApp. Use Twilio if you need a separate business number.',
    surfaceScope: 'all',
  },
  {
    id: 'slack',
    label: 'Slack',
    image: '/brand-assets/channels/slack.svg?v=3',
    connectorIds: ['slack'],
    capabilityTags: ['Channels', 'DMs'],
    summary: 'Use Slack when Sage should work in team channels and direct messages.',
    setupHint: 'Connect Slack with workspace sign-in when Sage should work in team channels and direct messages.',
    surfaceScope: 'all',
  },
  {
    id: 'discord_bot',
    label: 'Discord',
    image: '/brand-assets/channels/discord.svg?v=3',
    connectorIds: ['discord_bot'],
    capabilityTags: ['Servers', 'DMs'],
    summary: 'Discord bot deployments let Sage or Studio agents work in configured servers, channels, and DMs.',
    setupHint: 'Connect Discord through an app install when Sage or a Studio agent should work in Discord.',
    surfaceScope: 'all',
  },
  {
    id: 'github',
    label: 'GitHub',
    image: '/brand-assets/apps/github.svg?v=3',
    connectorIds: ['github'],
    capabilityTags: ['Issues', 'Pull requests'],
    summary: 'GitHub gives Sage repo, issue, and pull-request context.',
    setupHint: 'Connect GitHub when Sage should read or act on repositories and pull requests.',
    surfaceScope: 'all',
  },
  {
    id: 'notion',
    label: 'Notion',
    image: '/brand-assets/apps/notion.svg?v=3',
    connectorIds: ['notion'],
    capabilityTags: ['Pages', 'Search'],
    summary: 'Use Notion when Sage should search notes, docs, and workspace pages.',
    setupHint: 'Connect Notion to make workspace pages available to Sage.',
    surfaceScope: 'all',
  },
  {
    id: 'linear',
    label: 'Linear',
    image: '/brand-assets/apps/linear.svg?v=3',
    connectorIds: ['linear'],
    capabilityTags: ['Issues', 'Work tracking'],
    summary: 'Linear lets Sage read, create, and update issue work when connected.',
    setupHint: 'Connect Linear when Sage should help with product and engineering task flow.',
    surfaceScope: 'all',
  },
  {
    id: 'drive',
    label: 'Drive',
    image: '/brand-assets/apps/google-drive.svg?v=3',
    connectorIds: ['google_workspace'],
    capabilityTags: ['Files', 'Search'],
    summary: 'Google Drive gives Sage permissioned workspace files and documents when connected through Google Workspace.',
    setupHint: 'Connect Google Workspace first, then Sage can use Drive files from Plugins.',
    surfaceScope: 'all',
  },
  {
    id: 'uploads',
    label: 'Uploads',
    image: '/brand-assets/generic/uploads.svg?v=3',
    connectorIds: ['uploads'],
    capabilityTags: ['Files', 'Context'],
    summary: 'Uploads are local workspace files Sage can use without a third-party account.',
    setupHint: 'Upload files from chat or Plugins when this lane is enabled.',
    surfaceScope: 'all',
  },
  {
    id: 'websites',
    label: 'Websites',
    image: '/brand-assets/generic/websites.svg?v=3',
    connectorIds: ['websites'],
    capabilityTags: ['Web', 'Retrieval'],
    summary: 'Websites let Sage use approved public pages or domains as knowledge sources.',
    setupHint: 'Add websites when Sage needs stable product docs, support docs, or public references.',
    surfaceScope: 'all',
  },
  {
    id: 'microsoft_365',
    label: 'Microsoft 365',
    image: '/brand-assets/apps/microsoft365.svg?v=3',
    connectorIds: ['microsoft_365'],
    capabilityTags: ['Mail', 'Calendar'],
    summary: 'Microsoft 365 lets Sage work with Outlook mail and calendar in one connection.',
    setupHint: 'Connect Microsoft 365 when your email and calendar live in Outlook.',
    surfaceScope: 'all',
  },
  {
    id: 'webhook',
    label: 'Webhook',
    image: '/brand-assets/generic/webhook.svg?v=3',
    connectorIds: ['webhook'],
    capabilityTags: ['App action', 'Automation'],
    summary: 'App actions let Sage notify or start work in another system without a full app connection.',
    setupHint: 'Add an app action when you need lightweight automation.',
    surfaceScope: 'all',
  },
  {
    id: 'dropbox',
    label: 'Dropbox',
    image: '/brand-assets/apps/dropbox.svg?v=3',
    connectorIds: ['dropbox'],
    capabilityTags: ['Files', 'Folders'],
    summary: 'Dropbox lets Sage list folders, search files, create shared links, and run approved storage actions.',
    setupHint: 'Connect Dropbox when Sage should work with shared files and folders.',
    surfaceScope: 'all',
  },
  {
    id: 'figma',
    label: 'Figma',
    image: '/brand-assets/apps/figma.ico?v=3',
    connectorIds: ['figma'],
    capabilityTags: ['Design files', 'Comments'],
    summary: 'Figma lets Sage use design files, metadata, and comments for product work.',
    setupHint: 'Connect Figma when Sage should read design context and comments.',
    surfaceScope: 'all',
  },
  {
    id: 'todoist',
    label: 'Todoist',
    image: '/brand-assets/apps/todoist.ico?v=3',
    connectorIds: ['todoist'],
    capabilityTags: ['Tasks', 'Projects'],
    summary: 'Todoist lets Sage read and update personal tasks and project lists.',
    setupHint: 'Connect Todoist when Sage should manage personal tasks.',
    surfaceScope: 'all',
  },
  {
    id: 'airtable',
    label: 'Airtable',
    image: '/brand-assets/apps/airtable.png?v=3',
    connectorIds: ['airtable'],
    capabilityTags: ['Bases', 'Records'],
    summary: 'Airtable lets Sage read bases, inspect schema, and update records with approval.',
    setupHint: 'Connect Airtable when Sage should work with structured records and bases.',
    surfaceScope: 'all',
  },
  {
    id: 'canva',
    label: 'Canva',
    image: '/brand-assets/apps/canva.ico?v=3',
    connectorIds: ['canva'],
    capabilityTags: ['Designs', 'Assets'],
    summary: 'Canva lets Sage use design metadata, folders, and assets in creative workflows.',
    setupHint: 'Connect Canva when Sage should work with Canva designs and brand assets.',
    surfaceScope: 'all',
  },
  {
    id: 'asana',
    label: 'Asana',
    image: '/brand-assets/apps/asana.ico?v=3',
    connectorIds: ['asana'],
    capabilityTags: ['Tasks', 'Projects'],
    summary: 'Asana lets Sage read tasks, projects, and workspaces, then prepare approved project updates.',
    setupHint: 'Connect Asana when Sage should help manage project work.',
    surfaceScope: 'all',
  },
  {
    id: 'hubspot',
    label: 'HubSpot',
    image: '/brand-assets/apps/hubspot.png?v=3',
    connectorIds: ['hubspot'],
    capabilityTags: ['CRM', 'Sales'],
    summary: 'HubSpot lets Sage work with contacts, companies, deals, and approved follow-up workflows.',
    setupHint: 'Connect HubSpot when Sage should help with CRM and sales operations.',
    surfaceScope: 'all',
  },
  {
    id: 'zoom',
    label: 'Zoom',
    image: '/brand-assets/apps/zoom.ico?v=3',
    connectorIds: ['zoom'],
    capabilityTags: ['Meetings', 'Profile'],
    summary: 'Zoom lets Sage use meeting account context and prepare approved meeting workflows.',
    setupHint: 'Connect Zoom when Sage should help with meeting operations.',
    surfaceScope: 'all',
  },
  {
    id: 'calendly',
    label: 'Calendly',
    image: '/brand-assets/apps/calendly.ico?v=3',
    connectorIds: ['calendly'],
    capabilityTags: ['Scheduling', 'Events'],
    summary: 'Calendly lets Sage use scheduling links, event types, and booking context.',
    setupHint: 'Connect Calendly when Sage should coordinate scheduling workflows.',
    surfaceScope: 'all',
  },
  {
    id: 'clickup',
    label: 'ClickUp',
    image: '/brand-assets/apps/clickup.png?v=3',
    connectorIds: ['clickup'],
    capabilityTags: ['Tasks', 'Lists'],
    summary: 'ClickUp lets Sage read tasks, lists, and workspaces, then prepare approved project updates.',
    setupHint: 'Connect ClickUp when Sage should help manage project execution.',
    surfaceScope: 'all',
  },
  {
    id: 'jira',
    label: 'Jira',
    image: '/brand-assets/apps/jira.ico?v=3',
    connectorIds: ['jira'],
    capabilityTags: ['Issues', 'Projects'],
    summary: 'Jira lets Sage read issues, projects, comments, and prepare approved project updates.',
    setupHint: 'Connect Jira when Sage should help manage engineering and product work.',
    surfaceScope: 'all',
  },
  {
    id: 'stripe',
    label: 'Stripe',
    image: '/brand-assets/apps/stripe.ico?v=3',
    connectorIds: ['stripe'],
    capabilityTags: ['Payments', 'Revenue'],
    summary: 'Stripe lets Sage use account, customer, payment, invoice, and revenue context.',
    setupHint: 'Connect Stripe when Sage should help understand payments and business operations.',
    surfaceScope: 'all',
  },
  {
    id: 'salesforce',
    label: 'Salesforce',
    image: '/brand-assets/apps/salesforce.ico?v=3',
    connectorIds: ['salesforce'],
    capabilityTags: ['CRM', 'Sales'],
    summary: 'Salesforce lets Sage work with CRM records, accounts, leads, and approved sales workflows.',
    setupHint: 'Connect Salesforce when Sage should support CRM and sales operations.',
    surfaceScope: 'all',
  },
  {
    id: 'webflow',
    label: 'Webflow',
    image: '/brand-assets/apps/webflow.ico?v=3',
    connectorIds: ['webflow'],
    capabilityTags: ['Sites', 'CMS'],
    summary: 'Webflow lets Sage use sites, pages, CMS, assets, and form context.',
    setupHint: 'Connect Webflow when Sage should help with website and CMS workflows.',
    surfaceScope: 'all',
  },
  {
    id: 'monday',
    label: 'monday.com',
    image: '/brand-assets/apps/monday.ico?v=3',
    connectorIds: ['monday'],
    capabilityTags: ['Boards', 'Workflows'],
    summary: 'monday.com lets Sage use boards, updates, workspaces, and approved project workflows.',
    setupHint: 'Connect monday.com when Sage should help manage operational work.',
    surfaceScope: 'all',
  },
  {
    id: 'box',
    label: 'Box',
    image: '/brand-assets/apps/box.ico?v=3',
    connectorIds: ['box'],
    capabilityTags: ['Files', 'Folders'],
    summary: 'Box lets Sage work with enterprise files, folders, and approved document workflows.',
    setupHint: 'Connect Box when Sage should help with shared files and enterprise documents.',
    surfaceScope: 'all',
  },
  {
    id: 'gitlab',
    label: 'GitLab',
    image: '/brand-assets/apps/gitlab.ico?v=3',
    connectorIds: ['gitlab'],
    capabilityTags: ['Code', 'Issues'],
    summary: 'GitLab lets Sage use repositories, merge requests, issues, and engineering context.',
    setupHint: 'Connect GitLab when Sage should help with code and project delivery.',
    surfaceScope: 'all',
  },
  {
    id: 'bitbucket',
    label: 'Bitbucket',
    image: '/brand-assets/apps/bitbucket.ico?v=3',
    connectorIds: ['bitbucket'],
    capabilityTags: ['Code', 'Pull requests'],
    summary: 'Bitbucket lets Sage use repositories, pull requests, issues, and approved code workflows.',
    setupHint: 'Connect Bitbucket when Sage should help with Atlassian code workflows.',
    surfaceScope: 'all',
  },
  {
    id: 'confluence',
    label: 'Confluence',
    image: '/brand-assets/apps/confluence.ico?v=3',
    connectorIds: ['confluence'],
    capabilityTags: ['Docs', 'Knowledge'],
    summary: 'Confluence lets Sage use spaces, pages, knowledge, and approved content workflows.',
    setupHint: 'Connect Confluence when Sage should use team knowledge and docs.',
    surfaceScope: 'all',
  },
  {
    id: 'miro',
    label: 'Miro',
    image: '/brand-assets/apps/miro.png?v=3',
    connectorIds: ['miro'],
    capabilityTags: ['Boards', 'Planning'],
    summary: 'Miro lets Sage use boards, whiteboards, and approved planning workflows.',
    setupHint: 'Connect Miro when Sage should help with planning and visual collaboration.',
    surfaceScope: 'all',
  },
  {
    id: 'mailchimp',
    label: 'Mailchimp',
    image: '/brand-assets/apps/mailchimp.ico?v=3',
    connectorIds: ['mailchimp'],
    capabilityTags: ['Marketing', 'Campaigns'],
    summary: 'Mailchimp lets Sage use audiences, campaigns, reports, and approved marketing workflows.',
    setupHint: 'Connect Mailchimp when Sage should help with marketing operations.',
    surfaceScope: 'all',
  },
  {
    id: 'pipedrive',
    label: 'Pipedrive',
    image: '/brand-assets/apps/pipedrive.ico?v=3',
    connectorIds: ['pipedrive'],
    capabilityTags: ['CRM', 'Deals'],
    summary: 'Pipedrive lets Sage use deals, contacts, activities, and approved sales workflows.',
    setupHint: 'Connect Pipedrive when Sage should help with sales pipeline work.',
    surfaceScope: 'all',
  },
  {
    id: 'intercom',
    label: 'Intercom',
    image: '/brand-assets/apps/intercom.ico?v=3',
    connectorIds: ['intercom'],
    capabilityTags: ['Support', 'Conversations'],
    summary: 'Intercom lets Sage use conversations, users, tickets, and approved support workflows.',
    setupHint: 'Connect Intercom when Sage should help with customer support operations.',
    surfaceScope: 'all',
  },
  {
    id: 'docusign',
    label: 'Docusign',
    image: '/brand-assets/apps/docusign.ico?v=3',
    connectorIds: ['docusign'],
    capabilityTags: ['Documents', 'Signatures'],
    summary: 'Docusign lets Sage use envelopes, signing status, and approved document workflows.',
    setupHint: 'Connect Docusign when Sage should help with signing workflows.',
    surfaceScope: 'all',
  },
  {
    id: 'square',
    label: 'Square',
    image: '/brand-assets/apps/square.ico?v=3',
    connectorIds: ['square'],
    capabilityTags: ['Payments', 'Orders'],
    summary: 'Square lets Sage use seller profile, customers, orders, payments, and invoices.',
    setupHint: 'Connect Square when Sage should help with seller and payment operations.',
    surfaceScope: 'all',
  },
  {
    id: 'typeform',
    label: 'Typeform',
    image: '/brand-assets/apps/typeform.ico?v=3',
    connectorIds: ['typeform'],
    capabilityTags: ['Forms', 'Responses'],
    summary: 'Typeform lets Sage use forms, responses, accounts, and approved form workflows.',
    setupHint: 'Connect Typeform when Sage should help with surveys and intake forms.',
    surfaceScope: 'all',
  },
  {
    id: 'quickbooks',
    label: 'QuickBooks',
    image: '/brand-assets/apps/quickbooks.png?v=3',
    connectorIds: ['quickbooks'],
    capabilityTags: ['Accounting', 'Invoices'],
    summary: 'QuickBooks lets Sage use accounting context, customers, invoices, and finance workflows.',
    setupHint: 'Connect QuickBooks when Sage should help with accounting operations.',
    surfaceScope: 'all',
  },
  {
    id: 'xero',
    label: 'Xero',
    image: '/brand-assets/apps/xero.ico?v=3',
    connectorIds: ['xero'],
    capabilityTags: ['Accounting', 'Contacts'],
    summary: 'Xero lets Sage use accounting organisations, contacts, invoices, and finance workflows.',
    setupHint: 'Connect Xero when Sage should help with accounting and finance work.',
    surfaceScope: 'all',
  },
  {
    id: 'freshbooks',
    label: 'FreshBooks',
    image: '/brand-assets/apps/freshbooks.ico?v=3',
    connectorIds: ['freshbooks'],
    capabilityTags: ['Accounting', 'Clients'],
    summary: 'FreshBooks lets Sage use clients, invoices, expenses, and accounting workflows.',
    setupHint: 'Connect FreshBooks when Sage should help with small-business accounting.',
    surfaceScope: 'all',
  },
  {
    id: 'vercel',
    label: 'Vercel',
    image: '/brand-assets/apps/vercel.ico?v=3',
    connectorIds: ['vercel'],
    capabilityTags: ['Deployments', 'Projects'],
    summary: 'Vercel lets Sage use account identity, projects, deployments, and developer workflows.',
    setupHint: 'Connect Vercel when Sage should help with web app delivery.',
    surfaceScope: 'all',
  },
  {
    id: 's3',
    label: 'Amazon S3',
    image: '/brand-assets/apps/aws-s3.svg?v=3',
    connectorIds: ['s3'],
    capabilityTags: ['Buckets', 'Objects'],
    summary: 'Amazon S3 lets Sage inspect buckets, work with objects, and run approved storage actions.',
    setupHint: 'Connect Amazon S3 when Sage should work with cloud storage.',
    surfaceScope: 'all',
  },
  {
    id: 'smtp',
    label: 'SMTP / IMAP Email',
    image: '/brand-assets/generic/email.svg?v=3',
    connectorIds: ['smtp'],
    capabilityTags: ['Send email', 'Fetch inbox'],
    summary: 'SMTP / IMAP gives Sage direct mailbox send and fetch actions without turning email into a live inbound channel.',
    setupHint: 'Connect SMTP / IMAP when you need a custom mailbox outside Google or Microsoft.',
    surfaceScope: 'all',
  },
  {
    id: 'wechat_work',
    label: 'WeChat Work',
    image: '/brand-assets/channels/wechat.svg?v=3',
    connectorIds: ['wechat_work'],
    capabilityTags: ['Webhook', 'Messages'],
    summary: 'WeChat Work lets Sage send approved outbound workspace messages through a configured webhook.',
    setupHint: 'Connect a WeChat Work webhook when Sage should notify a workspace channel.',
    surfaceScope: 'all',
  },
  {
    id: 'instagram_business',
    label: 'Instagram Business',
    image: '/brand-assets/apps/instagram.svg?v=3',
    connectorIds: ['instagram_business'],
    capabilityTags: ['Comments', 'DMs'],
    summary: 'Instagram Business lets Sage send approved comment replies and direct messages through the Graph API.',
    setupHint: 'Connect Instagram Business when Sage should assist with social replies.',
    surfaceScope: 'all',
  },
];

type ConnectorDetailEntry = {
  description: string;
  examplePrompts: string[];
  suggestedToolNames: string[];
  /** Official MCP server endpoint for this app (from APP_MCP_SERVER_MAP in connection_oauth_service.py).
   *  When set, the Connect button triggers OAuth → credential vault → MCP server registration.
   *  When undefined, the app uses credential vault only (no MCP tool discovery). */
  mcpEndpoint?: string;
};

const CONNECTOR_DETAIL_MAP: Record<string, ConnectorDetailEntry> = {
  gmail: {
    description:
      'Connect Gmail to let your agent read, search, draft, and send emails on your behalf. Your agent can triage your inbox, find important threads, compose replies for your approval, and keep you on top of follow-ups — all without leaving your workflow.',
    examplePrompts: [
      'Search my Gmail for emails from last week about the product launch',
      'Summarize my unread emails from my team since yesterday',
      'Draft a reply to the latest email from Sarah about the budget review',
      'Find any emails with attachments related to the Q3 report',
    ],
    suggestedToolNames: ['gmail.search', 'gmail.read', 'gmail.send', 'gmail.draft', 'gmail.modify', 'gmail.label'],
    mcpEndpoint: 'https://gmailmcp.googleapis.com/mcp/v1',
  },
  google_calendar: {
    description:
      'Connect Google Calendar so your agent can check your schedule, create events, find open slots, and help you prepare for meetings. It reads your availability and can propose times, send invites, and summarize your day.',
    examplePrompts: [
      'What meetings do I have tomorrow?',
      'Create a 30-minute event for project sync next Tuesday at 2pm',
      'Find a free hour this week for a team brainstorm',
      'Cancel my 3pm meeting and notify attendees',
    ],
    suggestedToolNames: ['calendar.list', 'calendar.create', 'calendar.update', 'calendar.delete', 'calendar.search'],
    mcpEndpoint: 'https://calendarmcp.googleapis.com/mcp/v1',
  },
  github: {
    description:
      'Connect GitHub to let your agent read repositories, triage issues, review pull requests, and draft code changes. It can search across your repos, summarize PR discussions, and help you stay on top of engineering work.',
    examplePrompts: [
      'Show me open pull requests that need my review',
      'Search for issues labeled "bug" created this week',
      'Summarize the discussion on PR #342',
      'Create a new issue for tracking the API migration',
    ],
    suggestedToolNames: ['search_repositories', 'get_issue', 'create_issue', 'list_pull_requests', 'get_pull_request', 'search_code'],
    mcpEndpoint: 'https://api.githubcopilot.com/mcp/',
  },
  notion: {
    description:
      'Connect Notion to let your agent search, read, and create pages across your workspace. It can find notes, summarize docs, update databases, and create new pages from conversations — turning your Notion workspace into an extension of your agent.',
    examplePrompts: [
      'Search my Notion workspace for notes about the design system',
      'Summarize the project spec page for the new onboarding flow',
      'Create a new page in my tasks database for the Q4 planning',
      'Find all pages tagged with "engineering" updated this month',
    ],
    suggestedToolNames: ['notion.search', 'notion.read_page', 'notion.create_page', 'notion.update_page', 'notion.query_database'],
    mcpEndpoint: 'https://mcp.notion.com/mcp',
  },
  linear: {
    description:
      'Connect Linear to let your agent read, create, and update issues across your teams. It can triage incoming bugs, summarize sprint progress, create tasks from conversations, and help you keep engineering work organized.',
    examplePrompts: [
      'Show me high-priority issues assigned to me this sprint',
      'Create a new bug from this conversation and assign it to the platform team',
      'What issues have been stuck in review for more than 3 days?',
      'Summarize the status of the current cycle',
    ],
    suggestedToolNames: ['linear.search_issues', 'linear.get_issue', 'linear.create_issue', 'linear.update_issue', 'linear.get_teams'],
    mcpEndpoint: 'https://mcp.linear.app/mcp',
  },
  slack: {
    description:
      'Connect Slack to let your agent read channels, search messages, and send updates. It can summarize threads, surface decisions from channel history, draft messages for your review, and keep you connected to your team without switching contexts.',
    examplePrompts: [
      'Summarize the #engineering channel from the last 2 hours',
      'Search Slack for messages about the deployment from yesterday',
      'Draft a status update for the #general channel',
      'Find messages where someone asked me a question I haven\'t replied to',
    ],
    suggestedToolNames: ['slack.search_messages', 'slack.read_channel', 'slack.send_message', 'slack.list_channels', 'slack.get_user'],
    mcpEndpoint: 'https://mcp.slack.com/mcp',
  },
  figma: {
    description:
      'Connect Figma to let your agent read design files, extract components, and surface design context. When you discuss features or review UI, your agent can pull in the latest designs, find relevant components, and reference design decisions.',
    examplePrompts: [
      'Find my latest Figma designs for the onboarding flow',
      'Extract the color tokens from the design system file',
      'Show me recent comments on the checkout page design',
      'List all components in the shared library',
    ],
    suggestedToolNames: ['figma.get_file', 'figma.get_comments', 'figma.search_files', 'figma.get_me', 'figma.get_file_nodes'],
    mcpEndpoint: 'https://mcp.figma.com/mcp',
  },
  canva: {
    description:
      'Connect Canva to let your agent search, read, and manage your designs and brand assets. It can find templates, browse folders, export designs, and help you keep brand materials organized and accessible.',
    examplePrompts: [
      'Find my Canva designs related to the marketing campaign',
      'Search for brand templates in the shared folder',
      'Export the latest version of the social media banner',
      'List all designs in the product launch folder',
    ],
    suggestedToolNames: ['canva.search', 'canva.read_design', 'canva.export', 'canva.list_folders', 'canva.create_folder'],
  },
  dropbox: {
    description:
      'Connect Dropbox to let your agent browse, search, and manage your files and folders. It can find documents, create shared links, check file metadata, and help you organize your cloud storage without opening the Dropbox app.',
    examplePrompts: [
      'Find the latest version of the contract in Dropbox',
      'List all files shared with me this week',
      'Create a shared link for the Q3 report',
      'Search my Dropbox for any files named "proposal"',
    ],
    suggestedToolNames: ['dropbox.list_folder', 'dropbox.search', 'dropbox.get_file', 'dropbox.create_folder', 'dropbox.share'],
    mcpEndpoint: 'https://mcp.dropbox.com/mcp',
  },
  todoist: {
    description:
      'Connect Todoist to let your agent manage your tasks and projects. It can create tasks from conversations, reschedule overdue items, summarize your day, and help you stay organized — turning natural language into structured task management.',
    examplePrompts: [
      'Add "Review Q3 budget" to my tasks for tomorrow',
      'What tasks are overdue this week?',
      'Move all tasks from the old project to the new one',
      'Show me tasks labeled "urgent" across all projects',
    ],
    suggestedToolNames: ['todoist.get_tasks', 'todoist.create_task', 'todoist.update_task', 'todoist.get_projects', 'todoist.quick_add'],
    mcpEndpoint: 'https://ai.todoist.net/mcp',
  },
};

const CONNECTOR_AUTH_FIELD_FALLBACKS: Record<string, string[]> = {
  s3: ['aws_access_key_id', 'aws_secret_access_key', 'region'],
  smtp: ['host', 'port', 'username', 'password', 'use_tls'],
  whatsapp_twilio: ['account_sid', 'auth_token', 'from_number', 'to_number'],
  wechat_work: ['webhook_url'],
  instagram_business: ['access_token', 'instagram_account_id', 'page_id'],
  discord_bot: ['bot_token', 'application_id', 'public_key', 'channel_id', 'guild_id'],
  slack: ['bot_token', 'user_token', 'team_id', 'channel_id'],
};

const MANAGED_OAUTH_PROVIDER_IDS = new Set([
  'google_workspace',
  'gmail',
  'google_calendar',
  'google_drive',
  'microsoft_365',
  'outlook',
  'github',
  'notion',
  'linear',
  'dropbox',
  'figma',
  'todoist',
  'airtable',
  'canva',
  'asana',
  'hubspot',
  'zoom',
  'calendly',
  'clickup',
  'jira',
  'stripe',
  'salesforce',
  'webflow',
  'monday',
  'box',
  'gitlab',
  'bitbucket',
  'confluence',
  'miro',
  'mailchimp',
  'pipedrive',
  'intercom',
  'docusign',
  'square',
  'typeform',
  'quickbooks',
  'xero',
  'freshbooks',
  'vercel',
  'discord_bot',
  'slack',
]);

const CONNECTOR_STATIC_LOCKED_IDS = new Set<string>();

const TECHNICAL_APP_CARD_IDS = new Set([
  's3',
  'smtp',
  'wechat_work',
  'webhook',
]);

const PROVIDER_MANAGED_CHANNEL_CARD_IDS = new Set([
  'whatsapp_twilio',
]);

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function normalizeIntegrationCategoryId(value: unknown): IntegrationWorkbenchCategoryId | null {
  const token = readString(value).toLowerCase().replace(/-/g, '_');
  switch (token) {
    case 'ai':
    case 'ai_accounts':
    case 'ai_runtime':
      return 'ai_runtime';
    case 'connectors':
    case 'connections':
    case 'connection':
      return 'apps';
    case 'recipes':
    case 'recipe':
    case 'workflows':
    case 'automations':
      return 'apps';
    case 'channels':
    case 'personal_messaging':
    case 'apps_messaging':
      return 'channels';
    case 'agent_computer':
    case 'agent-computer':
    case 'computer':
    case 'hardware':
      return 'advanced';
    case 'apps':
    case 'app_connections':
    case 'connected_apps':
      return 'apps';
    case 'skills':
    case 'knowledge':
    case 'sources':
      return 'advanced';
    case 'plugins':
    case 'extensions':
    case 'developer':
    case 'advanced':
      return 'advanced';
    default:
      return null;
  }
}

function readNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readInteger(value: unknown, fallback = 0): number {
  const parsed = readNumber(value, fallback);
  return Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : fallback;
}

function readOptionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => readString(item))
    .filter(Boolean);
}

function normalizeProviderAuthModes(value: unknown): ProviderAuthModeSnapshot[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
    const id = readString(record.id).toLowerCase();
    if (!id) {
      return [];
    }
    return [{
      id,
      label: readString(record.label, id),
      secretRequired: record.secret_required !== false,
    }];
  });
}

function chunkItems<T>(items: T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    rows.push(items.slice(index, index + size));
  }
  return rows;
}

function normalizeProviderCatalog(payload: unknown): ProviderSnapshot[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const providers = Array.isArray(record.providers)
    ? record.providers.filter((item): item is ProviderCatalogRecord => Boolean(item) && typeof item === 'object')
    : [];

  return providers.flatMap((provider) => {
    const id = readString(provider.id).toLowerCase();
    if (!id || readString(provider.kind).toLowerCase() !== 'provider') {
      return [];
    }
    const hidden = provider.hidden === true;
    const sageVisible = provider.sage_visible !== false;
    if (hidden || !sageVisible) {
      return [];
    }
    return [{
      id,
      label: readString(provider.label, id),
      state: readString(provider.state, 'unknown'),
      usable: provider.usable === true,
      active: provider.active === true,
      hidden,
      sageVisible,
      studioVisible: provider.studio_visible === true,
      localOnly: provider.local_only === true,
      defaultModel: readOptionalString(provider.default_model),
      defaultAuthMode: readOptionalString(provider.default_auth_mode),
      authModes: normalizeProviderAuthModes(provider.auth_modes),
      providerScopes: readStringList(provider.provider_scopes),
      activeSource: readOptionalString(provider.runtime_active_source) ?? readOptionalString(provider.connection_active_source),
      credentialPlane: readOptionalString(provider.credential_plane),
      credentialPlaneLabel: readOptionalString(provider.credential_plane_label),
      credentialOwnerKind: readOptionalString(provider.credential_owner_kind),
      workspaceConnected: provider.workspace_connected === true,
      hostedSageAiPolicy: readOptionalString(provider.hosted_sage_ai_policy),
      stateDetail: readOptionalString(provider.runtime_state_detail) ?? readOptionalString(provider.connection_state_detail),
      modelsSyncedAt: readOptionalString(provider.models_synced_at),
      modelsError: readOptionalString(provider.models_error),
      models: Array.isArray(provider.models)
        ? provider.models.filter((item): item is ProviderCatalogModelRecord => Boolean(item) && typeof item === 'object')
        : [],
    }];
  });
}

function normalizeHostedSageAi(payload: unknown): HostedSageAiSnapshot {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const hosted = record.hosted_sage_ai && typeof record.hosted_sage_ai === 'object'
    ? record.hosted_sage_ai as Record<string, unknown>
    : {};
  if (!hosted || Object.keys(hosted).length === 0) {
    return DEFAULT_HOSTED_SAGE_AI;
  }
  const monthlyCapUsd = readNumber(hosted.monthly_cap_usd, 0);
  const monthlyCostUsd = readNumber(hosted.monthly_cost_usd, 0);
  const monthlyRemainingUsd = readNumber(hosted.monthly_remaining_usd, 0);
  const creditsPerUsd = readInteger(hosted.credits_per_usd, 1000);
  return {
    allowed: hosted.allowed === true,
    planAllowsHostedAi: hosted.plan_allows_hosted_ai === true,
    policy: readString(hosted.policy, DEFAULT_HOSTED_SAGE_AI.policy),
    reason: readOptionalString(hosted.reason),
    message: readOptionalString(hosted.message),
    monthlyCapUsd,
    monthlyCostUsd,
    monthlyRemainingUsd,
    creditsPerUsd,
    monthlyCreditCap: readInteger(hosted.monthly_credit_cap, monthlyCapUsd * creditsPerUsd),
    monthlyCreditsUsed: readInteger(hosted.monthly_credits_used, monthlyCostUsd * creditsPerUsd),
    monthlyCreditsRemaining: readInteger(hosted.monthly_credits_remaining, monthlyRemainingUsd * creditsPerUsd),
  };
}

function formatCredits(value: number): string {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(Math.max(0, Math.round(Number.isFinite(value) ? value : 0)));
}

function formatRelativeTimestamp(value: unknown): string {
  const token = readString(value);
  if (!token) {
    return 'No activity yet';
  }
  const parsed = Date.parse(token);
  if (!Number.isFinite(parsed)) {
    return token;
  }
  const deltaMs = Date.now() - parsed;
  const future = deltaMs < 0;
  const deltaMinutes = Math.round(Math.abs(deltaMs) / 60000);
  if (deltaMinutes < 1) {
    return future ? 'In under a minute' : 'Just now';
  }
  if (deltaMinutes < 60) {
    return future ? `In ${deltaMinutes} min` : `${deltaMinutes} min ago`;
  }
  const deltaHours = Math.round(deltaMinutes / 60);
  if (deltaHours < 24) {
    return future ? `In ${deltaHours} hr` : `${deltaHours} hr ago`;
  }
  const deltaDays = Math.round(deltaHours / 24);
  return future ? `In ${deltaDays} day${deltaDays === 1 ? '' : 's'}` : `${deltaDays} day${deltaDays === 1 ? '' : 's'} ago`;
}

function gatewayConnectionStatus(gateway: GatewayRegistrationRecord | null): string {
  return readString(gateway?.connection_status || gateway?.status).toLowerCase();
}

function gatewayIsOnline(gateway: GatewayRegistrationRecord | null): boolean {
  const connectionStatus = gatewayConnectionStatus(gateway);
  if (['online', 'connected'].includes(connectionStatus)) {
    return true;
  }
  const heartbeatFresh = gateway?.['heartbeat_fresh'];
  if (heartbeatFresh === true && readString(gateway?.status).toLowerCase() === 'active') {
    return true;
  }
  return false;
}

function gatewayDisplayName(gateway: GatewayRegistrationRecord | null): string {
  return readString(
    gateway?.display_name
      || gateway?.['name']
      || gateway?.['hostname']
      || gateway?.['device_name']
      || gateway?.gateway_id,
    'Agent Computer',
  );
}

function describeGatewaySelectionRepair(summary: GatewaySelectionSummary): string {
  if (!summary.selectedGatewayMissing) {
    if (summary.selectedGatewayOffline && summary.repairGateway) {
      return `Agent Computer offline — connect Hardware first.`;
    }
    return '';
  }
  if (summary.repairGateway) {
    return `${gatewayDisplayName(summary.repairGateway)} is online, but it is not selected for Sage yet.`;
  }
  return 'The selected Agent Computer is not available. Choose an online computer before using personal channels.';
}

function describeAvailableGatewayChoice(summary: GatewaySelectionSummary): string {
  if (summary.repairGateway) {
    return `${gatewayDisplayName(summary.repairGateway)} is online. Choose it as Sage Agent Computer before using personal channels.`;
  }
  return 'Choose Sage Agent Computer before using personal channels.';
}

function latestPersonalChannelActivity(gateway: GatewayRegistrationRecord | null, payload: PersonalChannelViewPayload | null): string {
  const state = payload?.state && typeof payload.state === 'object' ? payload.state : null;
  const recentMessages = Array.isArray(payload?.recent_messages) ? payload.recent_messages : [];
  const latestMessage = recentMessages
    .map((item) => readString(item.created_at || item.ts || item.timestamp))
    .filter(Boolean)
    .sort((left, right) => right.localeCompare(left))[0];
  return formatRelativeTimestamp(latestMessage || state?.connected_at || gateway?.last_seen_at);
}

function defaultPersonalChannelDraft(channel: PersonalCommunicationChannel): PersonalChannelDraft {
  return {
    phoneNumber: '',
    apiId: '',
    apiHash: '',
    loginCode: '',
    password: '',
    recipient: '',
    text: channel === 'telegram'
      ? 'Empyralis Telegram test from Agent Computer.'
      : 'Empyralis WhatsApp test from Agent Computer.',
  };
}

function describeHostedSageAi(hostedSageAi: HostedSageAiSnapshot, hostedProviderCard: ProviderCardRecord | null): string {
  if (hostedSageAi.allowed && hostedProviderCard) {
    return `${hostedAiTierLabel(hostedProviderCard)} · Workspace AI · ${formatCredits(hostedSageAi.monthlyCreditsRemaining)} / ${formatCredits(hostedSageAi.monthlyCreditCap)} monthly usage left`;
  }
  if (hostedSageAi.allowed) {
    return `${formatCredits(hostedSageAi.monthlyCreditsRemaining)} / ${formatCredits(hostedSageAi.monthlyCreditCap)} monthly usage left · hosted model not configured`;
  }
  if (hostedSageAi.reason === 'owner_approval_required') {
    return 'Workspace AI needs workspace owner approval before use.';
  }
  if (hostedSageAi.reason === 'cap_reached') {
    return "You've reached your AI limit.";
  }
  if (hostedSageAi.monthlyCreditCap > 0) {
    return `${formatCredits(Math.max(0, hostedSageAi.monthlyCreditsRemaining))} / ${formatCredits(hostedSageAi.monthlyCreditCap)} monthly usage left`;
  }
  return 'Workspace AI is not active yet. Configure workspace AI or connect your own AI account.';
}

function hostedCreditUsageLabel(hostedSageAi: HostedSageAiSnapshot): string {
  if (hostedSageAi.monthlyCreditCap <= 0) {
    return 'No workspace AI limit configured';
  }
  return `${formatCredits(hostedSageAi.monthlyCreditsRemaining)} / ${formatCredits(hostedSageAi.monthlyCreditCap)} monthly usage left`;
}

function hostedProviderDetailLabel(hostedSageAi: HostedSageAiSnapshot, hostedProviderCard: ProviderCardRecord): string {
  return `${hostedAiTierLabel(hostedProviderCard)} · Workspace AI · ${hostedCreditUsageLabel(hostedSageAi)}`;
}

function fallbackProviderCatalog(): ProviderSnapshot[] {
  return SAMPLE_PROVIDER_IDS.map((id) => ({
    id,
    label: SAMPLE_PROVIDER_LABELS[id] ?? id,
    state: 'unknown',
    usable: false,
    active: false,
    hidden: false,
    sageVisible: true,
    studioVisible: id !== 'openai-codex' && id !== 'ollama',
    localOnly: id === 'ollama',
    defaultModel: null,
    defaultAuthMode: id === 'openai-codex' ? 'oauth_token' : id === 'ollama' ? 'none' : 'api_key',
    authModes: [],
    providerScopes: [],
    activeSource: null,
    credentialPlane: null,
    credentialPlaneLabel: null,
    credentialOwnerKind: null,
    workspaceConnected: false,
    hostedSageAiPolicy: null,
    stateDetail: null,
    modelsSyncedAt: null,
    modelsError: null,
    models: [],
  }));
}

function normalizeProviderProfiles(payload: unknown): ProviderProfileRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return Array.isArray(record.items)
    ? record.items.filter((item): item is ProviderProfileRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeVaultCredentials(payload: unknown): VaultCredentialRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return Array.isArray(record.items)
    ? record.items.filter((item): item is VaultCredentialRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeMcpServers(payload: unknown): McpServerRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return Array.isArray(record.items)
    ? record.items.filter((item): item is McpServerRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeProviderModels(payload: unknown): ProviderCatalogModelRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const models = Array.isArray(record.models) ? record.models : [];
  return models
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .map((id) => ({
      id,
      label: id,
      provider: 'ollama',
    }));
}

function sortProfiles(profiles: ProviderProfileRecord[]): ProviderProfileRecord[] {
  return [...profiles].sort((left, right) => {
    const leftPriority = Number(left.priority ?? 100);
    const rightPriority = Number(right.priority ?? 100);
    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }
    return readString(left.id).localeCompare(readString(right.id));
  });
}

function profileMetadataRecord(profile: ProviderProfileRecord | null | undefined): Record<string, unknown> {
  return profile && typeof profile.metadata === 'object' && profile.metadata
    ? profile.metadata as Record<string, unknown>
    : {};
}

function sortCredentials(credentials: VaultCredentialRecord[]): VaultCredentialRecord[] {
  return [...credentials].sort((left, right) =>
    readString(right.updated_at ?? right.created_at).localeCompare(readString(left.updated_at ?? left.created_at)));
}

function compactNavInitials(label: string): string {
  const words = label.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0]?.[0] ?? ''}${words[1]?.[0] ?? ''}`.toUpperCase();
  }
  return label.trim().slice(0, 2).toUpperCase();
}

function isSecretlessConnection(providerId: string, profile: ProviderProfileRecord | null): boolean {
  const authMode = readString(profile?.auth_mode).toLowerCase();
  if (authMode === 'local_cli' || authMode === 'none') {
    return true;
  }
  if (providerId === 'ollama' && readString(profile?.health).toLowerCase() !== 'disabled') {
    return true;
  }
  return false;
}

function providerAuthModeConfig(provider: ProviderSnapshot, profile: ProviderProfileRecord | null = null): ProviderAuthModeSnapshot | null {
  const activeMode = readString(profile?.auth_mode || provider.defaultAuthMode).toLowerCase();
  if (activeMode) {
    const match = provider.authModes.find((item) => item.id === activeMode);
    if (match) {
      return match;
    }
  }
  return provider.authModes[0] ?? null;
}

function providerRequiresSecret(provider: ProviderSnapshot, profile: ProviderProfileRecord | null = null): boolean {
  const mode = providerAuthModeConfig(provider, profile);
  if (mode) {
    return mode.secretRequired;
  }
  const authMode = readString(profile?.auth_mode || provider.defaultAuthMode).toLowerCase();
  return authMode !== 'none' && authMode !== 'local_cli';
}

function providerCredentialLabel(provider: ProviderSnapshot, profile: ProviderProfileRecord | null = null): string {
  const mode = providerAuthModeConfig(provider, profile);
  if (mode?.label) {
    return mode.label;
  }
  return providerRequiresSecret(provider, profile) ? 'Connection detail' : 'No connection detail required';
}

function providerCredentialPlaceholder(provider: ProviderSnapshot): string {
  const authMode = readString(provider.defaultAuthMode).toLowerCase();
  if (authMode === 'none' || authMode === 'local_cli') {
    return '';
  }
  return 'Paste connection detail';
}

function providerNeedsBaseUrl(provider: ProviderSnapshot): boolean {
  return provider.id === 'custom_openai_compatible' || provider.id === 'azure_openai';
}

function providerBaseUrlLabel(provider: ProviderSnapshot): string {
  return provider.id === 'azure_openai' ? 'Azure endpoint or compatible base URL' : 'Base URL';
}

function providerBaseUrlPlaceholder(provider: ProviderSnapshot): string {
  return provider.id === 'azure_openai'
    ? 'https://your-resource.openai.azure.com/openai/v1'
    : 'https://api.example.com/v1';
}

function providerNeedsGateway(providerId: string): boolean {
  return providerId === 'ollama' || providerId === 'openai-codex';
}

function providerIsPersonalSubscriptionTransport(record: ProviderCardRecord | null): boolean {
  if (!record) {
    return false;
  }
  const providerId = readString(record.provider.id).toLowerCase();
  return providerId === 'openai-codex';
}

function providerIsLocalOnly(record: ProviderCardRecord | null): boolean {
  if (!record) {
    return false;
  }
  return record.provider.localOnly === true || record.provider.id === 'ollama';
}

function providerPickerStatusLabel(record: ProviderCardRecord, localCompanionOnline: boolean): string {
  if (providerNeedsGateway(record.provider.id) && !localCompanionOnline) {
    return 'Connect the selected Agent Computer';
  }
  return record.connected ? 'Connected' : 'Not configured';
}

function providerPickerConnected(record: ProviderCardRecord, localCompanionOnline: boolean): boolean {
  if (providerNeedsGateway(record.provider.id) && !localCompanionOnline) {
    return false;
  }
  return record.connected;
}

function providerActiveModelLabel(record: ProviderCardRecord | null): string {
  if (!record) {
    return 'No model selected';
  }
  const selectedModel = readString(record.profile?.model);
  const credentialPlane = readString(record.provider.credentialPlane).toLowerCase();
  const activeSource = readString(record.provider.activeSource).toLowerCase();
  const hostedEmpyralis = credentialPlane === 'platform_runtime'
    || record.provider.hostedSageAiPolicy === 'allowed'
    || record.provider.hostedSageAiPolicy === 'enabled_with_cap'
    || activeSource.includes('platform')
    || activeSource.includes('hosted');
  if (hostedEmpyralis) {
    const tierToken = selectedModel.toLowerCase();
    if (tierToken === 'light') {
      return 'Light';
    }
    if (tierToken === 'pro') {
      return 'Pro';
    }
    if (tierToken === 'max') {
      return 'Max';
    }
  }
  return selectedModel
    || readString(record.provider.defaultModel)
    || readString(record.provider.models[0]?.label)
    || readString(record.provider.models[0]?.id)
    || 'No model selected';
}

function hostedAiTierLabel(record: ProviderCardRecord | null): string {
  const metadata = profileMetadataRecord(record?.profile ?? null);
  const explicitTier = readString(metadata.chat_model_tier || metadata.public_tier || metadata.model_tier).toLowerCase();
  const selectedModel = readString(record?.profile?.model || record?.provider.defaultModel).toLowerCase();
  if (explicitTier === 'light' || selectedModel === 'light' || selectedModel.includes('flash')) {
    return 'Light AI';
  }
  if (explicitTier === 'max' || selectedModel === 'max' || selectedModel.includes('reasoner')) {
    return 'Max AI';
  }
  return 'Pro AI';
}

function providerPathLabel(record: ProviderCardRecord): string {
  const providerId = readString(record.provider.id).toLowerCase();
  const credentialPlane = readString(record.provider.credentialPlane).toLowerCase();
  const defaultAuthMode = readString(record.provider.defaultAuthMode).toLowerCase();
  const activeSource = readString(record.provider.activeSource).toLowerCase();

  if (providerId === 'ollama') {
    return 'Agent Computer';
  }
  if (providerId === 'openai-codex') {
    return 'Personal subscription';
  }
  if (activeSource.includes('cli') || defaultAuthMode === 'local_cli' || defaultAuthMode === 'local_subscription') {
    return 'Agent Computer session';
  }
  if (providerId === 'ollama_cloud') {
    return 'Ollama Cloud';
  }
  if (
    credentialPlane === 'platform_runtime'
    || record.provider.hostedSageAiPolicy === 'allowed'
    || record.provider.hostedSageAiPolicy === 'enabled_with_cap'
    || activeSource.includes('platform')
    || activeSource.includes('hosted')
  ) {
    return 'Workspace AI';
  }
  if (
    credentialPlane === 'workspace_connection'
    || record.provider.workspaceConnected
    || Boolean(record.credential)
    || Boolean(readString(record.profile?.credential_id))
  ) {
    return 'Your AI account';
  }
  if (record.provider.localOnly === true || credentialPlane === 'local_runtime') {
    return 'Agent Computer';
  }
  return record.label;
}

function providerAvailabilityLabel(record: ProviderCardRecord | null, localCompanionOnline: boolean): string {
  if (!record) {
    return 'Not available';
  }
  if (providerPickerConnected(record, localCompanionOnline) || record.provider.usable) {
    return 'available';
  }
  return 'configurable';
}

function providerActiveSummaryLabel(
  activeProviderCard: ProviderCardRecord | null,
  hostedProviderCard: ProviderCardRecord | null,
  explicitSelectedProfile: ProviderProfileRecord | null,
): string {
  if (!activeProviderCard) {
    return 'No AI model active';
  }
  if (activeProviderCard === hostedProviderCard && !explicitSelectedProfile) {
    return `${hostedAiTierLabel(activeProviderCard)} through Workspace AI`;
  }
  return `${activeProviderCard.label} through ${providerPathLabel(activeProviderCard)}`;
}

function providerActiveSummaryDetail(
  activeProviderCard: ProviderCardRecord | null,
  hostedProviderCard: ProviderCardRecord | null,
  explicitSelectedProfile: ProviderProfileRecord | null,
  hostedSageAi: HostedSageAiSnapshot,
): string {
  if (!activeProviderCard) {
    return 'Choose an AI model before Sage can answer with hosted or connected models.';
  }
  if (activeProviderCard === hostedProviderCard && !explicitSelectedProfile) {
    return hostedProviderDetailLabel(hostedSageAi, activeProviderCard);
  }
  return `${providerActiveModelLabel(activeProviderCard)} · ${providerPathLabel(activeProviderCard)}`;
}

function workspaceAiRouteNeedsSetup(route: Record<string, unknown> | null): boolean {
  if (!route) {
    return false;
  }
  const status = readString(route.status).toLowerCase();
  const description = readString(route.description).toLowerCase();
  return (
    status === 'setup_required'
    || status === 'degraded'
    || status === 'disabled'
    || status === 'unavailable'
    || description.includes('credential resolution failed')
    || description.includes('credential id not found')
  );
}

function errorMentionsMissingCredential(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '');
  return message.toLowerCase().includes('credential id not found');
}

function errorMentionsMissingAccessToken(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '');
  const lower = message.toLowerCase();
  return lower.includes('access token')
    && (lower.includes('required') || lower.includes('missing'));
}

function humanizeConnectionSetupError(error: unknown, label = 'This connection'): string {
  const message = error instanceof Error ? error.message : String(error ?? '');
  const normalized = message.trim();
  const lower = normalized.toLowerCase();
  if (lower.includes('oauth is not configured')) {
    return `${label} sign-in is not configured in this environment yet. Add the provider OAuth app in deployment settings, then retry.`;
  }
  if (errorMentionsMissingAccessToken(error) || errorMentionsMissingCredential(error)) {
    return `${label} is not connected yet. Click Connect to sign in with the provider.`;
  }
  // Empyralis is single-user — show raw backend error instead of role-based mislabeling.
  return normalized || `${label} could not start setup.`;
}

function maskKeyTail(credential: VaultCredentialRecord | null): string | null {
  if (!credential || typeof credential.metadata !== 'object' || !credential.metadata) {
    return null;
  }
  return readOptionalString((credential.metadata as Record<string, unknown>).credential_last4);
}

function connectorAccountLabel(credential: VaultCredentialRecord | null): string | null {
  if (!credential || typeof credential.metadata !== 'object' || !credential.metadata) {
    return null;
  }
  const metadata = credential.metadata as Record<string, unknown>;
  return readOptionalString(metadata.email)
    ?? readOptionalString(metadata.account_email)
    ?? readOptionalString(metadata.account_name)
    ?? readOptionalString(metadata.account_label)
    ?? readOptionalString(metadata.team_name)
    ?? readOptionalString(metadata.workspace_name)
    ?? readOptionalString(metadata.username)
    ?? readOptionalString(metadata.handle)
    ?? maskKeyTail(credential);
}

function BrandLogo({
  id,
  label,
  src,
  failedLogos,
  onError,
}: {
  id: string;
  label: string;
  src: string;
  failedLogos: Set<string>;
  onError: (id: string) => void;
}) {
  const failed = failedLogos.has(id);
  if (failed || !src) {
    return (
      <span className="sage-integration-brand sage-integration-brand--fallback" aria-hidden="true">
        {label.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <span className="sage-integration-brand">
      <img
        src={src}
        alt={label}
        width={40}
        height={40}
        className="sage-integration-brand__image"
        onError={() => onError(id)}
      />
    </span>
  );
}

function describeProviderCard(record: ProviderCardRecord, localCompanionOnline: boolean): string {
  const modelCount = record.provider.models.length;
  const pathLabel = providerPathLabel(record);
  const modelLabel = providerActiveModelLabel(record);
  const authMode = readString(record.profile?.auth_mode).replace(/_/g, ' ');
  if (record.provider.id === 'ollama' && !localCompanionOnline) {
    return `${pathLabel} · Connect the selected Agent Computer first`;
  }
  if (record.connected) {
    if (modelLabel && modelLabel !== 'No model selected') {
      return `${pathLabel} · ${modelLabel}`;
    }
    if (modelCount > 0) {
      return `${pathLabel} · ${modelCount} model${modelCount === 1 ? '' : 's'} ready`;
    }
    if (authMode) {
      return `${pathLabel} · ${authMode}`;
    }
    if (record.provider.stateDetail) {
      return `${pathLabel} · ${record.provider.stateDetail}`;
    }
    return `${pathLabel} · Ready`;
  }
  if (record.provider.id === 'ollama') {
    return localCompanionOnline ? `${pathLabel} · Browse local models` : `${pathLabel} · Needs the selected Agent Computer`;
  }
  if (record.provider.modelsError) {
    return `${pathLabel} · Needs model refresh`;
  }
  return providerRequiresSecret(record.provider, record.profile)
    ? `${pathLabel} · Add ${providerCredentialLabel(record.provider, record.profile).toLowerCase()}`
    : `${pathLabel} · Connect to continue`;
}

function providerRouteBadge(record: ProviderCardRecord): { label: string; tone: 'neutral' | 'hosted' | 'local' } | null {
  const providerId = readString(record.provider.id).toLowerCase();
  const credentialPlane = readString(record.provider.credentialPlane).toLowerCase();
  const defaultAuthMode = readString(record.provider.defaultAuthMode).toLowerCase();
  const activeSource = readString(record.provider.activeSource).toLowerCase();

  if (providerId === 'openai-codex') {
    return { label: 'Personal subscription', tone: 'neutral' };
  }
  if (defaultAuthMode === 'local_cli' || defaultAuthMode === 'local_subscription' || activeSource.includes('cli')) {
    return { label: 'The selected Agent Computer', tone: 'local' };
  }
  if (providerId === 'ollama' || record.provider.localOnly || credentialPlane === 'local_runtime') {
    return { label: 'The selected Agent Computer', tone: 'local' };
  }
  if (
    credentialPlane === 'platform_runtime'
    || record.provider.hostedSageAiPolicy === 'allowed'
    || activeSource.includes('platform')
    || activeSource.includes('hosted')
  ) {
    return { label: 'Via Empyralis', tone: 'hosted' };
  }
  if (
    credentialPlane === 'workspace_connection'
    || record.provider.workspaceConnected
    || Boolean(record.credential)
    || Boolean(readString(record.profile?.credential_id))
  ) {
    return { label: 'Your account', tone: 'neutral' };
  }
  return null;
}

function providerStatusPresentation(
  record: ProviderCardRecord,
  localCompanionOnline: boolean,
): { label: string; className: string | null; showDot: boolean } {
  const pathLabel = providerPathLabel(record);
  if (record.provider.id === 'ollama' && !localCompanionOnline) {
    return {
      label: 'Offline',
      className: 'sage-unified-card__status--warning',
      showDot: false,
    };
  }
  if (record.status === 'connected') {
    if (pathLabel === 'Workspace AI') {
      return {
        label: 'Workspace AI ready',
        className: 'sage-unified-card__status--connected',
        showDot: true,
      };
    }
    if (pathLabel === 'Your AI account' || pathLabel === 'Ollama Cloud') {
      return {
        label: 'Account ready',
        className: 'sage-unified-card__status--connected',
        showDot: true,
      };
    }
    if (pathLabel.startsWith('Agent Computer')) {
      return {
        label: 'Ready on the selected Agent Computer',
        className: 'sage-unified-card__status--connected',
        showDot: true,
      };
    }
    return {
      label: 'Connected',
      className: 'sage-unified-card__status--connected',
      showDot: true,
    };
  }
  return {
    label: 'Not connected',
    className: null,
    showDot: false,
  };
}

function describeConnectorCard(record: ConnectorCardRecord, connected = record.connected): string {
  if (connected) {
    return `${record.label} is ready when you ask Sage to use it.`;
  }
  return record.definition.setupHint;
}

function summarizeGatewayState(
  gateway: GatewayRegistrationRecord | null,
  doctor: GatewayDoctorPayload | null,
  selectionSummary: GatewaySelectionSummary,
): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  if (!gateway) {
    if (selectionSummary.selectedGatewayMissing) {
      const repairDetail = describeGatewaySelectionRepair(selectionSummary);
      return {
        statusLabel: 'Selection stale',
        statusTone: 'warning',
        detail: repairDetail || 'Selected Agent Computer is not available.',
        summary: 'Sage keeps one explicit Agent Computer selection. The saved selection no longer matches an online registration.',
        nextStep: selectionSummary.repairGateway
          ? `Use ${gatewayDisplayName(selectionSummary.repairGateway)} or choose another Agent Computer in Hardware.`
          : 'Choose an online Agent Computer in Hardware before using personal channels.',
      };
    }
    if (selectionSummary.repairGateway) {
      return {
        statusLabel: 'Choose computer',
        statusTone: 'warning',
        detail: describeAvailableGatewayChoice(selectionSummary),
        summary: 'Sage uses one explicit Agent Computer selection for hardware, browser, and personal channels.',
        nextStep: `Use ${gatewayDisplayName(selectionSummary.repairGateway)} or choose another Agent Computer in Hardware.`,
      };
    }
    return {
      statusLabel: 'Connect computer',
      statusTone: 'warning',
      detail: 'Connect a computer so Empyralis can run on selected hardware.',
      summary: 'Agent Computer brings hardware connection and governed local execution online for this workspace.',
      nextStep: 'Open computer setup and connect the selected hardware.',
    };
  }
  const connectionStatus = gatewayConnectionStatus(gateway);
  const doctorStatus = readString(doctor?.status).toLowerCase();
  if (connectionStatus === 'online' && ['healthy', 'pass', 'connected'].includes(doctorStatus)) {
    return {
      statusLabel: 'Connected',
      statusTone: 'connected',
      detail: 'Agent Computer is connected and ready.',
      summary: 'Agent Computer connection and local execution are ready for governed hardware work.',
      nextStep: null,
    };
  }
  return {
    statusLabel: 'Needs attention',
    statusTone: 'danger',
    detail: 'Agent Computer needs attention before Sage can use it.',
    summary: 'Reconnect the selected Agent Computer to restore browser and personal app access.',
    nextStep: 'Open computer setup to reconnect.',
  };
}

function summarizeBrowserState(
  gateway: GatewayRegistrationRecord | null,
  doctor: GatewayDoctorPayload | null,
  selectionSummary: GatewaySelectionSummary,
): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  if (!gateway) {
    if (selectionSummary.selectedGatewayMissing) {
      return {
        statusLabel: 'Selection stale',
        statusTone: 'warning',
        detail: describeGatewaySelectionRepair(selectionSummary) || 'Selected Agent Computer is not available.',
        summary: 'Browser sessions stay unavailable until Sage has a valid selected Agent Computer.',
        nextStep: selectionSummary.repairGateway
          ? `Use ${gatewayDisplayName(selectionSummary.repairGateway)} or choose another Agent Computer in Hardware.`
          : 'Choose an online Agent Computer in Hardware first.',
      };
    }
    if (selectionSummary.repairGateway) {
      return {
        statusLabel: 'Choose computer',
        statusTone: 'warning',
        detail: describeAvailableGatewayChoice(selectionSummary),
        summary: 'Browser sessions stay unavailable until Sage has a selected Agent Computer.',
        nextStep: `Use ${gatewayDisplayName(selectionSummary.repairGateway)} or choose another Agent Computer in Hardware.`,
      };
    }
    return {
      statusLabel: 'Connect computer',
      statusTone: 'warning',
      detail: 'Connect a computer before Sage can use your browser.',
      summary: 'Your signed-in browser stays on the selected Agent Computer and only runs when you ask.',
      nextStep: 'Connect a computer first.',
    };
  }
  const gatewayOnline = gatewayIsOnline(gateway);
  if (!gatewayOnline) {
    return {
      statusLabel: 'Needs attention',
      statusTone: 'danger',
      detail: 'Agent Computer needs attention before browser use is available.',
      summary: 'Signed-in sites stay unavailable until the selected Agent Computer reconnects.',
      nextStep: 'Open computer setup to reconnect first.',
    };
  }
  const browserRecord = doctor && typeof doctor.browser === 'object' ? doctor.browser as Record<string, unknown> : {};
  const browserAttachRecord = doctor && typeof doctor.browser_attach === 'object'
    ? doctor.browser_attach as Record<string, unknown>
    : {};
  const activeCount = Number(browserRecord.active_count ?? 0);
  const attachCount = Number(browserAttachRecord.count ?? 0);
  const attachedCount = Number(browserAttachRecord.attached_count ?? 0);
  const pendingAttachCount = Number(browserAttachRecord.pending_count ?? 0);
  const attachApprovalRequiredCount = Number(browserAttachRecord.approval_required_count ?? 0);
  const attachFailedCount = Number(browserAttachRecord.failed_count ?? 0);
  const status = readString(browserRecord.status).toLowerCase();
  const attachStatus = readString(browserAttachRecord.status).toLowerCase();
  if (attachApprovalRequiredCount > 0) {
    return {
      statusLabel: 'Approval needed',
      statusTone: 'warning',
      detail: 'Your browser is waiting for approval.',
      summary: 'Sage needs approval before using signed-in browser pages on Agent Computer.',
      nextStep: 'Open computer setup to review browser approvals.',
    };
  }
  if (attachedCount > 0 && attachStatus === 'pass') {
    return {
      statusLabel: 'Connected',
      statusTone: 'connected',
      detail: 'Your signed-in browser is ready on the selected Agent Computer.',
      summary: readString(
        browserAttachRecord.summary,
        'Sage is ready to use your existing signed-in browser session on the selected Agent Computer.',
      ),
      nextStep: 'Open computer setup to review browser access.',
    };
  }
  if (attachFailedCount > 0 || attachStatus === 'fail') {
    return {
      statusLabel: 'Needs attention',
      statusTone: 'danger',
      detail: readString(browserAttachRecord.summary, 'Your browser connection failed.'),
      summary: 'Sage could not reach your signed-in browser session. Private sites stay unavailable until it recovers.',
      nextStep: 'Open computer setup to retry browser access.',
    };
  }
  if (attachCount > 0 && pendingAttachCount > 0) {
    return {
      statusLabel: 'Needs attention',
      statusTone: 'warning',
      detail: readString(browserAttachRecord.summary, 'Your browser is not ready yet.'),
      summary: 'Sage still needs a reachable browser session on the selected Agent Computer before it can use private sites.',
      nextStep: 'Open computer setup to finish browser access.',
    };
  }
  if (activeCount > 0 && status === 'pass') {
    return {
      statusLabel: 'Connected',
      statusTone: 'connected',
      detail: 'Your browser is ready on the selected Agent Computer.',
      summary: `${readString(browserRecord.summary, 'Browser access is ready on the selected Agent Computer.')} Signed-in pages stay on Agent Computer.`,
      nextStep: 'Open computer setup to review browser access.',
    };
  }
  if (activeCount === 0 && status === 'pass') {
    return {
      statusLabel: 'Not connected',
      statusTone: 'neutral',
      detail: 'No browser session is active yet.',
      summary: 'Agent Computer is online. Start browser access only when you want Sage to use signed-in pages.',
      nextStep: 'Open computer setup to start browser access.',
    };
  }
  return {
    statusLabel: 'Needs attention',
    statusTone: status === 'warn' ? 'warning' : 'danger',
    detail: readString(browserRecord.summary, 'Browser access needs attention.'),
    summary: 'Browser approvals stay on Agent Computer.',
    nextStep: 'Open computer setup to resolve browser access.',
  };
}

function summarizeWhatsappPersonalState(
  gateway: GatewayRegistrationRecord | null,
  payload: PersonalChannelViewPayload | null,
  selectionSummary: GatewaySelectionSummary,
): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
  connectedIdentity: string | null;
  lastActivityLabel: string;
  ownershipLabel: string;
  channel: PersonalCommunicationChannel;
} {
  const ownershipSummary = 'Stays on Agent Computer. This is your personal WhatsApp, not a business account.';
  const lastActivityLabel = latestPersonalChannelActivity(gateway, payload);
  if (!gateway) {
    if (selectionSummary.selectedGatewayMissing) {
      return {
        statusLabel: 'Selection stale',
        statusTone: 'warning',
        detail: describeGatewaySelectionRepair(selectionSummary) || 'Selected Agent Computer is not available.',
        summary: ownershipSummary,
        nextStep: selectionSummary.repairGateway
          ? `Use ${gatewayDisplayName(selectionSummary.repairGateway)} before pairing WhatsApp.`
          : 'Choose an online Agent Computer before pairing WhatsApp.',
        connectedIdentity: null,
        lastActivityLabel,
        ownershipLabel: ownershipSummary,
        channel: 'whatsapp',
      };
    }
    if (selectionSummary.repairGateway) {
      return {
        statusLabel: 'Choose computer',
        statusTone: 'warning',
        detail: describeAvailableGatewayChoice(selectionSummary),
        summary: ownershipSummary,
        nextStep: `Use ${gatewayDisplayName(selectionSummary.repairGateway)} before pairing WhatsApp.`,
        connectedIdentity: null,
        lastActivityLabel,
        ownershipLabel: ownershipSummary,
        channel: 'whatsapp',
      };
    }
    return {
      statusLabel: 'Connect computer',
      statusTone: 'warning',
      detail: 'Connect a computer before Sage can use your WhatsApp.',
      summary: ownershipSummary,
      nextStep: 'Connect a computer first, then connect WhatsApp here.',
      connectedIdentity: null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'whatsapp',
    };
  }
  const state = payload?.state && typeof payload.state === 'object' ? payload.state : null;
  const status = readString(state?.status).toLowerCase();
  const metadata = state?.metadata && typeof state.metadata === 'object' ? state.metadata as Record<string, unknown> : {};
  const linkedLabel = readString(state?.linked_name || state?.linked_jid, '');
  if (!state || !status || status === 'idle' || status === 'disconnected') {
    return {
      statusLabel: 'Not connected',
      statusTone: 'neutral',
      detail: 'Your WhatsApp is not linked yet.',
      summary: ownershipSummary,
      nextStep: 'Use setup or reconnect from this card.',
      connectedIdentity: null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'whatsapp',
    };
  }
  if (status === 'connected') {
    return {
      statusLabel: 'Connected',
      statusTone: 'connected',
      detail: linkedLabel ? `${linkedLabel} is linked on Agent Computer.` : 'Your WhatsApp is linked on Agent Computer.',
      summary: `${ownershipSummary} Sage can reply through your personal WhatsApp when you ask.`,
      nextStep: null,
      connectedIdentity: linkedLabel || 'Linked WhatsApp account',
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'whatsapp',
    };
  }
  if (['qr_required', 'pairing_code_required', 'code_required', 'login_required'].includes(status) || Boolean(state?.qr_code) || Boolean(state?.login_hint) || Boolean(metadata.pairing_code)) {
    return {
      statusLabel: 'Waiting for QR/login',
      statusTone: 'warning',
      detail: 'WhatsApp is waiting for a QR scan or pairing code step.',
      summary: ownershipSummary,
      nextStep: 'Complete the QR or pairing-code step from this app card.',
      connectedIdentity: linkedLabel || null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'whatsapp',
    };
  }
  if (['connecting', 'reconnecting', 'resuming'].includes(status)) {
    return {
      statusLabel: 'Reconnecting',
      statusTone: 'warning',
      detail: 'WhatsApp is reconnecting on Agent Computer.',
      summary: ownershipSummary,
      nextStep: 'Reconnect from this card if it does not recover.',
      connectedIdentity: linkedLabel || null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'whatsapp',
    };
  }
  return {
    statusLabel: 'Needs attention',
    statusTone: 'danger',
    detail: `Your WhatsApp is ${status.replace(/_/g, ' ')}.`,
    summary: ownershipSummary,
    nextStep: 'Reconnect from this card or review recent activity.',
    connectedIdentity: linkedLabel || null,
    lastActivityLabel,
    ownershipLabel: ownershipSummary,
    channel: 'whatsapp',
  };
}

function summarizeTelegramPersonalState(
  gateway: GatewayRegistrationRecord | null,
  payload: PersonalChannelViewPayload | null,
  selectionSummary: GatewaySelectionSummary,
): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
  connectedIdentity: string | null;
  lastActivityLabel: string;
  ownershipLabel: string;
  channel: PersonalCommunicationChannel;
} {
  const ownershipSummary = 'Stays on Agent Computer. This is your personal Telegram.';
  const lastActivityLabel = latestPersonalChannelActivity(gateway, payload);
  if (!gateway) {
    if (selectionSummary.selectedGatewayMissing) {
      return {
        statusLabel: 'Selection stale',
        statusTone: 'warning',
        detail: describeGatewaySelectionRepair(selectionSummary) || 'Selected Agent Computer is not available.',
        summary: ownershipSummary,
        nextStep: selectionSummary.repairGateway
          ? `Use ${gatewayDisplayName(selectionSummary.repairGateway)} before pairing Telegram.`
          : 'Choose an online Agent Computer before pairing Telegram.',
        connectedIdentity: null,
        lastActivityLabel,
        ownershipLabel: ownershipSummary,
        channel: 'telegram',
      };
    }
    if (selectionSummary.repairGateway) {
      return {
        statusLabel: 'Choose computer',
        statusTone: 'warning',
        detail: describeAvailableGatewayChoice(selectionSummary),
        summary: ownershipSummary,
        nextStep: `Use ${gatewayDisplayName(selectionSummary.repairGateway)} before pairing Telegram.`,
        connectedIdentity: null,
        lastActivityLabel,
        ownershipLabel: ownershipSummary,
        channel: 'telegram',
      };
    }
    return {
      statusLabel: 'Connect computer',
      statusTone: 'warning',
      detail: 'Connect a computer before Sage can use your Telegram.',
      summary: ownershipSummary,
      nextStep: 'Connect a computer first, then connect Telegram here.',
      connectedIdentity: null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'telegram',
    };
  }
  const state = payload?.state && typeof payload.state === 'object' ? payload.state : null;
  const status = readString(state?.status).toLowerCase();
  const linkedLabel = readString(state?.linked_name || state?.linked_username || state?.linked_phone, '');
  if (!state || !status || status === 'idle' || status === 'disconnected') {
    return {
      statusLabel: 'Not connected',
      statusTone: 'neutral',
      detail: 'Your Telegram is not linked yet.',
      summary: ownershipSummary,
      nextStep: 'Use setup or reconnect from this card.',
      connectedIdentity: null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'telegram',
    };
  }
  if (status === 'connected') {
    return {
      statusLabel: 'Connected',
      statusTone: 'connected',
      detail: linkedLabel ? `${linkedLabel} is linked on Agent Computer.` : 'Your Telegram is linked on Agent Computer.',
      summary: `${ownershipSummary} Sage can reply through your personal Telegram when you ask.`,
      nextStep: null,
      connectedIdentity: linkedLabel || 'Linked Telegram account',
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'telegram',
    };
  }
  if (['code_required', 'password_required', 'login_required'].includes(status) || Boolean(state?.login_hint)) {
    return {
      statusLabel: 'Waiting for QR/login',
      statusTone: 'warning',
      detail: 'Telegram is waiting for a login code or confirmation.',
      summary: ownershipSummary,
      nextStep: 'Complete Telegram login from this app card.',
      connectedIdentity: linkedLabel || null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'telegram',
    };
  }
  if (['connecting', 'reconnecting', 'resuming'].includes(status)) {
    return {
      statusLabel: 'Reconnecting',
      statusTone: 'warning',
      detail: 'Telegram is reconnecting on Agent Computer.',
      summary: ownershipSummary,
      nextStep: 'Reconnect from this card if it does not recover.',
      connectedIdentity: linkedLabel || null,
      lastActivityLabel,
      ownershipLabel: ownershipSummary,
      channel: 'telegram',
    };
  }
  return {
    statusLabel: 'Needs attention',
    statusTone: 'danger',
    detail: `Your Telegram is ${status.replace(/_/g, ' ')}.`,
    summary: ownershipSummary,
    nextStep: 'Reconnect from this card or review recent activity.',
    connectedIdentity: linkedLabel || null,
    lastActivityLabel,
    ownershipLabel: ownershipSummary,
    channel: 'telegram',
  };
}

function summarizePersonalSurfaceState(
  surface: PersonalChannelSurfaceRecord | null | undefined,
  fallback: {
    statusLabel: string;
    statusTone: PersonalCardStatusTone;
    detail: string;
    summary: string;
    nextStep: string | null;
  },
): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  if (!surface) {
    return fallback;
  }
  const status = readString(surface.status).toLowerCase();
  const label = readString(surface.label, 'This channel');
  const connected = surface.connected === true;
  const liveCapable = surface.live_capable === true;
  const detail = readString(surface.detail)
    || (connected
      ? `${label} is connected on Agent Computer.`
      : `${label} is ${status ? status.replace(/_/g, ' ') : 'available'} on Agent Computer.`);
  return {
    statusLabel: readString(surface.status_label)
      || (connected ? 'Connected' : liveCapable ? 'Available' : fallback.statusLabel),
    statusTone: connected ? 'connected' : status.includes('error') || status.includes('blocked') ? 'danger' : status.includes('connecting') ? 'warning' : 'neutral',
    detail,
    summary: liveCapable
      ? `${label} is an Agent Computer personal channel. It is separate from Studio business/customer channels.`
      : `${label} is listed as an Agent Computer channel, but setup and send stay disabled until its local adapter is ready.`,
    nextStep: readOptionalString(surface.next_step) ?? fallback.nextStep,
  };
}

function summarizePlannedSignalPersonalState(surface?: PersonalChannelSurfaceRecord | null): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  const state = summarizePersonalSurfaceState(surface, {
    statusLabel: 'Requires Agent Computer',
    statusTone: 'neutral',
    detail: 'Signal runs through Agent Computer.',
    summary: 'Signal is a Sage personal channel. It needs a local bridge on the selected Agent Computer and stays separate from Studio business channels.',
    nextStep: 'Connect an Agent Computer with a Signal bridge to enable Sage messaging.',
  });
  return { ...state, statusLabel: 'Requires Agent Computer' };
}

function summarizePlannedIMessagePersonalState(surface?: PersonalChannelSurfaceRecord | null): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  const state = summarizePersonalSurfaceState(surface, {
    statusLabel: 'Requires Agent Computer',
    statusTone: 'neutral',
    detail: 'iMessage runs through a Mac Agent Computer.',
    summary: 'iMessage is a Sage personal channel. It needs a local Mac bridge and stays separate from Studio business channels.',
    nextStep: 'Connect a Mac Agent Computer with an iMessage bridge to enable Sage messaging.',
  });
  return { ...state, statusLabel: 'Requires Agent Computer' };
}

function summarizeWeChatPersonalState(surface?: PersonalChannelSurfaceRecord | null): {
  statusLabel: string;
  statusTone: PersonalCardStatusTone;
  detail: string;
  summary: string;
  nextStep: string | null;
} {
  const state = summarizePersonalSurfaceState(surface, {
    statusLabel: 'Requires Agent Computer',
    statusTone: 'neutral',
    detail: 'WeChat personal runs through Agent Computer.',
    summary: 'WeChat is a Sage personal channel. It needs a local bridge on the selected Agent Computer and stays separate from Studio business channels.',
    nextStep: 'Connect an Agent Computer with a WeChat bridge to enable Sage messaging.',
  });
  return { ...state, statusLabel: 'Requires Agent Computer' };
}

export function WorkstationSageConnectorsPane({
  showProviders = true,
  showTools = true,
  connectorIds,
  className,
  surface = 'sage',
}: {
  showProviders?: boolean;
  showTools?: boolean;
  connectorIds?: string[];
  className?: string;
  surface?: 'sage' | 'studio';
} = {}) {
  const { bootstrap, routeManifest } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const router = useRouter();
  const searchParams = useSearchParams();
  const cacheKey = `${bootstrap.workspace.id}:${surface}:connection-split-v2`;
  const cachedState = sageConnectorsPaneCache.get(cacheKey) ?? null;
  const workspaceId = bootstrap.workspace.id;
  const [providers, setProviders] = useState<ProviderSnapshot[]>(() => cachedState?.providers ?? []);
  const [profiles, setProfiles] = useState<ProviderProfileRecord[]>(() => cachedState?.profiles ?? []);
  const [credentials, setCredentials] = useState<VaultCredentialRecord[]>(() => cachedState?.credentials ?? []);
  const [connectorVault, setConnectorVault] = useState<VaultCredentialRecord[]>(() => cachedState?.connectorVault ?? []);
  const [gateways, setGateways] = useState<GatewayRegistrationRecord[]>(() => cachedState?.gateways ?? []);
  const [selectedGatewayId, setSelectedGatewayId] = useState<string | null>(() => cachedState?.selectedGatewayId ?? null);
  const [doctor, setDoctor] = useState<GatewayDoctorPayload | null>(() => cachedState?.doctor ?? null);
  const [whatsappPersonal, setWhatsappPersonal] = useState<PersonalChannelViewPayload | null>(() => cachedState?.whatsappPersonal ?? null);
  const [telegramPersonal, setTelegramPersonal] = useState<PersonalChannelViewPayload | null>(() => cachedState?.telegramPersonal ?? null);
  const [personalChannelSurfaces, setPersonalChannelSurfaces] = useState<PersonalChannelSurfaceRecord[]>(() => cachedState?.personalChannelSurfaces ?? []);
  const [hostedSageAi, setHostedSageAi] = useState<HostedSageAiSnapshot>(() => cachedState?.hostedSageAi ?? DEFAULT_HOSTED_SAGE_AI);
  const [workspaceAiRoute, setWorkspaceAiRoute] = useState<WorkspaceAiRoutePayload | null>(() => cachedState?.workspaceAiRoute ?? null);
  const [billingCredits, setBillingCredits] = useState<ChatHostedCreditState | null>(() => cachedState?.billingCredits ?? null);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>(() => cachedState?.mcpServers ?? []);
  const [connectionStatusItems, setConnectionStatusItems] = useState<ConnectionStatusItem[]>(() => cachedState?.connectionStatusItems ?? []);
  const [isLoading, setIsLoading] = useState(() => cachedState === null);
  const [error, setError] = useState<unknown>(null);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
  const [oauthCallbackError, setOAuthCallbackError] = useState<{ message: string; provider: string } | null>(null);
  function setCardError(cardId: string, message: string) {
    setCardErrors((prev) => ({ ...prev, [cardId]: message }));
  }
  function clearCardError(cardId: string) {
    setCardErrors((prev) => {
      const next = { ...prev };
      delete next[cardId];
      return next;
    });
  }
  const [personalChannelError, setPersonalChannelError] = useState<string | null>(null);
  const [, setStatus] = useState<string | null>(null);
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);
  const [telegramChannelMode, setTelegramChannelMode] = useState<'bot' | 'personal'>('bot');
  const [telegramHostedPairing, setTelegramHostedPairing] = useState<{ pairingCode: string; deepLink: string } | null>(null);
  const [telegramHostedPairingLoading, setTelegramHostedPairingLoading] = useState(false);
  const [telegramHostedPaired, setTelegramHostedPaired] = useState(false);
  const [telegramHostedPolling, setTelegramHostedPolling] = useState(false);
  const [telegramHostedBotUsername, setTelegramHostedBotUsername] = useState<string | null>(null);
  const [discordPairingLoading, setDiscordPairingLoading] = useState(false);
  const [discordPaired, setDiscordPaired] = useState(false);
  const [discordPairingCode, setDiscordPairingCode] = useState<string | null>(null);
  const [discordChannelMode, setDiscordChannelMode] = useState<'bot' | 'personal'>('bot');
  const [discordPublicConfig, setDiscordPublicConfig] = useState<{ client_id: string; bot_user_id: string } | null>(null);
  const [discordOAuthUrl, setDiscordOAuthUrl] = useState<string | null>(null);
  const [whatsappChannelMode, setWhatsappChannelMode] = useState<ChannelRouteMode>('hardware');
  const [selectedIntegrationId, setSelectedIntegrationId] = useState<IntegrationWorkbenchCategoryId>(() => (
    normalizeIntegrationCategoryId(searchParams.get('section') ?? searchParams.get('connection'))
    ?? (showProviders ? 'apps' : 'channels')
  ));
  const [providerDraftKeys, setProviderDraftKeys] = useState<Record<string, string>>({});
  const [providerDraftBaseUrls, setProviderDraftBaseUrls] = useState<Record<string, string>>({});
  const [connectorDraftCredentials, setConnectorDraftCredentials] = useState<Record<string, Record<string, string>>>({});
  const [providerPickerOpen, setProviderPickerOpen] = useState(false);
  const [computerConnectOpen, setComputerConnectOpen] = useState(false);
  const [providerPickerDraftId, setProviderPickerDraftId] = useState<string | null>(null);
  const [connectorMemoryEnabled, setConnectorMemoryEnabled] = useState<Record<string, boolean>>({});
  const [providerModelOverrides, setProviderModelOverrides] = useState<Record<string, ProviderCatalogModelRecord[]>>({});
  const [failedLogos, setFailedLogos] = useState<Set<string>>(() => new Set());
  const [busyCardId, setBusyCardId] = useState<string | null>(null);
  const [mcpServerDraft, setMcpServerDraft] = useState<McpServerDraft>(DEFAULT_MCP_SERVER_DRAFT);
  const [configChannelId, setConfigChannelId] = useState<PersonalCommunicationChannel | null>(null);
  const [telegramSetupStep, setTelegramSetupStep] = useState<TelegramSetupStep>('phone');
  const [channelDrafts, setChannelDrafts] = useState<Record<PersonalCommunicationChannel, PersonalChannelDraft>>({
    telegram: defaultPersonalChannelDraft('telegram'),
    whatsapp: defaultPersonalChannelDraft('whatsapp'),
  });

  const loadState = useCallback(async () => {
    const [catalogResult, routeResult, profileResult, credentialResult, connectorResult, mcpResult, connectionStatusResult, gatewayResult, billingResult] = await Promise.allSettled([
      services.client.listProviderCatalog(),
      services.client.getWorkspaceAiRoute(),
      services.client.listProviderProfiles(),
      services.client.listVaultCredentials(),
      services.client.listConnectorsVault(),
      services.client.listMcpServers(),
      services.client.listConnectionStatus({ surface }),
      (async () => {
        const registrationsPayload = await services.client.requestJson<Record<string, unknown>>({
          path: `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
          allowStatuses: [404],
        });
        const registrationItems = Array.isArray(registrationsPayload?.items)
          ? registrationsPayload.items.filter((item): item is GatewayRegistrationRecord => Boolean(item) && typeof item === 'object')
          : [];
        const selectionPayload = await services.client.getSageAgentComputerSelection().catch(() => null);
        const selection = asConnectorPayload(selectionPayload?.selection);
        const selectionRoot = asConnectorPayload(selectionPayload);
        const currentGatewayId = readString(selection?.selected_gateway_id)
          || readString(selectionRoot?.selected_gateway_id);
        const selectedGateway = currentGatewayId
          ? registrationItems.find((item) => readString(item.gateway_id, '') === currentGatewayId) ?? null
          : null;
        const gatewayId = readString(selectedGateway?.gateway_id, '') || null;
        if (!gatewayId) {
          return {
            gateways: registrationItems,
            selectedGatewayId: currentGatewayId || null,
            doctor: null,
            whatsappPersonal: null,
            telegramPersonal: null,
            personalChannelSurfaces: [],
          };
        }
        const [doctorPayload, whatsappPayload, telegramPayload, channelSurfacesPayload] = await Promise.all([
          services.client.requestJson<GatewayDoctorPayload>({
            path: `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/doctor`,
            allowStatuses: [403, 404],
          }),
          services.client.requestJson<PersonalChannelViewPayload>({
            path: `/api/personal-channels/whatsapp/gateways/${encodeURIComponent(gatewayId)}`,
            allowStatuses: [403, 404],
          }),
          services.client.requestJson<PersonalChannelViewPayload>({
            path: `/api/personal-channels/telegram/gateways/${encodeURIComponent(gatewayId)}`,
            allowStatuses: [403, 404],
          }),
          services.client.requestJson<PersonalChannelSurfacesPayload>({
            path: `/api/personal-channels/gateways/${encodeURIComponent(gatewayId)}/channels`,
            allowStatuses: [403, 404],
          }),
        ]);
        return {
          gateways: registrationItems,
          selectedGatewayId: gatewayId,
          doctor: doctorPayload,
          whatsappPersonal: whatsappPayload,
          telegramPersonal: telegramPayload,
          personalChannelSurfaces: Array.isArray(channelSurfacesPayload?.items)
            ? channelSurfacesPayload.items.filter((item): item is PersonalChannelSurfaceRecord => Boolean(item) && typeof item === 'object')
            : [],
        };
      })(),
      services.client.getBillingSummary().catch(() => null),
    ]);

    const catalogPayload = catalogResult.status === 'fulfilled' ? catalogResult.value : null;
    const billingCreditsPayload = billingResult.status === 'fulfilled' ? billingResult.value : null;
    const normalizedConnectionStatusItems = connectionStatusResult.status === 'fulfilled' && Array.isArray(connectionStatusResult.value?.items)
      ? connectionStatusResult.value.items.filter((item): item is ConnectionStatusItem => Boolean(item) && typeof item === 'object')
      : [];
    const agentComputerStatusItem = normalizedConnectionStatusItems.find((item) => readString(item.id) === 'agent_computer') ?? null;
    const statusGatewayCandidate = asConnectorPayload(agentComputerStatusItem?.online_gateway_candidate);
    const statusFallbackGatewayId = readString(statusGatewayCandidate?.gateway_id);
    const statusFallbackSelectedGatewayId = readString(agentComputerStatusItem?.selected_gateway_id);
    const statusFallbackGateway = statusFallbackGatewayId
      ? {
          ...statusGatewayCandidate,
          gateway_id: statusFallbackGatewayId,
          connection_status: readString(statusGatewayCandidate?.connection_status, 'online'),
        } as GatewayRegistrationRecord
      : null;
    const gatewayResultPayload = gatewayResult.status === 'fulfilled' ? gatewayResult.value : {
      gateways: [],
      selectedGatewayId: null,
      doctor: null,
      whatsappPersonal: null,
      telegramPersonal: null,
      personalChannelSurfaces: [],
    };
    const gatewayPayload = {
      ...gatewayResultPayload,
      gateways: gatewayResultPayload.gateways.length > 0
        ? gatewayResultPayload.gateways
        : statusFallbackGateway
          ? [statusFallbackGateway]
          : [],
      selectedGatewayId: gatewayResultPayload.selectedGatewayId ?? (statusFallbackSelectedGatewayId || null),
    };
    const normalizedProviders = normalizeProviderCatalog(catalogPayload);
    const nextState: SageConnectorsPaneCache = {
      providers: normalizedProviders.length > 0 ? normalizedProviders : fallbackProviderCatalog(),
      profiles: profileResult.status === 'fulfilled' ? normalizeProviderProfiles(profileResult.value) : [],
      credentials: credentialResult.status === 'fulfilled' ? normalizeVaultCredentials(credentialResult.value) : [],
      connectorVault: connectorResult.status === 'fulfilled' ? normalizeVaultCredentials(connectorResult.value) : [],
      gateways: gatewayPayload.gateways,
      selectedGatewayId: gatewayPayload.selectedGatewayId,
      doctor: gatewayPayload.doctor,
      whatsappPersonal: gatewayPayload.whatsappPersonal,
      telegramPersonal: gatewayPayload.telegramPersonal,
      personalChannelSurfaces: gatewayPayload.personalChannelSurfaces,
      hostedSageAi: normalizeHostedSageAi(catalogPayload),
      workspaceAiRoute: routeResult.status === 'fulfilled' ? routeResult.value : null,
      billingCredits: billingCreditsPayload && typeof billingCreditsPayload === 'object'
        ? normalizeHostedCreditStateForChat(billingCreditsPayload as Record<string, unknown>)
        : null,
      mcpServers: mcpResult.status === 'fulfilled' ? normalizeMcpServers(mcpResult.value) : [],
      connectionStatusItems: normalizedConnectionStatusItems,
    };
    sageConnectorsPaneCache.set(cacheKey, nextState);
    setProviders(nextState.providers);
    setProfiles(nextState.profiles);
    setCredentials(nextState.credentials);
    setConnectorVault(nextState.connectorVault);
    setGateways(nextState.gateways);
    setSelectedGatewayId(nextState.selectedGatewayId);
    setDoctor(nextState.doctor);
    setWhatsappPersonal(nextState.whatsappPersonal);
    setTelegramPersonal(nextState.telegramPersonal);
    setPersonalChannelSurfaces(nextState.personalChannelSurfaces);
    setHostedSageAi(nextState.hostedSageAi);
    setWorkspaceAiRoute(nextState.workspaceAiRoute);
    setBillingCredits(nextState.billingCredits);
    setMcpServers(nextState.mcpServers);
    setConnectionStatusItems(nextState.connectionStatusItems);

    // Load Telegram hosted bot pairing state so it survives page refresh.
    // /pair/status returns { paired: boolean } — check it even when the
    // main data load fails so a paired workspace always shows as connected.
    try {
      const pairStatusResp = await fetch(
        `/api/sage/telegram-hosted/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`,
        { headers: buildCookieAuthHeaders('GET'), credentials: 'include' },
      );
      if (pairStatusResp.ok) {
        const pairStatus = await pairStatusResp.json() as { paired: boolean; configured: boolean };
        setTelegramHostedPaired(pairStatus.paired === true);
      }
    } catch { /* pairing check is best-effort */ }

    // Load bot display info so the "Send a message to @..." text is accurate.
    try {
      const infoResp = await fetch('/api/sage/telegram-hosted/info', {
        headers: buildCookieAuthHeaders('GET'), credentials: 'include',
      });
      if (infoResp.ok) {
        const info = await infoResp.json() as { configured: boolean; username: string | null };
        if (info.username) setTelegramHostedBotUsername(info.username);
      }
    } catch { /* info fetch is best-effort */ }

    // Load Discord pairing status so it survives page refresh.
    try {
      const discordStatusResp = await fetch(
        `/api/sage/discord/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`,
        { headers: buildCookieAuthHeaders('GET'), credentials: 'include' },
      );
      if (discordStatusResp.ok) {
        const discordStatus = await discordStatusResp.json() as { paired: boolean; has_pending_code: boolean };
        setDiscordPaired(discordStatus.paired === true);
      }
      // Load public Discord identifiers (client_id, bot_user_id) for install URL + open-bot link.
      const discordConfigResp = await fetch('/api/sage/discord/public-config', {
        headers: buildCookieAuthHeaders('GET'), credentials: 'include',
      });
      if (discordConfigResp.ok) {
        const config = await discordConfigResp.json() as { client_id: string; bot_user_id: string };
        setDiscordPublicConfig(config);
      }
      // Load OAuth URL for one-click identify-bind.
      const discordOAuthUrlResp = await fetch(
        `/api/sage/discord/oauth/url?workspace_id=${encodeURIComponent(workspaceId)}`,
        { headers: buildCookieAuthHeaders('GET'), credentials: 'include' },
      );
      if (discordOAuthUrlResp.ok) {
        const oauthData = await discordOAuthUrlResp.json() as { url: string };
        if (oauthData.url) setDiscordOAuthUrl(oauthData.url);
      }
    } catch { /* pairing check is best-effort */ }

    // Catalog failure is non-fatal — the page still renders with an empty catalog.
    // Per-card errors are handled individually via cardErrors state.
  }, [cacheKey, services.client, surface, workspaceId]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(sageConnectorsPaneCache.get(cacheKey) === null);
    void loadState()
      .catch(() => {
        // loadState is defensive; rejections only surface transport errors
        // that individual cards handle via their own fetch paths.
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cacheKey, loadState]);

  useEffect(() => {
    const connectedProvider = readString(searchParams.get('connected'));
    const callbackError = readString(searchParams.get('connection_error'));

    if (connectedProvider || callbackError) {
      // Reload connection status so cards reflect the OAuth result immediately.
      void loadState();
    }

    if (connectedProvider && typeof window !== 'undefined') {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete('connected');
      window.history.replaceState(window.history.state, '', nextUrl.toString());
    }

    if (callbackError) {
      const errorMessage = humanizeConnectionSetupError(new Error(callbackError), 'Connection');
      // Store the callback error so individual cards can display it.
      // Cards match on their connection provider when expanded.
      setOAuthCallbackError({ message: errorMessage, provider: '' });
      if (typeof window !== 'undefined') {
        const nextUrl = new URL(window.location.href);
        nextUrl.searchParams.delete('connection_error');
        window.history.replaceState(window.history.state, '', nextUrl.toString());
      }
    }
  }, [searchParams, loadState]);

  // Detect Discord OAuth completion — both from popup postMessage and from URL query param.
  useEffect(() => {
    // Case 1: This window IS the OAuth popup (redirected back with ?discord=connected).
    const discordStatus = readString(searchParams.get('discord'));
    if (discordStatus === 'connected') {
      setDiscordPaired(true);
      // Notify the opener window so it can update its UI without a refresh.
      if (typeof window !== 'undefined' && window.opener) {
        try {
          window.opener.postMessage({ type: 'discord-oauth-complete' }, window.location.origin);
        } catch { /* cross-origin guard */ }
      }
      // Clean up the query param.
      if (typeof window !== 'undefined') {
        const nextUrl = new URL(window.location.href);
        nextUrl.searchParams.delete('discord');
        nextUrl.searchParams.delete('dm');
        window.history.replaceState(window.history.state, '', nextUrl.toString());
      }
    }

    // Case 2: This window OPENED the popup and receives the postMessage.
    const handler = (e: MessageEvent) => {
      if (e.data?.type === 'discord-oauth-complete') {
        setDiscordPaired(true);
        // Re-fetch pair status to confirm server-side state.
        void loadState();
      }
    };
    if (typeof window !== 'undefined') {
      window.addEventListener('message', handler);
      return () => window.removeEventListener('message', handler);
    }
  }, [searchParams, loadState]);

  const localCompanionOnline = useMemo(
    () => bootstrap.runtime.runtimeTargets.some((target) => target.id === 'local_companion' && target.online),
    [bootstrap.runtime.runtimeTargets],
  );

  useEffect(() => {
    let cancelled = false;
    if (!localCompanionOnline) {
      setProviderModelOverrides((current) => (current.ollama ? { ...current, ollama: [] } : current));
      return undefined;
    }
    void services.client.listProviderModels({ providerId: 'ollama' })
      .then((payload: Record<string, unknown>) => {
        if (cancelled) {
          return;
        }
        setProviderModelOverrides((current) => ({
          ...current,
          ollama: normalizeProviderModels(payload),
        }));
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setProviderModelOverrides((current) => ({
          ...current,
          ollama: [],
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [localCompanionOnline, services.client]);

  const providerCards = useMemo<ProviderCardRecord[]>(() => providers.map((provider) => {
    const resolvedProvider = provider.id === 'ollama'
      ? { ...provider, models: providerModelOverrides.ollama ?? [] }
      : providerModelOverrides[provider.id]?.length
        ? { ...provider, models: providerModelOverrides[provider.id] }
        : provider;
    const providerProfiles = sortProfiles(
      profiles.filter((item) => readString(item.provider).toLowerCase() === resolvedProvider.id),
    );
    const providerCredentials = sortCredentials(
      credentials.filter((item) => readString(item.provider).toLowerCase() === resolvedProvider.id),
    );
    const profile = providerProfiles[0] ?? null;
    const credential = providerCredentials[0] ?? null;
    const secretlessConnected = isSecretlessConnection(resolvedProvider.id, profile);
    const connected = resolvedProvider.id === 'ollama'
      ? localCompanionOnline && (Boolean(credential) || secretlessConnected || resolvedProvider.models.length > 0)
      : Boolean(credential) || secretlessConnected;
    return {
      kind: 'provider',
      id: resolvedProvider.id,
      label: resolvedProvider.label,
      image: PROVIDER_IMAGE_BY_ID[resolvedProvider.id] ?? '',
      status: connected ? 'connected' : 'not_connected',
      provider: resolvedProvider,
      credential,
      profile,
      connected,
      keyTail: maskKeyTail(credential),
    } satisfies ProviderCardRecord;
  }), [credentials, localCompanionOnline, profiles, providerModelOverrides, providers]);

  const hostedProviderCard = useMemo(() => {
    const isHostedEligible = (record: ProviderCardRecord): boolean => (
      hostedSageAi.allowed && (
        record.provider.hostedSageAiPolicy === 'enabled_with_cap'
        || record.provider.hostedSageAiPolicy === 'allowed'
        || readString(record.provider.credentialPlane).toLowerCase() === 'platform_runtime'
      )
    ) || providerRouteBadge(record)?.label === 'Via Empyralis';
    const hostedEligibleCards = providerCards.filter(isHostedEligible);
    if (hostedEligibleCards.length === 0) {
      return null;
    }
    const launchHostedPriority = ['deepseek', 'gemini', 'openai'];
    for (const providerId of launchHostedPriority) {
      const preferred = hostedEligibleCards.find((record) => record.provider.id === providerId);
      if (preferred) {
        return preferred;
      }
    }
    return hostedEligibleCards[0] ?? null;
  }, [hostedSageAi.allowed, providerCards]);

  const explicitSelectedProfile = useMemo(
    () => sortProfiles(profiles).find((profile) =>
      readString(profileMetadataRecord(profile).chat_model_selection).toLowerCase() === 'explicit'
      && profile.enabled !== false) ?? null,
    [profiles],
  );

  const workspaceDefaultRoute = useMemo(
    () => (workspaceAiRoute?.workspaceDefault && typeof workspaceAiRoute.workspaceDefault === 'object'
      ? workspaceAiRoute.workspaceDefault
      : null),
    [workspaceAiRoute],
  );
  const workspaceDefaultRouteNeedsSetup = useMemo(
    () => workspaceAiRouteNeedsSetup(workspaceDefaultRoute),
    [workspaceDefaultRoute],
  );

  const activeProviderCard = useMemo(() => {
    if (workspaceDefaultRoute && !workspaceDefaultRouteNeedsSetup) {
      const routeKind = readString(workspaceDefaultRoute.kind).toLowerCase();
      const routeProviderId = readString(workspaceDefaultRoute.providerId).toLowerCase();
      if (routeKind === 'empyralis_managed') {
        return hostedProviderCard;
      }
      if (routeProviderId) {
        const routeCard = providerCards.find((record) => record.provider.id === routeProviderId) ?? null;
        if (routeCard && (!providerIsLocalOnly(routeCard) || providerPickerConnected(routeCard, localCompanionOnline))) {
          return routeCard;
        }
      }
      return null;
    }
    if (workspaceDefaultRouteNeedsSetup) {
      return null;
    }
    const explicitProviderId = readString(explicitSelectedProfile?.provider).toLowerCase();
    if (explicitProviderId) {
      const explicitCard = providerCards.find((record) => record.provider.id === explicitProviderId) ?? null;
      if (explicitCard && (!providerIsLocalOnly(explicitCard) || providerPickerConnected(explicitCard, localCompanionOnline))) {
        return explicitCard;
      }
    }
    return hostedProviderCard
      ?? providerCards.find((record) => record.provider.active && (!providerIsLocalOnly(record) || providerPickerConnected(record, localCompanionOnline)))
      ?? providerCards.find((record) => providerPickerConnected(record, localCompanionOnline) && !providerIsLocalOnly(record))
      ?? null;
  }, [explicitSelectedProfile, hostedProviderCard, localCompanionOnline, providerCards, workspaceDefaultRoute, workspaceDefaultRouteNeedsSetup]);

  const backupProviderCard = useMemo(() => {
    const backupPriority = ['gemini', 'openai', 'anthropic'];
    for (const providerId of backupPriority) {
      const card = providerCards.find((record) => record.provider.id === providerId) ?? null;
      if (card) {
        return card;
      }
    }
    return null;
  }, [providerCards]);

  const aiProviderSummary = useMemo<AiProviderSummary>(() => {
    const backupName = backupProviderCard?.provider.id === 'gemini' ? 'Gemini' : backupProviderCard?.label ?? 'Backup model';
    const backupAvailability = providerAvailabilityLabel(backupProviderCard, localCompanionOnline);
    // Canonical credit source: billing/summary endpoint (same as chat/billing/settings panes).
    // Falls back to catalog-hostedSageAi while billing loads.
    const canonicalRemaining = billingCredits?.monthlyCreditsRemaining ?? hostedSageAi.monthlyCreditsRemaining;
    const canonicalCap = billingCredits?.monthlyCreditCap ?? hostedSageAi.monthlyCreditCap;
    const creditsLabel = canonicalCap > 0
      ? `${formatCredits(canonicalRemaining)} remaining`
      : hostedSageAi.allowed
        ? 'Available'
        : 'Not active';
    const creditsDetail = canonicalCap > 0
      ? `${formatCredits(canonicalRemaining)} / ${formatCredits(canonicalCap)} monthly usage left`
      : describeHostedSageAi(hostedSageAi, hostedProviderCard);
    return {
      activeLabel: providerActiveSummaryLabel(activeProviderCard, hostedProviderCard, explicitSelectedProfile),
      activeDetail: providerActiveSummaryDetail(activeProviderCard, hostedProviderCard, explicitSelectedProfile, hostedSageAi),
      creditsLabel,
      creditsDetail,
      backupLabel: backupProviderCard ? `${backupName} ${backupAvailability}` : 'No backup configured',
      backupDetail: backupProviderCard
        ? `${backupProviderCard.label} stays available as ${providerPathLabel(backupProviderCard)}.`
        : 'Connect Gemini, OpenAI, or Anthropic when you want a backup hosted provider.',
      configLabel: 'Provider configuration',
      configDetail: 'Connect another AI account. Computer models stay optional for local/private work.',
    };
  }, [activeProviderCard, backupProviderCard, billingCredits, explicitSelectedProfile, hostedProviderCard, hostedSageAi, localCompanionOnline]);

  const providerPickerSections = useMemo<ProviderPickerSection[]>(() => {
    const visibleProviderCards = providerCards.filter((record) =>
      record.provider.sageVisible && !record.provider.hidden,
    );
    const routeProviderCards = visibleProviderCards.filter((record) => !providerIsPersonalSubscriptionTransport(record));
    const byokItems = routeProviderCards.filter((record) => !providerIsLocalOnly(record));
    const localItems = routeProviderCards.filter(providerIsLocalOnly);
    const sections: ProviderPickerSection[] = [];
    if (hostedProviderCard || hostedSageAi.planAllowsHostedAi) {
      sections.push({
        id: 'hosted',
        label: 'Workspace AI',
        items: hostedProviderCard ? [hostedProviderCard] : [],
      });
    }
    sections.push({
      id: 'byok',
      label: 'Your AI accounts',
      items: byokItems,
    });
    if (localItems.length > 0) {
      sections.push({
        id: 'local',
        label: 'Computer models',
        items: localItems,
      });
    }
    return sections;
  }, [hostedProviderCard, hostedSageAi.planAllowsHostedAi, providerCards]);

  const selectedGateway = useMemo(
    () => gateways.find((gateway) => readString(gateway.gateway_id, '') === readString(selectedGatewayId, '')) ?? null,
    [gateways, selectedGatewayId],
  );
  const selectedGatewayOnline = gatewayIsOnline(selectedGateway);
  const gatewaySelectionSummary = useMemo<GatewaySelectionSummary>(() => {
    const selectedId = readString(selectedGatewayId);
    const selectedGatewayMissing = Boolean(selectedId) && selectedGateway === null;
    const onlineGateways = gateways.filter((gateway) => gatewayIsOnline(gateway));
    const selectedGatewayOffline = Boolean(selectedGateway) && !gatewayIsOnline(selectedGateway);
    return {
      selectedGatewayMissing,
      selectedGatewayOffline,
      repairGateway: (selectedGateway === null || selectedGatewayOffline) && onlineGateways.length === 1 ? onlineGateways[0] : null,
    };
  }, [gateways, selectedGateway, selectedGatewayId]);
  const showPersonalSurface = surface === 'sage';
  const personalChannelSurfaceByKey = useMemo(() => {
    const byKey = new Map<string, PersonalChannelSurfaceRecord>();
    personalChannelSurfaces.forEach((item) => {
      const key = readString(item.channel_key).toLowerCase();
      if (key) {
        byKey.set(key, item);
      }
    });
    return byKey;
  }, [personalChannelSurfaces]);

  const personalCards = useMemo<PersonalCardRecord[]>(() => {
    const device = summarizeGatewayState(selectedGateway, doctor, gatewaySelectionSummary);
    const browser = summarizeBrowserState(selectedGateway, doctor, gatewaySelectionSummary);
    const telegram = summarizeTelegramPersonalState(selectedGateway, telegramPersonal, gatewaySelectionSummary);
    const whatsapp = summarizeWhatsappPersonalState(selectedGateway, whatsappPersonal, gatewaySelectionSummary);
    const signal = summarizePlannedSignalPersonalState(personalChannelSurfaceByKey.get('signal_personal'));
    const imessage = summarizePlannedIMessagePersonalState(personalChannelSurfaceByKey.get('imessage_personal'));
    const wechat = summarizeWeChatPersonalState(personalChannelSurfaceByKey.get('wechat_personal'));
    return [
      { id: 'device', label: 'Agent Computer', image: '/brand-assets/generic/computer.svg?v=3', ...device },
      { id: 'browser', label: 'Use my browser', image: '/brand-assets/generic/browser.svg?v=3', ...browser },
      { id: 'telegram_personal', label: 'Your Telegram', image: '/brand-assets/channels/telegram.svg?v=3', ...telegram },
      { id: 'whatsapp_personal', label: 'Your WhatsApp', image: '/brand-assets/channels/whatsapp.svg?v=3', ...whatsapp },
      { id: 'signal_personal', label: 'Signal', image: '/brand-assets/channels/signal.svg?v=3', ...signal },
      { id: 'imessage_personal', label: 'iMessage', image: '/brand-assets/channels/imessage.svg?v=3', ...imessage },
      { id: 'wechat_personal', label: 'WeChat', image: '/brand-assets/channels/wechat.svg?v=3', ...wechat },
    ];
  }, [doctor, gatewaySelectionSummary, personalChannelSurfaceByKey, selectedGateway, telegramPersonal, whatsappPersonal]);

  const communicationPersonalCards = useMemo(
    () => personalCards.filter((card) =>
      card.id === 'telegram_personal'
      || card.id === 'whatsapp_personal'
      || card.id === 'signal_personal'
      || card.id === 'imessage_personal'
      || card.id === 'wechat_personal',
    ),
    [personalCards],
  );

  const thisComputerCards = useMemo<ExternalIntegrationCardRecord[]>(() => {
    const device = personalCards.find((card) => card.id === 'device');
    const cards: ExternalIntegrationCardRecord[] = [];
    if (device) {
      cards.push({
        ...device,
        id: 'computer_gateway',
        label: 'Agent Computer',
        detail: device.statusTone === 'connected'
          ? 'Connected. Local/private computer work is available when Sage needs it.'
          : 'Connect only when Sage needs local files, apps, browser sessions, terminal, or private hardware.',
        summary: device.statusTone === 'connected'
          ? 'Agent Computer is the optional edge runtime for local/private work. Cloud apps and hosted browser stay first.'
          : 'Sage works without installation. Agent Computer adds real-machine control for local/private requests.',
        nextStep: device.statusTone === 'connected'
          ? 'Connected. Manage or revoke it only when local/private work changes.'
          : 'Connect the selected Agent Computer when cloud apps or hosted browser cannot do the task.',
        actionLabel: device.statusTone === 'connected' ? 'Manage computer' : 'Connect a computer',
        actionTarget: 'computer',
      });
    }
    return cards;
  }, [personalCards]);

  const connectorCards = useMemo<ConnectorCardRecord[]>(() => {
    const latestConnectorById = new Map<string, VaultCredentialRecord>();
    sortCredentials(connectorVault).forEach((item) => {
      const connectorId = readString(item.connector).toLowerCase();
      if (connectorId && !latestConnectorById.has(connectorId)) {
        latestConnectorById.set(connectorId, item);
      }
    });

    return CONNECTOR_DEFINITIONS.flatMap((definition) => {
      if (surface === 'sage' && definition.surfaceScope === 'studio_only') {
        return [];
      }
      if (Array.isArray(connectorIds) && connectorIds.length > 0 && !connectorIds.includes(definition.id)) {
        return [];
      }
      const connectorCredential = (definition.connectorIds ?? [])
        .map((connectorId) => latestConnectorById.get(connectorId))
        .find((item): item is VaultCredentialRecord => Boolean(item)) ?? null;
      const connected = Boolean(connectorCredential);
      return [{
        kind: 'connector',
        id: definition.id,
        label: definition.label,
        image: definition.image,
        status: connected ? 'connected' : 'not_connected',
        connected,
        credential: connectorCredential,
        definition,
      }];
    });
  }, [connectorIds, connectorVault, surface]);

  const connectionStatusById = useMemo(() => {
    const out = new Map<string, ConnectionStatusItem>();
    connectionStatusItems.forEach((item) => {
      const id = readString(item.id).toLowerCase();
      if (id) {
        out.set(id, item);
      }
      if (Array.isArray(item.connector_ids)) {
        item.connector_ids.forEach((alias) => {
          const normalizedAlias = readString(alias).toLowerCase();
          if (normalizedAlias && !out.has(normalizedAlias)) {
            out.set(normalizedAlias, item);
          }
        });
      }
    });
    return out;
  }, [connectionStatusItems]);

  function connectionStatusForConnector(record: ConnectorCardRecord): ConnectionStatusItem | null {
    const candidates = [record.id, ...(record.definition.connectorIds ?? [])];
    for (const candidate of candidates) {
      const match = connectionStatusById.get(readString(candidate).toLowerCase());
      if (match) {
        return match;
      }
    }
    return null;
  }

  function connectionStatusForPersonal(record: PersonalCardRecord): ConnectionStatusItem | null {
    return connectionStatusById.get(readString(record.id).toLowerCase()) ?? null;
  }

  function connectionDisplayState(item: ConnectionStatusItem | null): string {
    return readString(item?.display_state).toLowerCase();
  }

  function connectionIsConnectable(item: ConnectionStatusItem | null): boolean {
    const displayState = connectionDisplayState(item);
    if (displayState === 'connectable') {
      return true;
    }
    if (displayState !== 'requires_setup') {
      return false;
    }
    const launchStatus = readString(item?.launch_status).toLowerCase();
    const nextAction = readString(item?.next_action).toLowerCase();
    return item?.setup_available !== false
      && item?.runtime_usable !== false
      && nextAction !== 'locked'
      && launchStatus !== 'planned'
      && launchStatus !== 'partial'
      && launchStatus !== 'locked';
  }

  function connectionUsesManagedSetup(item: ConnectionStatusItem | null): boolean {
    const setupKind = readString(item?.setup_kind).toLowerCase();
    return setupKind === 'oauth'
      || setupKind === 'oauth_or_app_install'
      || setupKind === 'oauth_mailbox';
  }

  function connectionProviderUsesManagedOAuth(provider: string | null | undefined): boolean {
    return MANAGED_OAUTH_PROVIDER_IDS.has(readString(provider).toLowerCase());
  }

  function connectionLaunchLocked(item: ConnectionStatusItem | null): boolean {
    if (!item) {
      return false;
    }
    const displayState = connectionDisplayState(item);
    if (displayState) {
      if (displayState === 'requires_setup') {
        const launchStatus = readString(item.launch_status).toLowerCase();
        const nextAction = readString(item.next_action).toLowerCase();
        return item.setup_available === false
          || item.runtime_usable === false
          || nextAction === 'locked'
          || launchStatus === 'planned'
          || launchStatus === 'partial'
          || launchStatus === 'locked';
      }
      return displayState === 'requires_agent_computer'
        || displayState === 'unavailable';
    }
    const launchStatus = readString(item.launch_status).toLowerCase();
    const nextAction = readString(item.next_action).toLowerCase();
    return nextAction === 'locked'
      || item.runtime_usable === false
      || item.setup_available === false
      || launchStatus === 'planned'
      || launchStatus === 'partial'
      || launchStatus === 'locked';
  }

  function connectionLockedLabel(item: ConnectionStatusItem | null, fallback: string): string {
    const displayState = connectionDisplayState(item);
    if (displayState === 'requires_agent_computer') {
      return 'Requires Agent Computer';
    }
    if (displayState === 'requires_setup') {
      return 'Requires setup';
    }
    if (displayState === 'unavailable') {
      return 'Unavailable';
    }
    const launchStatus = readString(item?.launch_status).toLowerCase();
    if (launchStatus === 'partial') {
      return 'Requires setup';
    }
    if (launchStatus === 'planned') {
      return 'Requires setup';
    }
    return fallback;
  }

  function connectorActionLabel(record: ConnectorCardRecord): string | null {
    const statusItem = connectionStatusForConnector(record);
    if (connectionIsConnectable(statusItem)) {
      return 'Connect';
    }
    if (statusItem) {
      return null;
    }
    return null;
  }

  function consumerSetupState(record: ConnectorCardRecord, statusItem: ConnectionStatusItem | null): ConsumerSetupState | null {
    if (record.connected || statusItem?.connected === true) {
      return null;
    }
    return connectionIsConnectable(statusItem) && connectionUsesManagedSetup(statusItem) ? 'oauth_ready' : null;
  }

  function consumerSetupMessage(record: ConnectorCardRecord, setupState: ConsumerSetupState | null): string | null {
    void record;
    void setupState;
    return null;
  }

  function consumerSetupStatusLabel(setupState: ConsumerSetupState | null): string | null {
    if (setupState === 'oauth_ready') {
      return 'Connect';
    }
    if (setupState === 'oauth_not_wired') {
      return 'Connect';
    }
    if (setupState === 'advanced_only') {
      return 'Advanced setup';
    }
    return null;
  }

  function runtimeDependencyLabel(value: GatewayRuntimeDependency): string {
    if (value === 'gateway_required') {
      return 'Gateway required';
    }
    if (value === 'gateway_optional') {
      return 'Gateway optional';
    }
    return 'Cloud only';
  }

  function withRuntimeDependency(
    record: ExternalIntegrationCardRecord,
    runtimeDependency: GatewayRuntimeDependency,
  ): ExternalIntegrationCardRecord {
    return {
      ...record,
      runtimeDependency,
    };
  }

  function connectorAsExternalCard(record: ConnectorCardRecord): ExternalIntegrationCardRecord {
    const statusItem = connectionStatusForConnector(record);
    const connected = statusItem ? statusItem.connected === true : record.connected;
    const fallbackConnectionId = readString(record.definition.connectorIds?.[0]) || record.id;
    const connectionId = readString(statusItem?.id) || fallbackConnectionId || null;
    const opensAdvancedSetup = record.id === 'webhook';
    const locked = connectionLaunchLocked(statusItem)
      || CONNECTOR_STATIC_LOCKED_IDS.has(record.id)
      || CONNECTOR_STATIC_LOCKED_IDS.has(fallbackConnectionId);
    const connectionProvider = readString(statusItem?.vault_provider)
      || readString(statusItem?.account_provider)
      || readString(statusItem?.connector_id)
      || fallbackConnectionId
      || readString(connectionId);
    const connectionAuthFields = readStringList(statusItem?.auth_required_fields);
    const fallbackAuthFields = CONNECTOR_AUTH_FIELD_FALLBACKS[connectionProvider]
      ?? CONNECTOR_AUTH_FIELD_FALLBACKS[fallbackConnectionId]
      ?? [];
    const managedOAuth = connectionUsesManagedSetup(statusItem)
      || connectionProviderUsesManagedOAuth(connectionProvider)
      || connectionProviderUsesManagedOAuth(fallbackConnectionId);
    const setupState = consumerSetupState(record, statusItem)
      ?? (!statusItem && managedOAuth ? 'oauth_ready' : null);
    const hideCredentialFields = setupState === 'oauth_ready' || managedOAuth;
    const effectiveAuthFields = hideCredentialFields
      ? []
      : fallbackAuthFields.length > 0 ? fallbackAuthFields : connectionAuthFields;
    const canCredentialSetup = !locked && readString(connectionProvider) !== '' && effectiveAuthFields.length > 0;
    const actionLabel = opensAdvancedSetup
      ? 'Open Advanced'
      : setupState === 'oauth_ready'
      ? 'Connect'
      : setupState
        ? null
        : connectorActionLabel(record) ?? (canCredentialSetup ? 'Connect' : null);
    return {
      id: `connector_${record.id}`,
      label: record.label,
      image: record.image,
      detail: locked ? readString(statusItem?.description, record.definition.summary) : describeConnectorCard(record, connected),
      statusLabel: opensAdvancedSetup
        ? 'Advanced setup'
        : locked ? connectionLockedLabel(statusItem, 'Requires setup') : connected ? 'Connected' : consumerSetupStatusLabel(setupState) ?? 'Not connected',
      statusTone: locked ? 'neutral' : connected ? 'connected' : 'neutral',
      summary: record.definition.summary,
      nextStep: locked ? null : connected ? null : record.definition.setupHint,
      actionLabel: locked ? null : actionLabel,
      actionTarget: opensAdvancedSetup
        ? 'advanced'
        : setupState === 'oauth_ready' || canCredentialSetup || (connectionId && connectionIsConnectable(statusItem)) ? 'connection' : 'close',
      connectionId,
      connectionProvider,
      connectionAuthFields: effectiveAuthFields,
      connectorCardId: record.id,
      locked,
      consumerSetupState: setupState,
      consumerSetupMessage: consumerSetupMessage(record, setupState),
    };
  }

  function personalAsExternalCard(record: PersonalCardRecord): ExternalIntegrationCardRecord {
    const statusItem = connectionStatusForPersonal(record);
    const locked = connectionLaunchLocked(statusItem);
    const bridgePersonalChannel = record.id === 'signal_personal'
      || record.id === 'imessage_personal'
      || record.id === 'wechat_personal';
    const bridgeLocked = bridgePersonalChannel || locked;
    const pairLabel = record.channel
      ? record.statusTone === 'connected'
        ? `Manage ${record.label.replace(/^Your\s+/, '')}`
        : `Pair ${record.label.replace(/^Your\s+/, '')}`
      : null;
    return {
      id: record.id,
      label: record.label,
      image: record.image,
      detail: locked && !bridgePersonalChannel ? readString(statusItem?.description, 'Requires Agent Computer bridge.') : record.detail,
      statusLabel: locked && !bridgePersonalChannel ? connectionLockedLabel(statusItem, 'Requires setup') : record.statusLabel,
      statusTone: locked && !bridgePersonalChannel ? 'neutral' : record.statusTone,
      summary: record.summary,
      nextStep: bridgeLocked ? null : record.nextStep,
      actionLabel: bridgeLocked ? null : pairLabel ?? (record.statusTone === 'connected' ? 'Review connection' : 'Set up'),
      secondaryActionLabel: bridgeLocked ? null : record.channel ? 'Agent Computer settings' : null,
      actionTarget: bridgeLocked ? 'close' : 'gateway',
      connectionId: readString(statusItem?.id) || null,
      channel: record.channel ?? null,
      locked: bridgeLocked,
      runtimeDependency: 'gateway_required',
    };
  }

  const knowledgeConnectorCards = useMemo(
    () => connectorCards.filter((card) =>
      card.id === 'drive'
      || card.id === 'notion'
      || card.id === 'uploads'
      || card.id === 'websites'
      || card.id === 'github'
      || card.id === 'dropbox'
      || card.id === 'figma'
      || card.id === 'airtable'
      || card.id === 'canva'
      || card.id === 'asana'
      || card.id === 'clickup'
      || card.id === 'jira'
      || card.id === 'webflow'
      || card.id === 'monday'
      || card.id === 'box'
      || card.id === 'gitlab'
      || card.id === 'bitbucket'
      || card.id === 'confluence'
      || card.id === 'miro'
      || card.id === 'typeform'
      || card.id === 'vercel'
      || card.id === 's3',
    ),
    [connectorCards],
  );

  const knowledgeCards = useMemo<ExternalIntegrationCardRecord[]>(
    () => knowledgeConnectorCards.map(connectorAsExternalCard),
    [knowledgeConnectorCards],
  );

  function connectorAsAppSectionCard(card: ConnectorCardRecord): ExternalIntegrationCardRecord {
    const record = connectorAsExternalCard(card);
    if (record.locked || record.statusTone === 'connected') {
      return {
        ...record,
        connectionAuthFields: [],
      };
    }
    if (record.actionTarget === 'advanced') {
      return {
        ...record,
        connectionAuthFields: [],
        actionLabel: 'Open Advanced',
      };
    }
    return {
      ...record,
      connectionAuthFields: [],
      actionLabel: record.actionLabel ?? (record.connectionId ? 'Connect' : null),
      actionTarget: record.connectionId ? 'connection' : record.actionTarget,
    };
  }

  const appCards = useMemo<ExternalIntegrationCardRecord[]>(
    () => connectorCards
      .filter((card) =>
        card.id === 'gmail'
        || card.id === 'google_calendar'
        || card.id === 'microsoft_365'
        || card.id === 'github'
        || card.id === 'linear'
        || card.id === 'notion'
        || card.id === 'webhook'
        || card.id === 'dropbox'
        || card.id === 'figma'
        || card.id === 'todoist'
        || card.id === 'airtable'
        || card.id === 'canva'
        || card.id === 'asana'
        || card.id === 'hubspot'
        || card.id === 'zoom'
        || card.id === 'calendly'
        || card.id === 'clickup'
        || card.id === 'jira'
        || card.id === 'stripe'
        || card.id === 'salesforce'
        || card.id === 'webflow'
        || card.id === 'monday'
        || card.id === 'box'
        || card.id === 'gitlab'
        || card.id === 'bitbucket'
        || card.id === 'confluence'
        || card.id === 'miro'
        || card.id === 'mailchimp'
        || card.id === 'pipedrive'
        || card.id === 'intercom'
        || card.id === 'docusign'
        || card.id === 'square'
        || card.id === 'typeform'
        || card.id === 'quickbooks'
        || card.id === 'xero'
        || card.id === 'freshbooks'
        || card.id === 'vercel'
        || card.id === 's3'
        || card.id === 'smtp'
        || card.id === 'wechat_work'
        || card.id === 'instagram_business',
      )
      .map(connectorAsAppSectionCard),
    [connectorCards],
  );

  const channelCards = useMemo<ExternalIntegrationCardRecord[]>(
    () => {
      const telegramBotRecord = connectorCards.find((card) => card.id === 'sage_telegram_hosted')
        ?? connectorCards.find((card) => card.id === 'telegram_bot')
        ?? null;
      const telegramPersonalRecord = communicationPersonalCards.find((card) => card.id === 'telegram_personal') ?? null;
      const whatsappPersonalRecord = communicationPersonalCards.find((card) => card.id === 'whatsapp_personal') ?? null;
      const telegramBot = telegramBotRecord ? connectorAsExternalCard(telegramBotRecord) : null;
      const telegramPersonal = telegramPersonalRecord ? personalAsExternalCard(telegramPersonalRecord) : null;
      const whatsappPersonal = whatsappPersonalRecord ? personalAsExternalCard(whatsappPersonalRecord) : null;
      const telegramConnected = telegramBot?.statusTone === 'connected' || telegramPersonal?.statusTone === 'connected';
      const telegramCard: ExternalIntegrationCardRecord | null = telegramBot || telegramPersonal
        ? {
            id: 'telegram',
            label: 'Telegram',
            image: telegramBot?.image ?? telegramPersonal?.image ?? '',
            detail: 'Use hosted Telegram or your personal Telegram account.',
            statusLabel: telegramBot?.statusTone === 'connected'
              ? 'Bot connected'
              : telegramPersonal?.statusTone === 'connected'
                ? 'Personal connected'
                : 'Choose setup',
            statusTone: telegramConnected ? 'connected' : 'neutral',
            summary: 'Choose hosted Telegram for the simple cloud setup, or Personal Telegram when Sage must use your logged-in account through Agent Computer.',
            nextStep: null,
            actionTarget: 'close',
            runtimeDependency: 'gateway_optional',
          }
        : null;
      const whatsappCard: ExternalIntegrationCardRecord | null = null; // WhatsApp personal deprecated — business Twilio path only
      const cloudChannelOrder = ['slack', 'discord_bot', 'whatsapp_twilio'];
      const cloudCards = cloudChannelOrder
        .map((id) => connectorCards.find((card) => card.id === id) ?? null)
        .filter((card): card is ConnectorCardRecord => Boolean(card))
        .map((card) => withRuntimeDependency(connectorAsExternalCard(card), 'cloud_only'));
      const personalCards = communicationPersonalCards
        .filter((card) => card.id !== 'telegram_personal')
        .filter((card) => card.id !== 'whatsapp_personal' || !showPersonalSurface)
        .map((card) => withRuntimeDependency(personalAsExternalCard(card), 'gateway_required'));
      return [
        ...(telegramCard ? [telegramCard] : []),
        ...(whatsappCard ? [whatsappCard] : []),
        ...cloudCards,
        ...(showPersonalSurface ? personalCards : []),
      ];
    },
    [communicationPersonalCards, connectorCards, showPersonalSurface],
  );

  const includeAiRuntimeGroup = normalizeIntegrationCategoryId(searchParams.get('section') ?? searchParams.get('connection')) === 'ai_runtime';

  const integrationGroups = useMemo<IntegrationWorkbenchGroup[]>(() => {
    if (includeAiRuntimeGroup) {
      return [{
        id: 'ai_runtime' as const,
        label: 'AI setup',
        description: 'Default AI route, provider accounts, and model source.',
        detail: aiProviderSummary.activeLabel,
        countLabel: activeProviderCard ? 'Active' : 'Setup',
        statusTone: activeProviderCard ? 'connected' : 'warning',
      }];
    }
    const groups: IntegrationWorkbenchGroup[] = [];
    groups.push({
      id: 'apps',
      label: 'Apps',
      description: 'Apps Sage can connect to.',
      detail: 'Gmail, Calendar, GitHub, Notion, Linear, Dropbox, S3, SMTP, WeChat Work, Instagram, and more.',
      countLabel: `${appCards.length}`,
      statusTone: appCards.some((card) => card.statusTone === 'connected') ? 'connected' : 'neutral',
    });
    groups.push({
      id: 'channels',
      label: 'Channels',
      description: 'Messaging channels Sage can use.',
      detail: 'Telegram, WhatsApp, Signal, iMessage, WeChat, Slack, and Discord.',
      countLabel: `${channelCards.length}`,
      statusTone: channelCards.some((card) => card.statusTone === 'connected') ? 'connected' : 'neutral',
    });
    groups.push({
      id: 'advanced',
      label: 'Advanced',
      description: 'MCP, skills, AI setup, and technical controls.',
      detail: 'MCP, skills, provider routes, custom APIs, and webhooks.',
      countLabel: showTools || mcpServers.length > 0 ? 'Ready' : 'Setup',
      statusTone: showTools || mcpServers.some((server) => server.enabled !== false) ? 'connected' : 'neutral',
    });
    return groups;
  }, [
    activeProviderCard,
    aiProviderSummary.activeLabel,
    includeAiRuntimeGroup,
    mcpServers,
    channelCards,
    appCards,
    showTools,
  ]);

  const selectedIntegrationGroup = useMemo(
    () => integrationGroups.find((group) => group.id === selectedIntegrationId) ?? integrationGroups[0] ?? null,
    [integrationGroups, selectedIntegrationId],
  );

  const requestedIntegrationId = useMemo<IntegrationWorkbenchCategoryId | null>(() => {
    return normalizeIntegrationCategoryId(searchParams.get('section') ?? searchParams.get('connection'));
  }, [searchParams]);

  useEffect(() => {
    if (integrationGroups.length === 0) {
      return;
    }
    if (requestedIntegrationId && integrationGroups.some((group) => group.id === requestedIntegrationId)) {
      setSelectedIntegrationId(requestedIntegrationId);
      return;
    }
    setSelectedIntegrationId((current) => (
      integrationGroups.some((group) => group.id === current)
        ? current
        : integrationGroups[0].id
    ));
  }, [integrationGroups, requestedIntegrationId]);

  useEffect(() => {
    setConnectorMemoryEnabled((current) => {
      const next = { ...current };
      connectorCards.forEach((card) => {
        if (card.connected && typeof next[card.id] !== 'boolean') {
          next[card.id] = true;
        }
      });
      return next;
    });
  }, [connectorCards]);

  async function refreshAfterMutation(successMessage: string): Promise<void> {
    await loadState();
    setStatus(successMessage);
    setError(null);
  }

  function openIntegrationSection(sectionId: IntegrationWorkbenchCategoryId) {
    setSelectedIntegrationId(sectionId);
    setExpandedCardId(null);
    if (typeof window !== 'undefined') {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set('section', sectionId);
      nextUrl.searchParams.delete('connection');
      window.history.replaceState(window.history.state, '', nextUrl.toString());
    }
  }

  async function handleMcpServerSave() {
    const serverId = readString(mcpServerDraft.serverId);
    const endpoint = readString(mcpServerDraft.endpoint);
    if (!serverId || !endpoint) {
      setError('MCP server needs an ID and endpoint.');
      return;
    }
    setBusyCardId('mcp:save');
    try {
      await services.client.saveMcpServer({
        serverId,
        label: readString(mcpServerDraft.label) || serverId,
        endpoint,
        discoverTools: true,
      });
      setMcpServerDraft(DEFAULT_MCP_SERVER_DRAFT);
      await refreshAfterMutation('MCP server saved. Review and approve discovered tools before Sage can use them.');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'MCP server could not be saved.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function startTelegramHostedPairing() {
    setTelegramHostedPairingLoading(true);
    try {
      const resp = await fetch('/api/sage/telegram-hosted/pair/start', {
        method: 'POST',
        headers: buildCookieAuthHeaders('POST', { 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ workspace_id: bootstrap.workspace.id }),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error((errData as any)?.detail || 'Failed to start pairing');
      }
      const data = await resp.json() as { pairing_code: string; deep_link: string; status: string };
      if (data.deep_link) {
        setTelegramHostedPairing({ pairingCode: data.pairing_code, deepLink: data.deep_link });
        window.open(data.deep_link, '_blank');
        // Poll for pairing completion
        setTelegramHostedPolling(true);
        const poll = async () => {
          try {
            // First, poll Telegram for pending messages (local dev — webhook can't reach us)
            await fetch('/api/sage/telegram-hosted/dev-poll', {
              method: 'POST',
              headers: buildCookieAuthHeaders('POST'),
              credentials: 'include',
            }).catch(() => {});
            // Then check if pairing completed
            const statusResp = await fetch('/api/sage/telegram-hosted/pair/status?workspace_id=' + encodeURIComponent(bootstrap.workspace.id), {
              headers: buildCookieAuthHeaders('GET'),
              credentials: 'include',
            });
            if (statusResp.ok) {
              const statusData = await statusResp.json() as { paired: boolean };
              if (statusData.paired) {
                setTelegramHostedPaired(true);
                setTelegramHostedPolling(false);
                return;
              }
            }
          } catch { /* ignore polling errors */ }
          setTimeout(poll, 3000);
        };
        setTimeout(poll, 2000);
      } else if (data.pairing_code) {
        setTelegramHostedPairing({ pairingCode: data.pairing_code, deepLink: '' });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start Telegram pairing.');
    } finally {
      setTelegramHostedPairingLoading(false);
    }
  }

  async function startDiscordPairing() {
    setDiscordPairingLoading(true);
    try {
      const resp = await fetch('/api/sage/discord/pair/start', {
        method: 'POST',
        headers: buildCookieAuthHeaders('POST', { 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ workspace_id: bootstrap.workspace.id }),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error((errData as any)?.detail || 'Failed to start pairing');
      }
      const data = await resp.json() as { pairing_code: string; status: string };
      setDiscordPairingCode(data.pairing_code || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start Discord pairing.');
    } finally {
      setDiscordPairingLoading(false);
    }
  }

  async function handleConnectionSetupStart(record: ExternalIntegrationCardRecord) {
    const connectionId = readString(record.connectionId);
    if (!connectionId) {
      setCardError(record.id, 'Connection setup is not available yet.');
      return;
    }
    clearCardError(record.id);
    setBusyCardId(record.id);
    try {
      const response = await services.client.startConnectionSetup({
        connectionId,
        surface,
        selectedGatewayId,
        metadata: {
          source: 'sage_connectors',
          card_id: record.id,
        },
      });
      const session = response && typeof response === 'object'
        ? response.setup_session as Record<string, unknown> | undefined
        : undefined;
      const sessionId = readString(session?.id);
      const nextAction = response && typeof response === 'object'
        ? readString(response.next_action).toLowerCase()
        : '';
      if (nextAction === 'oauth_redirect') {
        const authorizationUrl = response && typeof response === 'object'
          ? readString(response.authorization_url)
          : '';
        if (!authorizationUrl) {
          throw new Error('Provider login did not return a redirect URL.');
        }
        // ── TODO: Phase 1 bridge — OAuth callback wiring ──────────────────────
        // The OAuth flow redirects to the provider, then the provider redirects
        // BACK to the backend at GET /api/connections/oauth/{provider}/callback
        // (routes_connections.py:497, complete_connection_oauth_callback).
        //
        // Currently that callback calls complete_oauth_callback() which stores
        // the credential in the vault but does NOT register any MCP servers.
        //
        // For apps that have an mcpEndpoint (declared in CONNECTOR_DETAIL_MAP),
        // the callback should instead call connect_app_via_oauth_to_mcp()
        // (connection_oauth_service.py:1342) which does:
        //   1. Exchange OAuth code → token
        //   2. Store credential in vault
        //   3. Look up MCP server URLs from APP_MCP_SERVER_MAP
        //   4. Register MCP servers with credential injection
        //   5. Discover tools from the MCP servers
        //
        // The endpoint is already registered at POST /api/apps/{provider}/oauth/complete
        // (routes_connectors.py:520).  The frontend CANNOT call it because we
        // never see the OAuth authorization code — the backend exchanges it.
        //
        // Fix: In routes_connections.py complete_connection_oauth_callback,
        // after decoding the state, call connect_app_via_oauth_to_mcp() instead of
        // complete_oauth_callback() (or call both in sequence). The bridge function
        // handles all the MCP server registration automatically.
        // ───────────────────────────────────────────────────────────────────────
        setStatus('Opening provider sign-in...');
        window.location.assign(authorizationUrl);
        return;
      }
      if (nextAction === 'connector_vault_setup') {
        setStatus('Enter the connection details and save credentials from this card.');
        setExpandedCardId(record.id);
      } else if (nextAction === 'agent_computer_local_bridge_setup') {
        const bridgeContract = response && typeof response === 'object'
          ? response.bridge_contract as Record<string, unknown> | undefined
          : undefined;
        const env = bridgeContract && typeof bridgeContract.gateway_env === 'object'
          ? bridgeContract.gateway_env as Record<string, unknown>
          : {};
        const urlEnv = readString(env.url);
        setStatus(urlEnv
          ? `Configure ${urlEnv} on the selected Agent Computer, then refresh this card.`
          : 'Configure this bridge on the selected Agent Computer, then refresh this card.');
        setExpandedCardId(record.id);
      } else {
        setStatus(sessionId ? 'Setup session started.' : 'Connection setup started.');
        setExpandedCardId(null);
      }
      setError(null);
      await loadState().catch(() => undefined);
    } catch (connectionError) {
      const message = humanizeConnectionSetupError(connectionError, record.label);
      setCardError(record.id, message);
      // Keep the card expanded so the user can see the error and retry
      setExpandedCardId(record.id);
    } finally {
      setBusyCardId(null);
    }
  }

  function connectorCredentialFieldLabel(field: string): string {
    if (field === 'access_token') {
      return 'Access token';
    }
    if (field === 'personal_access_token') {
      return 'Personal access token';
    }
    if (field === 'integration_token') {
      return 'Integration token';
    }
    if (field === 'api_key') {
      return 'API key';
    }
    if (field === 'webhook_url') {
      return 'Webhook URL';
    }
    if (field === 'aws_access_key_id') {
      return 'AWS access key ID';
    }
    if (field === 'aws_secret_access_key') {
      return 'AWS secret access key';
    }
    if (field === 'region') {
      return 'Region';
    }
    if (field === 'instagram_account_id') {
      return 'Instagram account ID';
    }
    if (field === 'page_id') {
      return 'Page ID';
    }
    if (field === 'app_id') {
      return 'App ID';
    }
    if (field === 'installation_id') {
      return 'Installation ID';
    }
    if (field === 'private_key_pem') {
      return 'Private key';
    }
    if (field === 'bot_token') {
      return 'Bot token';
    }
    if (field === 'chat_id') {
      return 'Target chat ID';
    }
    if (field === 'user_token') {
      return 'User token';
    }
    if (field === 'team_id') {
      return 'Team ID';
    }
    if (field === 'team_name') {
      return 'Team name';
    }
    if (field === 'channel_id') {
      return 'Channel ID';
    }
    if (field === 'guild_id') {
      return 'Guild ID';
    }
    if (field === 'application_id') {
      return 'Application ID';
    }
    if (field === 'public_key') {
      return 'Public key';
    }
    if (field === 'application_public_key') {
      return 'Application public key';
    }
    if (field === 'account_sid') {
      return 'Account SID';
    }
    if (field === 'auth_token') {
      return 'Auth token';
    }
    if (field === 'from_number') {
      return 'From number';
    }
    if (field === 'to_number') {
      return 'To number';
    }
    if (field === 'host') {
      return 'SMTP host';
    }
    if (field === 'port') {
      return 'Port';
    }
    if (field === 'username') {
      return 'Username';
    }
    if (field === 'password') {
      return 'Password';
    }
    if (field === 'use_tls') {
      return 'Use TLS';
    }
    const normalized = field.replace(/_/g, ' ').trim();
    return normalized ? normalized.replace(/\b\w/g, (match) => match.toUpperCase()) : 'Value';
  }

  function connectorCredentialInputType(field: string): string {
    const token = field.toLowerCase();
    return token.includes('token')
      || token.includes('secret')
      || token.includes('password')
      || token.includes('private_key')
      || token.includes('api_key')
      ? 'password'
      : 'text';
  }

  function connectorCredentialPlaceholder(field: string): string {
    if (field === 'access_token') {
      return 'Access token';
    }
    if (field === 'personal_access_token') {
      return 'Personal access token';
    }
    if (field === 'integration_token') {
      return 'Integration token';
    }
    if (field === 'api_key') {
      return 'API key';
    }
    if (field === 'webhook_url') {
      return 'https://...';
    }
    if (field === 'aws_access_key_id') {
      return 'AKIA...';
    }
    if (field === 'aws_secret_access_key') {
      return 'AWS secret access key';
    }
    if (field === 'region') {
      return 'us-east-1';
    }
    if (field === 'instagram_account_id') {
      return 'Instagram business account ID';
    }
    if (field === 'page_id') {
      return 'Facebook page ID (optional)';
    }
    if (field === 'app_id') {
      return 'App ID';
    }
    if (field === 'installation_id') {
      return 'Installation ID';
    }
    if (field === 'private_key_pem') {
      return '-----BEGIN PRIVATE KEY-----';
    }
    if (field === 'bot_token') {
      return '123456789:AA...';
    }
    if (field === 'chat_id') {
      return 'Chat ID or @channel username';
    }
    if (field === 'user_token') {
      return 'xoxp-...';
    }
    if (field === 'team_id') {
      return 'T0123456789';
    }
    if (field === 'team_name') {
      return 'Workspace name';
    }
    if (field === 'channel_id') {
      return 'Channel ID';
    }
    if (field === 'guild_id') {
      return 'Guild ID';
    }
    if (field === 'application_id') {
      return 'Application ID';
    }
    if (field === 'public_key' || field === 'application_public_key') {
      return 'Discord application public key';
    }
    if (field === 'host') {
      return 'smtp.example.com';
    }
    if (field === 'port') {
      return '587';
    }
    if (field === 'username') {
      return 'user@example.com';
    }
    if (field === 'password') {
      return 'App password';
    }
    if (field === 'use_tls') {
      return 'true';
    }
    return connectorCredentialFieldLabel(field);
  }

  function connectorCredentialFieldLabelForRecord(record: ExternalIntegrationCardRecord, field: string): string {
    const provider = readString(record.connectionProvider).toLowerCase();
    if (provider === 'github' && field === 'personal_access_token') {
      return 'Personal access token';
    }
    if (provider === 'notion' && field === 'integration_token') {
      return 'Notion integration token';
    }
    if (provider === 'linear' && field === 'api_key') {
      return 'Linear API key';
    }
    if (provider === 'dropbox' && field === 'access_token') {
      return 'Dropbox access token';
    }
    if (provider === 'wechat_work' && field === 'webhook_url') {
      return 'WeChat Work webhook URL';
    }
    if (provider === 'instagram_business' && field === 'access_token') {
      return 'Instagram access token';
    }
    if (provider === 'telegram_bot' && field === 'bot_token') {
      return 'Telegram bot token';
    }
    if (provider === 'discord_bot' && field === 'bot_token') {
      return 'Discord bot token';
    }
    if (provider === 'slack' && field === 'bot_token') {
      return 'Slack bot token';
    }
    return connectorCredentialFieldLabel(field);
  }

  function connectorCredentialSaveLabel(record: ExternalIntegrationCardRecord): string {
    const provider = readString(record.connectionProvider).toLowerCase();
    if (provider === 'telegram_bot') {
      return 'Save connection';
    }
    if (provider === 'discord_bot' || provider === 'slack' || provider === 'whatsapp_twilio') {
      return 'Connect channel';
    }
    return 'Connect account';
  }

  function updateConnectorDraftCredential(cardId: string, field: string, value: string) {
    setConnectorDraftCredentials((current) => ({
      ...current,
      [cardId]: {
        ...(current[cardId] ?? {}),
        [field]: value,
      },
    }));
  }

  async function handleConnectorCredentialSave(record: ExternalIntegrationCardRecord): Promise<void> {
    const provider = readString(record.connectionProvider);
    const fields = (record.connectionAuthFields ?? []).filter(Boolean);
    if (!provider || fields.length === 0) {
      setError('Connection setup is not available yet.');
      return;
    }
    const draft = connectorDraftCredentials[record.id] ?? {};
    const credentials: Record<string, string | number | boolean> = {};
    const missing: string[] = [];
    const optionalFields = new Set(['port', 'use_tls', 'region', 'page_id', 'application_id', 'public_key', 'application_public_key', 'channel_id', 'guild_id', 'user_token', 'team_id', 'team_name']);
    fields.forEach((field) => {
      const value = readString(draft[field]);
      if (!value) {
        if (field === 'use_tls') {
          credentials[field] = true;
        } else if (field === 'region') {
          credentials[field] = 'us-east-1';
        } else if (!optionalFields.has(field)) {
          missing.push(connectorCredentialFieldLabelForRecord(record, field));
        }
        return;
      }
      if (field === 'port') {
        const parsed = Number(value);
        credentials[field] = Number.isFinite(parsed) ? parsed : value;
      } else if (field === 'use_tls') {
        credentials[field] = ['true', '1', 'yes', 'on'].includes(value.toLowerCase());
      } else {
        credentials[field] = value;
      }
    });
    if (missing.length > 0) {
      setError(`${missing.join(', ')} required.`);
      return;
    }
    setBusyCardId(record.id);
    setError(null);
    try {
      await services.client.createStudioChannelAccount({
        provider,
        label: record.label,
        credentials,
        metadata: {
          source: 'sage_connectors',
          connection_id: record.connectionId,
          card_id: record.id,
        },
        skipValidation: false,
      });
      const normalizedProvider = provider.toLowerCase();
      let verifiedAccountName = '';
      if (record.connectionId && (normalizedProvider === 'telegram_bot' || normalizedProvider === 'discord_bot')) {
        const verifyPayload = await services.client.verifyConnection({
          connectionId: record.connectionId,
          surface,
          selectedGatewayId,
        });
        if (verifyPayload?.valid !== true) {
          throw new Error('Connection could not be verified.');
        }
        verifiedAccountName = readString(verifyPayload.account_name);
      }
      setConnectorDraftCredentials((current) => {
        const next = { ...current };
        delete next[record.id];
        return next;
      });
      await refreshAfterMutation(
        verifiedAccountName ? `${record.label} connected as ${verifiedAccountName}.` : `${record.label} connected.`,
      );
      setExpandedCardId(null);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Connection details could not be saved.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function chooseSageAgentComputer(gatewayId: string) {
    const cleanGatewayId = gatewayId.trim();
    if (!cleanGatewayId) {
      return;
    }
    setBusyCardId(`gateway:${cleanGatewayId}`);
    setPersonalChannelError(null);
    try {
      await services.client.setSageAgentComputerSelection({
        selectedGatewayId: cleanGatewayId,
        metadata: { source: 'sage_connectors' },
      });
      await loadState();
    } catch (selectionError) {
      setPersonalChannelError(selectionError instanceof Error ? selectionError.message : 'Could not choose Sage Agent Computer.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleMcpServerRefresh(server: McpServerRecord) {
    const serverId = readString(server.id);
    if (!serverId) {
      return;
    }
    setBusyCardId(`mcp:${serverId}:refresh`);
    try {
      await services.client.refreshMcpServer({ serverId });
      await refreshAfterMutation('MCP tools refreshed. New tools stay hidden until approved.');
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : 'MCP server could not refresh.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleMcpToolApprove(server: McpServerRecord, tool: McpToolRecord) {
    const serverId = readString(server.id);
    const toolName = readString(tool.name);
    if (!serverId || !toolName) {
      return;
    }
    setBusyCardId(`mcp:${serverId}:${toolName}:approve`);
    try {
      await services.client.approveMcpTool({ serverId, toolName });
      await refreshAfterMutation(`${readString(tool.label, toolName)} approved for Sage.`);
    } catch (approveError) {
      setError(approveError instanceof Error ? approveError.message : 'MCP tool could not be approved.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleMcpServerDelete(server: McpServerRecord) {
    const serverId = readString(server.id);
    if (!serverId) {
      return;
    }
    const confirmed = window.confirm(`Remove MCP server ${readString(server.label, serverId)} from this workspace?`);
    if (!confirmed) {
      return;
    }
    setBusyCardId(`mcp:${serverId}:delete`);
    try {
      await services.client.deleteMcpServer({ serverId });
      await refreshAfterMutation('MCP server removed.');
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'MCP server could not be removed.');
    } finally {
      setBusyCardId(null);
    }
  }

  function markLogoFailed(id: string) {
    setFailedLogos((current) => {
      const next = new Set(current);
      next.add(id);
      return next;
    });
  }

  async function handleProviderSave(record: ProviderCardRecord): Promise<void> {
    const draftApiKey = (providerDraftKeys[record.id] ?? '').trim();
    const draftBaseUrl = (providerDraftBaseUrls[record.id] ?? '').trim();
    if (providerRequiresSecret(record.provider, record.profile) && !draftApiKey) {
      setError(`${providerCredentialLabel(record.provider, record.profile)} is required before Sage can connect this provider.`);
      return;
    }
    if (providerNeedsBaseUrl(record.provider) && !draftBaseUrl) {
      setError(`${providerBaseUrlLabel(record.provider)} is required before Sage can fetch models for this provider.`);
      return;
    }
    setBusyCardId(record.id);
    setError(null);
    setStatus(null);
    try {
      await services.client.upsertWorkspaceProviderCredential({
        provider: record.provider.id,
        apiKey: draftApiKey || null,
        baseUrl: draftBaseUrl || null,
        model: null,
      });
      emitWorkstationProviderChanged({
        workspaceId: services.scope.workspaceId,
        providerId: record.provider.id,
        action: 'saved',
      });
      await refreshAfterMutation(`${record.label} is now connected.`);
      setProviderDraftKeys((current) => ({ ...current, [record.id]: '' }));
      setProviderDraftBaseUrls((current) => ({ ...current, [record.id]: '' }));
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'AI model connection failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function setActiveProvider(
    record: ProviderCardRecord,
    {
      hosted = false,
      profileOverride = null,
      credentialOverride = null,
    }: {
      hosted?: boolean;
      profileOverride?: ProviderProfileRecord | null;
      credentialOverride?: VaultCredentialRecord | null;
    } = {},
  ): Promise<void> {
    const activeModel = providerActiveModelLabel(record);
    const overrideProviderId = readString(profileOverride?.provider).toLowerCase();
    const mergedProfiles = profileOverride
      ? [
          ...profiles.filter((profile) => {
            const sameId = readString(profile.id) && readString(profile.id) === readString(profileOverride.id);
            const sameProvider = overrideProviderId && readString(profile.provider).toLowerCase() === overrideProviderId;
            return !sameId && !sameProvider;
          }),
          profileOverride,
        ]
      : profiles;
    const nextProfiles = sortProfiles(mergedProfiles).filter((profile) => {
      const providerId = readString(profile.provider).toLowerCase();
      return providerId && providerCards.some((card) => card.provider.id === providerId);
    });
    const targetProviderId = record.provider.id;
    const targetProfile = nextProfiles.find((profile) => readString(profile.provider).toLowerCase() === targetProviderId) ?? null;
    if (!targetProfile) {
      await services.client.upsertProviderProfile({
        id: null,
        provider: targetProviderId,
        label: `Sage ${record.provider.label}`,
        credentialId: readString(credentialOverride?.id) || readString(record.credential?.id) || readString(profileOverride?.credential_id) || null,
        authMode: readString(profileOverride?.auth_mode) || readString(record.provider.defaultAuthMode) || null,
        priority: 10,
        enabled: true,
        model: activeModel || null,
        metadata: {
          chat_model_selection: 'default',
        },
      });
    }

    const hostedPreset = hosted && ['light', 'pro'].includes(activeModel.toLowerCase())
      ? activeModel.toLowerCase()
      : null;
    const routeKind: WorkspaceAiRouteKind = hosted
      ? 'empyralis_managed'
      : providerIsLocalOnly(record)
        ? 'local_model'
        : 'user_api_key';
    const routeUpdate = {
      kind: routeKind,
      provider: targetProviderId,
      model: activeModel || null,
      modelPreset: hostedPreset,
    };
    try {
      await services.client.updateWorkspaceAiRouteDefault(routeUpdate);
    } catch (routeError) {
      const staleProviderId = readString(workspaceDefaultRoute?.providerId).toLowerCase();
      if (
        errorMentionsMissingCredential(routeError)
        && staleProviderId
        && staleProviderId !== targetProviderId
      ) {
        await services.client.deleteWorkspaceProviderCredential({ provider: staleProviderId });
        emitWorkstationProviderChanged({
          workspaceId: services.scope.workspaceId,
          providerId: staleProviderId,
          action: 'deleted',
        });
        await services.client.updateWorkspaceAiRouteDefault(routeUpdate);
      } else {
        throw routeError;
      }
    }

    emitWorkstationProviderChanged({
      workspaceId: services.scope.workspaceId,
      providerId: record.provider.id,
      action: 'saved',
    });
    await refreshAfterMutation(
      hosted
        ? 'Empyralis default model is now active.'
        : `${record.label} is now active.`,
    );
  }

  async function handleProviderSelect(record: ProviderCardRecord, { hosted = false }: { hosted?: boolean } = {}) {
    if (providerNeedsGateway(record.provider.id) && !localCompanionOnline) {
      setError(`${record.label} requires a connected local computer before it can be selected.`);
      return;
    }
    if (!hosted && !providerPickerConnected(record, localCompanionOnline) && providerRequiresSecret(record.provider, record.profile)) {
      setProviderPickerDraftId(record.id);
      setStatus(null);
      setError(null);
      return;
    }
    setBusyCardId(record.id);
    setError(null);
    setStatus(null);
    try {
      if (!providerPickerConnected(record, localCompanionOnline) && !providerRequiresSecret(record.provider, record.profile)) {
        await handleProviderSave(record);
      }
      await setActiveProvider(record, { hosted });
      setProviderPickerDraftId(null);
      setProviderPickerOpen(false);
    } catch (selectionError) {
      setError(selectionError instanceof Error ? selectionError.message : 'Could not select this AI model.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleProviderConnectAndSelect(record: ProviderCardRecord) {
    const draftApiKey = (providerDraftKeys[record.id] ?? '').trim();
    const draftBaseUrl = (providerDraftBaseUrls[record.id] ?? '').trim();
    if (!draftApiKey) {
      setError(`${providerCredentialLabel(record.provider, record.profile)} is required before Sage can connect this provider.`);
      return;
    }
    if (providerNeedsBaseUrl(record.provider) && !draftBaseUrl) {
      setError(`${providerBaseUrlLabel(record.provider)} is required before Sage can fetch models for this provider.`);
      return;
    }
    setBusyCardId(record.id);
    setError(null);
    setStatus(null);
    try {
      const savedProvider = await services.client.upsertWorkspaceProviderCredential({
        provider: record.provider.id,
        apiKey: draftApiKey,
        baseUrl: draftBaseUrl || null,
        model: null,
      });
      const savedProfile = normalizeProviderProfiles({ items: [savedProvider?.['profile']] })[0] ?? null;
      const savedCredential = normalizeVaultCredentials({ items: [savedProvider?.['credential']] })[0] ?? null;
      emitWorkstationProviderChanged({
        workspaceId: services.scope.workspaceId,
        providerId: record.provider.id,
        action: 'saved',
      });
      await loadState();
      setProviderDraftKeys((current) => ({ ...current, [record.id]: '' }));
      setProviderDraftBaseUrls((current) => ({ ...current, [record.id]: '' }));
      await setActiveProvider(
        {
          ...record,
          profile: savedProfile ?? record.profile,
          credential: savedCredential ?? record.credential,
          connected: true,
          status: 'connected',
        },
        {
          profileOverride: savedProfile,
          credentialOverride: savedCredential,
        },
      );
      setProviderPickerDraftId(null);
      setProviderPickerOpen(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'AI model connection failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleProviderDisconnect(record: ProviderCardRecord): Promise<void> {
    setBusyCardId(record.id);
    setError(null);
    setStatus(null);
    try {
      await services.client.deleteWorkspaceProviderCredential({ provider: record.provider.id });
      emitWorkstationProviderChanged({
        workspaceId: services.scope.workspaceId,
        providerId: record.provider.id,
        action: 'deleted',
      });
      await refreshAfterMutation(`${record.label} disconnected.`);
      setExpandedCardId(null);
    } catch (disconnectError) {
      setError(disconnectError instanceof Error ? disconnectError.message : 'AI model disconnect failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleConnectorDisconnect(record: ConnectorCardRecord): Promise<void> {
    if (!record.credential?.id) {
      setStatus(`${record.label} is not managed from this panel yet.`);
      return;
    }
    setBusyCardId(record.id);
    setError(null);
    setStatus(null);
    try {
      await services.client.deleteConnectorVaultCredential({ credentialId: record.credential.id });
      await refreshAfterMutation(`${record.label} disconnected.`);
      setExpandedCardId(null);
    } catch (disconnectError) {
      setError(disconnectError instanceof Error ? disconnectError.message : 'Connected app disconnect failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  function updateChannelDraft(channel: PersonalCommunicationChannel, patch: Partial<PersonalChannelDraft>) {
    setChannelDrafts((current) => ({
      ...current,
      [channel]: {
        ...current[channel],
        ...patch,
      },
    }));
  }

  async function handlePersonalChannelSetup(
    channel: PersonalCommunicationChannel,
    setupStep?: TelegramSetupStep,
  ): Promise<void> {
    setError(null);
    setSelectedIntegrationId('channels');
    if (typeof window !== 'undefined') {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set('section', 'channels');
      nextUrl.searchParams.delete('connection');
      window.history.replaceState(window.history.state, '', nextUrl.toString());
    }
    if (!selectedGatewayId) {
      setPersonalChannelError('Choose Sage Agent Computer before setting up a personal channel.');
      return;
    }
    if (!selectedGatewayOnline) {
      setPersonalChannelError('Agent Computer offline — connect Hardware first.');
      return;
    }
    const draft = channelDrafts[channel];
    const payload: Record<string, unknown> = {};
    if (channel === 'whatsapp') {
      if (draft.phoneNumber.trim()) {
        payload.phone_number = draft.phoneNumber.trim();
      }
    } else {
      const step = setupStep ?? telegramSetupStep;
      const apiId = Number.parseInt(draft.apiId.trim(), 10);
      if (Number.isFinite(apiId)) {
        payload.api_id = apiId;
      }
      if (draft.apiHash.trim()) {
        payload.api_hash = draft.apiHash.trim();
      }
      if ((step === 'phone' || step === 'code' || step === 'password') && draft.phoneNumber.trim()) {
        payload.phone_number = draft.phoneNumber.trim();
      }
      if ((step === 'code' || step === 'password') && draft.loginCode.trim()) {
        payload.login_code = draft.loginCode.trim();
      }
      if (step === 'password' && draft.password.trim()) {
        payload.password = draft.password.trim();
      }
    }
    if (Object.keys(payload).length === 0) {
      setPersonalChannelError(
        channel === 'telegram'
          ? telegramSetupStep === 'code'
            ? 'Enter the Telegram login code first.'
            : telegramSetupStep === 'password'
              ? 'Enter the Telegram two-step verification password first.'
              : 'Enter a Telegram phone number first.'
          : 'Enter a WhatsApp phone number first.',
      );
      return;
    }
    setBusyCardId(`${channel}_personal`);
    setPersonalChannelError(null);
    setStatus(null);
    try {
      const setupResult = await services.client.requestJson<Record<string, unknown>>({
        path: `/api/personal-channels/${channel}/gateways/${encodeURIComponent(selectedGatewayId)}/setup`,
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify(payload),
        },
      });
      if (channel === 'telegram') {
        const setupState = readTelegramStatePayload(setupResult) ?? asConnectorPayload(setupResult);
        const setupStatus = String(setupState?.['status'] ?? '').trim();
        const setupLoginHint = String(setupState?.['login_hint'] ?? setupState?.['loginHint'] ?? '').trim();
        const nextStep = resolveTelegramSetupStep(setupResult, telegramSetupStep);
        if (setupState) {
          setTelegramPersonal((current) => ({
            ...(current ?? {}),
            state: setupState as PersonalChannelStateRecord,
          }));
        }
        setTelegramSetupStep(nextStep);
        if (setupStatus === 'authorization_required' && setupLoginHint === 'api_credentials_required') {
          setTelegramSetupStep('phone');
          setPersonalChannelError('Telegram cannot send a code yet. Platform Telegram credentials are not configured for Agent Computer.');
          return;
        }
        if (setupStatus === 'authorization_required' && setupLoginHint === 'phone_number_required') {
          setTelegramSetupStep('phone');
          setPersonalChannelError('Enter your Telegram phone number first.');
          return;
        }
        setPersonalChannelError(null);
        if (nextStep === 'connected') {
          await loadState().catch(() => undefined);
        }
      } else {
        await loadState().catch(() => undefined);
      }
    } catch (setupError) {
      setPersonalChannelError(setupError instanceof Error ? setupError.message : 'Personal Channels setup failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  async function handlePersonalChannelTest(channel: PersonalCommunicationChannel): Promise<void> {
    if (!selectedGatewayId) {
      setPersonalChannelError('Connect the selected Agent Computer before sending a personal channel test.');
      return;
    }
    const draft = channelDrafts[channel];
    if (!draft.recipient.trim() || !draft.text.trim()) {
      setPersonalChannelError(`Enter a ${channel === 'telegram' ? 'Telegram recipient' : 'WhatsApp recipient'} and test message first.`);
      return;
    }
    setBusyCardId(`${channel}_personal:test`);
    setPersonalChannelError(null);
    setStatus(null);
    try {
      await services.client.requestJson<Record<string, unknown>>({
        path: `/api/personal-channels/${channel}/gateways/${encodeURIComponent(selectedGatewayId)}/messages`,
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            remote_jid: draft.recipient.trim(),
            text: draft.text.trim(),
            idempotency_key: `integrations-${channel}-${Date.now().toString(36)}`,
          }),
        },
      });
      setStatus(
        channel === 'telegram'
          ? 'Telegram test requested through Agent Computer.'
          : 'WhatsApp test requested through Agent Computer.',
      );
      await loadState().catch(() => undefined);
    } catch (testError) {
      setPersonalChannelError(testError instanceof Error ? testError.message : 'Personal Channels test failed.');
    } finally {
      setBusyCardId(null);
    }
  }

  function handleProviderKeyPaste(providerId: string, event: ClipboardEvent<HTMLInputElement>) {
    const pastedText = event.clipboardData.getData('text');
    if (!pastedText) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setProviderDraftKeys((current) => ({ ...current, [providerId]: pastedText }));
  }

  function openGatewaySurface() {
    router.push(routeManifest.routeIndex.hardware?.href ?? `/w/${encodeURIComponent(workspaceId)}/hardware`);
  }

  function openComputerConnectSheet() {
    setExpandedCardId(null);
    setComputerConnectOpen(true);
  }

  function openWorkspaceRoute(routeId: 'gateway' | 'channels' | 'gatewayActivity' | 'gatewayApprovals') {
    if (routeId === 'gateway' || routeId === 'gatewayActivity' || routeId === 'gatewayApprovals') {
      router.push(routeManifest.routeIndex.hardware?.href ?? `/w/${encodeURIComponent(workspaceId)}/hardware`);
      return;
    }

    router.push(routeManifest.routeIndex.channels?.href ?? `/w/${encodeURIComponent(workspaceId)}/channels`);
  }

  function openRecipeInSage(recipe: SageRecipeDefinition) {
    const chatHref = routeManifest.routeIndex.chat?.href ?? `/w/${encodeURIComponent(workspaceId)}/sage`;
    router.push(`${chatHref}?prompt=${encodeURIComponent(recipe.starterPrompt)}`);
  }

  function openBillingSettings() {
    router.push(`/w/${encodeURIComponent(workspaceId)}/settings?section=billing`);
  }

  function providerApiDisplayLabel(record: ProviderCardRecord): string {
    switch (record.provider.id) {
      case 'deepseek':
        return 'DeepSeek API';
      case 'gemini':
        return 'Google Gemini API';
      case 'openai':
        return 'OpenAI API';
      case 'anthropic':
        return 'Anthropic API';
      case 'ollama_cloud':
        return 'Ollama Cloud API';
      case 'ollama':
        return 'Ollama on Agent Computer';
      case 'openai-codex':
        return 'OpenAI Codex transport';
      default:
        return `${record.label} API`;
    }
  }

  function renderAiProviderChoiceCard(record: ProviderCardRecord, section: ProviderPickerSection) {
    if (section.id === 'hosted') {
      return null;
    }
    const needsGateway = providerNeedsGateway(record.provider.id) && !localCompanionOnline;
    const isActive = activeProviderCard?.id === record.id;
    const connected = providerPickerConnected(record, localCompanionOnline);
    const requiresSecret = providerRequiresSecret(record.provider, record.profile);
    const modelLabel = providerActiveModelLabel(record);
    const statusLabel = isActive
      ? 'Active'
      : connected
        ? 'Connected'
        : needsGateway
          ? 'Connect computer'
          : requiresSecret
            ? 'Add API key'
            : 'Available';
    const actionLabel = isActive ? 'Selected' : connected ? 'Use' : requiresSecret ? 'Add key' : 'Use';
    const isSubscriptionTransport = providerIsPersonalSubscriptionTransport(record);
    const detail = connected
      ? `${modelLabel} · ${providerPathLabel(record)}`
      : needsGateway
        ? `Hardware only — ${isSubscriptionTransport ? 'uses your personal subscription through Agent Computer. Not available on cloud.' : 'connect a computer before using this provider.'}`
        : requiresSecret
          ? 'Add an API key to use this provider.'
          : providerPathLabel(record);
    return (
      <button
        key={`${section.id}-${record.id}`}
        type="button"
        className={joinClassNames(
          'sage-ai-provider-card',
          isActive && 'sage-ai-provider-card--active',
        )}
        disabled={busyCardId === record.id}
        onClick={() => {
          if (!connected && requiresSecret) {
            setProviderPickerOpen(true);
            setProviderPickerDraftId(record.id);
            setStatus(null);
            setError(null);
            return;
          }
          void handleProviderSelect(record, { hosted: false });
        }}
      >
        <BrandLogo
          id={record.id}
          label={platformSafeLabel(record.provider.credentialPlane, record.label)}
          src={platformSafeImage(record.provider.credentialPlane, record.image)}
          failedLogos={failedLogos}
          onError={markLogoFailed}
        />
        <span className="sage-ai-provider-card__copy">
          <span className="sage-ai-provider-card__topline">
            <strong className="sage-ai-provider-card__name">{providerApiDisplayLabel(record)}</strong>
          </span>
          <span className="sage-ai-provider-card__detail">{detail}</span>
        </span>
        <span className={joinClassNames('sage-ai-provider-card__status', connected && 'sage-ai-provider-card__status--connected')}>
          <span
            className={joinClassNames(
              'sage-ai-provider-card__dot',
              connected && 'sage-ai-provider-card__dot--connected',
            )}
            aria-hidden="true"
          />
          {statusLabel}
        </span>
        <span className="sage-ai-provider-card__action">{actionLabel}</span>
      </button>
    );
  }

  function renderConnectorCard(record: ConnectorCardRecord) {
    const isExpanded = expandedCardId === record.id;
    return (
      <button
        key={record.id}
        type="button"
        className={joinClassNames('sage-unified-card', isExpanded && 'sage-unified-card--selected')}
        onClick={() => {
          setExpandedCardId(isExpanded ? null : record.id);
        }}
      >
        <BrandLogo
          id={record.id}
          label={record.label}
          src={record.image}
          failedLogos={failedLogos}
          onError={markLogoFailed}
        />
        <strong className="sage-unified-card__title">{record.label}</strong>
        <span className="sage-unified-card__detail">{describeConnectorCard(record)}</span>
        <span className={joinClassNames('sage-unified-card__status', record.status === 'connected' && 'sage-unified-card__status--connected')}>
          {record.status === 'connected' ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
          {record.status === 'connected' ? 'Connected' : 'Not connected'}
        </span>
      </button>
    );
  }

  function personalStatusClassName(record: { statusTone: PersonalCardStatusTone }): string | null {
    if (record.statusTone === 'connected') {
      return 'sage-unified-card__status--connected';
    }
    if (record.statusTone === 'warning') {
      return 'sage-unified-card__status--warning';
    }
    if (record.statusTone === 'danger') {
      return 'sage-unified-card__status--danger';
    }
    return null;
  }

  function renderPersonalCard(record: PersonalCardRecord) {
    const isExpanded = expandedCardId === record.id;
    return (
      <button
        key={record.id}
        type="button"
        className={joinClassNames('sage-unified-card', isExpanded && 'sage-unified-card--selected')}
        onClick={() => {
          setExpandedCardId(isExpanded ? null : record.id);
        }}
      >
        <BrandLogo
          id={record.id}
          label={record.label}
          src={record.image}
          failedLogos={failedLogos}
          onError={markLogoFailed}
        />
        <strong className="sage-unified-card__title">{record.label}</strong>
        <span className="sage-unified-card__detail">{record.detail}</span>
        {record.channel ? (
          <span className="sage-unified-card__detail">
            {`Identity: ${record.connectedIdentity || 'Not linked'} · Last activity: ${record.lastActivityLabel || 'No activity yet'}`}
          </span>
        ) : null}
        <span className={joinClassNames('sage-unified-card__status', personalStatusClassName(record))}>
          {record.statusTone === 'connected' ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
          {record.statusLabel}
        </span>
      </button>
    );
  }

  function renderExternalCard(record: ExternalIntegrationCardRecord) {
    const isExpanded = expandedCardId === record.id;
    return (
      <button
        key={record.id}
        type="button"
        className={joinClassNames(
          'sage-unified-card',
          isExpanded && 'sage-unified-card--selected',
          record.locked && 'sage-unified-card--disabled',
        )}
        onClick={() => {
          if (record.locked) {
            return;
          }
          if (record.actionTarget === 'computer') {
            openComputerConnectSheet();
            return;
          }
          if (record.actionTarget === 'ai') {
            setProviderPickerOpen(true);
            setProviderPickerDraftId(null);
            return;
          }
          if (record.actionTarget === 'advanced') {
            openIntegrationSection('advanced');
            return;
          }
          if (record.actionTarget === 'connection') {
            // Expand card first to show details and a Connect button,
            // rather than redirecting to the provider immediately.
            setExpandedCardId(isExpanded ? null : record.id);
            return;
          }
          if (record.id === 'telegram' && !isExpanded) {
            setTelegramChannelMode('bot');
          }
          if (record.id === 'whatsapp' && !isExpanded) {
            setWhatsappChannelMode('cloud');
            setConfigChannelId(null);
            setPersonalChannelError(null);
          }
          setExpandedCardId(isExpanded ? null : record.id);
        }}
      >
        <BrandLogo
          id={record.id}
          label={record.label}
          src={record.image}
          failedLogos={failedLogos}
          onError={markLogoFailed}
        />
        <span className="sage-unified-card__title-row">
          <strong className="sage-unified-card__title">{record.label}</strong>
          {record.runtimeDependency ? (
            <span
              className={joinClassNames(
                'sage-unified-card__badge',
                record.runtimeDependency === 'cloud_only' && 'sage-unified-card__badge--cloud',
                record.runtimeDependency === 'gateway_optional' && 'sage-unified-card__badge--gateway-optional',
                record.runtimeDependency === 'gateway_required' && 'sage-unified-card__badge--gateway-required',
              )}
            >
              {runtimeDependencyLabel(record.runtimeDependency)}
            </span>
          ) : null}
        </span>
        <span className="sage-unified-card__detail">{record.detail}</span>
        <span className={joinClassNames('sage-unified-card__status', personalStatusClassName(record))}>
          {record.statusTone === 'connected' ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
          {renderCardActionLabel(record)}
        </span>
      </button>
    );
  }

  function renderCardActionLabel(record: ExternalIntegrationCardRecord): string {
    if (record.locked) {
      return record.statusLabel;
    }
    if (record.statusTone === 'connected') {
      return 'Connected';
    }
    if (record.consumerSetupState === 'oauth_ready') {
      return 'Connect';
    }
    if (record.consumerSetupState === 'oauth_not_wired') {
      return 'Connect';
    }
    if (record.actionTarget === 'connection' && (record.connectionAuthFields ?? []).length > 0) {
      return 'Set up';
    }
    if (record.actionTarget === 'connection' && !(record.connectionAuthFields ?? []).length) {
      return 'Connect';
    }
    return record.statusLabel;
  }

  function renderProviderExpand(record: ProviderCardRecord, options: { showClose?: boolean } = {}) {
    const busy = busyCardId === record.id;
    const showClose = options.showClose !== false;
    return (
      <MotionSlidePanel className="sage-unified-expand">
        <div className="sage-unified-expand__header">
          <strong className="sage-unified-expand__title">{record.label}</strong>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label={`Close ${record.label}`}
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {record.status === 'connected' ? (
          <>
            <div className="sage-unified-expand__text">
              {`Connected. ${record.keyTail ? `Saved sign-in ends in ${record.keyTail}` : providerRequiresSecret(record.provider, record.profile) ? 'Saved sign-in details are hidden.' : 'No sign-in details required.'}`}
            </div>
            {record.provider.id === 'ollama' && !localCompanionOnline ? (
              <div className="sage-unified-expand__text">
                Connect a computer to use Ollama.
              </div>
            ) : null}
            {record.provider.modelsError ? (
              <div className="sage-unified-expand__text">
                {record.provider.modelsError}
              </div>
            ) : null}
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                tone="ghost"
                disabled={busy}
                onClick={() => {
                  void handleProviderDisconnect(record);
                }}
              >
                Disconnect
              </AppButton>
            </div>
          </>
        ) : (
          <>
            {providerRequiresSecret(record.provider, record.profile) ? (
              <FormField label={providerCredentialLabel(record.provider, record.profile)}>
                <FormInput
                  type="text"
                  value={providerDraftKeys[record.id] ?? ''}
                  placeholder={providerCredentialPlaceholder(record.provider)}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  data-1p-ignore="true"
                  data-lpignore="true"
                  onPasteCapture={(event) => {
                    handleProviderKeyPaste(record.id, event);
                  }}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setProviderDraftKeys((current) => ({ ...current, [record.id]: value }));
                  }}
                />
              </FormField>
            ) : (
              <div className="sage-unified-expand__text">
                {localCompanionOnline
                  ? 'Agent Computer can use available Ollama models from the selected Agent Computer.'
                  : 'Connect a computer to use Ollama.'}
              </div>
            )}
            {providerNeedsBaseUrl(record.provider) ? (
              <FormField label={providerBaseUrlLabel(record.provider)}>
                <FormInput
                  type="url"
                  value={providerDraftBaseUrls[record.id] ?? ''}
                  placeholder={providerBaseUrlPlaceholder(record.provider)}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setProviderDraftBaseUrls((current) => ({ ...current, [record.id]: value }));
                  }}
                />
              </FormField>
            ) : null}
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                disabled={busy}
                onClick={() => {
                  void handleProviderSave(record);
                }}
              >
                {busy ? 'Saving…' : 'Save'}
              </AppButton>
              {showClose ? (
                <button
                  type="button"
                  className="sage-unified-expand__link"
                  onClick={() => setExpandedCardId(null)}
                >
                  Cancel
                </button>
              ) : null}
            </div>
          </>
        )}
      </MotionSlidePanel>
    );
  }

  function renderProviderSkeletons() {
    const rows = chunkItems(fallbackProviderCatalog(), 4);
    return (
      <section className="sage-unified-section">
        <p className="sage-unified-section__label">AI Models</p>
        {rows.map((row, rowIndex) => (
          <div
            key={`AI Models-skeleton-${rowIndex}`}
            className="sage-unified-grid sage-unified-grid--4"
          >
            {row.map((provider) => (
              <div key={provider.id} className="sage-unified-card" aria-hidden="true">
                <SkeletonBlock height="40px" width="40px" />
                <SkeletonBlock height="16px" width="70%" />
                <SkeletonBlock height="12px" width="54%" />
              </div>
            ))}
          </div>
        ))}
      </section>
    );
  }

  function renderPersonalChannelConfig(
    channel: PersonalCommunicationChannel,
    channelDraft: PersonalChannelDraft,
    channelBusy: boolean,
  ) {
    const isTelegram = channel === 'telegram';
    const telegramStep = isTelegram
      ? resolveTelegramSetupStep(telegramPersonal, telegramSetupStep)
      : 'phone';
    const connectedPhone = isTelegram ? readTelegramConnectedPhone(telegramPersonal, channelDraft.phoneNumber) : '';
    if (!selectedGateway) {
      return (
        <div className="sage-unified-expand__config app-stack-3">
          <div className="sage-unified-expand__text">
            Choose the Agent Computer that owns this personal channel.
          </div>
          {gateways.length > 0 ? (
            <div className="sage-unified-expand__actions">
              {gateways.map((gateway) => {
                const gatewayId = readString(gateway.gateway_id, '');
                const label = readString(gateway.display_name || gateway.device_name || gateway.hostname, gatewayId || 'Agent Computer');
                const status = readString(gateway.connection_status || gateway.status, 'unknown');
                return (
                  <AppButton
                    key={gatewayId || label}
                    type="button"
                    tone="secondary"
                    disabled={busyCardId === `gateway:${gatewayId}`}
                    onClick={() => {
                      void chooseSageAgentComputer(gatewayId);
                    }}
                  >
                    {busyCardId === `gateway:${gatewayId}` ? 'Choosing...' : `${label} · ${status}`}
                  </AppButton>
                );
              })}
            </div>
          ) : null}
          <div className="sage-unified-expand__actions">
            <AppButton type="button" onClick={openGatewaySurface}>
              {gateways.length > 0 ? 'Manage computers' : 'Connect Agent Computer'}
            </AppButton>
          </div>
        </div>
      );
    }
    if (!selectedGatewayOnline) {
      return (
        <div className="sage-unified-expand__config app-stack-3">
          <AppNotice tone="warning">Agent Computer offline — connect Hardware first.</AppNotice>
          <div className="sage-unified-expand__actions">
            <AppButton
              type="button"
              onClick={openGatewaySurface}
            >
              Go to Hardware
            </AppButton>
          </div>
        </div>
      );
    }
    if (!isTelegram) {
      return (
        <div className="sage-unified-expand__config app-stack-3">
          <div className="sage-unified-expand__text">
            Connect your personal WhatsApp account through Agent Computer.
          </div>
          {personalChannelError ? <AppNotice tone="warning">{personalChannelError}</AppNotice> : null}
          <FormField label="Phone number">
            <FormInput
              value={channelDraft.phoneNumber}
              placeholder="+8618657105303"
              autoComplete="tel"
              onChange={(event) => updateChannelDraft('whatsapp', { phoneNumber: event.currentTarget.value })}
            />
          </FormField>
          <div className="sage-unified-expand__actions">
            <AppButton
              type="button"
              disabled={channelBusy}
              onClick={() => {
                void handlePersonalChannelSetup('whatsapp');
              }}
            >
              {channelBusy && busyCardId === 'whatsapp_personal' ? 'Connecting...' : 'Connect WhatsApp'}
            </AppButton>
          </div>
        </div>
      );
    }

    return (
      <div className="sage-unified-expand__config app-stack-3">
        <div className="sage-unified-expand__text">
          Connect your personal Telegram account. Sage can read incoming chats and reply through your approved Agent Computer.
        </div>
        {personalChannelError ? <AppNotice tone="warning">{personalChannelError}</AppNotice> : null}
        {telegramStep === 'phone' ? (
          <>
            <FormField label="Phone number">
              <FormInput
                value={channelDraft.phoneNumber}
                placeholder="+8618657105303"
                autoComplete="tel"
                onChange={(event) => updateChannelDraft('telegram', { phoneNumber: event.currentTarget.value })}
              />
            </FormField>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                disabled={channelBusy}
                onClick={() => {
                  void handlePersonalChannelSetup('telegram', 'phone');
                }}
              >
                {channelBusy && busyCardId === 'telegram_personal' ? 'Sending...' : 'Send Code'}
              </AppButton>
            </div>
          </>
        ) : null}
        {telegramStep === 'code' ? (
          <>
            <div className="sage-unified-expand__text">
              {`Code sent to ${channelDraft.phoneNumber.trim() || 'your Telegram account'}.`}
            </div>
            <FormField label="Verification code">
              <FormInput
                inputMode="numeric"
                value={channelDraft.loginCode}
                placeholder="123456"
                autoComplete="one-time-code"
                onChange={(event) => updateChannelDraft('telegram', { loginCode: event.currentTarget.value })}
              />
            </FormField>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                disabled={channelBusy}
                onClick={() => {
                  void handlePersonalChannelSetup('telegram', 'code');
                }}
              >
                {channelBusy && busyCardId === 'telegram_personal' ? 'Verifying...' : 'Verify'}
              </AppButton>
            </div>
          </>
        ) : null}
        {telegramStep === 'password' ? (
          <>
            <div className="sage-unified-expand__text">Two-step verification enabled.</div>
            <FormField label="Password">
              <FormInput
                type="password"
                value={channelDraft.password}
                autoComplete="current-password"
                onChange={(event) => updateChannelDraft('telegram', { password: event.currentTarget.value })}
              />
            </FormField>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                disabled={channelBusy}
                onClick={() => {
                  void handlePersonalChannelSetup('telegram', 'password');
                }}
              >
                {channelBusy && busyCardId === 'telegram_personal' ? 'Connecting...' : 'Continue'}
              </AppButton>
            </div>
          </>
        ) : null}
        {telegramStep === 'connected' ? (
          <>
            <div className="sage-unified-expand__text">
              {`Connected${connectedPhone ? ` as ${connectedPhone}` : ''}.`}
            </div>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                tone="ghost"
                onClick={() => {
                  setExpandedCardId(null);
                }}
              >
                Done
              </AppButton>
              <AppButton
                type="button"
                tone="secondary"
                disabled
              >
                Disconnect
              </AppButton>
            </div>
          </>
        ) : null}
        {telegramStep !== 'connected' ? (
          <details className="sage-unified-expand__config-disclosure">
            <summary className="sage-unified-expand__config-summary">Use custom credentials</summary>
            <div className="sage-unified-expand__config app-stack-3">
              <FormField label="Telegram app ID">
                <FormInput
                  value={channelDraft.apiId}
                  placeholder="123456"
                  onChange={(event) => updateChannelDraft('telegram', { apiId: event.currentTarget.value })}
                />
              </FormField>
              <FormField label="Telegram app secret">
                <FormInput
                  value={channelDraft.apiHash}
                  placeholder="Telegram app secret"
                  onChange={(event) => updateChannelDraft('telegram', { apiHash: event.currentTarget.value })}
                />
              </FormField>
            </div>
          </details>
        ) : null}
      </div>
    );
  }

  function renderPersonalExpand(record: PersonalCardRecord) {
    const showChannelActions = record.id === 'telegram_personal' || record.id === 'whatsapp_personal';
    const bridgeChannel = record.id === 'signal_personal'
      || record.id === 'imessage_personal'
      || record.id === 'wechat_personal';
    const channel = record.channel ?? null;
    const channelDraft = channel ? channelDrafts[channel] : null;
      const channelBusy = channel ? busyCardId === `${channel}_personal` || busyCardId === `${channel}_personal:test` : false;
    const configOpen = channel ? configChannelId === channel : false;
    return (
      <MotionSlidePanel className="sage-unified-expand">
        <div className="sage-unified-expand__header">
          <strong className="sage-unified-expand__title">{record.label}</strong>
          <button
            type="button"
            className="sage-unified-expand__close"
            onClick={() => setExpandedCardId(null)}
            aria-label={`Close ${record.label}`}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>
        <div className="sage-unified-expand__text">{record.summary}</div>
        {showChannelActions ? (
          <>
            <div className="sage-unified-expand__text">
              {`${record.statusLabel} · ${record.connectedIdentity || 'Not linked'} · ${record.lastActivityLabel || 'No activity yet'}`}
            </div>
            <div className="sage-unified-expand__tag-row">
              <span className="sage-unified-expand__tag">Runs on Agent Computer</span>
              <span className="sage-unified-expand__tag">Personal Channels</span>
              {record.id === 'whatsapp_personal' ? (
                <span className="sage-unified-expand__tag">Not a business account</span>
              ) : null}
            </div>
          </>
        ) : null}
        {record.nextStep ? (
          <div className="sage-unified-expand__text">{record.nextStep}</div>
        ) : null}
        <div className="sage-unified-expand__actions">
          {showChannelActions && channel ? (
            <AppButton
              type="button"
              disabled={channelBusy}
              onClick={() => {
                setConfigChannelId(configOpen ? null : channel);
                setPersonalChannelError(null);
                setError(null);
              }}
            >
              {!selectedGateway ? 'Choose Agent Computer' : channel === 'telegram' ? 'Pair Telegram' : 'Pair WhatsApp'}
            </AppButton>
          ) : (
            <AppButton
              type="button"
              disabled={bridgeChannel && busyCardId === record.id}
              onClick={() => {
                if (bridgeChannel) {
                  void handleConnectionSetupStart({
                    id: record.id,
                    label: record.label,
                    image: record.image,
                    detail: record.detail,
                    statusLabel: record.statusLabel,
                    statusTone: record.statusTone,
                    summary: record.summary,
                    nextStep: record.nextStep,
                    actionTarget: 'connection',
                    connectionId: record.id,
                  });
                  return;
                }
                openGatewaySurface();
              }}
            >
              {bridgeChannel
                ? busyCardId === record.id ? 'Checking setup...' : 'Set up bridge'
                : record.statusLabel === 'Connect computer'
                ? 'Connect a computer'
                : record.id === 'browser'
                  ? 'Open browser sessions'
                  : 'Open computer setup'}
            </AppButton>
          )}
          {record.id === 'device' || showChannelActions ? (
            <AppButton
              type="button"
              tone="ghost"
              onClick={() => {
                openWorkspaceRoute('gateway');
              }}
            >
              {showChannelActions ? 'Agent Computer settings' : 'Revoke access'}
            </AppButton>
          ) : null}
          <button
            type="button"
            className="sage-unified-expand__link"
            onClick={() => setExpandedCardId(null)}
          >
            Close
          </button>
        </div>
        {showChannelActions && channel && channelDraft && configOpen ? renderPersonalChannelConfig(channel, channelDraft, channelBusy) : null}
      </MotionSlidePanel>
    );
  }

  function renderTelegramChannelExpand(record: ExternalIntegrationCardRecord, options: { showClose?: boolean } = {}) {
    const showClose = options.showClose !== false;
    const telegramBotRecord = connectorCards.find((card) => card.id === 'sage_telegram_hosted')
      ?? connectorCards.find((card) => card.id === 'telegram_bot')
      ?? null;
    const telegramPersonalRecord = communicationPersonalCards.find((card) => card.id === 'telegram_personal') ?? null;
    const botRecord = telegramBotRecord ? connectorAsExternalCard(telegramBotRecord) : null;
    const botSetupRecord: ExternalIntegrationCardRecord = {
      id: botRecord?.id ?? 'connector_telegram_bot',
      label: botRecord?.label ?? 'Sage on Telegram',
      image: botRecord?.image ?? record.image,
      detail: botRecord?.detail ?? 'Hosted Telegram channel.',
      statusLabel: botRecord?.statusLabel ?? 'Not connected',
      statusTone: botRecord?.statusTone ?? 'neutral',
      summary: botRecord?.summary ?? 'Sage on Telegram is the hosted cloud path. It does not need Agent Computer or user-managed bot credentials.',
      nextStep: botRecord?.nextStep ?? 'Hosted Telegram must be enabled by the platform before users can connect it.',
      actionLabel: botRecord?.actionLabel ?? null,
      actionTarget: 'connection',
      connectionId: botRecord?.connectionId ?? 'sage_telegram_hosted',
      connectionProvider: botRecord?.connectionProvider ?? 'sage_telegram_hosted',
      connectionAuthFields: botRecord?.connectionAuthFields ?? [],
      locked: botRecord?.locked ?? false,
    };
    const channelDraft = channelDrafts.telegram;
    const channelBusy = busyCardId === 'telegram_personal' || busyCardId === 'telegram_personal:test';
    const repairGatewayId = readString(gatewaySelectionSummary.repairGateway?.gateway_id);
    const repairGatewayName = gatewayDisplayName(gatewaySelectionSummary.repairGateway);
    const channelComputerIssue = gatewaySelectionSummary.selectedGatewayMissing
      ? {
          message: describeGatewaySelectionRepair(gatewaySelectionSummary),
          actionLabel: repairGatewayId ? `Use ${repairGatewayName}` : 'Choose Agent Computer',
          action: repairGatewayId ? 'repair' : 'hardware',
        }
      : !selectedGateway
        ? {
            message: describeAvailableGatewayChoice(gatewaySelectionSummary),
            actionLabel: repairGatewayId ? `Use ${repairGatewayName}` : 'Choose Agent Computer',
            action: repairGatewayId ? 'repair' : 'hardware',
          }
        : !selectedGatewayOnline
          ? {
              message: 'Agent Computer offline — connect Hardware first.',
              actionLabel: 'Go to Hardware',
              action: 'hardware',
            }
          : null;
    const showBotCredentialForm = Boolean(
      botSetupRecord.actionTarget === 'connection'
      && !botSetupRecord.locked
      && readString(botSetupRecord.connectionProvider) !== ''
      && (botSetupRecord.connectionAuthFields ?? []).length > 0,
    );

    return (
      <div className="sage-unified-expand sage-channel-expand">
        <div className="sage-unified-expand__header">
          <div className="sage-channel-expand__identity">
            <BrandLogo
              id={record.id}
              label={record.label}
              src={record.image}
              failedLogos={failedLogos}
              onError={markLogoFailed}
            />
            <strong className="sage-unified-expand__title">{record.label}</strong>
          </div>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label="Close Telegram"
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <div className="sage-channel-route-tabs" role="tablist" aria-label="Telegram setup type">
          <button
            type="button"
            role="tab"
            aria-selected={telegramChannelMode === 'bot'}
            className={joinClassNames('sage-channel-route-tab', telegramChannelMode === 'bot' && 'sage-channel-route-tab--active')}
            onClick={() => {
              setTelegramChannelMode('bot');
              setConfigChannelId(null);
              setPersonalChannelError(null);
            }}
          >
            Bot
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={telegramChannelMode === 'personal'}
            className={joinClassNames('sage-channel-route-tab', telegramChannelMode === 'personal' && 'sage-channel-route-tab--active')}
            onClick={() => {
              setTelegramChannelMode('personal');
              setConfigChannelId('telegram');
              setPersonalChannelError(null);
            }}
          >
            Personal
            <span className="sage-channel-route-tab__hint" style={{ fontSize: 10, opacity: 0.6, marginLeft: 4 }}>unstable</span>
          </button>
        </div>
        {telegramChannelMode === 'bot' ? (
          <>
            {!showBotCredentialForm ? (
              <>
                <div className="sage-unified-expand__text">{botSetupRecord.summary}</div>
                {telegramHostedPaired ? (
                  <div className="sage-unified-expand__pairing">
                    <div className="sage-unified-expand__pairing-code" style={{ color: 'var(--app-color-success, #22c55e)' }}>
                      <span className="sage-unified-expand__pairing-label">Status</span>
                      <code className="sage-unified-expand__pairing-value" style={{ fontSize: 16 }}>✓ Connected to Telegram</code>
                    </div>
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <button
                        type="button"
                        className="connector-disconnect-btn"
                        onClick={async () => {
                          try {
                            await fetch(
                              '/api/sage/telegram-hosted/pair?workspace_id=' + encodeURIComponent(bootstrap.workspace.id),
                              {
                                method: 'DELETE',
                                headers: buildCookieAuthHeaders('DELETE'),
                                credentials: 'include',
                              }
                            );
                          } catch { /* ignore */ }
                          setTelegramHostedPaired(false);
                          setTelegramHostedPairing(null);
                        }}
                      >
                        Disconnect
                      </button>
                    </div>
                    <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                      {telegramHostedBotUsername
                        ? `Sage is now available on Telegram. Send a message to @${telegramHostedBotUsername}.`
                        : 'Sage is now available on Telegram.'}
                    </p>
                  </div>
                ) : telegramHostedPairing ? (
                  <div className="sage-unified-expand__pairing">
                    <div className="sage-unified-expand__pairing-code">
                      <span className="sage-unified-expand__pairing-label">Pairing code</span>
                      <code className="sage-unified-expand__pairing-value">{telegramHostedPairing.pairingCode}</code>
                    </div>
                    {telegramHostedPairing.deepLink ? (
                      <a
                        href={telegramHostedPairing.deepLink}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="app-button app-button--primary"
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 10 }}
                      >
                        {telegramHostedPolling ? 'Waiting for you in Telegram...' : 'Open Telegram to Pair'}
                      </a>
                    ) : null}
                    <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                      {telegramHostedPolling
                        ? 'Checking for your pairing message every few seconds...'
                        : 'Send the code above or click the link to instantly connect your Telegram account to Sage.'}
                    </p>
                  </div>
                ) : null}
                {!telegramHostedPairing && botSetupRecord.nextStep ? <div className="sage-unified-expand__text">{botSetupRecord.nextStep}</div> : null}
                {botSetupRecord.locked ? <AppNotice tone="warning">Hosted Telegram is not enabled in this environment yet.</AppNotice> : null}
              </>
            ) : null}
            {showBotCredentialForm ? renderFlatConnectorCredentialForm(botSetupRecord) : null}
            {botSetupRecord.actionLabel && !showBotCredentialForm && !botSetupRecord.locked ? (
              <div className="sage-unified-expand__actions">
                <button
                  type="button"
                  className="app-button app-button--primary"
                  onClick={() => {
                    if (botSetupRecord.connectionId === 'sage_telegram_hosted' || botSetupRecord.connectionProvider === 'sage_telegram_hosted_bot') {
                      void startTelegramHostedPairing();
                    } else {
                      void handleConnectionSetupStart(botSetupRecord);
                    }
                  }}
                >
                  {botSetupRecord.actionLabel}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <>
            <AppNotice tone="warning">
              Personal Telegram uses phone-number login through Agent Computer. Telegram may flag or ban MTProto accounts. Use the Bot tab for a reliable cloud connection.
            </AppNotice>
            {!telegramHostedPaired && !telegramHostedPairing ? (
              <div className="sage-unified-expand__actions" style={{ marginTop: 8 }}>
                <AppButton type="button" tone="secondary" onClick={() => setTelegramChannelMode('bot')}>
                  ← Use Bot tab instead
                </AppButton>
              </div>
            ) : null}
            {channelComputerIssue ? <AppNotice tone="warning">{channelComputerIssue.message}</AppNotice> : null}
            <div className="sage-unified-expand__actions">
              {channelComputerIssue ? (
                <AppButton
                  type="button"
                  disabled={channelComputerIssue.action === 'repair' && busyCardId === `gateway:${repairGatewayId}`}
                  onClick={() => {
                    if (channelComputerIssue.action === 'repair' && repairGatewayId) {
                      void chooseSageAgentComputer(repairGatewayId);
                      return;
                    }
                    openGatewaySurface();
                  }}
                >
                  {channelComputerIssue.action === 'repair' && busyCardId === `gateway:${repairGatewayId}`
                    ? 'Switching...'
                    : channelComputerIssue.actionLabel}
                </AppButton>
              ) : null}
              {!channelComputerIssue ? (
                <AppButton type="button" tone="ghost" onClick={openGatewaySurface}>
                  Agent Computer settings
                </AppButton>
              ) : null}
              {showClose ? (
                <button
                  type="button"
                  className="sage-unified-expand__link"
                  onClick={() => setExpandedCardId(null)}
                >
                  Close
                </button>
              ) : null}
            </div>
            {!channelComputerIssue ? renderPersonalChannelConfig('telegram', channelDraft, channelBusy) : null}
          </>
        )}
      </div>
    );
  }

  function renderDiscordChannelExpand(record: ExternalIntegrationCardRecord, options: { showClose?: boolean } = {}) {
    const showClose = options.showClose !== false;
    const botInstallUrl = discordPublicConfig?.client_id
      ? `https://discord.com/oauth2/authorize?client_id=${encodeURIComponent(discordPublicConfig.client_id)}&scope=bot&permissions=2048`
      : null;
    const botUserUrl = discordPublicConfig?.bot_user_id
      ? `https://discord.com/users/${encodeURIComponent(discordPublicConfig.bot_user_id)}`
      : null;

    return (
      <div className="sage-unified-expand sage-channel-expand">
        <div className="sage-unified-expand__header">
          <div className="sage-channel-expand__identity">
            <BrandLogo
              id={record.id}
              label={record.label}
              src={record.image}
              failedLogos={failedLogos}
              onError={markLogoFailed}
            />
            <strong className="sage-unified-expand__title">{record.label}</strong>
          </div>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label="Close Discord"
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <div className="sage-channel-route-tabs" role="tablist" aria-label="Discord setup type">
          <button
            type="button"
            role="tab"
            aria-selected={discordChannelMode === 'bot'}
            className={joinClassNames('sage-channel-route-tab', discordChannelMode === 'bot' && 'sage-channel-route-tab--active')}
            onClick={() => { setDiscordChannelMode('bot'); }}
          >
            Bot
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={discordChannelMode === 'personal'}
            className={joinClassNames('sage-channel-route-tab', discordChannelMode === 'personal' && 'sage-channel-route-tab--active')}
            onClick={() => { setDiscordChannelMode('personal'); }}
          >
            Personal
            <span className="sage-channel-route-tab__hint" style={{ fontSize: 10, opacity: 0.6, marginLeft: 4 }}>unstable</span>
          </button>
        </div>
        {discordChannelMode === 'bot' ? (
          <>
            <div className="sage-unified-expand__text">
              Sage on Discord is the official Empyralis Discord bot. Connect your Discord account in one click and DM the bot to start messaging Sage instantly.
            </div>
            {!discordPaired ? (
              <>
                <div className="sage-unified-expand__actions">
                  <button
                    type="button"
                    className="app-button app-button--primary"
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                    onClick={async () => {
                      let url = discordOAuthUrl;
                      if (!url) {
                        try {
                          const resp = await fetch(
                            `/api/sage/discord/oauth/url?workspace_id=${encodeURIComponent(bootstrap.workspace.id)}`,
                            { headers: buildCookieAuthHeaders('GET'), credentials: 'include' },
                          );
                          if (resp.ok) {
                            const data = await resp.json() as { url?: string };
                            if (data.url) {
                              url = data.url;
                              setDiscordOAuthUrl(data.url);
                            }
                          } else {
                            const txt = await resp.text().catch(() => '');
                            alert(`Could not start Discord OAuth (${resp.status}). ${txt.slice(0, 200)}`);
                            return;
                          }
                        } catch (err) {
                          alert(`Could not reach Discord OAuth endpoint: ${err}`);
                          return;
                        }
                      }
                      if (!url) {
                        alert('Discord OAuth URL is not available. Use the pairing code below as a fallback.');
                        return;
                      }
                      window.location.href = url;
                    }}
                  >
                    Connect with Discord
                  </button>
                </div>
                <details style={{ marginTop: 12 }}>
                  <summary style={{ fontSize: 13, opacity: 0.6, cursor: 'pointer' }}>
                    Or use a pairing code instead
                  </summary>
                  <div style={{ marginTop: 8 }}>
                    {discordPairingCode ? (
                      <div className="sage-unified-expand__pairing">
                        <div className="sage-unified-expand__pairing-code">
                          <span className="sage-unified-expand__pairing-label">Pairing code</span>
                          <code className="sage-unified-expand__pairing-value">{discordPairingCode}</code>
                        </div>
                        <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                          DM @EmpyralisBot and send{' '}
                          <code style={{ fontWeight: 600 }}>/pair {discordPairingCode}</code>
                        </p>
                        {botUserUrl ? (
                          <a
                            href={botUserUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="app-button app-button--primary"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 10 }}
                          >
                            Open @EmpyralisBot on Discord
                          </a>
                        ) : null}
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="app-button app-button--primary"
                        disabled={discordPairingLoading}
                        onClick={() => { void startDiscordPairing(); }}
                      >
                        {discordPairingLoading ? 'Generating code...' : 'Generate pairing code'}
                      </button>
                    )}
                  </div>
                </details>
              </>
            ) : (
              <div className="sage-unified-expand__pairing">
                <div className="sage-unified-expand__pairing-code" style={{ color: 'var(--app-color-success, #22c55e)' }}>
                  <span className="sage-unified-expand__pairing-label">Status</span>
                  <code className="sage-unified-expand__pairing-value" style={{ fontSize: 16 }}>✓ Connected to Discord DM</code>
                </div>
                <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                  ✓ Connected! Open Discord and DM @EmpyralisBot to start.
                </p>
                <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className="connector-disconnect-btn"
                    onClick={async () => {
                      try {
                        await fetch(
                          '/api/sage/discord/pair?workspace_id=' + encodeURIComponent(bootstrap.workspace.id),
                          { method: 'DELETE', headers: buildCookieAuthHeaders('DELETE'), credentials: 'include' }
                        );
                      } catch { /* ignore */ }
                      setDiscordPaired(false);
                      setDiscordPairingCode(null);
                    }}
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <AppNotice tone="warning">
              Discord doesn't support real personal-account automation safely. Install the bot to your own Discord server instead — it will respond to @mentions and commands in any channel it can access.
            </AppNotice>
            <div className="sage-unified-expand__text">
              Install Sage as a bot in your own Discord server. Sage will respond to @mentions and commands in any channel it can access.
            </div>
            {botInstallUrl ? (
              <div className="sage-unified-expand__actions" style={{ marginTop: 8 }}>
                <a
                  href={botInstallUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="app-button app-button--primary"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                  Install in your server
                </a>
              </div>
            ) : (
              <AppNotice tone="warning">Discord Application ID is not configured. The install URL cannot be generated.</AppNotice>
            )}
            <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
              Bot installed? Check your Discord server — the bot starts responding automatically once added.
            </p>
            <div className="sage-unified-expand__actions" style={{ marginTop: 8 }}>
              <AppButton type="button" tone="secondary" onClick={() => setDiscordChannelMode('bot')}>
                ← Use Bot tab instead
              </AppButton>
            </div>
          </>
        )}
      </div>
    );
  }

  function renderWhatsappChannelExpand(record: ExternalIntegrationCardRecord, options: { showClose?: boolean } = {}) {
    const showClose = options.showClose !== false;
    const businessSetupRecord: ExternalIntegrationCardRecord = {
      id: 'whatsapp_business',
      label: 'WhatsApp',
      image: record.image,
      detail: 'WhatsApp Business API cloud channel.',
      statusLabel: 'Not connected',
      statusTone: 'neutral',
      summary: 'WhatsApp Business API cloud channel.',
      nextStep: null,
      actionTarget: 'connection',
      connectionId: 'whatsapp_twilio',
      connectionProvider: 'whatsapp_twilio',
      connectionAuthFields: ['account_sid', 'auth_token', 'from_number', 'to_number'],
      locked: false,
    };
    const channelDraft = channelDrafts.whatsapp;
    const channelBusy = busyCardId === 'whatsapp_personal' || busyCardId === 'whatsapp_personal:test';
    const channelComputerIssue = !selectedGateway || !selectedGatewayOnline;

    return (
      <div className="sage-unified-expand sage-channel-expand">
        <div className="sage-unified-expand__header">
          <div className="sage-channel-expand__identity">
            <BrandLogo
              id={record.id}
              label={record.label}
              src={record.image}
              failedLogos={failedLogos}
              onError={markLogoFailed}
            />
            <strong className="sage-unified-expand__title">{record.label}</strong>
          </div>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label="Close WhatsApp"
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <div className="sage-channel-route-tabs" role="tablist" aria-label="WhatsApp setup type">
          <button
            type="button"
            role="tab"
            aria-selected={whatsappChannelMode === 'cloud'}
            className={joinClassNames('sage-channel-route-tab', whatsappChannelMode === 'cloud' && 'sage-channel-route-tab--active')}
            onClick={() => {
              setWhatsappChannelMode('cloud');
              setConfigChannelId(null);
              setPersonalChannelError(null);
            }}
          >
            Business API
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={whatsappChannelMode === 'hardware'}
            className={joinClassNames('sage-channel-route-tab', whatsappChannelMode === 'hardware' && 'sage-channel-route-tab--active')}
            onClick={() => {
              setWhatsappChannelMode('hardware');
              setConfigChannelId('whatsapp');
              setPersonalChannelError(null);
            }}
          >
            Your WhatsApp
          </button>
        </div>
        {whatsappChannelMode === 'cloud' ? (
          renderFlatConnectorCredentialForm(businessSetupRecord)
        ) : (
          <>
            {channelComputerIssue ? (
              <>
                <AppNotice tone="warning">Agent Computer is offline. Open Empyralis on your Mac to connect your personal WhatsApp.</AppNotice>
                <div className="sage-unified-expand__actions">
                  <button type="button" className="app-button app-button--primary" onClick={openGatewaySurface}>
                    Go to Hardware
                  </button>
                </div>
              </>
            ) : renderPersonalChannelConfig('whatsapp', channelDraft, channelBusy)}
          </>
        )}
      </div>
    );
  }

  function renderBridgeChannelExpand(record: ExternalIntegrationCardRecord, options: { showClose?: boolean } = {}) {
    const showClose = options.showClose !== false;
    const gatewayReady = Boolean(selectedGateway && selectedGatewayOnline);
    const instruction = record.id === 'imessage_personal'
      ? 'Set up the iMessage bridge on the selected Mac Agent Computer.'
      : record.id === 'signal_personal'
        ? 'Set up the Signal bridge on the selected Agent Computer.'
        : 'Set up the WeChat bridge on the selected Agent Computer.';
    return (
      <div className="sage-unified-expand sage-channel-expand">
        <div className="sage-unified-expand__header">
          <div className="sage-channel-expand__identity">
            <BrandLogo
              id={record.id}
              label={record.label}
              src={record.image}
              failedLogos={failedLogos}
              onError={markLogoFailed}
            />
            <strong className="sage-unified-expand__title">{record.label}</strong>
          </div>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label={`Close ${record.label}`}
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {gatewayReady ? (
          <>
            <div className="sage-unified-expand__text">Set up bridge on Agent Computer.</div>
            <div className="sage-unified-expand__text">{instruction}</div>
            <div className="sage-unified-expand__actions">
              <button type="button" className="app-button app-button--primary" onClick={openGatewaySurface}>
                Go to Hardware
              </button>
            </div>
          </>
        ) : (
          <>
            <AppNotice tone="warning">Agent Computer offline — connect Hardware first.</AppNotice>
            <div className="sage-unified-expand__actions">
              <button type="button" className="app-button app-button--primary" onClick={openGatewaySurface}>
                Go to Hardware
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  function renderConnectorModeSetup(record: ExternalIntegrationCardRecord) {
    const sourceConnector = record.connectorCardId
      ? connectorCards.find((card) => card.id === record.connectorCardId) ?? null
      : null;
    const connectedConnector = Boolean(sourceConnector?.connected && sourceConnector.credential);
    const accountLabel = connectorAccountLabel(sourceConnector?.credential ?? null);
    const hasFields = (record.connectionAuthFields ?? []).filter(Boolean).length > 0;

    if (connectedConnector && sourceConnector) {
      return (
        <>
          <div className="sage-unified-expand__text">
            {`${record.label} is connected${accountLabel ? ` as ${accountLabel}` : ''}.`}
          </div>
          <div className="sage-unified-expand__actions">
            <AppButton
              type="button"
              tone="ghost"
              disabled={busyCardId === sourceConnector.id}
              onClick={() => {
                void handleConnectorDisconnect(sourceConnector);
              }}
            >
              {busyCardId === sourceConnector.id ? 'Disconnecting...' : 'Disconnect'}
            </AppButton>
          </div>
        </>
      );
    }
    if (record.consumerSetupMessage) {
      return <AppNotice tone="warning">{record.consumerSetupMessage}</AppNotice>;
    }
    if (record.consumerSetupState === 'oauth_ready') {
      return null;
    }
    if (hasFields) {
      return renderFlatConnectorCredentialForm(record);
    }
    return <AppNotice tone="warning">Setup is unavailable.</AppNotice>;
  }

  function renderFlatConnectorCredentialForm(record: ExternalIntegrationCardRecord) {
    const fields = (record.connectionAuthFields ?? []).filter(Boolean);
    if (fields.length === 0 || !readString(record.connectionProvider)) {
      return null;
    }
    const draft = connectorDraftCredentials[record.id] ?? {};
    const busy = busyCardId === record.id;
    return (
      <form
        className="sage-connector-credential-form"
        onSubmit={(event) => {
          event.preventDefault();
          void handleConnectorCredentialSave(record);
        }}
      >
        <div className="sage-connector-credential-form__fields">
          {fields.map((field) => (
            <FormField key={`${record.id}:${field}`} label={connectorCredentialFieldLabelForRecord(record, field)}>
              <FormInput
                type={connectorCredentialInputType(field)}
                value={draft[field] ?? ''}
                placeholder={connectorCredentialPlaceholder(field)}
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                data-1p-ignore="true"
                data-lpignore="true"
                onChange={(event) => {
                  updateConnectorDraftCredential(record.id, field, event.currentTarget.value);
                }}
              />
            </FormField>
          ))}
        </div>
        <button type="submit" className="app-button app-button--primary" disabled={busy}>
          {busy ? 'Saving...' : connectorCredentialSaveLabel(record)}
        </button>
      </form>
    );
  }

  function renderExternalExpand(record: ExternalIntegrationCardRecord, options: { showClose?: boolean } = {}) {
    if (record.id === 'telegram') {
      return renderTelegramChannelExpand(record, options);
    }
    if (record.id === 'whatsapp' || record.id === 'connector_whatsapp_twilio') {
      return renderWhatsappChannelExpand(record, options);
    }
    if (record.id === 'connector_discord_bot') {
      return renderDiscordChannelExpand(record, options);
    }
    if (record.id === 'signal_personal' || record.id === 'imessage_personal' || record.id === 'wechat_personal') {
      return renderBridgeChannelExpand(record, options);
    }
    const channel = record.channel ?? null;
    const channelDraft = channel ? channelDrafts[channel] : null;
    const channelBusy = channel ? busyCardId === `${channel}_personal` || busyCardId === `${channel}_personal:test` : false;
    const configOpen = channel ? true : false;
    const showClose = options.showClose !== false;
    const repairGatewayId = readString(gatewaySelectionSummary.repairGateway?.gateway_id);
    const repairGatewayName = gatewayDisplayName(gatewaySelectionSummary.repairGateway);
    const channelComputerIssue = channel
      ? gatewaySelectionSummary.selectedGatewayMissing
        ? {
            message: describeGatewaySelectionRepair(gatewaySelectionSummary),
            actionLabel: repairGatewayId ? `Use ${repairGatewayName}` : 'Choose Agent Computer',
            action: repairGatewayId ? 'repair' : 'hardware',
          }
        : !selectedGateway
          ? {
              message: describeAvailableGatewayChoice(gatewaySelectionSummary),
              actionLabel: repairGatewayId ? `Use ${repairGatewayName}` : 'Choose Agent Computer',
              action: repairGatewayId ? 'repair' : 'hardware',
            }
          : !selectedGatewayOnline
            ? {
                message: 'Agent Computer offline — connect Hardware first.',
                actionLabel: 'Go to Hardware',
                action: 'hardware',
              }
            : null
      : null;
    const showConnectorCredentialForm = record.actionTarget === 'connection'
      && !record.locked
      && !channel
      && readString(record.connectionProvider) !== ''
      && (record.connectionAuthFields ?? []).length > 0;
    const sourceConnector = record.connectorCardId
      ? connectorCards.find((card) => card.id === record.connectorCardId) ?? null
      : null;
    const accountLabel = connectorAccountLabel(sourceConnector?.credential ?? null);
    const connectedConnector = Boolean(sourceConnector?.connected && sourceConnector.credential);
    return (
      <div className="sage-unified-expand sage-channel-expand">
        <div className="sage-unified-expand__header">
          <div className="sage-channel-expand__identity">
            <BrandLogo
              id={record.id}
              label={record.label}
              src={record.image}
              failedLogos={failedLogos}
              onError={markLogoFailed}
            />
            <strong className="sage-unified-expand__title">{record.label}</strong>
          </div>
          {showClose ? (
            <button
              type="button"
              className="sage-unified-expand__close"
              onClick={() => setExpandedCardId(null)}
              aria-label={`Close ${record.label}`}
            >
              <X size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {renderAppDetailContent(record, connectedConnector)}
        {connectedConnector ? (
          <div className="sage-unified-expand__text">
            {`${record.label} is connected${accountLabel ? ` as ${accountLabel}` : ''}.`}
          </div>
        ) : null}
        {!showConnectorCredentialForm && !connectedConnector ? <div className="sage-unified-expand__text">{record.summary}</div> : null}
        {!showConnectorCredentialForm && !connectedConnector && record.nextStep ? <div className="sage-unified-expand__text">{record.nextStep}</div> : null}
        {record.consumerSetupMessage && !connectedConnector ? (
          <AppNotice tone="warning">{record.consumerSetupMessage}</AppNotice>
        ) : null}
        {channelComputerIssue ? <AppNotice tone="warning">{channelComputerIssue.message}</AppNotice> : null}
        {cardErrors[record.id] ? <AppNotice tone="danger">{cardErrors[record.id]}</AppNotice> : null}
        {!cardErrors[record.id] && oauthCallbackError && record.connectionId ? (
          <AppNotice tone="danger">{oauthCallbackError.message}</AppNotice>
        ) : null}
        {showConnectorCredentialForm ? renderConnectorCredentialForm(record) : null}
        <div className="sage-unified-expand__actions">
          {connectedConnector && sourceConnector ? (
            <>
              <AppButton
                type="button"
                tone="ghost"
                disabled={busyCardId === sourceConnector.id}
                onClick={() => {
                  void handleConnectorDisconnect(sourceConnector);
                }}
              >
                {busyCardId === sourceConnector.id ? 'Disconnecting...' : 'Disconnect'}
              </AppButton>
              <AppButton
                type="button"
                tone="secondary"
                disabled={busyCardId === record.id}
                onClick={() => {
                  clearCardError(record.id);
                  void handleConnectionSetupStart(record);
                }}
              >
                {busyCardId === record.id ? 'Reconnecting...' : 'Reconnect'}
              </AppButton>
            </>
          ) : null}
          {channelComputerIssue ? (
            <AppButton
              type="button"
              disabled={channelComputerIssue.action === 'repair' && busyCardId === `gateway:${repairGatewayId}`}
              onClick={() => {
                if (channelComputerIssue.action === 'repair' && repairGatewayId) {
                  void chooseSageAgentComputer(repairGatewayId);
                  return;
                }
                openGatewaySurface();
              }}
            >
              {channelComputerIssue.action === 'repair' && busyCardId === `gateway:${repairGatewayId}`
                ? 'Switching...'
                : channelComputerIssue.actionLabel}
            </AppButton>
          ) : null}
          {record.actionLabel && !channel && !showConnectorCredentialForm ? (
            <button
              type="button"
              className="app-button app-button--primary"
              onClick={() => {
                if (record.actionTarget === 'computer') {
                  openComputerConnectSheet();
                  return;
                }
                if (record.actionTarget === 'ai' || record.id === 'local_models') {
                  setProviderPickerOpen(true);
                  setProviderPickerDraftId(null);
                  return;
                }
                if (record.actionTarget === 'connection') {
                  void handleConnectionSetupStart(record);
                  return;
                }
                if (record.actionTarget === 'advanced') {
                  openIntegrationSection('advanced');
                  return;
                }
                if (record.actionTarget === 'close') {
                  setExpandedCardId(null);
                  return;
                }
                openGatewaySurface();
              }}
            >
              {record.actionLabel}
            </button>
          ) : null}
          {record.secondaryActionLabel && !channelComputerIssue ? (
            <AppButton
              type="button"
              tone="ghost"
              onClick={() => {
                openWorkspaceRoute('gateway');
              }}
            >
              {record.secondaryActionLabel}
            </AppButton>
          ) : null}
        </div>
        {channel && channelDraft && configOpen && !channelComputerIssue ? renderPersonalChannelConfig(channel, channelDraft, channelBusy) : null}
      </div>
    );
  }

  function renderAppDetailContent(record: ExternalIntegrationCardRecord, connected: boolean) {
    // Look up detail entry by connectorCardId (the bare connector name like "gmail"
    // or "github"), not by record.id (which is prefixed like "connector_gmail").
    const lookupKey = record.connectorCardId || record.id.replace(/^connector_/, '');
    const detailEntry = CONNECTOR_DETAIL_MAP[lookupKey];
    const sourceConnector = record.connectorCardId
      ? connectorCards.find((card) => card.id === record.connectorCardId) ?? null
      : null;
    // Find the MCP server registered for this app.  Match by the server's endpoint
    // against the mcpEndpoint declared in CONNECTOR_DETAIL_MAP, or fall back to
    // matching by server id against the connectorCardId.
    const declaredEndpoint = detailEntry?.mcpEndpoint;
    const sourceServer = mcpServers.find(
      (s) => {
        const serverEndpoint = readString(s.endpoint);
        if (declaredEndpoint && serverEndpoint === declaredEndpoint) return true;
        const serverId = readString(s.id).toLowerCase();
        return serverId === lookupKey.toLowerCase();
      }
    ) ?? null;
    const tools = Array.isArray(sourceServer?.tools) ? (sourceServer.tools as McpToolRecord[]) : [];
    const isAppCard = record.actionTarget === 'connection' && !record.channel;
    if (!isAppCard) return null;

    const hasMcpEndpoint = Boolean(declaredEndpoint);
    const hasDiscoveredTools = tools.length > 0;
    const toolBadgeCount = hasDiscoveredTools ? tools.length : (detailEntry?.suggestedToolNames.length ?? 0);

    return (
      <div className="sage-app-detail">
        {/* Connection status banner */}
        {connected ? (
          <div className="sage-app-detail__status-banner" style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 12px', borderRadius: 6,
            background: 'var(--sage-surface-raised)',
            marginBottom: 8, fontSize: 13,
          }}>
            <span className="sage-unified-card__dot" aria-hidden="true" style={{ color: 'var(--app-color-success, #22c55e)' }} />
            <strong style={{ color: 'var(--app-color-success, #22c55e)' }}>Connected</strong>
            {hasMcpEndpoint ? (
              <span style={{ opacity: 0.7 }}>
                — MCP tools auto-discovered via {hasDiscoveredTools ? `${tools.length} live tools` : 'MCP server'}
              </span>
            ) : (
              <span style={{ opacity: 0.7 }}>— Credential stored in vault</span>
            )}
          </div>
        ) : null}

        {detailEntry && detailEntry.examplePrompts.length > 0 ? (
          <div className="sage-app-detail__prompts-strip">
            <p className="sage-app-detail__label">Try asking</p>
            <div className="sage-app-detail__prompts-scroll">
              {detailEntry.examplePrompts.map((prompt, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="sage-app-detail__prompt-chip"
                  onClick={() => {
                    const query = encodeURIComponent(prompt);
                    window.open(`/?query=${query}`, '_blank');
                  }}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {detailEntry ? (
          <div className="sage-app-detail__section">
            <p className="sage-unified-expand__text">{detailEntry.description}</p>
          </div>
        ) : null}
        <div className="sage-app-detail__section">
          <div className="sage-app-detail__section-header">
            <p className="sage-app-detail__label">
              Tools{' '}
              <span className="sage-app-detail__badge">{toolBadgeCount}</span>
            </p>
          </div>
          <div className="sage-app-detail__tools">
            {hasDiscoveredTools ? (
              tools.map((tool, idx) => (
                <span key={idx} className="sage-app-detail__tool-pill">
                  {readString(tool.name) || readString(tool.label) || `tool-${idx}`}
                </span>
              ))
            ) : detailEntry ? (
              <>
                {detailEntry.suggestedToolNames.map((name, idx) => (
                  <span key={idx} className="sage-app-detail__tool-pill sage-app-detail__tool-pill--placeholder">
                    {name}
                  </span>
                ))}
                {!connected ? (
                  <span className="sage-app-detail__tool-note">
                    {hasMcpEndpoint
                      ? 'Connect to auto-discover tools via MCP'
                      : 'After connecting, exact tools will be auto-discovered'}
                  </span>
                ) : hasMcpEndpoint && !hasDiscoveredTools ? (
                  <span className="sage-app-detail__tool-note">
                    MCP server registered — tool discovery pending. Reconnect or refresh to discover.
                  </span>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
        {sourceServer ? (
          <details className="sage-app-detail__details">
            <summary className="sage-app-detail__details-toggle">Connection details</summary>
            <div className="sage-app-detail__details-body">
              <p>Endpoint: {readString(sourceServer.endpoint) || '—'}</p>
              <p>Authenticated with your {record.label} account</p>
            </div>
          </details>
        ) : connected && hasMcpEndpoint ? (
          <details className="sage-app-detail__details">
            <summary className="sage-app-detail__details-toggle">MCP endpoint</summary>
            <div className="sage-app-detail__details-body">
              <p>Endpoint: {declaredEndpoint}</p>
              <p>MCP server will be registered when you connect via OAuth.</p>
            </div>
          </details>
        ) : null}
      </div>
    );
  }

  function renderConnectorCredentialForm(record: ExternalIntegrationCardRecord) {
    return renderFlatConnectorCredentialForm(record);
  }

  function renderComputerConnectSheet() {
    const connected = selectedGateway !== null && readString(selectedGateway.connection_status || selectedGateway.status).toLowerCase() === 'online';
    const trustState = readString(selectedGateway?.device_trust_state, connected ? 'verified' : 'not connected');
    const lastSeen = selectedGateway ? formatRelativeTimestamp(selectedGateway.last_seen_at) : 'Not connected';
    const statusLabel = connected ? 'Online and ready' : 'Not connected';
    return (
      <ConnectorSetupModalShell
        open={computerConnectOpen}
        title="Connect Agent Computer"
        description="Choose a computer source for Sage. Cloud remains the default when no computer is connected."
        className="sage-computer-connect-modal"
        onClose={() => setComputerConnectOpen(false)}
        actions={(
          <>
            <AppButton
              type="button"
              tone="ghost"
              onClick={() => setComputerConnectOpen(false)}
            >
              Not now
            </AppButton>
            <AppButton
              type="button"
              onClick={openGatewaySurface}
            >
              {connected ? 'Manage computer' : 'Connect a computer'}
            </AppButton>
          </>
        )}
      >
        <div className="sage-computer-connect">
          <div className="sage-computer-connect__hero sage-computer-connect__hero--compact">
            <div className="sage-computer-connect__copy">
              <span className={joinClassNames('sage-computer-connect__status', connected && 'sage-computer-connect__status--online')}>
                {statusLabel}
              </span>
              <strong>Agent Computer source.</strong>
              <p>
                This Device and Dedicated Computer use the same gateway pairing path. Server/VPS and Cloud Computer stay
                visible as separate sources so users understand where Sage is running work.
              </p>
            </div>
          </div>
          <div className="sage-computer-connect__capabilities" aria-label="Agent Computer components">
            {['Connection', 'Local execution'].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <div className="sage-computer-connect__state">
            <div>
              <span>Status</span>
              <strong>{statusLabel}</strong>
            </div>
            <div>
              <span>Trust</span>
              <strong>{trustState.replace(/_/g, ' ')}</strong>
            </div>
            <div>
              <span>Last seen</span>
              <strong>{lastSeen}</strong>
            </div>
          </div>
          <details className="sage-computer-connect__config">
            <summary>Connection details</summary>
            <p>Connection details are only for reconnecting, revoking, or debugging the selected Agent Computer. Normal users should only need the connect button.</p>
          </details>
        </div>
      </ConnectorSetupModalShell>
    );
  }

  function renderConnectorConfigDetails(record: ConnectorCardRecord) {
    return (
      <details className="sage-unified-expand__config-disclosure">
        <summary className="sage-unified-expand__config-summary">Connection details</summary>
        <div className="sage-unified-expand__text">
          App actions Sage can use after you connect this integration.
        </div>
        <div className="sage-unified-expand__tag-row">
          {record.definition.capabilityTags.map((tag) => (
            <span key={tag} className="sage-unified-expand__tag">{tag}</span>
          ))}
        </div>
      </details>
    );
  }

  function renderConnectorExpand(record: ConnectorCardRecord) {

    return (
      <MotionSlidePanel className="sage-unified-expand">
        <div className="sage-unified-expand__header">
          <strong className="sage-unified-expand__title">{record.label}</strong>
          <button
            type="button"
            className="sage-unified-expand__close"
            onClick={() => setExpandedCardId(null)}
            aria-label={`Close ${record.label}`}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>
        {record.definition.surfaceScope === 'studio_only' ? (
          <div className="sage-unified-expand__text">
            This connector belongs to the business channel lane. Personal messaging stays in Personal Messaging through Agent Computer.
          </div>
        ) : null}
        {record.connected ? (
          <>
            <div className="sage-unified-expand__text">{record.definition.summary}</div>
            {renderConnectorConfigDetails(record)}
            <div className="sage-unified-expand__toggle-row">
              <span className="sage-unified-expand__text">Memory</span>
              <button
                type="button"
                className={joinClassNames(
                  'sage-tool-toggle',
                  connectorMemoryEnabled[record.id] !== false && 'sage-tool-toggle--enabled',
                )}
                onClick={() => {
                  setConnectorMemoryEnabled((current) => ({ ...current, [record.id]: !current[record.id] }));
                }}
                aria-pressed={connectorMemoryEnabled[record.id] !== false}
              >
                <span className="sage-tool-toggle__thumb" />
              </button>
            </div>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                tone="ghost"
                disabled={busyCardId === record.id}
                onClick={() => {
                  void handleConnectorDisconnect(record);
                }}
              >
                Disconnect
              </AppButton>
            </div>
          </>
        ) : (
          <>
            <div className="sage-unified-expand__text">{record.definition.summary}</div>
            <div className="sage-unified-expand__text">{record.definition.setupHint}</div>
            {/* Discord pairing UI — OAuth identify-bind with pairing-code fallback */}
            {record.id === 'discord_bot' && !discordPaired ? (
              <div className="sage-unified-expand__pairing" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="app-button app-button--primary"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  onClick={() => {
                    if (discordOAuthUrl) {
                      const popup = window.open(discordOAuthUrl, 'discord-oauth', 'width=500,height=700');
                      if (!popup) window.location.href = discordOAuthUrl;
                    }
                  }}
                >
                  Connect with Discord
                </button>
                <details style={{ marginTop: 10 }}>
                  <summary style={{ fontSize: 13, opacity: 0.6, cursor: 'pointer' }}>
                    Or use a pairing code instead
                  </summary>
                  <div style={{ marginTop: 8 }}>
                    {discordPairingCode ? (
                      <>
                        <div className="sage-unified-expand__pairing-code">
                          <span className="sage-unified-expand__pairing-label">Pairing code</span>
                          <code className="sage-unified-expand__pairing-value">{discordPairingCode}</code>
                        </div>
                        <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                          DM @EmpyralisBot and send{' '}
                          <code style={{ fontWeight: 600 }}>/pair {discordPairingCode}</code>
                        </p>
                        {(discordPublicConfig?.bot_user_id) ? (
                          <a
                            href={`https://discord.com/users/${encodeURIComponent(discordPublicConfig.bot_user_id)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="app-button app-button--primary"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 10 }}
                          >
                            Open @EmpyralisBot on Discord
                          </a>
                        ) : null}
                      </>
                    ) : (
                      <button
                        type="button"
                        className="app-button app-button--primary"
                        disabled={discordPairingLoading}
                        onClick={() => { void startDiscordPairing(); }}
                      >
                        {discordPairingLoading ? 'Generating code...' : 'Generate pairing code'}
                      </button>
                    )}
                  </div>
                </details>
              </div>
            ) : record.id === 'discord_bot' && discordPaired ? (
              <div className="sage-unified-expand__pairing" style={{ marginTop: 12 }}>
                <div className="sage-unified-expand__pairing-code" style={{ color: 'var(--app-color-success, #22c55e)' }}>
                  <span className="sage-unified-expand__pairing-label">Status</span>
                  <code className="sage-unified-expand__pairing-value" style={{ fontSize: 16 }}>✓ Connected to Discord DM</code>
                </div>
                <p className="sage-unified-expand__text" style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                  ✓ Connected! Open Discord and DM @EmpyralisBot to start.
                </p>
                <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className="connector-disconnect-btn"
                    onClick={async () => {
                      try {
                        await fetch(
                          '/api/sage/discord/pair?workspace_id=' + encodeURIComponent(bootstrap.workspace.id),
                          { method: 'DELETE', headers: buildCookieAuthHeaders('DELETE'), credentials: 'include' }
                        );
                      } catch { /* ignore */ }
                      setDiscordPaired(false);
                      setDiscordPairingCode(null);
                    }}
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            ) : null}
            {renderConnectorConfigDetails(record)}
            <div className="sage-unified-expand__actions">
              <button
                type="button"
                className="sage-unified-expand__link"
                onClick={() => setExpandedCardId(null)}
              >
                Close
              </button>
            </div>
          </>
        )}
      </MotionSlidePanel>
    );
  }

  function renderProviderPickerRow(record: ProviderCardRecord, sectionId: ProviderPickerSection['id']) {
    const isHostedSection = sectionId === 'hosted';
    const isLocalSection = sectionId === 'local';
    const pickerConnected = sectionId === 'hosted'
      ? hostedSageAi.allowed
      : providerPickerConnected(record, localCompanionOnline);
    const isActive = activeProviderCard?.id === record.id && (!isHostedSection || !explicitSelectedProfile);
    const requiresSecret = providerRequiresSecret(record.provider, record.profile);
    const showInlineKey = !isHostedSection && providerPickerDraftId === record.id && !pickerConnected && requiresSecret;
    const busy = busyCardId === record.id;
    const displayLabel = sectionId === 'hosted'
      ? hostedAiTierLabel(record)
      : record.provider.id === 'ollama_cloud'
        ? 'Ollama Cloud'
        : record.provider.id === 'ollama'
          ? 'Ollama on Agent Computer'
          : record.label;
    const detailLabel = sectionId === 'hosted'
      ? hostedProviderDetailLabel(hostedSageAi, record)
      : isLocalSection
        ? (localCompanionOnline ? 'Agent Computer · Uses the Ollama models available here' : 'Agent Computer · Connect the selected Agent Computer first')
      : record.provider.id === 'ollama_cloud'
        ? `${providerPickerStatusLabel(record, localCompanionOnline)} · Ollama Cloud`
        : `${providerPickerStatusLabel(record, localCompanionOnline)} · ${providerPathLabel(record)}`;
    return (
      <div key={`${sectionId}:${record.id}`} className={joinClassNames('sage-provider-picker__item', isHostedSection && 'sage-provider-picker__item--hosted')}>
        <button
          type="button"
          className={joinClassNames('sage-provider-picker__row', isHostedSection && 'sage-provider-picker__row--hosted', isActive && 'sage-provider-picker__row--active')}
          disabled={isHostedSection && !hostedSageAi.allowed}
          onClick={() => {
            void handleProviderSelect(record, { hosted: sectionId === 'hosted' });
          }}
        >
          <BrandLogo
            id={record.id}
            label={displayLabel}
            src={platformSafeImage(record.provider.credentialPlane, record.image)}
            failedLogos={failedLogos}
            onError={markLogoFailed}
          />
          <div className="sage-provider-picker__copy">
            <span className="sage-provider-picker__name">{displayLabel}</span>
            <span className="sage-provider-picker__detail">{detailLabel}</span>
          </div>
          {isHostedSection ? (
            <span className="sage-provider-picker__pill">Use Workspace AI</span>
          ) : null}
          <span className={joinClassNames('sage-provider-picker__status-dot', pickerConnected && 'sage-provider-picker__status-dot--connected')} aria-hidden="true" />
          {isActive ? <Check size={14} strokeWidth={2} aria-hidden="true" /> : null}
        </button>
        {showInlineKey ? (
          <div className="sage-provider-picker__inline-key">
            <FormField label={providerCredentialLabel(record.provider, record.profile)}>
              <FormInput
                type="text"
                value={providerDraftKeys[record.id] ?? ''}
                placeholder={providerCredentialPlaceholder(record.provider)}
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                data-1p-ignore="true"
                data-lpignore="true"
                onPasteCapture={(event) => {
                  handleProviderKeyPaste(record.id, event);
                }}
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setProviderDraftKeys((current) => ({ ...current, [record.id]: value }));
                }}
              />
            </FormField>
            {providerNeedsBaseUrl(record.provider) ? (
              <FormField label={providerBaseUrlLabel(record.provider)}>
                <FormInput
                  type="url"
                  value={providerDraftBaseUrls[record.id] ?? ''}
                  placeholder={providerBaseUrlPlaceholder(record.provider)}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setProviderDraftBaseUrls((current) => ({ ...current, [record.id]: value }));
                  }}
                />
              </FormField>
            ) : null}
            <div className="sage-provider-picker__inline-actions">
              <AppButton
                type="button"
                disabled={busy}
                onClick={() => {
                  void handleProviderConnectAndSelect(record);
                }}
              >
                {busy ? 'Saving…' : 'Save'}
              </AppButton>
              <button
                type="button"
                className="sage-unified-expand__link"
                onClick={() => {
                  setProviderPickerDraftId(null);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  function renderAiOverview() {
    if (isLoading) {
      return renderProviderSkeletons();
    }
    const creditMax = Math.max(1, hostedSageAi.monthlyCreditCap);
    const creditValue = Math.max(0, Math.min(creditMax, hostedSageAi.monthlyCreditsRemaining));
    const creditPercent = Math.round((creditValue / creditMax) * 100);
    const activeConnected = activeProviderCard
      ? activeProviderCard === hostedProviderCard && !explicitSelectedProfile
        ? hostedSageAi.allowed
        : providerPickerConnected(activeProviderCard, localCompanionOnline)
      : false;
    const configurableSections = providerPickerSections.filter((section) => section.id !== 'hosted');
    return (
      <section className="sage-unified-section">
        <p className="sage-unified-section__label">AI</p>
        <p className="sage-unified-section__description">Choose the model source Sage uses. Credits stay separate from API accounts.</p>
        <div className="sage-ai-provider-panel">
          <div className="sage-ai-credit-card">
            <div className="sage-ai-credit-card__header">
              <span>
                <span className="sage-ai-credit-card__eyebrow">Workspace AI</span>
                <strong>{aiProviderSummary.creditsLabel}</strong>
              </span>
              <span className={joinClassNames('sage-ai-provider-card__status', hostedSageAi.allowed && 'sage-ai-provider-card__status--connected')}>
                <span
                  className={joinClassNames(
                    'sage-ai-provider-card__dot',
                    hostedSageAi.allowed && 'sage-ai-provider-card__dot--connected',
                  )}
                  aria-hidden="true"
                />
                {hostedSageAi.allowed ? 'Available' : 'Needs setup'}
              </span>
            </div>
            <progress className="sage-ai-credit-card__meter" value={creditValue} max={creditMax}>
              {creditPercent}%
            </progress>
            <div className="sage-ai-credit-card__footer">
              <span>{aiProviderSummary.creditsDetail}</span>
              <AppButton type="button" tone="ghost" onClick={openBillingSettings}>
                View usage
              </AppButton>
            </div>
          </div>

          <div className="sage-ai-current-model">
            <div className="sage-ai-current-model__identity">
              {activeProviderCard ? (
                <BrandLogo
                  id={activeProviderCard.id}
                  label={platformSafeLabel(activeProviderCard?.provider.credentialPlane, activeProviderCard.label, 'Workspace AI')}
                  src={platformSafeImage(activeProviderCard?.provider.credentialPlane, activeProviderCard.image)}
                  failedLogos={failedLogos}
                  onError={markLogoFailed}
                />
              ) : null}
              <span>
                <span className="sage-ai-credit-card__eyebrow">Current model</span>
                <strong>{aiProviderSummary.activeLabel}</strong>
                <small>{aiProviderSummary.activeDetail}</small>
              </span>
            </div>
            <span className={joinClassNames('sage-ai-provider-card__status', activeConnected && 'sage-ai-provider-card__status--connected')}>
              <span
                className={joinClassNames(
                  'sage-ai-provider-card__dot',
                  activeConnected && 'sage-ai-provider-card__dot--connected',
                )}
                aria-hidden="true"
              />
              {activeConnected ? 'Connected' : 'Needs setup'}
            </span>
          </div>

          <details className="sage-progressive-disclosure" open={Boolean(explicitSelectedProfile)}>
            <summary>
              <span>
                <strong>Use your own AI account</strong>
                <small>Provider and local-model setup stays here when you need it.</small>
              </span>
            </summary>
            <div className="sage-ai-provider-sections">
              {configurableSections.map((section) => (
                <section key={section.id} className="sage-ai-provider-section">
                  <div className="sage-ai-provider-section__header">
                    <strong>{section.id === 'local' ? 'Models on Agent Computer' : 'AI APIs'}</strong>
                    <span>{section.items.length} option{section.items.length === 1 ? '' : 's'}</span>
                  </div>
                  {section.items.length > 0 ? (
                    <div className="sage-ai-provider-grid">
                      {section.items.map((record) => renderAiProviderChoiceCard(record, section))}
                    </div>
                  ) : (
                    <div className="sage-integrations-detail-card">
                      <strong>{section.label}</strong>
                      <span>No provider is available in this section yet.</span>
                    </div>
                  )}
                </section>
              ))}
            </div>
          </details>
        </div>
      </section>
    );
  }

  function renderAiRouteSummary() {
    const currentRoute = workspaceDefaultRoute;
    const routeNeedsSetup = workspaceDefaultRouteNeedsSetup;
    // Single source of truth: credits from billing/summary (canonical, same as chat/billing/settings panes),
    // tier from aiProviderSummary.activeLabel (profile-derived, same as current model card).
    // Falls back to hostedSageAi while billing loads. Previously these read
    // workspaceAiRoute.budgets.remainingCredits (could be 0) and currentRoute.label (could say "Light").
    const remainingCredits = billingCredits?.monthlyCreditsRemaining ?? hostedSageAi.monthlyCreditsRemaining;
    const creditCap = billingCredits?.monthlyCreditCap ?? hostedSageAi.monthlyCreditCap;
    const budgetDetail = creditCap > 0
      ? `${formatCredits(remainingCredits)} / ${formatCredits(creditCap)} credits remaining`
      : hostedSageAi.allowed
        ? 'Credits: Available'
        : 'Credits: Not active';
    const routeLabel = routeNeedsSetup
      ? 'Needs setup'
      : aiProviderSummary.activeLabel;
    const routeDescription = routeNeedsSetup
      ? readString(currentRoute?.description) || 'Reconnect this provider before it can be the workspace AI route.'
      : readString(currentRoute?.description) || aiProviderSummary.activeDetail;
    return (
      <section className="sage-unified-section sage-ai-route-summary" aria-label="Workspace AI route">
        <p className="sage-unified-section__label">AI & Runtime</p>
        <p className="sage-unified-section__description">
          One workspace AI route is shared by Sage, Studio agents, and mini-apps. Provider setup lives here, not inside each surface.
        </p>
        <div className="sage-ai-route-summary__grid">
          <article className="sage-ai-route-summary__card sage-ai-route-summary__card--primary">
            <span>Current route</span>
            <strong>Workspace default: {routeLabel}</strong>
            <p>{routeDescription}</p>
            <p className="sage-ai-route-summary__credits">{budgetDetail}</p>
          </article>
        </div>
      </section>
    );
  }

  function renderExternalCollection(
    label: string,
    description: string,
    cards: ExternalIntegrationCardRecord[],
    emptyMessage: string,
  ) {
    const expandedCard = cards.find((card) => card.id === expandedCardId) ?? null;
    return (
      <section className="sage-unified-section">
        <p className="sage-unified-section__label">{label}</p>
        <p className="sage-unified-section__description">{description}</p>
        {cards.length > 0 ? (
          <>
            <div className="sage-unified-grid sage-unified-grid--4">
              {cards.map(renderExternalCard)}
            </div>
            {expandedCard ? (
              <div
                className="sage-unified-modal"
                role="dialog"
                aria-modal="true"
                aria-label={`${expandedCard.label} setup`}
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) {
                    setExpandedCardId(null);
                  }
                }}
              >
                <div className="sage-unified-modal__panel">
                  {renderExternalExpand(expandedCard)}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="sage-integrations-detail-card">
            <strong>{label}</strong>
            <span>{emptyMessage}</span>
          </div>
        )}
      </section>
    );
  }

  function renderAgentComputerConnections() {
    const device = personalCards.find((card) => card.id === 'device');
    const connected = device?.statusTone === 'connected';
    const statusTone = device?.statusTone ?? 'warning';
    const statusClass = personalStatusClassName({ statusTone });
    const personalDoctor = doctor?.personal_channels && typeof doctor.personal_channels === 'object'
      ? doctor.personal_channels
      : null;
    const personalDoctorStatus = readString(personalDoctor?.status, 'warn').toLowerCase();
    const personalDoctorTone: PersonalCardStatusTone = personalDoctorStatus === 'pass' || personalDoctorStatus === 'healthy'
      ? 'connected'
      : personalDoctorStatus === 'fail' || personalDoctorStatus === 'blocked'
        ? 'danger'
        : 'warning';
    const personalDoctorCount = readInteger(personalDoctor?.count, communicationPersonalCards.length);
    const personalDoctorConnectedCount = readInteger(personalDoctor?.connected_count, communicationPersonalCards.filter((card) => card.statusTone === 'connected').length);
    return (
      <section className="sage-unified-section sage-agent-computer">
        <p className="sage-unified-section__label">Agent Computer</p>
        <p className="sage-unified-section__description">
          Sage runs in cloud first. Use Agent Computer only for local files, apps, browser sessions, personal channels, terminal, or private hardware.
        </p>
        <div className="sage-agent-computer__panel">
          <div className="sage-agent-computer__setting">
            <div className="sage-agent-computer__copy">
              <strong>Computer Assistant</strong>
              <span>Connect this computer, a Mac mini, or a server only for local/private work Sage cannot do through connected apps.</span>
            </div>
            <span className={joinClassNames('sage-unified-card__status', statusClass)}>
              {connected ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
              {device?.statusLabel ?? 'Not connected'}
            </span>
            <AppButton type="button" onClick={openComputerConnectSheet}>
              {connected ? 'Manage' : 'Connect'}
            </AppButton>
          </div>
          <div className="sage-agent-computer__setting">
            <div className="sage-agent-computer__copy">
              <strong>Personal messaging doctor</strong>
              <span>{readString(personalDoctor?.summary, 'Telegram, WhatsApp, Signal, iMessage, and WeChat readiness appears here when those personal channels run on Agent Computer.')}</span>
            </div>
            <span className={joinClassNames('sage-unified-card__status', personalStatusClassName({ statusTone: personalDoctorTone }))}>
              {personalDoctorTone === 'connected' ? <span className="sage-unified-card__dot" aria-hidden="true" /> : null}
              {personalDoctorConnectedCount}/{personalDoctorCount} connected
            </span>
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => {
                setSelectedIntegrationId('advanced');
                setExpandedCardId(null);
              }}
            >
              Review
            </AppButton>
          </div>
          <div className="sage-agent-computer__setting">
            <div className="sage-agent-computer__copy">
              <strong>Phone app</strong>
              <span>Use mobile for chat, notifications, and approvals. It is not a separate computer runtime.</span>
            </div>
            <span className="sage-unified-card__status sage-unified-card__status--connected">Built in</span>
          </div>
          <details className="sage-agent-computer__advanced">
            <summary>Remote and SSH options</summary>
            <p>
              Cloud Computer and Server/VPS stay behind the computer setup flow. Normal app work should use connected apps or hosted browser first.
            </p>
            <AppButton type="button" tone="ghost" onClick={() => openWorkspaceRoute('gateway')}>
              Open computer settings
            </AppButton>
          </details>
        </div>
      </section>
    );
  }

  function renderAiRuntimeOverview() {
    return (
      <>
        {renderAiRouteSummary()}
        {showProviders ? renderAiOverview() : null}
      </>
    );
  }

  function renderConnectionsOverview() {
    return (
      <>
        {showPersonalSurface && thisComputerCards.length > 0 ? renderAgentComputerConnections() : null}
      </>
    );
  }

  function renderAppsMessagingOverview() {
    return (
      <>
        {renderExternalCollection(
          'App Connections',
          'Connect work apps here. These are external services Sage can read or act inside.',
          appCards,
          'No app connectors are available for this surface yet.',
        )}
        {renderExternalCollection(
          showPersonalSurface ? 'Personal Messaging' : 'Business Channels',
          showPersonalSurface
            ? 'Personal channels stay on Agent Computer. Business/customer channels stay separate.'
            : 'Studio channels are customer-facing and separate from Sage personal channels.',
          channelCards,
          'No messaging connectors are available for this surface yet.',
        )}
      </>
    );
  }

  function renderChannelsOverview() {
    return (
      <>
        {renderExternalCollection(
          showPersonalSurface ? 'Channels' : 'Channels',
          showPersonalSurface
            ? 'Cloud channels work without Agent Computer. Personal account channels use Agent Computer only when needed.'
            : 'Studio channels are customer-facing and separate from Sage personal channels.',
          channelCards,
          'No messaging connectors are available for this surface yet.',
        )}
      </>
    );
  }

  function renderAppsOverview() {
    return (
      <>
        {renderExternalCollection(
          'Apps',
          'Connect work apps here. These are external services Sage can read or act inside. Tools are auto-discovered after connecting.',
          appCards,
          'No app connectors are available for this surface yet.',
        )}
      </>
    );
  }

  function renderRecipesOverview() {
    const routeLabel: Record<SageRecipeRouteMode, string> = {
      chat_only: 'Basic Assistant',
      connector_api: 'Connected Assistant',
      cloud_browser: 'Cloud Browser',
      cloud_computer: 'Cloud Computer',
      gateway_required: 'Computer Assistant',
    };
    const approvalLabel: Record<SageRecipeDefinition['approvalProfile'], string> = {
      read_only: 'Read only',
      external_write: 'Approval before sending or changing',
      computer_action: 'Computer approval',
    };
    return (
      <section className="sage-unified-section">
        <p className="sage-unified-section__label">Recipes</p>
        <p className="sage-unified-section__description">
          Start repeatable work from a clean prompt. Sage will use connected apps first and ask for setup when something is missing.
        </p>
        <div className="sage-unified-grid sage-unified-grid--3">
          {SAGE_RECIPE_DEFINITIONS.map((recipe) => (
            <article key={recipe.id} className="sage-integrations-detail-card">
              <strong>{recipe.title}</strong>
              <span>{recipe.summary}</span>
              <div className="sage-unified-card__tags" aria-label={`${recipe.title} requirements`}>
                <span>{routeLabel[recipe.preferredRoute]}</span>
                <span>{approvalLabel[recipe.approvalProfile]}</span>
                {recipe.scheduleSupported ? <span>Can be scheduled</span> : null}
              </div>
              <span>Needs: {recipe.requiredConnections.join(', ')}</span>
              <AppButton
                type="button"
                tone="secondary"
                onClick={() => {
                  openRecipeInSage(recipe);
                }}
              >
                Start recipe
              </AppButton>
            </article>
          ))}
        </div>
      </section>
    );
  }

  function renderSkillsOverview() {
    const readySkills = [
      { name: 'Files', detail: 'Read and write workspace files with approval.', status: 'Ready' },
      { name: 'Browser', detail: 'Open pages, inspect screens, and use web surfaces.', status: 'Ready' },
      { name: 'Screenshot', detail: 'Capture the connected computer through Hardware.', status: 'Ready' },
      { name: 'Memory', detail: 'Read and update Sage memory with policy checks.', status: 'Ready' },
      { name: 'Shell / code', detail: 'Run approved commands through Agent Computer.', status: 'Approval' },
    ];
    const setupSkills = [
      { name: 'Whisper', detail: 'Speech-to-text for voice notes and audio files.', status: 'Setup' },
      { name: 'TTS', detail: 'Text-to-speech and voice replies.', status: 'Setup' },
      { name: 'Image generation', detail: 'Generate images through a selected media model.', status: 'Setup' },
      { name: 'Video generation', detail: 'Generate video through a selected media model.', status: 'Setup' },
    ];
    const renderSkillCard = (skill: { name: string; detail: string; status: string }) => (
      <div key={skill.name} className="sage-skill-card">
        <div>
          <strong>{skill.name}</strong>
          <span>{skill.detail}</span>
        </div>
        <em>{skill.status}</em>
      </div>
    );

    return (
      <div className="sage-skills-surface">
        <section className="sage-unified-section">
          <p className="sage-unified-section__label">Ready</p>
          <div className="sage-skill-card-grid">{readySkills.map(renderSkillCard)}</div>
        </section>
        <section className="sage-unified-section">
          <p className="sage-unified-section__label">Needs setup</p>
          <div className="sage-skill-card-grid">{setupSkills.map(renderSkillCard)}</div>
        </section>
      </div>
    );
  }

  function renderPluginsOverview() {
    return renderDeveloperOverview();
  }

  function renderAdvancedOverview() {
    return (
      <>
        {renderSkillsOverview()}
        {renderPluginsOverview()}
      </>
    );
  }

  function renderDeveloperOverview() {
    return (
      <>
        <section className="sage-unified-section">
          <p className="sage-unified-section__label">MCP</p>
          <p className="sage-unified-section__description">Connect technical tool servers only. Apps and skills stay in their own tabs.</p>
          <div className="sage-integrations-detail-card">
            <strong>MCP servers, custom APIs, and webhooks</strong>
            <span>Use this lane for custom tools that should not be mixed with everyday app plugins. MCP tools stay unavailable until reviewed here.</span>
          </div>
          <div className="sage-unified-expand">
            <div className="sage-unified-expand__header">
              <strong className="sage-unified-expand__title">Add MCP server</strong>
            </div>
            <FormField label="Server ID">
              <FormInput
                value={mcpServerDraft.serverId}
                placeholder="inventory-feed"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                onChange={(event) => {
                  const serverId = event.currentTarget.value;
                  setMcpServerDraft((current) => ({ ...current, serverId }));
                }}
              />
            </FormField>
            <FormField label="Label">
              <FormInput
                value={mcpServerDraft.label}
                placeholder="Inventory Feed"
                onChange={(event) => {
                  const label = event.currentTarget.value;
                  setMcpServerDraft((current) => ({ ...current, label }));
                }}
              />
            </FormField>
            <FormField label="Endpoint">
              <FormInput
                type="url"
                value={mcpServerDraft.endpoint}
                placeholder="https://example.com/mcp"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                onChange={(event) => {
                  const endpoint = event.currentTarget.value;
                  setMcpServerDraft((current) => ({ ...current, endpoint }));
                }}
              />
            </FormField>
            <div className="sage-unified-expand__actions">
              <AppButton
                type="button"
                disabled={busyCardId === 'mcp:save'}
                onClick={() => {
                  void handleMcpServerSave();
                }}
              >
                {busyCardId === 'mcp:save' ? 'Saving...' : 'Save and discover tools'}
              </AppButton>
            </div>
          </div>
          {mcpServers.length > 0 ? (
            <div className="sage-ai-provider-sections">
              {mcpServers.map((server) => {
                const serverId = readString(server.id);
                const serverTools = Array.isArray(server.tools) ? server.tools : [];
                return (
                  <section key={serverId || readString(server.endpoint)} className="sage-ai-provider-section">
                    <div className="sage-ai-provider-section__header">
                      <strong>{readString(server.label, serverId || 'MCP server')}</strong>
                      <span>{server.enabled === false ? 'Disabled' : `${serverTools.length} tool${serverTools.length === 1 ? '' : 's'}`}</span>
                    </div>
                    <div className="sage-integrations-detail-card">
                      <strong>{readString(server.endpoint, 'No endpoint recorded')}</strong>
                      <span>
                        {server.last_synced_at ? `Last synced ${readString(server.last_synced_at)}` : 'Refresh to discover current tools.'}
                      </span>
                      <div className="sage-unified-expand__actions">
                        <AppButton
                          type="button"
                          tone="secondary"
                          disabled={busyCardId === `mcp:${serverId}:refresh`}
                          onClick={() => {
                            void handleMcpServerRefresh(server);
                          }}
                        >
                          {busyCardId === `mcp:${serverId}:refresh` ? 'Refreshing...' : 'Refresh tools'}
                        </AppButton>
                        <AppButton
                          type="button"
                          tone="ghost"
                          disabled={busyCardId === `mcp:${serverId}:delete`}
                          onClick={() => {
                            void handleMcpServerDelete(server);
                          }}
                        >
                          {busyCardId === `mcp:${serverId}:delete` ? 'Removing...' : 'Remove'}
                        </AppButton>
                      </div>
                    </div>
                    {serverTools.length > 0 ? (
                      <div className="sage-unified-grid sage-unified-grid--4">
                        {serverTools.map((tool) => {
                          const toolName = readString(tool.name);
                          const approved = tool.approved !== false;
                          const approveKey = `mcp:${serverId}:${toolName}:approve`;
                          return (
                            <article key={toolName || readString(tool.label)} className="sage-unified-card">
                              <span className="sage-unified-card__label">{readString(tool.label, toolName || 'MCP tool')}</span>
                              <span className="sage-unified-card__detail">
                                {readString(tool.description, 'No description provided.')}
                              </span>
                              <span className={joinClassNames('sage-unified-card__status', approved && 'sage-unified-card__status--connected')}>
                                {approved ? 'Approved' : 'Needs review'}
                              </span>
                              <span className="sage-unified-card__detail">
                                {[readString(tool.action_class, 'read'), readString(tool.risk_level, 'low'), tool.requires_approval ? 'needs approval' : 'policy bound'].join(' · ')}
                              </span>
                              {!approved ? (
                                <AppButton
                                  type="button"
                                  tone="secondary"
                                  disabled={busyCardId === approveKey}
                                  onClick={() => {
                                    void handleMcpToolApprove(server, tool);
                                  }}
                                >
                                  {busyCardId === approveKey ? 'Approving...' : 'Approve tool'}
                                </AppButton>
                              ) : null}
                            </article>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="sage-integrations-detail-card">
                        <strong>No tools discovered yet</strong>
                        <span>Refresh the server or check its MCP endpoint.</span>
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="sage-integrations-detail-card">
              <strong>No MCP servers connected</strong>
              <span>Add a server to make custom tools visible for approval.</span>
            </div>
          )}
        </section>
      </>
    );
  }

  function renderIntegrationSidebar() {
    if (isLoading && integrationGroups.length === 0) {
      return (
        <div className="sage-integrations-nav">
          <div className="sage-integrations-nav__group">
            <span className="sage-integrations-nav__group-label">Loading</span>
            <div className="sage-integrations-nav__placeholder">Loading apps and accounts…</div>
          </div>
        </div>
      );
    }
    return (
      <div className="sage-integrations-nav">
        {integrationGroups.map((group) => (
          <button
            key={group.id}
            type="button"
            className={joinClassNames(
              'sage-integrations-nav__bucket',
              selectedIntegrationGroup?.id === group.id && 'sage-integrations-nav__bucket--active',
            )}
            aria-selected={selectedIntegrationGroup?.id === group.id}
            onClick={() => {
              setSelectedIntegrationId(group.id);
              setExpandedCardId(null);
              if (typeof window !== 'undefined') {
                const nextUrl = new URL(window.location.href);
                nextUrl.searchParams.set('section', group.id);
                nextUrl.searchParams.delete('connection');
                window.history.replaceState(window.history.state, '', nextUrl.toString());
              }
            }}
          >
            <span className="sage-integrations-nav__icon" aria-hidden="true">
              {compactNavInitials(group.label)}
            </span>
            <span className="sage-integrations-nav__bucket-copy">
              <span className="sage-integrations-nav__label">{group.label}</span>
              <span className="sage-integrations-nav__detail">{group.detail}</span>
            </span>
            <span className={joinClassNames('sage-integrations-nav__status', `sage-integrations-nav__status--${group.statusTone}`)}>
              {group.countLabel}
            </span>
          </button>
        ))}
      </div>
    );
  }

  function renderSelectedIntegrationDetail() {
    if (!selectedIntegrationGroup) {
      return (
        <div className="sage-settings-empty">
          No setup options available.
        </div>
      );
    }
    const detail = (() => {
      switch (selectedIntegrationGroup.id) {
        case 'ai_runtime':
          return renderAiRuntimeOverview();
        case 'channels':
          return renderChannelsOverview();
        case 'apps':
          return renderAppsOverview();
        case 'recipes':
          return renderRecipesOverview();
        case 'advanced':
        default:
          return renderAdvancedOverview();
      }
    })();
    return detail;
  }


  return (
    <div className={joinClassNames('sage-settings-panel sage-settings-panel--connectors', className)} data-workstation-surface="integrations">
      <WorkstationSplitWorkbench
        ariaLabel="Setup"
        className="sage-integrations-workbench sage-integrations-workbench--single"
        sidebar={null}
      >
        {integrationGroups.length > 1 ? (
          <div className="sage-integrations-topnav">{renderIntegrationSidebar()}</div>
        ) : null}
        {renderSelectedIntegrationDetail()}
      </WorkstationSplitWorkbench>
      {/* Global error banner removed — per-card errors are shown inline on each card */}

      {renderComputerConnectSheet()}

      <ConnectorSetupModalShell
        open={providerPickerOpen}
        title="Choose workspace AI route"
        description="Use the workspace default route, provider API keys, or local models from AI & Runtime."
        onClose={() => {
          setProviderPickerOpen(false);
          setProviderPickerDraftId(null);
        }}
      >
        <div className="sage-provider-picker">
          {providerPickerSections.map((section) => (
            <section key={section.id} className="sage-provider-picker__section">
              <h3 className="sage-provider-picker__section-label">{section.label}</h3>
              <div className="sage-provider-picker__list">
                {section.items.length > 0 ? (
                  section.items.map((record) => renderProviderPickerRow(record, section.id))
                ) : section.id === 'hosted' ? (
                  <div className="sage-provider-picker__empty">
                    <span>{describeHostedSageAi(hostedSageAi, null)}</span>
                    <button type="button" onClick={openBillingSettings}>
                      View usage
                    </button>
                  </div>
                ) : null}
              </div>
            </section>
          ))}
        </div>
      </ConnectorSetupModalShell>
    </div>
  );
}
