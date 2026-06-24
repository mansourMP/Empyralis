// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

function buildAgent(overrides = {}) {
  return {
    id: 'dagent-seed',
    owner_workspace_id: 'ws-1',
    backing_install_id: 'ainstall-seed',
    name: 'Store Assistant',
    avatar: 'https://example.com/avatar.png',
    persona: 'Retail support specialist',
    system_prompt: 'Use the catalog and return policy.',
    deployment_state: 'draft',
    channels: {
      telegram: {
        enabled: true,
        endpoint_key: 'store-bot',
      },
    },
    knowledge_sources: [{ id: 'kb-1', uri: 'kb://catalog', label: 'kb://catalog' }],
    runtime_target: 'cloud',
    billing_plan: 'free',
    provider: 'openai',
    model: 'gpt-4o',
    metadata: {
      provider: 'openai',
      model: 'gpt-4o',
      memory_enabled: false,
      monthly_cost_cap_usd: 25,
      current_budget_cycle: {
        usage_month: '2026-04-01',
        current_burn_usd: 8,
        percent_used: 32,
        last_threshold_reached: 'none',
      },
    },
    created_at: '2026-04-13T10:00:00Z',
    updated_at: '2026-04-13T10:00:00Z',
    ...overrides,
  };
}

function buildProviderCatalog() {
  return {
    workspace_id: 'ws-1',
    summary: { provider_total: 4 },
    providers: [
      {
        id: 'openai',
        kind: 'provider',
        label: 'OpenAI',
        state: 'active',
        provider_scopes: ['sage_main', 'studio_safe'],
        default_model: 'gpt-4o',
        privacy_posture: 'Managed API with vendor-hosted processing.',
        jurisdiction: 'United States',
        residency: 'Provider-managed cloud regions.',
        capability_labels: ['Tools', 'Fast', 'Hosted API'],
        models: [
          {
            id: 'gpt-4o',
            label: 'GPT-4o',
            context_window_tokens: 128000,
            input_cost_per_1k_usd: 0.005,
            output_cost_per_1k_usd: 0.015,
            capability_labels: ['Balanced', 'Tools', 'Multimodal'],
          },
          {
            id: 'gpt-4o-mini',
            label: 'GPT-4o Mini',
            context_window_tokens: 128000,
            input_cost_per_1k_usd: 0.00015,
            output_cost_per_1k_usd: 0.0006,
            capability_labels: ['Low cost', 'Fast', 'Tools'],
          },
        ],
      },
      {
        id: 'anthropic',
        kind: 'provider',
        label: 'Anthropic',
        state: 'active',
        provider_scopes: ['sage_main', 'studio_safe'],
        default_model: 'claude-3-5-sonnet-20241022',
        privacy_posture: 'Managed API or local Claude subscription transport.',
        jurisdiction: 'United States',
        residency: 'Provider-managed cloud regions or local CLI session.',
        capability_labels: ['Reasoning', 'Long context', 'Tools'],
        models: [
          {
            id: 'claude-3-5-sonnet-20241022',
            label: 'Claude 3.5 Sonnet',
            context_window_tokens: 200000,
            input_cost_per_1k_usd: 0.003,
            output_cost_per_1k_usd: 0.015,
            capability_labels: ['Reasoning', 'Long context', 'Tools'],
          },
        ],
      },
      {
        id: 'deepseek',
        kind: 'provider',
        label: 'DeepSeek',
        state: 'configured',
        provider_scopes: ['sage_main', 'studio_safe'],
        default_model: 'deepseek-chat',
        privacy_posture: 'Managed third-party API.',
        jurisdiction: 'Third-party hosted service',
        residency: 'Provider-managed cloud regions.',
        capability_labels: ['Reasoning', 'Cost-efficient', 'Hosted API'],
        models: [
          {
            id: 'deepseek-chat',
            label: 'DeepSeek Chat',
            context_window_tokens: 128000,
            input_cost_per_1k_usd: 0,
            output_cost_per_1k_usd: 0,
            capability_labels: ['Low cost', 'Fast', 'Hosted API'],
          },
          {
            id: 'deepseek-reasoner',
            label: 'DeepSeek Reasoner',
            context_window_tokens: 128000,
            input_cost_per_1k_usd: 0,
            output_cost_per_1k_usd: 0,
            capability_labels: ['Reasoning', 'Low cost', 'Hosted API'],
          },
        ],
      },
      {
        id: 'ollama',
        kind: 'provider',
        label: 'Ollama',
        state: 'configured',
        provider_scopes: ['sage_main', 'studio_safe'],
        default_model: 'llama3.2',
        privacy_posture: 'Local or self-hosted inference on your own machine.',
        jurisdiction: 'Self-hosted / operator-controlled',
        residency: 'Local machine or self-hosted runtime.',
        capability_labels: ['Local', 'Self-hosted', 'Offline-capable'],
        models: [
          {
            id: 'llama3.2',
            label: 'Llama 3.2',
            context_window_tokens: 128000,
            input_cost_per_1k_usd: 0,
            output_cost_per_1k_usd: 0,
            capability_labels: ['Local', 'Self-hosted', 'Offline-capable'],
          },
        ],
      },
    ],
  };
}

function buildConversationRecord(overrides = {}) {
  return {
    session_id: 'sess-1',
    channel: 'telegram',
    last_message: 'Can I return this order?',
    last_message_at: '2026-04-13T12:05:00Z',
    customer: {
      id: 'customer-1',
      label: 'Customer One',
      type: 'customer',
    },
    latest_run_id: 'run-1',
    escalation_state: 'approval_requested',
    outcome: 'completed',
    ...overrides,
  };
}

function buildConversationList(agentId: string, items = [buildConversationRecord({ session_id: `sess-${agentId}` })]) {
  return {
    deployed_agent_id: agentId,
    items,
    offset: 0,
    limit: 50,
    has_more: false,
  };
}

