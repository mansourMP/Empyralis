export const meta = {
  name: 'empyralis-platform-map',
  description: 'Exhaustive platform map using graphify — file-by-file, connection-by-connection. Produces docs/handoff/PLATFORM-MAP.md.',
  phases: [
    { title: 'Graph and docs', detail: 'Refresh graphify AST, fetch Linear docs' },
    { title: 'Map files', detail: 'Fan out to map every directory in parallel' },
    { title: 'Trace connections', detail: 'Subsystem connection traces from graph data' },
    { title: 'Synthesize', detail: 'Combine all sections into PLATFORM-MAP.md' },
  ],
};

// ── Phase 1: graphify refresh + Linear doc fetch ──
phase('Graph and docs');

const [graphifyResult, linearDocs] = await parallel([
  function() {
    return agent(
      'Run `graphify update .` in the repo root (/Users/mansur/empyralis). Report the node/edge/community counts from the output.',
      { label: 'graphify-refresh' }
    );
  },
  function() {
    return agent(
      'Fetch these Linear documents using mcp__linear-server__get_document. ' +
      'Read each one and extract every factual claim about architecture, product shape, ' +
      'channels, agents, MCP, and decisions. Return a structured summary of each doc.\n\n' +
      'Documents: platform-overview-empyralis-one-agent-one-service-many-configurations-86a18989075a, ' +
      'product-shape-agent-model-one-agent-class-user-facing-configurations-09aad1212721, ' +
      'product-shape-channel-families-cloud-channels-gateway-required-per-87f79a94df41, ' +
      'product-shape-execution-layers-brain-hands-memory-8293a6c89488, ' +
      'product-shape-mcp-integration-cloud-to-cloud-no-user-hardware-b8322eecf6d0, ' +
      'product-shape-supported-mcp-apps-30-wired-6-removed-9c37c3a4e8cb, ' +
      'decision-ai-provider-strategy-platform-credits-byok-one-road-no-silent-2f13c73882e7, ' +
      'decision-agent-simplification-synthesis-from-anthropic-openai-research-ce005e458c1e',
      { label: 'linear-docs' }
    );
  },
]);

log('Graphify refresh: ' + (graphifyResult ? 'done' : 'failed'));
log('Linear docs: ' + (linearDocs ? 'fetched' : 'failed'));

// ── Phase 2: fan-out file mapping ──
phase('Map files');

