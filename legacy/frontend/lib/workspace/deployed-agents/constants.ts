import type {
  SpecialistOverlayTabId,
  StudioTemplate,
  WizardState,
  WizardStepId,
} from './types';

export const DEFAULT_SAFE_AGENT_PERSONA = 'Helpful Business Agent that answers customers clearly, stays within the configured business information, and asks a human for help when unsure.';
export const DEFAULT_SAFE_AGENT_SYSTEM_PROMPT = 'Answer using the Business Agent instructions and trusted knowledge sources. If the answer is not known, say so clearly and ask for a human follow-up. Do not invent prices, policies, availability, legal advice, medical advice, or financial advice.';
export const DEFAULT_SAFE_AGENT_PERSONA_VALUES = [
  DEFAULT_SAFE_AGENT_PERSONA,
  'Helpful customer-facing assistant that answers clearly, stays within the configured business information, and asks a human for help when unsure.',
];
export const DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES = [
  DEFAULT_SAFE_AGENT_SYSTEM_PROMPT,
  'Answer using the agent instructions and trusted knowledge sources. If the answer is not known, say so clearly and ask for a human follow-up. Do not invent prices, policies, availability, legal advice, medical advice, or financial advice.',
];
export const DEFAULT_SAFE_MONTHLY_COST_CAP_USD = '25';

export const DEPLOYED_AGENT_WIZARD_STEPS: Array<{
  id: WizardStepId;
  label: string;
  description: string;
}> = [
  {
    id: 'overview',
    label: 'Basics',
    description: 'Name the assistant and describe its job.',
  },
  {
    id: 'knowledge',
    label: 'Knowledge',
    description: 'Add the trusted information this Business Agent should use.',
  },
  {
    id: 'tools',
    label: 'Actions',
    description: 'Choose allowed actions and playbooks.',
  },
  {
    id: 'channels',
    label: 'Channels',
    description: 'Connect where the assistant will talk to customers.',
  },
  {
    id: 'memory',
    label: 'Memory',
    description: 'Control how the assistant remembers customers.',
  },
  {
    id: 'safety',
    label: 'Safety',
    description: 'Set owner handoff and customer handling boundaries.',
  },
  {
    id: 'test',
    label: 'Review',
    description: 'Final check before creating the draft.',
  },
];

export const CREATE_AGENT_WIZARD_STEPS: Array<{
  id: WizardStepId;
  label: string;
  description: string;
}> = [
  {
    id: 'overview',
    label: 'Create your agent',
    description: 'Start with a name and instructions. Knowledge, apps, channels, and actions come after creation.',
  },
];

export const STUDIO_AI_TIER_OPTIONS: ReadonlyArray<{
  value: WizardState['aiTier'];
  label: string;
  hint: string;
}> = [
  { value: 'light', label: 'Fast', hint: 'Lower cost for routine customer replies.' },
  { value: 'pro', label: 'Balanced', hint: 'Recommended default for production customer support.' },
  { value: 'max', label: 'Best', hint: 'Highest answer quality when accuracy matters more than cost.' },
];

export const STUDIO_AI_SOURCE_OPTIONS: ReadonlyArray<{
  value: WizardState['aiSource'];
  label: string;
  title: string;
  hint: string;
}> = [
  {
    value: 'empyralis_credits',
    label: 'Workspace default',
    title: 'Use workspace default',
    hint: 'Recommended route for most Business Agents. Inherits the workspace AI route from Connections.',
  },
  {
    value: 'workspace_api_key',
    label: 'Workspace API key',
    title: 'Use a workspace API key',
    hint: 'Runs through a provider credential saved for this workspace, not an agent-private secret.',
  },
  {
    value: 'local_model',
    label: 'Local or self-hosted',
    title: 'Use a local or self-hosted model',
    hint: 'Uses a paired Agent Computer or local provider for developer-controlled inference.',
  },
  {
    value: 'subscription_passthrough',
    label: 'Approved gateway',
    title: 'Use an approved gateway',
    hint: 'Reserved for workspace-approved AI gateways such as LiteLLM, Azure OpenAI, Bedrock, or Vertex AI.',
  },
];