function buildConversationDetail(agentId: string, sessionId = `sess-${agentId}`, overrides = {}) {
  return {
    deployed_agent_id: agentId,
    session_id: sessionId,
    channel: 'telegram',
    thread_id: 'thread-1',
    run_ids: ['run-1'],
    messages: [
      {
        id: 'msg-1',
        kind: 'message',
        direction: 'inbound',
        text: 'Can I return this order?',
        ts: '2026-04-13T12:00:00Z',
      },
      {
        id: 'msg-2',
        kind: 'message',
        direction: 'outbound',
        text: 'Let me check the return policy.',
        ts: '2026-04-13T12:00:05Z',
      },
    ],
    tool_calls: [
      {
        id: 'tool-1',
        kind: 'tool_call',
        tool_name: 'telegram_bot.send_message',
        summary: 'Connector action completed: telegram_bot.send_message.',
        ts: '2026-04-13T12:00:05Z',
      },
    ],
    approval_events: [
      {
        id: 'approval-1',
        kind: 'approval',
        summary: 'Approval requested for return exception.',
        ts: '2026-04-13T12:00:07Z',
      },
    ],
    escalation_events: [
      {
        id: 'escalation-1',
        kind: 'escalation',
        summary: 'Escalation triggered for manual review.',
        ts: '2026-04-13T12:00:08Z',
      },
    ],
    entries: [
      {
        id: 'msg-1',
        kind: 'message',
        direction: 'inbound',
        text: 'Can I return this order?',
        ts: '2026-04-13T12:00:00Z',
      },
      {
        id: 'msg-2',
        kind: 'message',
        direction: 'outbound',
        text: 'Let me check the return policy.',
        ts: '2026-04-13T12:00:05Z',
      },
      {
        id: 'tool-1',
        kind: 'tool_call',
        tool_name: 'telegram_bot.send_message',
        summary: 'Connector action completed: telegram_bot.send_message.',
        ts: '2026-04-13T12:00:05Z',
      },
      {
        id: 'approval-1',
        kind: 'approval',
        summary: 'Approval requested for return exception.',
        ts: '2026-04-13T12:00:07Z',
      },
      {
        id: 'escalation-1',
        kind: 'escalation',
        summary: 'Escalation triggered for manual review.',
        ts: '2026-04-13T12:00:08Z',
      },
    ],
    outcome: 'completed',
    customer: {
      id: 'customer-1',
      label: 'Customer One',
      type: 'customer',
    },
    ...overrides,
  };
}

function buildAnalytics(agentId: string, overrides = {}) {
  return {
    deployed_agent_id: agentId,
    active_users_last_30d: 12,
    message_volume: {
      day: 3,
      week: 18,
      month: 58,
      latest_message_at: '2026-04-13T12:05:00Z',
    },
    escalation: {
      total_sessions: 3,
      escalated_sessions: 1,
      rate: 0.3333,
      rate_percent: 33.33,
    },
    outcomes: {
      counts: {
        completed: 2,
        open: 1,
      },
      top_outcome: 'completed',
    },
    cost_burn: {
      usage_month: '2026-04-01',
      current_burn_usd: 8,
      cap_usd: 25,
      percent_used: 32,
      last_threshold_reached: 'none',
    },
    ...overrides,
  };
}

function buildAdminDashboard(agentId: string) {
  return {
    deployed_agent_id: agentId,
    total_users: 3,
    messages_today: 8,
    messages_this_calendar_month: 58,
    orders_today: 1,
    revenue_today_usd: 8,
    users_at_limit_today: 0,
    upgrade_clicks_this_month: 2,
    common_questions: [
      { question: 'Can I return this order?', count: 4 },
    ],
    customer_entry: {
      entry_url: 'https://example.com/customer-entry',
      cta_label: 'Message agent',
      qr_target: 'telegram',
      telegram_deep_link: 'https://t.me/store_bot',
    },
    specialist_profile: {
      knowledge: { title: 'Knowledge', mode: 'Catalog', summary: 'Return policy and catalog are attached.' },
      live_data: { title: 'Live data', mode: 'Connected', summary: 'Inventory spreadsheet connected.' },
      memory: { title: 'Memory', mode: 'Bounded', summary: 'Customer continuity is compacted.' },
      actions: { title: 'Actions', mode: 'Governed', summary: 'Approval required for risky changes.' },
      channel: { title: 'Channel', mode: 'Telegram', summary: 'Gateway-owned personal channel.' },
    },
    user_rows: [
      {
        external_user_id: 'customer-1',
        last_message_at: '2026-04-13T12:05:00Z',
        total_message_count: 5,
        memory_entry_count: 1,
        last_5_messages: [
          {
            id: 'dash-msg-1',
            role: 'user',
            content: 'Can I return this order?',
            created_at: '2026-04-13T12:00:00Z',
          },
        ],
      },
    ],
    limit: 50,
    has_more: false,
  };
}

function buildBusinessInsights(agentId: string) {
  return {
    workspace_id: 'ws-1',
    deployed_agent_id: agentId,
    count: 1,
    items: [
      {
        id: `binsight-${agentId}-price`,
        workspace_id: 'ws-1',
        deployed_agent_id: agentId,
        pattern_key: 'pricing.price_match_or_discount_pressure',
        insight_type: 'pricing_intelligence',
        title: 'Price-match or discount pressure detected',
        summary: 'Customers are asking for discounts or cheaper alternatives.',
        recommendation: 'Review whether the agent should explain the price policy more clearly. Do not change prices automatically.',
        sensitivity: 'orange',
        status: 'candidate',
        channel_key: 'telegram',
        event_count: 7,
        confidence: 0.82,
        redacted_examples: ['Customer asked whether the shop can match a competitor price.'],
        updated_at: '2026-04-13T12:07:00Z',
      },
    ],
  };
}