const fileMaps = await parallel([
  function() {
    return agent(
      'Map EVERY .py file in /Users/mansur/empyralis/server_modules/ (including subdirectories like connectors/). ' +
      'For each file: path, one-line purpose (read imports, function names, docstrings — do NOT guess), ' +
      'and whether it is live or dead (grep for its module name in other files; check if its routes are registered in server.py). ' +
      'Group by subsystem: Agent runtime (sage_*, agent_turn, turn_runtime), Chat/direct-chat (direct_chat_*), ' +
      'Channels/routing (agent_channel_router, channel_*, routes_*), Connectors (connectors/*), ' +
      'MCP/registry (mcp_registry*, skill_registry, connection_oauth*), Memory (memory_service, unified_memory*), ' +
      'Tools/brokering (tool_broker*, secrets_broker*), Vault/OAuth (vault_store, connection_oauth*), ' +
      'Gateway (gateway_*), Hardware (hardware_*), Runtime/sessions (runtime_*, session_*, runs_*, local_queue), ' +
      'Workflows (workflow_*), Billing/quotas (billing_*, quota_*, entitlements_*), ' +
      'Governance/safety (unified_governance*, safe_mode*), Utilities (logging_config, state_paths, url_security, etc.). ' +
      'Output as markdown tables per subsystem. EVERY file. No summarization.',
      { label: 'map-server-modules' }
    );
  },
  function() {
    return agent(
      'Map EVERY significant file in /Users/mansur/empyralis/frontend/. This is a Next.js 16 app.\n\n' +
      'Cover: frontend/app/ (all pages, layouts, routes, client components), ' +
      'frontend/lib/workspace/ (ALL files — chat panes, memory panes, channel pairing, runs, codex-chat/*), ' +
      'frontend/lib/ui/ (design system primitives), frontend/shared/ (symlink to ../shared).\n\n' +
      'For each file: path, purpose (read the file), and usage status.\n\n' +
      'READ THESE FULLY (recently modified): ' +
      'frontend/lib/workspace/codex-chat/event-projector.ts, ' +
      'frontend/lib/workspace/workstation-chat-timeline-projection.ts, ' +
      'frontend/lib/workspace/workstation-runs-pane.tsx, ' +
      'frontend/lib/server/control-plane-base-url.ts.\n\n' +
      'Check frontend/v2/ — is it just build artifacts (.next/, out/, node_modules/) with no source code?',
      { label: 'map-frontend' }
    );
  },
  function() {
    return agent(
      'Map three directories:\n\n' +
      '1. /Users/mansur/empyralis/empyralis-gateway/src/ — Node.js Gateway on user hardware (WSS tunnel). ' +
      'Group by: channels/, supervisor/, browser/, pairing/, protocol/, runtime/, health/, cloud/, dev/, bridges/.\n' +
      '2. /Users/mansur/empyralis/empyralis-supervisor/src/ — Rust policy kernel. ' +
      'NOTE: per F1 audit, this binary has NEVER been compiled. Confirm whether main.rs is a real HTTP server on 127.0.0.1:7788.\n' +
      '3. /Users/mansur/empyralis/empyralis-runtime-kernel/src/ — Rust execution runtime.\n\n' +
      'For every file: path, purpose (read the actual code), status (PROVEN/WIRED/SKELETON/DEAD).',
      { label: 'map-gateway-supervisor-kernel' }
    );
  },
  function() {
    return agent(
      'Map these directories:\n\n' +
      '1. /Users/mansur/empyralis/server/ — declared v2 backend target. For every .py file in agent/, tools/, ' +
      'oauth/, memory/, mcp/, vault/, channels/, api/, conversations/: path and purpose. Compare against ' +
      'server_modules/ equivalent — what is MISSING? Is it a real implementation or a stub?\n\n' +
      '2. /Users/mansur/empyralis/legacy/ — v1 reference. Top-level structure. What is a copy of root code vs unique? ' +
      'Check legacy/frontend vs root frontend/.\n\n' +
      '3. /Users/mansur/empyralis/shared/ — API contracts, design tokens, nav manifest. Read every file.\n\n' +
      '4. Root config: mcp.json, mcp_server.py, main.py, server.py (FastAPI composition root), ' +
      'Dockerfile.runtime, Dockerfile.sandbox, render.yaml, requirements.txt, package.json.',
      { label: 'map-server-legacy-shared' }
    );
  },
  function() {
    return agent(
      'Extract and analyze /Users/mansur/empyralis/graphify-out/graph.json. ' +
      'Use Python to parse it if it is large JSON. Report:\n\n' +
      '1. God objects — top 30 nodes by edge count with communities bridged.\n' +
      '2. ALL import cycles — every cycle with files involved.\n' +
      '3. Isolated nodes — total count, sample 20 largest, check if dead by grepping.\n' +
      '4. Communities — total count, top 10 by size, bottom 10 by cohesion.\n' +
      '5. Inferred edges — count, avg confidence, top 20 involving god objects.\n' +
      '6. Any new structural issues NOT in the 2026-06-30 report.',
      { label: 'graphify-analysis' }
    );
  },
]);

log('File maps: ' + fileMaps.filter(Boolean).length + '/5 agents returned');

// ── Phase 3: subsystem connection traces ──
phase('Trace connections');