export const STUDIO_API_PROVIDER_IDS = new Set([
  'anthropic',
  'azure_openai',
  'deepseek',
  'gemini',
  'groq',
  'mistral',
  'ollama_cloud',
  'openai',
  'openrouter',
  'qwen',
  'custom_openai_compatible',
  'vertex',
  'xai',
]);

export const STUDIO_LOCAL_PROVIDER_IDS = new Set([
  'codex',
  'codex_cli',
  'local',
  'local_ollama',
  'ollama',
  'openai-codex',
]);

export const STUDIO_DEFAULT_RUNTIME_PLACEMENT: WizardState['runtimePlacement'] = 'managed_cloud';

export const STUDIO_RUNTIME_OPTIONS: ReadonlyArray<{
  value: WizardState['runtimePlacement'];
  label: string;
  hint: string;
  supplier: WizardState['runtimeSupplierKind'];
  capabilities: string;
  runsWhere: string;
  privacy: string;
  costRisk: string;
  setup: string;
  bestFor: string;
}> = [
  {
    value: 'managed_cloud',
    label: 'Cloud worker',
    supplier: 'empyralis',
    hint: 'Default Business Agent runtime for customer conversations. No computer, browser, or shell access.',
    capabilities: 'Cloud conversation runtime with approved API routes.',
    runsWhere: 'Empyralis Cloud. The Business Agent calls the selected AI route from the platform.',
    privacy: 'High. No direct computer access surface.',
    costRisk: 'Predictable platform usage plus provider API cost.',
    setup: 'Default production path.',
    bestFor: 'Support, FAQs, triage, and policy-safe business workers.',
  },
  {
    value: 'hosted_hardware_pool',
    label: 'Agent Computer - Cloud Computer',
    supplier: 'empyralis',
    hint: 'Hosted Agent Computer with managed connection and governed execution in an isolated session.',
    capabilities: 'Managed connection and governed execution on an isolated cloud computer.',
    runsWhere: 'Isolated cloud computer managed by Empyralis.',
    privacy: 'Medium. Session activity can be logged for safety and audit.',
    costRisk: 'High.',
    setup: 'Enable computer automation policy and usage limits.',
    bestFor: 'Web workflows, operations playbooks, and guided back-office tasks.',
  },
  {
    value: 'customer_local',
    label: 'Agent Computer - This Device',
    supplier: 'customer',
    hint: 'Connected Agent Computer on a trusted device.',
    capabilities: 'Managed connection and governed execution on a connected computer.',
    runsWhere: 'A connected computer registered to this workspace.',
    privacy: 'Very high data locality, with host-device trust responsibility.',
    costRisk: 'Medium.',
    setup: 'Connect a computer, then grant scoped permissions.',
    bestFor: 'Personal workflows and device-local automation.',
  },
  {
    value: 'customer_hosted',
    label: 'Agent Computer - Server/VPS',
    supplier: 'customer',
    hint: 'Optional Agent Computer using customer-owned server infrastructure.',
    capabilities: 'Customer-managed execution with workspace-scoped controls.',
    runsWhere: 'Customer-owned infrastructure registered to this workspace.',
    privacy: 'Highest control in your own environment.',
    costRisk: 'Variable, depends on your infrastructure.',
    setup: 'Register and maintain a healthy Server/VPS runtime.',
    bestFor: 'Regulated environments and enterprise-owned operations.',
  },
];

export const STUDIO_APPROVAL_MODE_OPTIONS: ReadonlyArray<{
  value: WizardState['approvalMode'];
  label: string;
  hint: string;
}> = [
  { value: 'guarded', label: 'Guarded', hint: 'Escalate early and ask the owner sooner.' },
  { value: 'balanced', label: 'Balanced', hint: 'Default handoff posture for most Business Agents.' },
  { value: 'autonomous', label: 'Self-serve', hint: 'Handle more routine customer work before handoff.' },
];