test.describe('deployed agents surface', () => {
  test('route registers in workstation navigation', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-1/chat');

    const expandPanel = page.getByRole('button', { name: /expand left panel/i });
    if (await expandPanel.isVisible().catch(() => false)) {
      await expandPanel.click();
    }
    await page.locator('.workstation-shell-panel__nav-row').filter({ hasText: /^Sage$/ }).click();
    await page.locator('.workstation-shell-panel__projects-toggle').click();
    const agentStudioProject = page.locator('.workstation-shell-panel__project-row').filter({ hasText: /Agent Studio/ });
    await expect(agentStudioProject).toBeVisible();
    await expect(agentStudioProject).toHaveAttribute('href', '/w/ws-1/studio');
    await agentStudioProject.click();
    await expect(page).toHaveURL(/\/w\/ws-1\/studio$/);
    await expect(page.locator('[data-workstation-surface="deployed-agents"]')).toBeVisible();
  });

  test('wizard, deploy, pause, inbox, and transcript flow render through the deployed-agent APIs', async ({ page }) => {
    test.setTimeout(120_000);

    await loginAsOwner(page);
    const agents = [
      buildAgent(),
      buildAgent({
        id: 'dagent-billing',
        backing_install_id: 'ainstall-billing',
        name: 'Billing Guide',
        persona: 'Billing support specialist',
        system_prompt: 'Explain invoices and payment steps.',
        channels: {},
        knowledge_sources: [],
        provider: 'openai',
        model: 'gpt-4o-mini',
        metadata: {
          provider: 'openai',
          model: 'gpt-4o-mini',
          memory_enabled: false,
        },
      }),
    ];
    const providerCatalog = buildProviderCatalog();
    const seedConversations = [
      buildConversationRecord({
        session_id: `sess-${agents[0].id}-telegram-open`,
        channel: 'telegram',
        last_message: 'Can I return this order?',
        last_message_at: '2026-04-13T12:05:00Z',
        customer: {
          id: 'customer-1',
          label: 'Customer One',
          type: 'customer',
        },
        latest_run_id: 'run-1',
        escalation_state: 'approval_requested',
        outcome: 'open',
      }),
      buildConversationRecord({
        session_id: `sess-${agents[0].id}-whatsapp-open`,
        channel: 'whatsapp',
        last_message: 'I need a human for shipping support.',
        last_message_at: '2026-04-13T12:09:00Z',
        customer: {
          id: 'customer-2',
          label: 'Customer Two',
          type: 'customer',
        },
        latest_run_id: 'run-2',
        escalation_state: 'escalated',
        outcome: 'open',
      }),
      buildConversationRecord({
        session_id: `sess-${agents[0].id}-telegram-complete`,
        channel: 'telegram',
        last_message: 'Thanks, that solved it.',
        last_message_at: '2026-04-13T11:55:00Z',
        customer: {
          id: 'customer-3',
          label: 'Customer Three',
          type: 'customer',
        },
        latest_run_id: 'run-3',
        escalation_state: 'clear',
        outcome: 'completed',
      }),
    ];
    const analyticsByAgent = new Map([
      [
        agents[0].id,
        buildAnalytics(agents[0].id, {
          escalation: {
            total_sessions: 3,
            escalated_sessions: 2,
            rate: 0.6667,
            rate_percent: 66.67,
          },
          outcomes: {
            counts: {
              open: 2,
              completed: 1,
            },
            top_outcome: 'open',
          },
        }),
      ],
    ]);
    const businessInsightsByAgent = new Map([[agents[0].id, buildBusinessInsights(agents[0].id)]]);
    const conversationsByAgent = new Map([[agents[0].id, buildConversationList(agents[0].id, seedConversations)]]);
    const testTurnBodies = [];
    const externalChatBodies = [];
    let connectedExternalAgents = [
      {
        id: 'extagent-openclaw',
        surface_kind: 'connected_external_agent',
        studio_object_type: 'connected_external_agent',
        workspace_id: 'ws-1',
        tenant_id: 'tenant-1',
        name: 'OpenClaw Gateway',
        provider_kind: 'openclaw',
        status: 'active',
        enabled: true,
        connection_state: 'verified',
        trust_state: 'verified',
        endpoint_refs: {
          manifest_url: 'https://example.com/openclaw/manifest.json',
          chat_url: 'https://example.com/openclaw/chat',
          events_url: 'https://example.com/openclaw/events',
          sub_agents_url: 'https://example.com/openclaw/sub-agents',
        },
        capability_manifest: {
          capabilities: ['chat', 'logs', 'events', 'sub_agents'],
          chat: true,
          events: true,
          logs: true,
          sub_agents: true,
        },
        protocols: [{ kind: 'custom_http', version: '1' }],
        local_connector: { required: false },
        surface_sections: [
          {
            id: 'run_history',
            title: 'Run History',
            description: 'External workflow events from OpenClaw.',
            category: 'activity',
            priority: 20,
            icon: 'workflows',
            capability_required: 'events',
            data_endpoint_ref: 'events_url',
            actions_endpoint_ref: null,
            display_kind: 'timeline',
          },
          {
            id: 'sub_agents',
            title: 'Sub-agents',
            description: 'Workers managed by the external gateway.',
            category: 'resources',
            priority: 10,
            icon: 'sub_agents',
            capability_required: 'sub_agents',
            data_endpoint_ref: 'sub_agents_url',
            actions_endpoint_ref: 'actions_url',
            display_kind: 'cards',
            interaction: 'read_only',
          },
        ],
        object_types: ['external_agent_event', 'external_agent_sub_agent'],
        manifest: {
          id: 'openclaw-gateway',
          capabilities: ['chat', 'logs', 'events', 'sub_agents'],
        },
        last_manifest_refresh_at: '2026-04-13T12:00:00Z',
      },
      {
        id: 'extagent-local',
        surface_kind: 'connected_external_agent',
        studio_object_type: 'connected_external_agent',
        workspace_id: 'ws-1',
        tenant_id: 'tenant-1',
        name: 'Laptop Local Agent',
        provider_kind: 'custom',
        status: 'active',
        enabled: true,
        connection_state: 'verified',
        trust_state: 'verified',
        endpoint_refs: {
          manifest_url: 'https://example.com/local/manifest.json',
          chat_url: 'https://example.com/local/chat',
        },
        capability_manifest: {
          capabilities: ['chat'],
          chat: true,
        },
        protocols: [{ kind: 'custom_http', version: '1' }],
        local_connector: {
          required: true,
          mode: 'agent_computer_proxy',
          binding_state: 'bound',
          bound: true,
          proxy_available: false,
          agent_computer_id: 'computer-macbook',
          agent_computer_label: 'Mansur MacBook',
          agent_computer_capability: 'external_agent_proxy',
          attachment_kind: 'local_companion',
          binding_message: 'Agent Computer is bound, but private proxying is not enabled yet.',
        },
        surface_sections: [],
        object_types: [],
        manifest: {
          id: 'laptop-local-agent',
          capabilities: ['chat'],
        },
        last_manifest_refresh_at: '2026-04-13T12:00:00Z',
      },
    ];
    const runtimeAttachmentsPayload = {
      attachments: [
        {
          attachment_id: 'computer-macbook',
          attachment_kind: 'self_hosted_business_node',
          runtime_profile_id: 'runtime-macbook',
          runtime_node_id: 'node-macbook',
          label: 'Mansur MacBook',
          online: true,
          healthy: true,
          owner_approved: true,
          status: 'online',
          self_hosted_node_status: 'online',
          node_kind: 'macbook',
          heartbeat_at: '2026-04-13T12:10:00Z',
          capabilities: ['browser', 'files'],
        },
      ],
    };
    const transcriptsByAgent = new Map([
      [
        `${agents[0].id}:sess-${agents[0].id}-telegram-open`,
        buildConversationDetail(agents[0].id, `sess-${agents[0].id}-telegram-open`, {
          outcome: 'open',
          customer: {
            id: 'customer-1',
            label: 'Customer One',
            type: 'customer',
          },
        }),
      ],
      [
        `${agents[0].id}:sess-${agents[0].id}-whatsapp-open`,
        buildConversationDetail(agents[0].id, `sess-${agents[0].id}-whatsapp-open`, {
          channel: 'whatsapp',
          run_ids: ['run-2'],
          customer: {
            id: 'customer-2',
            label: 'Customer Two',
            type: 'customer',
          },
          messages: [
            {
              id: 'msg-3',
              kind: 'message',
              direction: 'inbound',
              text: 'I need a human for shipping support.',
              ts: '2026-04-13T12:08:00Z',
            },
            {
              id: 'msg-4',
              kind: 'message',
              direction: 'outbound',
              text: 'Escalating this to human support now.',
              ts: '2026-04-13T12:08:05Z',
            },
          ],
          tool_calls: [
            {
              id: 'tool-2',
              kind: 'tool_call',
              tool_name: 'whatsapp.send_message',
              summary: 'Connector action completed: whatsapp.send_message.',
              ts: '2026-04-13T12:08:05Z',
            },
          ],
          approval_events: [],
          escalation_events: [
            {
              id: 'escalation-2',
              kind: 'escalation',
              summary: 'Escalated to human shipping support.',
              ts: '2026-04-13T12:08:06Z',
            },
          ],
          entries: [
            {
              id: 'msg-3',
              kind: 'message',
              direction: 'inbound',
              text: 'I need a human for shipping support.',
              ts: '2026-04-13T12:08:00Z',
            },
            {
              id: 'msg-4',
              kind: 'message',
              direction: 'outbound',
              text: 'Escalating this to human support now.',
              ts: '2026-04-13T12:08:05Z',
            },
            {
              id: 'tool-2',
              kind: 'tool_call',
              tool_name: 'whatsapp.send_message',
              summary: 'Connector action completed: whatsapp.send_message.',
              ts: '2026-04-13T12:08:05Z',
            },
            {
              id: 'escalation-2',
              kind: 'escalation',
              summary: 'Escalated to human shipping support.',
              ts: '2026-04-13T12:08:06Z',
            },
          ],
          outcome: 'open',
        }),
      ],
      [
        `${agents[0].id}:sess-${agents[0].id}-telegram-complete`,
        buildConversationDetail(agents[0].id, `sess-${agents[0].id}-telegram-complete`, {
          run_ids: ['run-3'],
          customer: {
            id: 'customer-3',
            label: 'Customer Three',
            type: 'customer',
          },
          messages: [
            {
              id: 'msg-5',
              kind: 'message',
              direction: 'inbound',
              text: 'Thanks, that solved it.',
              ts: '2026-04-13T11:54:00Z',
            },
            {
              id: 'msg-6',
              kind: 'message',
              direction: 'outbound',
              text: 'Glad that resolved the issue.',
              ts: '2026-04-13T11:54:03Z',
            },
          ],
          tool_calls: [],
          approval_events: [],
          escalation_events: [],
          entries: [
            {
              id: 'msg-5',
              kind: 'message',
              direction: 'inbound',
              text: 'Thanks, that solved it.',
              ts: '2026-04-13T11:54:00Z',
            },
            {
              id: 'msg-6',
              kind: 'message',
              direction: 'outbound',
              text: 'Glad that resolved the issue.',
              ts: '2026-04-13T11:54:03Z',
            },
          ],
          outcome: 'completed',
        }),
      ],
    ]);

    await page.route('**/providers/catalog**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(providerCatalog),
      });
    });

    await page.route('**/agent-registry/runtime-attachments**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(runtimeAttachmentsPayload),
      });
    });

    await page.route('**/api/studio/agent-surfaces**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          workspace_id: 'ws-1',
          tenant_id: 'tenant-1',
          native_studio_agents: agents.map((agent) => ({
            id: agent.id,
            surface_kind: 'native_studio_agent',
            name: agent.name,
            status: agent.deployment_state,
            record: agent,
          })),
          connected_external_agents: connectedExternalAgents,
          agent_computers: [
            {
              id: 'computer-macbook',
              surface_kind: 'agent_computer',
              name: 'Mansur MacBook',
              status: 'online',
              record: runtimeAttachmentsPayload.attachments[0],
            },
          ],
          items: [],
        }),
      });
    });

    await page.route('**/api/studio/external-agents**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const method = request.method().toUpperCase();
      const segments = path.split('/').filter(Boolean);

      if (segments.length === 3 && segments[0] === 'api' && segments[1] === 'studio' && segments[2] === 'external-agents' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ items: connectedExternalAgents }),
        });
        return;
      }

      if (segments.length >= 4 && segments[0] === 'api' && segments[1] === 'studio' && segments[2] === 'external-agents') {
        const externalAgentId = segments[3];
        const externalAgent = connectedExternalAgents.find((item) => item.id === externalAgentId);

        if (!externalAgent) {
          await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Not found.' }),
          });
          return;
        }

        if (segments.length === 4 && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(externalAgent),
          });
          return;
        }

        if (segments.length === 5 && segments[4] === 'chat-turn' && method === 'POST') {
          const body = request.postDataJSON();
          externalChatBodies.push(body);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              reply: `OpenClaw answered privately: ${body.message}`,
              run_details: {
                surface_kind: 'connected_external_agent',
                endpoint: 'chat_url',
              },
            }),
          });
          return;
        }

        if (segments.length === 6 && segments[4] === 'sections' && segments[5] === 'run_history' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              external_agent_id: externalAgentId,
              section: {
                id: 'run_history',
                title: 'Run History',
                display_kind: 'timeline',
              },
              display_kind: 'timeline',
              items: [
                {
                  id: 'extagent-openclaw:external_agent_event:evt-1',
                  external_id: 'evt-1',
                  object_type: 'external_agent_event',
                  ownership: 'external',
                  title: 'Workflow finished',
                  status: 'ok',
                  summary: 'OpenClaw completed a private owner workflow.',
                },
              ],
            }),
          });
          return;
        }

        if (segments.length === 6 && segments[4] === 'sections' && segments[5] === 'sub_agents' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              external_agent_id: externalAgentId,
              section: {
                id: 'sub_agents',
                title: 'Sub-agents',
                display_kind: 'cards',
                interaction: 'read_only',
              },
              display_kind: 'cards',
              items: [
                {
                  id: 'extagent-openclaw:external_agent_sub_agent:researcher',
                  external_id: 'researcher',
                  object_type: 'external_agent_sub_agent',
                  ownership: 'external',
                  title: 'Research worker',
                  status: 'ready',
                  summary: 'Handles research inside OpenClaw.',
                  raw_redacted: {
                    provider: 'openclaw',
                    runtime: 'external',
                  },
                },
              ],
            }),
          });
          return;
        }

        if (segments.length === 5 && segments[4] === 'refresh-manifest' && method === 'POST') {
          connectedExternalAgents = connectedExternalAgents.map((item) => item.id === externalAgentId ? {
            ...item,
            connection_state: 'verified',
            trust_state: 'verified',
            last_manifest_refresh_at: '2026-04-13T12:12:00Z',
          } : item);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(connectedExternalAgents.find((item) => item.id === externalAgentId)),
          });
          return;
        }

        if (segments.length === 5 && segments[4] === 'disconnect' && method === 'POST') {
          connectedExternalAgents = connectedExternalAgents.map((item) => item.id === externalAgentId ? {
            ...item,
            connection_state: 'revoked',
            trust_state: 'revoked',
            enabled: false,
          } : item);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(connectedExternalAgents.find((item) => item.id === externalAgentId)),
          });
          return;
        }
      }

      await route.fallback();
    });

    await page.route('**/api/deployed-agents**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const method = request.method().toUpperCase();
      const segments = path.split('/').filter(Boolean);

      if (segments.length === 2 && segments[0] === 'api' && segments[1] === 'deployed-agents' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ items: agents }),
        });
        return;
      }

      if (segments.length === 3 && segments[0] === 'api' && segments[1] === 'deployed-agents' && segments[2] === 'analytics' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ items: Array.from(analyticsByAgent.values()) }),
        });
        return;
      }

      if (segments.length === 3 && segments[0] === 'api' && segments[1] === 'deployed-agents' && segments[2] === 'telegram-readiness' && method === 'GET') {
        const deployedAgentId = url.searchParams.get('deployed_agent_id') || '';
        const agent = agents.find((item) => item.id === deployedAgentId) ?? null;
        const telegramConfig = agent?.channels?.telegram ?? {};
        const connectorId = telegramConfig.connector_id ?? '';
        const endpointKey = telegramConfig.endpoint_key ?? '';
        const readyForLive = Boolean(connectorId && endpointKey);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ready_for_live: readyForLive,
            status: readyForLive ? 'ready' : 'draft',
            next_action: readyForLive
              ? 'Telegram connector, inbound ownership, and webhook contract are ready.'
              : 'Select a Telegram connector before Studio can mark this specialist ready.',
            blockers: readyForLive
              ? []
              : [
                  {
                    code: 'telegram_connector_required',
                    message: 'Choose a Telegram connector before this Studio specialist can go live.',
                    guidance: 'Bind one workspace Telegram bot in the Channels step.',
                    severity: 'warning',
                  },
                ],
            warnings: [],
            connectors: [
              {
                id: 'tg-connector-1',
                label: 'Returns Bot',
                endpoint_key: 'returns-bot',
                bot_username: 'returns_concierge_bot',
                webhook_path: '/hooks/telegram/returns-bot',
                webhook_url: 'https://example.com/hooks/telegram/returns-bot',
                profile_status: 'healthy',
                profile_issue: null,
                last_error: null,
                last_error_at: null,
              },
            ],
            configured_binding: readyForLive
              ? {
                  connector_id: connectorId,
                  endpoint_key: endpointKey,
                  is_inbound_owner: true,
                }
              : {},
            webhook: {
              status: readyForLive ? 'ready' : 'checking',
              path_template: '/hooks/telegram/{endpoint_key}',
            },
            autopilot: {
              enabled: true,
              delivery_mode: 'webhook',
            },
          }),
        });
        return;
      }

      if (segments.length === 2 && segments[0] === 'api' && segments[1] === 'deployed-agents' && method === 'POST') {
        const body = request.postDataJSON();
        const created = buildAgent({
          id: 'dagent-new',
          backing_install_id: 'ainstall-new',
          name: body.name,
          avatar: body.avatar,
          persona: body.persona,
          system_prompt: body.system_prompt,
          channels: body.channels,
          knowledge_sources: body.knowledge_sources,
          runtime_target: body.runtime_target,
          billing_plan: body.billing_plan,
          provider: body.provider ?? 'openai',
          model: body.model ?? 'gpt-4o',
          config: body.config ?? {},
          metadata: body.metadata ?? {},
          deployment_state: 'draft',
          updated_at: '2026-04-13T12:00:00Z',
        });
        created.metadata = {
          ...(created.metadata ?? {}),
          provider: created.provider,
          model: created.model,
        };
        agents.unshift(created);
        analyticsByAgent.set(
          created.id,
          buildAnalytics(created.id, {
            active_users_last_30d: 0,
            message_volume: {
              day: 0,
              week: 0,
              month: 0,
              latest_message_at: null,
            },
            escalation: {
              total_sessions: 0,
              escalated_sessions: 0,
              rate: 0,
              rate_percent: 0,
            },
            outcomes: {
              counts: {},
              top_outcome: 'open',
            },
            cost_burn: {
              usage_month: '2026-04-01',
              current_burn_usd: 0,
              cap_usd: body.metadata?.monthly_cost_cap_usd ?? null,
              percent_used: 0,
              last_threshold_reached: 'none',
            },
          }),
        );
        conversationsByAgent.set(created.id, buildConversationList(created.id));
        transcriptsByAgent.set(`${created.id}:sess-${created.id}`, buildConversationDetail(created.id));
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(created),
        });
        return;
      }

      if (segments.length >= 3 && segments[0] === 'api' && segments[1] === 'deployed-agents') {
        const deployedAgentId = segments[2];
        const agentIndex = agents.findIndex((item) => item.id === deployedAgentId);
        const agent = agentIndex >= 0 ? agents[agentIndex] : null;

        if (!agent) {
          await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Not found.' }),
          });
          return;
        }

        if (segments.length === 3 && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(agent),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'analytics' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(analyticsByAgent.get(deployedAgentId) ?? buildAnalytics(deployedAgentId)),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'admin-dashboard' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(buildAdminDashboard(deployedAgentId)),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'business-insights' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(businessInsightsByAgent.get(deployedAgentId) ?? { items: [], count: 0 }),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'test-turn' && method === 'POST') {
          const body = request.postDataJSON();
          testTurnBodies.push(body);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              reply: `I can help with returns privately. You said: ${body.message}`,
              policy_decisions: [],
              tools_considered: [],
              tools_used: [],
              memory_context: { enabled: false },
              approval_required: false,
              audit_events: [],
              trace_id: 'trace-owner-chat-1',
              transparency_events: [
                { surface: 'studio_test', kind: 'owner_chat' },
              ],
              model_provider: 'openai',
              model: 'gpt-4o',
              usage: { input_tokens: 12, output_tokens: 8 },
              prompt_context: {
                recent_messages_included: body.recent_messages?.length ?? 0,
                persona_applied: true,
                stored_instructions_applied: true,
                context_truncated: false,
              },
            }),
          });
          return;
        }

        if (segments.length === 6 && segments[3] === 'business-insights' && method === 'POST') {
          const insightId = segments[4];
          const action = segments[5];
          const payload = businessInsightsByAgent.get(deployedAgentId) ?? buildBusinessInsights(deployedAgentId);
          const nextItems = (payload.items ?? []).map((item) => {
            if (item.id !== insightId) {
              return item;
            }
            return {
              ...item,
              status: action === 'apply' ? 'applied' : action === 'approve' ? 'approved' : action === 'dismiss' ? 'dismissed' : 'archived',
              updated_at: '2026-04-13T12:08:00Z',
            };
          });
          const nextPayload = {
            ...payload,
            items: nextItems,
          };
          businessInsightsByAgent.set(deployedAgentId, nextPayload);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ insight: nextItems.find((item) => item.id === insightId) ?? null }),
          });
          return;
        }

        if (segments.length === 3 && method === 'PATCH') {
          const body = request.postDataJSON();
          const updated = {
            ...agent,
            ...body,
            system_prompt: body.system_prompt ?? agent.system_prompt,
            knowledge_sources: body.knowledge_sources ?? agent.knowledge_sources,
            deployment_state: body.deployment_state ?? agent.deployment_state,
            provider: body.provider ?? agent.provider,
            model: body.model ?? agent.model,
            config: body.config ?? agent.config,
            metadata: {
              ...(agent.metadata ?? {}),
              ...(body.metadata ?? {}),
              provider: body.provider ?? agent.provider,
              model: body.model ?? agent.model,
            },
            updated_at: '2026-04-13T12:02:00Z',
          };
          agents[agentIndex] = updated;
          analyticsByAgent.set(
            deployedAgentId,
            buildAnalytics(deployedAgentId, {
              cost_burn: {
                ...(analyticsByAgent.get(deployedAgentId)?.cost_burn ?? {}),
                cap_usd: body.metadata?.monthly_cost_cap_usd ?? analyticsByAgent.get(deployedAgentId)?.cost_burn?.cap_usd ?? 25,
              },
            }),
          );
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(updated),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'deploy' && method === 'POST') {
          const updated = {
            ...agent,
            deployment_state: 'live',
            updated_at: '2026-04-13T12:03:00Z',
          };
          agents[agentIndex] = updated;
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(updated),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'pause' && method === 'POST') {
          const updated = {
            ...agent,
            deployment_state: 'paused',
            updated_at: '2026-04-13T12:04:00Z',
          };
          agents[agentIndex] = updated;
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(updated),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'conversations' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(conversationsByAgent.get(deployedAgentId) ?? buildConversationList(deployedAgentId)),
          });
          return;
        }

        if (segments.length === 5 && segments[3] === 'conversations' && method === 'GET') {
          const sessionId = segments[4];
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(
              transcriptsByAgent.get(`${deployedAgentId}:${sessionId}`) ?? buildConversationDetail(deployedAgentId),
            ),
          });
          return;
        }

        if (segments.length === 4 && segments[3] === 'memory' && method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ items: [], limit: 50, offset: 0, has_more: false }),
          });
          return;
        }

        if (segments.length === 6 && segments[3] === 'external-users' && segments[5] === 'delete' && method === 'POST') {
          const externalUserId = segments[4];
          const existing = conversationsByAgent.get(deployedAgentId) ?? buildConversationList(deployedAgentId);
          const nextItems = (existing.items ?? []).filter((item) => item.customer?.id !== externalUserId);
          conversationsByAgent.set(deployedAgentId, {
            ...existing,
            items: nextItems,
          });
          for (const key of Array.from(transcriptsByAgent.keys())) {
            if (!key.startsWith(`${deployedAgentId}:`)) {
              continue;
            }
            const transcript = transcriptsByAgent.get(key);
            if (transcript?.customer?.id === externalUserId) {
              transcriptsByAgent.delete(key);
            }
          }
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              deployed_agent_id: deployedAgentId,
              channel: 'telegram',
              external_user_id: externalUserId,
              deleted_counts: {
                deleted_channel_event_count: 4,
                deleted_memory_count: 1,
                deleted_daily_usage_count: 2,
                deleted_acquisition_touch_count: 1,
              },
              request: { id: 'privreq_1', status: 'completed' },
              audit: { id: 'privaudit_1' },
            }),
          });
          return;
        }
      }

      await route.fallback();
    });

    await page.goto('/w/ws-1/studio');

    const surface = page.locator('[data-workstation-surface="deployed-agents"]');
    const studioRoster = page.locator('.workstation-shell-panel__build-list');
    await expect(surface).toBeVisible();
    await expect(studioRoster).toContainText(/store assistant/i);
    await expect(studioRoster).toContainText(/openclaw gateway/i);
    await expect(studioRoster).toContainText(/mansur macbook/i);
    await studioRoster.getByRole('button', { name: /store assistant/i }).click();
    await expect(surface).toContainText(/store assistant/i);
    await expect(surface).toContainText(/messages/i);
    await expect(surface).toContainText(/open/i);
    await expect(surface).toContainText(/traffic and cost/i);
    await expect(surface).toContainText(/\$8\.00/);
    await expect(surface).toContainText(/workers sage can run/i);
    const detailNav = page.locator('.workstation-shell-panel__agent-detail-nav');
    await expect(detailNav.getByRole('link', { name: /^overview$/i })).toBeVisible();
    await expect(detailNav.getByRole('link', { name: /^chat$/i })).toBeVisible();
    await page.goto('/w/ws-1/studio?externalAgent=extagent-openclaw');
    await expect(surface).toContainText(/connected agent/i);
    await expect(surface).toContainText(/connected agents start private/i);
    await expect(surface).not.toContainText(/go live/i);
    await detailNav.getByRole('link', { name: /^chat$/i }).click();
    await expect(surface).toContainText(/private external-agent chat/i);
    await page.getByPlaceholder(/message this connected agent privately/i).fill('Hello external agent');
    await page.getByRole('button', { name: /^send$/i }).click();
    await expect(surface).toContainText(/OpenClaw answered privately/i);
    expect(externalChatBodies).toHaveLength(1);
    expect(externalChatBodies[0].workspace_id).toBe('ws-1');
    await page.getByRole('button', { name: /manage connection/i }).click();
    await page.getByRole('button', { name: /^results$/i }).click();
    await expect(surface).toContainText(/sub-agents/i);
    await expect(surface).toContainText(/actions are declared but not enabled/i);
    await expect(surface).toContainText(/run history/i);
    const loadExternalSection = page.getByRole('button', { name: /^load$/i });
    if (await loadExternalSection.count() > 0) {
      await loadExternalSection.first().click();
    }
    await expect(surface).toContainText(/workflow finished/i);
    await expect(surface).toContainText(/external-owned/i);
    await expect(surface).toContainText(/external agent event/i);
    await expect(surface).not.toContainText(/add source/i);
    await page.getByRole('button', { name: /^close$/i }).click();
    await page.goto('/w/ws-1/studio?externalAgent=extagent-local');
    await expect(surface).toContainText(/agent computer/i);
    await expect(surface).toContainText(/mansur macbook/i);
    await expect(surface).toContainText(/private proxy not enabled yet/i);
    await expect(surface).toContainText(/localhost and private-network endpoints stay blocked/i);
    await expect(surface).not.toContainText(/go live/i);
    await page.goto('/w/ws-1/studio?agentComputer=computer-macbook');
    await expect(surface).toContainText(/runtime resource/i);
    await expect(surface).toContainText(/gateway \+ supervisor/i);
    await expect(detailNav.getByRole('link', { name: /^chat$/i })).toHaveCount(0);
    await page.goto('/w/ws-1/studio?agent=dagent-seed');
    await detailNav.getByRole('link', { name: /^chat$/i }).click();
    const chatPanel = page.locator('.studio-panel--chat');
    await expect(chatPanel.getByPlaceholder(/message this agent privately/i)).toBeVisible();
    await expect(chatPanel).not.toContainText(/private workspace chat · no customer send/i);
    await expect(chatPanel).not.toContainText(/telegram bot/i);
    await expect(chatPanel).not.toContainText(/whatsapp business/i);
    await page.getByPlaceholder(/message this agent privately/i).fill('Can you explain the return policy?');
    await page.getByLabel(/send private workspace chat message/i).click();
    await expect(chatPanel).toContainText(/I can help with returns privately/i);
    await expect(chatPanel).toContainText(/Run details/i);
    expect(testTurnBodies).toHaveLength(1);
    expect(testTurnBodies[0].channel).toBe('test');
    await detailNav.getByRole('link', { name: /^model$/i }).click();
    await detailNav.getByRole('link', { name: /^chat$/i }).click();
    await expect(chatPanel).toContainText(/I can help with returns privately/i);
    await detailNav.getByRole('link', { name: /^model$/i }).click();
    await expect(surface).toContainText(/recommended/i);
    await expect(surface).toContainText(/bring your own key/i);
    await expect(surface).toContainText(/developer\/local/i);
    await expect(surface).toContainText(/workspace api key/i);
    await detailNav.getByRole('link', { name: /results/i }).click();
    await expect(surface).toContainText(/owner intelligence/i);
    await expect(surface).toContainText(/price-match or discount pressure detected/i);
    await page.getByRole('button', { name: /^approve$/i }).click();
    await expect(surface).toContainText(/orange · approved/i);
    await page.goto('/w/ws-1/inbox?agent=dagent-seed');
    const inboxSurface = page.locator('[data-workstation-surface="deployed-agents"]');
    await expect(inboxSurface).toContainText(/business agent inbox/i);
    await expect(inboxSurface).toContainText(/live conversation inbox/i);
    await expect(inboxSurface).toContainText(/showing 3 of 3 customer sessions/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).toContainText(/customer one/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).toContainText(/customer two/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).toContainText(/customer three/i);
    await expect(page.locator('[data-deployed-agent-transcript="detail"]')).toContainText(/telegram_bot\.send_message/i);
    await expect(page.locator('[data-deployed-agent-transcript="detail"]')).toContainText(/approval requested/i);
    await expect(page.locator('[data-deployed-agent-transcript="detail"]')).toContainText(/escalation triggered/i);
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: /delete customer data/i }).click();
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).not.toContainText(/customer one/i);
    await expect(inboxSurface).toContainText(/showing 2 of 2 customer sessions/i);
    await page.getByLabel(/channel filter/i).selectOption('whatsapp');
    await expect(inboxSurface).toContainText(/showing 1 of 2 customer sessions/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).toContainText(/customer two/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).not.toContainText(/customer one/i);
    await page.getByLabel(/escalation filter/i).selectOption('escalated');
    await page.getByLabel(/outcome filter/i).selectOption('open');
    await expect(page.locator('[data-deployed-agent-transcript="detail"]')).toContainText(/whatsapp\.send_message/i);
    await expect(page.locator('[data-deployed-agent-transcript="detail"]')).toContainText(/human shipping support/i);
    await page.getByRole('button', { name: /clear filters/i }).click();
    await expect(inboxSurface).toContainText(/showing 2 of 2 customer sessions/i);
    await expect(page.locator('[data-deployed-agent-conversations="list"]')).not.toContainText(/customer one/i);

    await page.goto('/w/ws-1/studio');
    await page.getByRole('button', { name: /new agent/i }).click();
    const createAgentDialog = page.getByRole('dialog');
    await expect(createAgentDialog).toContainText(/create your agent/i);
    const createWizard = page.locator('[data-deployed-agent-wizard="root"]');
    await expect(createWizard).toBeVisible();
    await expect(createWizard).toContainText(/agent name/i);
    await expect(createWizard).toContainText(/what should it do/i);
    await expect(createWizard).not.toContainText(/launch settings/i);
    const agentNameInput = createWizard.getByRole('textbox', { name: /^agent name$/i });
    await agentNameInput.fill('Returns Concierge');
    await expect(agentNameInput).toHaveValue('Returns Concierge');
    await page.getByRole('button', { name: /^create agent$/i }).click();
    await expect(page.locator('[data-deployed-agent-wizard="root"]')).toHaveCount(0);

    const studioSurface = page.locator('[data-workstation-surface="deployed-agents"]');
    await expect(page).toHaveURL(/\/w\/ws-1\/studio\?agent=dagent-new/);
    await expect(studioSurface).toContainText(/returns concierge/i);
    await expect(studioSurface).toContainText(/draft/i);
    await detailNav.getByRole('link', { name: /^model$/i }).click();
    await expect(studioSurface).toContainText(/selected route/i);
    await expect(studioSurface).toContainText(/provider/i);
    await expect(studioSurface).toContainText(/model/i);
    await detailNav.getByRole('link', { name: /^channels$/i }).click();
    await expect(studioSurface).toContainText(/customer channels/i);
    await expect(studioSurface).toContainText(/connect where customers talk/i);
    await expect(studioSurface).toContainText(/available business channels/i);
    await detailNav.getByRole('link', { name: /^actions$/i }).click();
    await expect(studioSurface).toContainText(/allowed actions/i);
    await expect(studioSurface).toContainText(/action playbooks/i);
  });
});