const connectionTraces = await agent(
  'Trace call chains through the Empyralis platform by reading actual source files:\n\n' +
  '1. INBOUND to TURN ENGINE: For Telegram bot webhook, Discord Interactions, Slack Events, ' +
  'Gateway WSS, VPS HTTP poll — trace exact call chain from inbound message to agent_turn.py / turn_runtime.py. ' +
  'Read the route handler code and follow every function call.\n\n' +
  '2. TURN ENGINE to LLM to TOOLS: agent_turn.py -> turn_runtime.py -> direct_chat_generation_service.py ' +
  '(the LLM call) -> tool_broker.py -> MCP registry OR Gateway WSS OR VPS worker. Show branching.\n\n' +
  '3. TOOLS to RESPONSE to CHANNEL: Tool result -> agent loop -> reply dispatch -> channel adapter -> transport delivery.\n\n' +
  '4. OAUTH FLOW: User clicks Connect Gmail -> OAuth dance -> token vault -> MCP server registration -> ' +
  'tool discovery -> approval -> invocation. Read connection_oauth_service.py, mcp_registry_service.py, ' +
  'vault_store.py, skill_registry.py.\n\n' +
  '5. GATEWAY LIFECYCLE: Install -> pairing -> WSS connect -> capability advertisement -> ' +
  'tool dispatch -> result return. Read gateway_execution_service.py, gateway_protocol_service.py, ' +
  'gateway_pairing_service.py, and the Gateway TypeScript files.\n\n' +
  '6. VPS WORKER LIFECYCLE: Install -> HTTP poll -> claim -> execute -> heartbeat -> result. ' +
  'Read local_queue.py and runtime_* files.\n\n' +
  'For every step: cite file:line. Mark broken/unclear steps explicitly. ' +
  'Output as ASCII call-chain diagrams with file:line annotations.',
  { label: 'trace-connections', effort: 'high' }
);

log('Connection traces: ' + (connectionTraces ? 'done' : 'failed'));

// ── Phase 4: synthesize ──
phase('Synthesize');

const finalDoc = await agent(
  'SYNTHESIZE all data from previous agents into ONE document at ' +
  '/Users/mansur/empyralis/docs/handoff/PLATFORM-MAP.md using the Write tool.\n\n' +
  'SECTIONS (in order):\n\n' +
  '## 0. Reading Guide — what this is, relation to docs/PLATFORM-MAP.md and Linear docs, graphify stats.\n\n' +
  '## 1. Architecture Overview — ASCII diagram: consumer surfaces, control plane, execution runtimes, ' +
  'data flow arrows. Update docs/PLATFORM-MAP.md Part 1 with anything new found during mapping.\n\n' +
  '## 2. Complete File Map — EVERY file by directory, grouped by subsystem. ' +
  'Markdown tables: File | Purpose | Status | Key Dependencies. Subsections: 2.1 server_modules/ (by subsystem), ' +
  '2.2 server_modules/connectors/, 2.3 frontend/, 2.4 empyralis-gateway/, 2.5 empyralis-supervisor/, ' +
  '2.6 empyralis-runtime-kernel/, 2.7 server/ (v2 target with gap annotations), 2.8 legacy/, 2.9 shared/, ' +
  '2.10 Root config.\n\n' +
  '## 3. Subsystem Connection Map — all 6 call-chain traces with ASCII diagrams and file:line on every step. ' +
  'Mark broken/unclear steps.\n\n' +
  '## 4. Graphify Structural Analysis — god objects (top 30 with impact), all import cycles, ' +
  'isolated nodes (count, 20 sampled), communities (count, top 10, worst 5 cohesion), inferred edges.\n\n' +
  '## 5. Channel System Truth Table — every channel: Transport, Status, Session Owner, ' +
  'Routes Through Sage?, Requires Hardware?, Key Files, Known Gaps.\n\n' +
  '## 6. MCP/Apps Truth Table — every connector: Provider, OAuth?, MCP Endpoint, Tools, ' +
  'Frontend Shows?, Backend Invokes?, Status.\n\n' +
  '## 7. Current vs Target Gap — count how many of each "ONE primitive" exist in server_modules/. ' +
  'List every duplicate. Show what server/ has vs needs.\n\n' +
  '## 8. Dead Code Register — every dead file/function/route found, with evidence.\n\n' +
  'NO SUMMARIZATION. Every file, every trace, every table. Write the complete document.',
  { label: 'synthesize', effort: 'high' }
);

log('Document written: ' + (finalDoc ? 'yes' : 'may have failed'));
return { output: finalDoc || 'Check docs/handoff/PLATFORM-MAP.md on disk' };