export const STUDIO_RUNTIME_ACCESS_MODE_OPTIONS: ReadonlyArray<{
  value: WizardState['runtimeAccessMode'];
  label: string;
  hint: string;
}> = [
  {
    value: 'default_guarded',
    label: 'Default',
    hint: 'Runs low-risk work automatically. Risky file, terminal, browser, message, payment, install, or system actions ask first.',
  },
  {
    value: 'custom',
    label: 'Custom',
    hint: 'Choose what this agent can access, what always asks first, and what is blocked. Starts approval-gated until a policy is saved.',
  },
];

export const STUDIO_TOOL_OPTIONS: ReadonlyArray<{
  id: string;
  label: string;
  description: string;
}> = [
  {
    id: 'spreadsheet_read',
    label: 'Spreadsheet read',
    description: 'Read menu, availability, and daily specials from connected sheets.',
  },
  {
    id: 'spreadsheet_append',
    label: 'Spreadsheet append',
    description: 'Log confirmed orders or owner follow-ups into a connected sheet.',
  },
  {
    id: 'web_search',
    label: 'Web search',
    description: 'Search public websites for current facts and references.',
  },
  {
    id: 'http_request',
    label: 'HTTP request',
    description: 'Call approved APIs and webhooks for structured lookups.',
  },
  {
    id: 'gmail_send',
    label: 'Send email',
    description: 'Draft or send Gmail replies from a connected workspace mailbox.',
  },
  {
    id: 'calendar_write',
    label: 'Calendar write',
    description: 'Create or update calendar events for scheduling workflows.',
  },
];

export const STUDIO_ACTION_SKILL_OPTIONS: ReadonlyArray<{
  id: string;
  label: string;
  description: string;
  requiredToolIds: string[];
}> = [
  {
    id: 'book_appointment',
    label: 'Book appointment',
    description: 'Coordinate a time, create the calendar event, and send the customer a confirmation.',
    requiredToolIds: ['calendar_write', 'gmail_send'],
  },
  {
    id: 'look_up_sheet_record',
    label: 'Look up sheet records',
    description: 'Check approved sheets before answering about availability, orders, or accounts.',
    requiredToolIds: ['spreadsheet_read'],
  },
  {
    id: 'log_workspace_note',
    label: 'Log workspace note',
    description: 'Write confirmed orders, handoff notes, or follow-up tasks to a connected sheet.',
    requiredToolIds: ['spreadsheet_append'],
  },
  {
    id: 'send_email_update',
    label: 'Send email update',
    description: 'Draft or send a customer-ready email from the connected workspace mailbox.',
    requiredToolIds: ['gmail_send'],
  },
  {
    id: 'call_approved_api',
    label: 'Call approved API',
    description: 'Use an approved webhook or API endpoint for live account, order, or inventory lookups.',
    requiredToolIds: ['http_request'],
  },
];

export const CONTEXT_PRESET_OPTIONS: ReadonlyArray<{
  id: string;
  label: string;
  description: string;
}> = [
  {
    id: 'compact',
    label: 'Compact',
    description: 'Lower cost',
  },
  {
    id: 'standard',
    label: 'Standard',
    description: 'Recommended',
  },
  {
    id: 'extended',
    label: 'Extended',
    description: 'More source context',
  },
  {
    id: 'max',
    label: 'Max',
    description: 'Largest safe context',
  },
];

export function normalizeContextPresetId(value: string | null | undefined): string {
  const token = String(value || '').trim().toLowerCase();
  if (token === 'balanced') {
    return 'standard';
  }
  if (token === 'deep') {
    return 'extended';
  }
  return CONTEXT_PRESET_OPTIONS.some((option) => option.id === token) ? token : 'standard';
}

export const RETENTION_PRESET_OPTIONS: ReadonlyArray<{
  id: string;
  label: string;
  description: string;
}> = [
  {
    id: 'short',
    label: 'Short',
    description: 'Recent conversation only',
  },
  {
    id: 'standard',
    label: 'Standard',
    description: 'Normal support memory',
  },
  {
    id: 'extended',
    label: 'Extended',
    description: 'Long-running customer relationship',
  },
];

export const SPECIALIST_OVERLAY_TABS: Array<{
  id: SpecialistOverlayTabId;
  label: string;
}> = [
  { id: 'overview', label: 'Overview' },
  { id: 'chat', label: 'Chat' },
  { id: 'channels', label: 'Channels' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'ai', label: 'Model' },
  { id: 'tools', label: 'Actions' },
  { id: 'memory', label: 'Memory' },
  { id: 'artifacts', label: 'Artifacts' },
  { id: 'analytics', label: 'Results' },
];

export const NATIVE_SPECIALIST_OVERLAY_TABS = SPECIALIST_OVERLAY_TABS.filter((tab) => tab.id !== 'artifacts');

export const AGENT_STUDIO_OBJECT_LABELS = {
  studio_agent: 'Studio Agent',
  connected_external_agent: 'Connected Agent',
  agent_computer: 'Agent Computer',
  external_agent_reserved: 'External Agent',
  group_reserved: 'Agent Group',
} as const;

export const AGENT_VISIBILITY_LABELS = {
  private_workspace: 'Private workspace',
  public_channel: 'Public channel',
  reserved: 'Reserved',
} as const;

export function normalizeSpecialistOverlayTabId(value: string | null | undefined): SpecialistOverlayTabId {
  const normalized = String(value || '').trim();
  if (normalized === 'connectors') {
    return 'tools';
  }
  return SPECIALIST_OVERLAY_TABS.some((tab) => tab.id === normalized)
    ? normalized as SpecialistOverlayTabId
    : 'overview';
}

export const SPECIALIST_CONNECTOR_CARDS: ReadonlyArray<{
  id: string;
  label: string;
  image: string;
  connectorIds?: string[];
  capabilityTags: string[];
}> = [
  {
    id: 'telegram',
    label: 'Telegram Bot',
    image: '/brand-assets/channels/telegram.svg?v=3',
    capabilityTags: ['Messages', 'Replies'],
  },
  {
    id: 'whatsapp',
    label: 'WhatsApp Business',
    image: '/brand-assets/channels/whatsapp.svg?v=3',
    connectorIds: ['whatsapp_twilio'],
    capabilityTags: ['Messages', 'Autopilot'],
  },
  {
    id: 'gmail',
    label: 'Gmail',
    image: '/brand-assets/apps/gmail.svg?v=3',
    connectorIds: ['google_workspace'],
    capabilityTags: ['Send email', 'Read inbox'],
  },
  {
    id: 'calendar',
    label: 'Calendar',
    image: '/brand-assets/apps/google-calendar.svg?v=3',
    connectorIds: ['google_workspace', 'microsoft_365'],
    capabilityTags: ['Calendar', 'Events'],
  },
  {
    id: 'custom_api',
    label: 'Custom API',
    image: '/brand-assets/generic/webhook.svg?v=3',
    connectorIds: ['webhook'],
    capabilityTags: ['API', 'Webhook'],
  },
  {
    id: 'tool_server',
    label: 'Action server',
    image: '/brand-assets/generic/api.svg?v=3',
    connectorIds: ['mcp_server'],
    capabilityTags: ['MCP', 'Custom actions'],
  },
];

export const STUDIO_TEMPLATES: ReadonlyArray<StudioTemplate> = [
  {
    id: 'shop_assistant',
    title: 'Shop Assistant',
    category: 'Retail',
    icon: 'SA',
    outcome: 'Answers product questions, checks availability, and captures buyer details.',
    description: 'Customer assistant for local shops, boutiques, auto-parts stores, and product catalogs.',
    setupTime: '5 min setup',
    channelLabel: 'Telegram / WhatsApp',
    requiredConnectors: ['Customer Channels', 'Product catalog'],
    defaultName: 'Shop Assistant',
    persona: 'Friendly shop assistant that helps customers choose products and asks before promising availability.',
    systemPrompt: 'Answer product questions from approved catalog sources, ask clarifying questions when the customer is unsure, capture contact details, and escalate unavailable or uncertain items to a human.',
    knowledgePlaceholder: 'Paste product catalog links, spreadsheet references, pricing notes, or availability rules here.',
    selectedToolIds: ['spreadsheet_read', 'spreadsheet_append'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'restaurant_orders',
    title: 'Restaurant Orders',
    category: 'Food service',
    icon: 'RO',
    outcome: 'Answers menu questions and confirms orders.',
    description: 'Ordering assistant for restaurants, cafes, and local kitchens.',
    setupTime: '5 min setup',
    channelLabel: 'Telegram',
    requiredConnectors: ['Telegram bot', 'Menu sheet'],
    defaultName: 'Restaurant Order Assistant',
    persona: 'Fast, friendly ordering assistant for a restaurant or cafe.',
    systemPrompt: 'Answer menu questions, check availability from connected sheets, confirm orders clearly, and escalate uncertain cases to a human.',
    knowledgePlaceholder: 'Paste menu notes, Google Sheet references, or daily specials source here.',
    selectedToolIds: ['spreadsheet_read', 'spreadsheet_append'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'dental_receptionist',
    title: 'Dental Receptionist',
    category: 'Healthcare admin',
    icon: 'DR',
    outcome: 'Answers clinic FAQs, collects appointment needs, and routes urgent cases.',
    description: 'Front-desk assistant for dental clinics that need safe intake, FAQ answers, and booking handoff.',
    setupTime: '6 min setup',
    channelLabel: 'Telegram / Email',
    requiredConnectors: ['Clinic FAQ', 'Calendar'],
    defaultName: 'Dental Receptionist',
    persona: 'Polite dental front-desk assistant that answers only approved clinic information and routes medical uncertainty to staff.',
    systemPrompt: 'Answer clinic FAQs from approved knowledge, collect preferred appointment times and contact details, avoid diagnosis or medical advice, and escalate pain, emergency, billing, or uncertain cases to the clinic team.',
    knowledgePlaceholder: 'Paste clinic hours, services, insurance notes, booking rules, and emergency routing instructions here.',
    selectedToolIds: ['calendar_write', 'gmail_send'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'real_estate_leads',
    title: 'Real Estate Leads',
    category: 'Sales',
    icon: 'RE',
    outcome: 'Captures requirements and books follow-up.',
    description: 'Lead intake assistant for brokers, agents, and property teams.',
    setupTime: '6 min setup',
    channelLabel: 'Telegram / Email',
    requiredConnectors: ['Lead channel', 'Calendar'],
    defaultName: 'Real Estate Lead Assistant',
    persona: 'Calm, concise lead qualification assistant for real estate inquiries.',
    systemPrompt: 'Ask for location, budget, timing, property type, and contact preference. Summarize qualified leads and schedule follow-up when calendar access is available.',
    knowledgePlaceholder: 'Paste listing sheet, neighborhood notes, or qualification rules here.',
    selectedToolIds: ['calendar_write', 'gmail_send', 'spreadsheet_append'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'support_faq',
    title: 'Support FAQ',
    category: 'Customer support',
    icon: 'SF',
    outcome: 'Answers common questions and escalates edge cases.',
    description: 'FAQ assistant for product, account, delivery, or policy questions.',
    setupTime: '4 min setup',
    channelLabel: 'Email / Chat',
    requiredConnectors: ['FAQ source', 'Support inbox'],
    defaultName: 'Support FAQ Assistant',
    persona: 'Clear support assistant that answers from approved knowledge only.',
    systemPrompt: 'Answer only from approved knowledge sources. Ask clarifying questions when needed and escalate unsupported or account-sensitive issues to a human.',
    knowledgePlaceholder: 'Paste help center links, policy docs, or faq:// references here.',
    selectedToolIds: ['web_search', 'http_request', 'gmail_send'],
    memoryEnabled: false,
    contextBudgetPreset: 'compact',
  },
  {
    id: 'appointment_booking',
    title: 'Appointment Booking',
    category: 'Scheduling',
    icon: 'AB',
    outcome: 'Finds a time, confirms, and writes calendar events.',
    description: 'Booking assistant for salons, clinics, services, and local businesses.',
    setupTime: '5 min setup',
    channelLabel: 'Telegram / Email',
    requiredConnectors: ['Calendar', 'Customer Channels'],
    defaultName: 'Appointment Booking Assistant',
    persona: 'Polite scheduling assistant that confirms details before booking.',
    systemPrompt: 'Collect service type, preferred date and time, customer contact details, and confirmation. Write calendar events only after details are clear.',
    knowledgePlaceholder: 'Add service list, booking rules, and availability source here.',
    selectedToolIds: ['calendar_write', 'gmail_send'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'spreadsheet_catalog',
    title: 'Spreadsheet Catalog Bot',
    category: 'Catalog',
    icon: 'SC',
    outcome: 'Looks up rows and answers from a live catalog.',
    description: 'Assistant for products, SKUs, menus, inventory, or internal catalogs.',
    setupTime: '3 min setup',
    channelLabel: 'Any channel',
    requiredConnectors: ['Spreadsheet'],
    defaultName: 'Catalog Assistant',
    persona: 'Accurate catalog assistant that prefers exact matches and asks when ambiguous.',
    systemPrompt: 'Use connected spreadsheets as the source of truth. Return exact row details when possible and ask for clarification when there are multiple matches.',
    knowledgePlaceholder: 'Add sheet://catalog or paste the spreadsheet reference here.',
    selectedToolIds: ['spreadsheet_read', 'spreadsheet_append'],
    memoryEnabled: false,
    contextBudgetPreset: 'compact',
  },
  {
    id: 'telegram_sales',
    title: 'Telegram Sales Bot',
    category: 'Sales',
    icon: 'TS',
    outcome: 'Responds to inbound leads and captures next action.',
    description: 'General Telegram sales assistant for SMB customer conversations.',
    setupTime: '5 min setup',
    channelLabel: 'Telegram',
    requiredConnectors: ['Telegram bot', 'Sales notes'],
    defaultName: 'Telegram Sales Assistant',
    persona: 'Direct, friendly sales assistant for Telegram customer conversations.',
    systemPrompt: 'Answer product questions, qualify intent, capture contact details, and escalate high-intent or uncertain cases to a human.',
    knowledgePlaceholder: 'Paste product pages, pricing notes, or sales sheet references here.',
    selectedToolIds: ['spreadsheet_read', 'web_search', 'gmail_send'],
    memoryEnabled: true,
    contextBudgetPreset: 'standard',
  },
  {
    id: 'github_triage',
    title: 'GitHub Triage',
    category: 'Engineering',
    icon: 'GT',
    outcome: 'Summarizes issues and routes work.',
    description: 'Engineering assistant for issue triage, release notes, and team summaries.',
    setupTime: '8 min setup',
    channelLabel: 'GitHub / Email',
    requiredConnectors: ['GitHub', 'Email'],
    defaultName: 'GitHub Triage Assistant',
    persona: 'Precise engineering triage assistant that keeps summaries factual and actionable.',
    systemPrompt: 'Summarize issues, extract blockers, identify owners when available, and avoid changing code unless explicitly approved.',
    knowledgePlaceholder: 'Paste repository, issue board, or release-note source references here.',
    selectedToolIds: ['http_request', 'gmail_send'],
    memoryEnabled: false,
    contextBudgetPreset: 'extended',
  },
];

export const DEFAULT_STUDIO_TEMPLATE = STUDIO_TEMPLATES[0]!;
export const PRIMARY_STUDIO_TEMPLATE_ORDER = [
  'shop_assistant',
  'restaurant_orders',
  'dental_receptionist',
  'support_faq',
] as const;
export const PRIMARY_STUDIO_TEMPLATE_IDS = new Set<string>(PRIMARY_STUDIO_TEMPLATE_ORDER);
export const CUSTOM_STUDIO_TEMPLATE: StudioTemplate = {
  id: 'custom_agent',
  title: 'Custom Business Agent',
  category: 'Blank',
  icon: '+',
  outcome: 'Build a private worker from your own instructions.',
  description: 'Start with a clean draft when none of the templates match the job.',
  setupTime: 'Custom setup',
  channelLabel: 'Choose later',
  requiredConnectors: ['Choose tools', 'Choose channel'],
  defaultName: '',
  persona: '',
  systemPrompt: '',
  knowledgePlaceholder: 'Add the trusted sources this Business Agent should use.',
  selectedToolIds: [],
  memoryEnabled: false,
  contextBudgetPreset: 'standard',
};
