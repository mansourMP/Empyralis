"""MAN-310: Claude Agent SDK bridge — a second, selectable turn engine.

Empyralis stops maintaining its own agent harness (compaction, subagents,
skills, tool loop) and instead runs the Claude Agent SDK underneath its own
UI, as a second engine alongside the existing one. The existing runtime
(direct_chat_generation_service.stream_provider_backed_direct_chat, driven
from server_modules/sage_agent_runtime_service.py's _collect_stream_events)
stays in place, unmodified, and keeps serving the cheap platform-credit
tier. This module is purely additive: nothing outside the explicit
per-turn engine flag at that seam ever imports or calls it, so a deployment
that never sets the flag runs byte-for-byte as it did before this file
existed.

Prior art: spikes/man-310-claude-agent-sdk/ (branch
worktree-agent-ab0972dff6480d82d, commit b3958aea2) verified claude-agent-sdk
0.2.130 installs cleanly against this repo's requirements.txt with zero
version bumps to shared deps, and documented the SDK's message/event
dataclass shapes via both live introspection and the real parser fed
synthetic wire input. Re-run in this worktree with the same result before
this module was written — see that spike's inspect_event_shapes.py.

Two things this module is NOT:
  - Not a tool reimplementation. Every tool call is dispatched through
    Empyralis's EXISTING executor (generation_services.execute_single_direct_
    tool_call — the exact bound callable direct_chat_generation_service.py's
    own tool loop already calls), reached via an in-process SDK MCP server
    (claude_agent_sdk.create_sdk_mcp_server + @tool — no subprocess/IPC
    overhead). Governance (specialist tool-binding guard, broker decisions),
    secret redaction (secret_redaction_service), billing attribution, and
    tool_result_status classification all run exactly where they do today.
    This module only translates between the SDK's message vocabulary and
    Empyralis's connector_id/action_id/capability_id vocabulary, using the
    SAME derivation direct_chat_generation_service.py's own tool loop uses:
    direct_chat_operator_binding_service.parse_tool_name for connector_id/
    action_id and direct_tool_execution_service.build_direct_tool_trace_
    metadata for capability_id/execution_environment/kind-shaped metadata
    (mirroring direct_tool_execution_service.direct_tool_step_payload's own
    connector_id/action_id -> label/kind derivation, :209).
  - Not a full harness port. Three meta-tools the legacy engine handles
    INLINE in its own loop rather than through execute_single_direct_tool_
    call (task_complete, update_plan, query_tool_registry — see
    tool_registry_service.ALWAYS_ON_TOOL_NAMES's docstring) are deliberately
    excluded from what's registered with the SDK (_UNSUPPORTED_TOOL_NAMES
    below) rather than half-reimplemented. task_complete's function is
    already native to the SDK (a turn ends when the model stops calling
    tools; that's the ResultMessage this module already translates).
    query_tool_registry (lazy Tier-2 tool discovery) has no SDK equivalent
    wired yet — a real, intentional v1 gap, not an oversight.

    update_plan (MAN-310 Phase 2 investigated this one specifically —
    verdict: NOT building an equivalent, for two independent reasons):
      1. Its plan-tracking/visibility half is substantially covered IN
         SPIRIT by the SDK's own native TodoWrite tool and Claude Code's
         trained-in planning behavior — the model already tracks multi-step
         work without Empyralis having to offer a tool or explain the
         convention in the system prompt, the way the legacy engine must.
         Translating a TodoWrite call into Empyralis's own "plan.updated"
         trace event (so it renders in the Work tab the same way) would be
         a real, buildable feature — but it is a visibility/polish
         enhancement, not a correctness or reliability gap, and per this
         phase's own priority order it does not belong in this pass.
         Separately: TodoWrite is not reachable here at all any more.
         `ClaudeAgentOptions.tools=[]` (set in run_claude_agent_sdk_turn,
         emitting `--tools ""`) removes the CLI's entire built-in toolset
         from the model's reach. This paragraph used to argue the opposite
         — that leaving `tools` unset was probably harmless because
         `allowed_tools` would keep the built-ins unapproved — and that
         reasoning was WRONG, expensively: `allowed_tools` governs
         APPROVAL, not AVAILABILITY. A built-in TaskCreate call really did
         run, really did succeed in the CLI's own bookkeeping, and was
         reported to a customer as real Empyralis work. See the comment on
         the `tools=[]` line itself, and the foreign-tool guard in
         translate_sdk_message that now backstops it.
      2. Its iteration-budget-extension half (continuous work past
         max_iterations while a plan has open tasks — direct_chat_
         generation_service.py's _continuous_work_enabled /
         _plan_has_open_tasks / _continuous_work_budget_allows_more) has NO
         safe SDK-native equivalent to port without ALSO porting the
         token-budget gate that makes it safe on the legacy engine. This
         bridge's ClaudeAgentOptions.max_turns is set once, to the SAME
         _SAGE_OPERATOR_LOOP_MAX_ITERATIONS every legacy turn that never
         calls update_plan is ALSO capped at — i.e. today's SDK-engine
         behavior already matches the legacy engine's own no-plan default,
         byte for byte. Raising it unconditionally (e.g. to direct_chat_
         generation_service._CONTINUOUS_WORK_HARD_ITERATION_CAP) without a
         corresponding "is there still real, budgeted work pending" signal
         would trade a bounded, well-understood cap for a more expensive
         one with no matching safety check — a worse reliability/cost
         posture, not a better one. Left exactly as-is.

Non-Anthropic backend: Anthropic does not officially support pointing the
SDK/CLI at a non-Anthropic backend — see
https://github.com/anthropics/claude-agent-sdk-python/issues/410. Empyralis
does this knowingly: the founder runs Claude Code directly against
DeepSeek's Anthropic-Messages-API-compatible endpoint
(https://api.deepseek.com/anthropic) and reports it working flawlessly.
resolve_sdk_process_env() below takes ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL
from explicit per-turn arguments (falling back to the turn's resolved
`credentials` dict), never hardcoded, and hands them to
ClaudeAgentOptions(env=...), never os.environ, never a global default.

Credential isolation (multi-tenancy): the Python SDK's Transport MERGES
options.env ON TOP OF the parent (Empyralis backend) process's own
environment rather than replacing it (subprocess_cli.py's env handling;
also documented at code.claude.com/docs/en/llm-gateway-connect's Agent SDK
section — the TypeScript SDK does the opposite). A dict that only sets the
keys it has real per-turn values for is therefore NOT enough isolation: any
ANTHROPIC_*/CLAUDE_CODE_* variable ambiently set in Empyralis's own process
would otherwise leak into every spawned tenant subprocess. resolve_sdk_
process_env() always returns every credential-shaped key it knows about
(_CREDENTIAL_ENV_KEYS), blanked to "" when this turn has no value for it —
see that function's own docstring — and run_claude_agent_sdk_turn always
gives each turn a brand-new CLAUDE_CONFIG_DIR that has never had `claude
/login` run against it, destroyed the moment the turn ends, and spawns
exactly one fresh subprocess per turn (never a pooled/reused warm CLI
process across tenants).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from server_modules import agent_trace_service
from server_modules import config_defaults_service
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import openai_compat_adapter
from server_modules import secret_redaction_service

LOGGER = logging.getLogger(__name__)

# The value _run_sage_action_loop_v3's `engine_options={"engine": ...}` must
# carry to select this engine for a turn. Everything else (empty dict, key
# absent, any other value) takes the existing/legacy path unchanged.
ENGINE_ID = "claude_agent_sdk"

_MCP_SERVER_NAME = "empyralis"
_MCP_TOOL_PREFIX = f"mcp__{_MCP_SERVER_NAME}__"

# Meta-tools the legacy engine's own loop (direct_chat_generation_service.py)
# handles INLINE rather than by dispatching to execute_single_direct_tool_
# call — see this module's docstring. Registering these with the SDK and
# routing them through execute_single_direct_tool_call would fail (their
# bare names aren't in direct_chat_operator_binding_service.parse_tool_
# name's special-case list, so parsing raises and connector_id/action_id
# come back empty) or silently do the wrong thing, so they are filtered out
# before ever reaching the SDK, not half-wired.
_UNSUPPORTED_TOOL_NAMES = frozenset({"task_complete", "update_plan", "query_tool_registry"})

# trace.failed `code` values for the two ways a tool-shaped SDK message can
# fail to be Empyralis work. Both land in _collect_sage_operator_loop_v3_
# events' `blocked_tools` (its "trace.failed" branch) rather than its
# `tool_calls` list — which is the whole point: `tool_calls` is what the
# customer's Work tab renders as work done AND what tool_honesty_guard
# checks a reply's claims against, so anything that isn't a real Empyralis
# tool call must never enter it.
#
# _FOREIGN_TOOL_TRACE_CODE: the model called a tool this turn never
#   registered. This is the MAN-310 regression class in its general form —
#   the concrete instance was the CLI's own built-in TaskCreate, reachable
#   because ClaudeAgentOptions.tools defaulted to the full claude_code
#   preset. tools=[] closes that specific door; this closes the doorway.
# _ORPHAN_TOOL_RESULT_TRACE_CODE: a ToolResultBlock arrived whose
#   tool_use_id was never announced by a ToolUseBlock in this stream. The
#   collector would bucket it under a synthesized entry named
#   "direct_tool" with status "completed" (see its _tool_entry default) —
#   a completed row in the work ledger for a call nothing in this turn can
#   name. Same failure class, different door.
_FOREIGN_TOOL_TRACE_CODE = "foreign_tool_call"
_ORPHAN_TOOL_RESULT_TRACE_CODE = "orphan_tool_result"

# MAN-310 skills-delivery: the CLI's OWN built-in meta-tools that a turn may
# deliberately re-open (see build_skills_plugin_dir / run_claude_agent_sdk_
# turn's `tools=` construction) now that tools=[] no longer means "every
# built-in is unreachable" unconditionally. Mapped to the HONEST trace event
# type each one gets — never "tool.started"/"tool.result" (that bucket is
# `tool_calls`, what tool_honesty_guard checks a reply's claims against, and
# a Skill/Agent call is not a registered Empyralis tool doing Empyralis
# work), and never "trace.failed" either (that bucket is `blocked_tools`, a
# real failure — a deliberately-reopened meta-tool succeeding is not one).
# Exact names verified against the installed SDK, not guessed: `tools:
# list[str] | ToolsPreset | None` on ClaudeAgentOptions documents "list[str]
# — Specific tool names (e.g. ["Bash", "Read", "Edit"])"; AgentDefinition's
# own docstring says "Programmatically define custom subagents invokable via
# the Agent tool"; ClaudeAgentOptions.skills says skill files are "rejected
# by the Skill tool" when not listed — both PascalCase, both bare (neither
# is an mcp__*__* name, since neither is an MCP tool) — see venv/claude_
# agent_sdk/types.py (ClaudeAgentOptions.tools/.agents/.skills docstrings).
_META_TOOL_EVENT_TYPES = {
    "Agent": "subagent.invoked",
    "Skill": "skill.invoked",
}

# The one trace.failed `code` this module uses when the turn failed upstream
# (the provider/API, not a tool). Deliberately ONE token, not a taxonomy:
# the collector renders `code` verbatim as the blocked entry's NAME in the
# customer's Work tab (_collect_sage_operator_loop_v3_events' "trace.failed"
# branch), so every value here is customer-visible vocabulary that has to
# mean something. A real SDK subtype (e.g. "error_max_turns") is passed
# through as-is because the SDK already owns that name; anything else falls
# back to this rather than being invented here.
_PROVIDER_GENERATION_FAILED_CODE = "provider_generation_failed"

# ResultMessage.subtype values that do NOT name a failure. "success" is the
# whole point of this set: measured against the real Claude Code CLI, an
# API/upstream failure arrives as is_error=True while subtype STAYS
# "success". The installed SDK documents exactly that on
# ResultMessage.api_error_status — "HTTP status code (e.g. 429, 500, 529) of
# the failing API call when ``is_error`` is True and ``subtype`` is
# 'success'" (venv/lib/python3.12/site-packages/claude_agent_sdk/types.py).
# Taking subtype at face value therefore persisted a trace.failed row whose
# code was the literal string "success", and the Work tab rendered a blocked
# entry NAMED "success". A failure that files itself under "success" is the
# exact class of untruth this product cannot ship.
_NON_FAILURE_RESULT_SUBTYPES = frozenset({"success", ""})

# The `model` string the CLI stamps on an assistant message it FABRICATED
# rather than received from a model — API-error notices, "prompt too long",
# interrupt notices. Whatever text such a message carries, it is the CLI
# talking about a failure, never the agent's answer to the customer.
_SYNTHETIC_ASSISTANT_MODEL = "<synthetic>"

# Provider ids whose turns really are served by Anthropic's own API, so the
# CLI's client-side total_cost_usd (computed from Anthropic's price table)
# is a true number rather than a coincidence. "" means the caller named no
# provider, in which case the CLI has no endpoint to talk to BUT Anthropic's
# own default — see turn_is_served_by_anthropic, which additionally requires
# that no ANTHROPIC_BASE_URL override is in effect for the turn.
_ANTHROPIC_PROVIDER_IDS = frozenset({"", "anthropic"})

# Empyralis's own reasoning-effort vocabulary for the platform_credits/
# byok_api modes — the ONLY modes that ever reach this bridge (cli_
# subscription is a completely separate path: empyralis-gateway/src/llm/
# cli-runner.ts spawns the owner's own paired CLI directly and passes its
# own --effort/--reasoning-effort flags there; this module never sees that
# turn). Mirrors frontend/lib/workspace/fleet/fleet-provider-constants.ts's
# ReasoningEffort type and server_modules/fleet_tools.py's
# _VALID_REASONING_EFFORTS byte-for-byte — "" is the fifth, unwritten
# member of both: "no override was stored", never a level in its own right.
# Now the SDK's complete EffortLevel set (claude_agent_sdk/types.py:
# Literal["low", "medium", "high", "xhigh", "max"]) — all five are
# selectable from the picker.
_VALID_SDK_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def resolve_sdk_effort(reasoning_effort: str) -> Optional[str]:
    """Map Empyralis's stored `reasoning_effort` onto
    `ClaudeAgentOptions.effort`, or None to leave the field unset entirely.

    "Model default" (reasoning_effort == "", the REASONING_EFFORT_OPTIONS
    picker's own "" entry — see fleet-provider-constants.ts) MUST return
    None here, not a guessed level: `ClaudeAgentOptions.effort` defaults to
    None, and per its own docstring the SDK/model choose the effort in that
    case (documented default: "high"). Coercing "" to a specific level would
    change behavior for every agent that has never touched this control,
    which "model default" explicitly promises not to do. An unrecognized
    string (defensive only — fleet_tools.py's own _VALID_REASONING_EFFORTS
    check already rejects anything outside {"low","medium","high","xhigh"}
    before it can be saved) is treated the same as "": pass nothing rather
    than something the SDK might reject.

    Deliberately provider-agnostic: reasoning depth is an SDK-level concept
    here, not something this bridge maintains a per-provider translation
    table for. The chosen level is always forwarded to
    `ClaudeAgentOptions.effort` when the caller made an explicit choice,
    regardless of which provider ultimately serves the turn. For turns that
    reach Anthropic's own API this becomes a `--effort` flag to the `claude`
    CLI subprocess (see claude_agent_sdk's subprocess_cli.py). For
    adapter-routed providers (openai_compat_adapter.py's
    translate_anthropic_request_to_openai), the field simply is not part of
    that function's explicit allowlist of fields it copies onto the
    outgoing OpenAI-shaped body — it is stripped by omission, the same way
    `thinking`/`cache_control`/`metadata` already are, so there is no wire
    contract to get wrong and no 400 risk.
    """
    normalized = str(reasoning_effort or "").strip().lower()
    if normalized not in _VALID_SDK_REASONING_EFFORTS:
        return None
    return normalized


# Credential-shaped env vars claude_agent_sdk's spawned `claude` CLI
# subprocess recognizes, in Anthropic's own documented precedence order
# (code.claude.com/docs/en/authentication's "Authentication precedence"
# table, fetched 2026-08-05): cloud-provider flags (rank #1) outrank
# ANTHROPIC_AUTH_TOKEN (#2), which outranks ANTHROPIC_API_KEY (#3), which
# outranks CLAUDE_CODE_OAUTH_TOKEN (#5) and any subscription OAuth already
# saved via `claude /login` (#6, last).
#
# Why this list matters: per code.claude.com/docs/en/llm-gateway-connect's
# Agent SDK section, the PYTHON SDK's Transport MERGES ClaudeAgentOptions
# (env=...) ON TOP OF the parent process's own environment rather than
# replacing it (the TypeScript SDK does the opposite). A dict that only
# sets keys it has a real value for is therefore NOT sufficient isolation:
# any ANTHROPIC_*/CLAUDE_CODE_* variable that happens to be set in
# Empyralis's OWN backend process (an operator's local `claude /login`,
# a future accidental export, a cloud-provider flag meant for some other
# purpose) would leak into every spawned tenant subprocess, unrelated to
# that tenant's own resolved credential -- wrong billing attribution at
# best, cross-tenant credential use at worst (cloud-provider flags are
# rank #1 and would silently override everything this module passes).
# resolve_sdk_process_env() below therefore ALWAYS returns every one of
# these keys, blanked to "" when this turn has no value for them, rather
# than omitting the key -- an explicit "" in options.env still wins the
# merge over parent os.environ, which is what actually blocks the leak.
_CREDENTIAL_ENV_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
)

# MAN-313: the actual per-provider credential env vars the CLAUDE_CODE_USE_*
# flags above need once one of them is finally set true (see
# _CLOUD_ROUTED_PROVIDER_ENV_BUILDERS below) -- e.g. CLAUDE_CODE_USE_BEDROCK=1
# on its own authenticates nothing without AWS_ACCESS_KEY_ID/AWS_SECRET_
# ACCESS_KEY alongside it. These were NOT in _CREDENTIAL_ENV_KEYS above,
# which is a second, narrower instance of the exact leak that constant's own
# docstring warns about: this backend process's OWN environment plausibly
# already carries AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN
# ambiently (see artifact_service.py's own object-storage credential
# fallback, which reads exactly those three names from os.environ for
# Empyralis's OWN S3-compatible storage, entirely unrelated to any tenant's
# Bedrock credential). Before this constant existed, a turn for a provider
# OTHER than bedrock/vertex would omit these keys from options.env entirely
# rather than blanking them -- and per this module's documented Transport
# behavior (env merges ON TOP of the parent process's own environment,
# never replaces it), an omitted key means the CLI subprocess would inherit
# whatever this backend process's own ambient AWS/GCP env happens to be.
# Blanked to "" by default alongside _CREDENTIAL_ENV_KEYS for exactly the
# same reason: an explicit "" in options.env wins the merge; an absent key
# does not.
_CLOUD_PROVIDER_CREDENTIAL_ENV_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
)

# A provider's ordinary profile base_url (provider_profiles.py) is its
# OpenAI-shaped endpoint — DeepSeek's, for instance, is .../v1. Handing that
# to the `claude` CLI would point an Anthropic-Messages client at an
# OpenAI-protocol URL. This maps the providers that ship their OWN native
# Anthropic-Messages-compatible endpoint to that endpoint instead. Only
# providers verified against a real turn belong here: per the project's
# standing rule, a provider is supported only if it publishes a native
# Anthropic-compatible endpoint — never via a translating proxy. Anthropic
# itself is absent deliberately (no base_url override; the CLI's own default
# is correct).
_ANTHROPIC_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/anthropic",
}


def resolve_anthropic_compatible_base_url(provider: str) -> str:
    """The Anthropic-Messages-compatible endpoint for `provider`, or "" when
    that provider has none (so no ANTHROPIC_BASE_URL is emitted and the CLI
    talks to Anthropic directly). Never falls back to the provider's
    OpenAI-shaped profile URL — a wrong protocol is worse than no override,
    because it fails as an opaque HTTP error rather than an obvious
    unsupported-provider one."""
    return _ANTHROPIC_COMPATIBLE_BASE_URLS.get(str(provider or "").strip().lower(), "")


def resolve_ollama_anthropic_base_url(provider: str, credentials: Optional[Dict[str, Any]] = None) -> str:
    """Ollama's Anthropic-Messages-compatible endpoint for THIS turn — a
    sibling to resolve_anthropic_compatible_base_url for a provider that
    cannot live in _ANTHROPIC_COMPATIBLE_BASE_URLS's fixed map. Ollama
    (v0.14.0+) ships a native Anthropic-Messages-compatible surface too, but
    unlike DeepSeek's one fixed public URL, Ollama is self-hosted per
    workspace/credential: the host is whatever this turn's own resolved
    Ollama credential actually configured (commonly localhost:11434, but
    provider_profiles.py's "ollama" catalog entry's base_url default is only
    ever a fallback the LEGACY engine's OpenAICompatibleAdapter._base_url
    applies — never something this function invents). Returns "" (no
    override) when this turn's credentials carry no real base_url, same
    fail-safe contract as resolve_anthropic_compatible_base_url: a guessed
    localhost could point at a wrong or nonexistent local service on
    whatever machine happens to be running this backend process.

    Deliberately gated on `provider == "ollama"` (not "ollama_cloud", which
    is a hosted OpenAI-shaped endpoint with no native Anthropic surface and
    stays adapter-routed — see openai_compat_adapter.ADAPTER_ROUTED_
    PROVIDER_IDS): calling this for any other provider is a no-op so it is
    safe to unconditionally `or` into resolve_sdk_process_env's base_url
    chain without a separate provider check at every call site.

    credentials["base_url"] is the OpenAI-shaped value every provider
    profile in this codebase already uses (e.g. "http://localhost:11434/v1"
    default, or a custom host/port a workspace profile configured) — the
    exact same key OpenAICompatibleAdapter._base_url reads for the existing
    (non-SDK) engine's real Ollama calls. Ollama's Anthropic-compatible
    surface is served at the bare host, not under /v1 (confirmed against
    Ollama's own setup docs: ANTHROPIC_BASE_URL with no path suffix), so
    this strips any path/query/fragment down to scheme://host[:port] —
    tolerating a trailing slash, an already-bare host, or a missing scheme
    (defaulted to http, never guessed as https, matching Ollama's own local
    default)."""
    if str(provider or "").strip().lower() != "ollama":
        return ""
    creds = credentials if isinstance(credentials, dict) else {}
    raw = str(creds.get("base_url") or "").strip()
    if not raw:
        return ""
    scheme_sep = raw.find("://")
    if scheme_sep == -1:
        scheme, rest = "http", raw
    else:
        scheme, rest = raw[:scheme_sep], raw[scheme_sep + 3:]
    host = rest.split("/", 1)[0].strip()
    if not host:
        return ""
    return f"{scheme}://{host}"


# MAN-313: real per-provider dispatch for the CLAUDE_CODE_USE_* cloud-auth
# flags _CREDENTIAL_ENV_KEYS has declared since MAN-310 but resolve_sdk_
# process_env never actually set true for anyone -- every credential fell
# through to the generic ANTHROPIC_AUTH_TOKEN=api_key branch below instead,
# which for a cloud provider means shipping that provider's credential (an
# AWS access key, for Bedrock) to api.anthropic.com as if it were an
# Anthropic bearer token: wrong endpoint, and a real credential handed to a
# service that was never meant to see it.
#
# Only "bedrock" and "vertex" are wired here -- verified against provider_
# profiles.PROVIDER_CATALOG, the only two of the seven _CREDENTIAL_ENV_KEYS
# cloud flags with a real catalog entry AND a real ProviderAdapter
# (provider_profiles.PROVIDER_ADAPTERS). "foundry"/"anthropic_aws"/
# "anthropic_google_cloud"/"mantle" have NO provider_profiles.py catalog
# entry at all (grep confirms zero hits for any of those four strings in
# that file) -- there is no workspace credential shape that could ever
# reach this dispatch for them, so CLAUDE_CODE_USE_FOUNDRY/_ANTHROPIC_AWS/
# _ANTHROPIC_GOOGLE_CLOUD/_MANTLE are correctly left blank by the base env
# dict below, not a gap this ticket left open. CLAUDE_CODE_OAUTH_TOKEN (the
# seventh _CREDENTIAL_ENV_KEYS entry) is not a cloud-provider flag and
# doesn't belong in this dispatch either: it carries a `claude setup-token`
# subscription token, and provider_profiles.py's "anthropic" catalog entry's
# local_cli auth mode already means "use the CLI's own already-signed-in
# session" -- there is no separate OAuth-token STRING this backend resolves
# or holds for that mode, so nothing here could populate it without
# inventing a credential shape that doesn't exist.
#
# Each builder below returns the CLAUDE_CODE_USE_* flag plus the exact
# credential env var names the CLI's own first-party setup wizard writes for
# that mode -- verified against the installed @anthropic-ai/claude-code CLI
# binary (2.1.214) rather than guessed: its settings-writer functions build
# literal objects shaped exactly like these (`{CLAUDE_CODE_USE_BEDROCK:"1",
# ..., AWS_REGION:e.region, AWS_ACCESS_KEY_ID:void 0, ...}` for Bedrock;
# `{CLAUDE_CODE_USE_VERTEX:"1", ANTHROPIC_VERTEX_PROJECT_ID:e.projectId,
# CLOUD_ML_REGION:e.region, GOOGLE_APPLICATION_CREDENTIALS:void 0, ...}` for
# Vertex), and its /vertex-setup wizard's own copy ("Application Default
# Credentials (gcloud auth)" / "Service account key file" /
# "Use credentials already in my environment") independently confirms Vertex
# has no raw-bearer-token env var at all -- see _vertex_process_env's own
# docstring for what that means for this provider's current credential shape.
def _bedrock_process_env(credentials: Dict[str, Any]) -> Dict[str, str]:
    """AWS SDK credential env vars for CLAUDE_CODE_USE_BEDROCK=1: AWS_ACCESS_
    KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_REGION -- the same
    four names boto3 (and the CLI's own Bedrock setup wizard) read. Reads the
    SAME credential keys provider_profiles.BedrockAdapter._client already
    reads for the legacy engine's own Bedrock calls (api_key/aws_access_key_
    id, aws_secret_access_key/secret_key, aws_session_token, region/
    aws_region), so one saved Bedrock credential authenticates identically on
    both engines rather than needing a second, differently-shaped copy."""
    access_key = str(credentials.get("api_key") or credentials.get("aws_access_key_id") or "").strip()
    secret_key = str(credentials.get("aws_secret_access_key") or credentials.get("secret_key") or "").strip()
    session_token = str(credentials.get("aws_session_token") or "").strip()
    region = str(credentials.get("region") or credentials.get("aws_region") or "us-east-1").strip()
    env: Dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": region}
    if access_key:
        env["AWS_ACCESS_KEY_ID"] = access_key
    if secret_key:
        env["AWS_SECRET_ACCESS_KEY"] = secret_key
    if session_token:
        env["AWS_SESSION_TOKEN"] = session_token
    return env


def _vertex_process_env(credentials: Dict[str, Any]) -> Dict[str, str]:
    """CLAUDE_CODE_USE_VERTEX=1 plus ANTHROPIC_VERTEX_PROJECT_ID/CLOUD_ML_
    REGION -- the same two names provider_profiles.VertexAdapter._params
    reads as project_id/location, and the same names the CLI's own
    /vertex-setup wizard writes.

    Known, deliberate gap (not silently papered over): the installed CLI's
    Vertex auth is Google's standard ADC chain ONLY -- GOOGLE_APPLICATION_
    CREDENTIALS (a service-account key FILE) or ambient `gcloud auth
    application-default login` (verified directly against the CLI's own
    /vertex-setup wizard copy -- three choices offered, no fourth, none of
    them a bearer-token env var). provider_profiles.py's "vertex"
    PROVIDER_CATALOG entry stores a bare OAuth access_token (auth_modes:
    access_token) with no matching CLI env var to carry it, so this
    function correctly routes project_id/region and the CLAUDE_CODE_USE_
    VERTEX flag (closing the leak into ANTHROPIC_AUTH_TOKEN this ticket
    exists to fix) but a Vertex turn on THIS engine still cannot fully
    authenticate on that credential shape alone -- a separate, pre-existing
    gap in what the "vertex" profile collects, not something an env-var
    name invented here could paper over."""
    project_id = str(credentials.get("project_id") or "").strip()
    location = str(credentials.get("location") or "").strip()
    env: Dict[str, str] = {"CLAUDE_CODE_USE_VERTEX": "1"}
    if project_id:
        env["ANTHROPIC_VERTEX_PROJECT_ID"] = project_id
    if location:
        env["CLOUD_ML_REGION"] = location
    return env


_CLOUD_ROUTED_PROVIDER_ENV_BUILDERS: Dict[str, Any] = {
    "bedrock": _bedrock_process_env,
    "vertex": _vertex_process_env,
}


def resolve_sdk_process_env(
    *,
    credentials: Optional[Dict[str, Any]] = None,
    anthropic_api_key: str = "",
    anthropic_base_url: str = "",
    config_dir: str = "",
    provider: str = "",
    model: str = "",
) -> Dict[str, str]:
    """Build the `env` override for ClaudeAgentOptions — never os.environ,
    never a hardcoded URL. Explicit per-turn overrides win; otherwise falls
    back to whatever the turn's own resolved provider `credentials` dict
    carries (api_key is the standard key every provider profile in this
    codebase uses — see provider_profiles.py; base_url is opt-in and absent
    for providers whose profile doesn't set one, e.g. plain Anthropic).

    A workspace pointed at DeepSeek's Anthropic-compatible endpoint would
    pass anthropic_base_url="https://api.deepseek.com/anthropic" (or a
    credentials dict carrying that as "base_url") — this function does not
    special-case DeepSeek or hardcode that URL anywhere; it is only ever
    data the caller supplies for this turn.

    Every key in _CREDENTIAL_ENV_KEYS and _CLOUD_PROVIDER_CREDENTIAL_ENV_KEYS
    is ALWAYS present in the returned dict (see those constants' own
    docstrings for why an omitted key is not safe here, given the SDK's
    merge-over-parent-env behavior) — this function never reads os.environ
    itself, so nothing ambient can leak in through it either.

    Cloud-routed providers (currently "bedrock" and "vertex" — see
    _CLOUD_ROUTED_PROVIDER_ENV_BUILDERS) are dispatched to their own
    CLAUDE_CODE_USE_*/credential env vars before anything else in this
    function runs, and never fall through to the generic ANTHROPIC_AUTH_
    TOKEN=api_key path below.

    `config_dir`, when given, is forwarded as BOTH CLAUDE_CONFIG_DIR and
    CLAUDE_SECURESTORAGE_CONFIG_DIR (the latter covers macOS local dev,
    where credential storage also touches a Keychain entry namespaced by
    this directory, in addition to CLAUDE_CONFIG_DIR's on-disk store that
    covers Linux, how this deploys) — see run_claude_agent_sdk_turn, which
    always passes a fresh, never-logged-in-to directory for this reason.
    """
    creds = credentials if isinstance(credentials, dict) else {}
    env: Dict[str, str] = {
        key: "" for key in _CREDENTIAL_ENV_KEYS + _CLOUD_PROVIDER_CREDENTIAL_ENV_KEYS
    }
    api_key = str(
        anthropic_api_key or creds.get("api_key") or creds.get("anthropic_api_key") or ""
    ).strip()
    cloud_env_builder = _CLOUD_ROUTED_PROVIDER_ENV_BUILDERS.get(str(provider or "").strip().lower())
    # Cloud-routed providers (bedrock/vertex — see _CLOUD_ROUTED_PROVIDER_
    # ENV_BUILDERS's own docstring) are checked FIRST and are structurally
    # exclusive with every branch below: api_key is deliberately never
    # consulted for them even though it may be non-empty (Bedrock's own
    # credential shape stores the AWS access key under "api_key" — see
    # provider_profiles.BedrockAdapter._client), because ANTHROPIC_AUTH_
    # TOKEN=<that value> is exactly the MAN-313 bug (a Bedrock/Vertex
    # credential sent to api.anthropic.com instead of the real provider).
    if cloud_env_builder is not None:
        env.update(cloud_env_builder(creds))
    # Adapter-routed providers (openai/gemini/xai/... — anything with no
    # native Anthropic-Messages endpoint, see openai_compat_adapter.py) never
    # get the real provider key in the subprocess env at all. The CLI gets a
    # short-lived, per-turn OPAQUE token instead; the adapter itself holds
    # the real key in memory and exchanges the opaque token for it only when
    # making the actual upstream call. Strictly better than the plain
    # ANTHROPIC_AUTH_TOKEN=api_key path below: a leaked opaque token is
    # worthless the instant clear_turn_credential runs (see
    # run_claude_agent_sdk_turn's finally block).
    elif openai_compat_adapter.is_adapter_routed_provider(provider):
        # `creds` here — not `api_key` — is deliberately the real per-turn
        # credentials dict for THIS provider (e.g. an OpenAI key), not the
        # Anthropic-specific `anthropic_api_key` override above: that
        # parameter means "use this key for an Anthropic-shaped call",
        # which is not what an adapter-routed provider is.
        #
        # Fail-safe, matching every other credential-missing path in this
        # function: minting can raise (provider_profiles.py's own
        # credential validation, e.g. no api_key configured yet) and this
        # function must never propagate that — turn_is_served_by_anthropic
        # calls this purely to inspect the resolved base_url and has
        # nothing to do with whether real credentials exist. A genuinely
        # missing credential still surfaces, just later and naturally, as
        # an auth error from the real upstream once a turn actually runs —
        # exactly how a missing Anthropic/DeepSeek api_key already behaves
        # a few lines below, never by raising out of this resolver.
        try:
            env["ANTHROPIC_AUTH_TOKEN"] = openai_compat_adapter.mint_turn_token_for_provider(
                provider, creds, model,
            )
        except Exception as exc:
            LOGGER.debug(
                "resolve_sdk_process_env: mint_turn_token_for_provider(%s) failed — "
                "leaving ANTHROPIC_AUTH_TOKEN blank for this call: %s", provider, exc,
            )
    # ANTHROPIC_AUTH_TOKEN (rank #2), not ANTHROPIC_API_KEY (rank #3): an
    # API_KEY value the CLI hasn't seen before triggers a one-time
    # interactive "approve this key?" consent gate cached in .claude.json
    # (nothing this module could answer, and pointless anyway given
    # config_dir below is fresh every turn — the cache would never carry
    # over). AUTH_TOKEN carries no such gate. DeepSeek's own setup guide
    # for its Anthropic-Messages-API-compatible endpoint (see this module's
    # docstring) also instructs ANTHROPIC_AUTH_TOKEN for exactly this
    # reason.
    elif api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    # Ollama specifically: provider_profiles.py's "ollama" entry has
    # auth=["none"] (no real secret ever exists for it), so api_key above is
    # always "" and this branch is the only thing that ever sets a token for
    # it. Per Ollama's own setup docs, ANTHROPIC_AUTH_TOKEN is REQUIRED by
    # the client but its VALUE is ignored server-side — any non-empty string
    # satisfies it. Leaving it "" (today's generic no-api-key behavior) is
    # not "no auth needed", it is a value Ollama's docs say the client must
    # never send blank. "ollama" is a placeholder, never a real secret —
    # safe to log, safe to leak, authenticates nothing.
    elif str(provider or "").strip().lower() == "ollama":
        env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
    # An explicit per-turn override wins; otherwise the provider's own
    # native Anthropic-compatible endpoint (DeepSeek's fixed map entry, or
    # Ollama's per-turn-derived one — see resolve_ollama_anthropic_base_url,
    # which is a no-op for every provider except "ollama"); otherwise — for
    # a provider with no native endpoint — our own loopback adapter
    # (openai_compat_adapter), which speaks the CLI's wire format and
    # translates through to whatever OpenAI-shaped endpoint that provider
    # actually has. creds["base_url"] is deliberately NOT a direct fallback
    # here — for most providers that is the OpenAI-shaped URL (see
    # _ANTHROPIC_COMPATIBLE_BASE_URLS), which this client cannot speak
    # directly; resolve_ollama_anthropic_base_url is the one place that key
    # is read, and only for "ollama", precisely because that provider's
    # native surface is reachable at that same host once /v1 is stripped.
    base_url = str(
        anthropic_base_url or creds.get("anthropic_base_url") or ""
    ).strip() or resolve_anthropic_compatible_base_url(provider) or resolve_ollama_anthropic_base_url(provider, creds) or openai_compat_adapter.resolve_adapter_routed_base_url(provider)
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = config_dir
    return env


def turn_is_served_by_anthropic(
    *,
    provider: str = "",
    anthropic_base_url: str = "",
    credentials: Optional[Dict[str, Any]] = None,
) -> bool:
    """Did this turn's model calls actually go to Anthropic's own API?

    Asked for exactly one reason: ResultMessage.total_cost_usd is computed
    CLIENT-SIDE by the `claude` CLI from ANTHROPIC's price table, for
    whatever model string it saw. The CLI has no idea it was pointed
    somewhere else. On a DeepSeek-served turn (or any future adapter-routed
    provider) that number is not an estimate, it is fiction — a canned local
    response was billed at $0.0033 in testing. translate_sdk_message
    therefore only lets total_cost_usd through when this returns True; see
    TranslationState.served_by_anthropic.

    Two conditions, both required:

      - No ANTHROPIC_BASE_URL is in effect for the turn. This is resolved by
        calling resolve_sdk_process_env — the SAME function that builds the
        subprocess env — rather than re-deriving it, so the answer cannot
        drift from what the CLI is actually pointed at. That function also
        blanks every credential-shaped key (including the Bedrock/Vertex/
        Foundry flags), so an empty ANTHROPIC_BASE_URL here provably means
        the CLI talks to api.anthropic.com and nothing ambient can change
        that.
      - The provider names Anthropic (or names nothing at all, which leaves
        the CLI on its own Anthropic default).

    Anything else — an unrecognised provider, an explicit base-URL override,
    a provider with its own Anthropic-compatible endpoint — returns False,
    and the cost is omitted rather than guessed. A missing number is honest;
    a wrong one is not.
    """
    env = resolve_sdk_process_env(
        credentials=credentials,
        anthropic_base_url=anthropic_base_url,
        provider=provider or "",
    )
    if env.get("ANTHROPIC_BASE_URL"):
        return False
    return str(provider or "").strip().lower() in _ANTHROPIC_PROVIDER_IDS


def strip_mcp_tool_prefix(name: str) -> str:
    """The Claude Code CLI exposes SDK MCP tools to the model as
    `mcp__{server_name}__{tool_name}` (standard MCP-tool naming — the CLI
    subprocess applies this, not the Python SDK, so it isn't documented in
    claude_agent_sdk's own source). Strip it back to the registered
    Empyralis tool name before handing it to parse_tool_name, which knows
    nothing about that convention. A name without the prefix passes through
    unchanged, so this is safe to call unconditionally."""
    token = str(name or "").strip()
    if token.startswith(_MCP_TOOL_PREFIX):
        return token[len(_MCP_TOOL_PREFIX):]
    return token


def is_registered_empyralis_tool(
    raw_name: str, known_tool_names: Optional[frozenset] = None
) -> bool:
    """Is `raw_name` — the name EXACTLY as it appeared on a ToolUseBlock,
    before any prefix stripping — a tool Empyralis registered for this turn?

    Defence in depth behind `ClaudeAgentOptions.tools=[]`. That option is
    what actually keeps the CLI's own built-ins (TaskCreate, TodoWrite,
    Read, Write, Bash, Task, WebFetch, ...) out of the model's reach; this
    is the check that makes a REGRESSION of that option non-silent. A
    future options change, an ambient MCP server the CLI picks up, or a CLI
    update that re-adds a built-in would otherwise be translated straight
    into a genuine-looking tool.started/tool.result pair — which is exactly
    the failure tool_honesty_guard structurally cannot see, because the
    trace would corroborate the claim.

    `known_tool_names` holds the STRIPPED Empyralis tool names registered
    with the SDK MCP server this turn (run_claude_agent_sdk_turn always
    supplies it). Two conditions, and the strip_mcp_tool_prefix interplay
    is why both are needed rather than just the second:

      - The name must carry THIS server's `mcp__empyralis__` prefix. The
        CLI always presents SDK MCP tools that way, and this module already
        hard-depends on that convention elsewhere (`allowed_tools` is built
        from it), so requiring it costs nothing. Without this condition a
        CLI built-in that happened to share a bare name with an Empyralis
        tool would pass the membership test below, since
        strip_mcp_tool_prefix deliberately passes an unprefixed name
        through unchanged.
      - The stripped remainder must be in `known_tool_names`. This is what
        catches a FOREIGN MCP server: `mcp__github__create_issue` is not
        this server's prefix, so strip_mcp_tool_prefix leaves it whole and
        no whole `mcp__*` string is ever a registered Empyralis tool name.

    `known_tool_names=None` means "not configured" and returns True for any
    non-empty name — the pure-translation mode translate_sdk_message's own
    unit tests use, where there is no registered tool set to check against.
    Production never takes that branch; RunClaudeAgentSdkTurnForeignToolTests
    pins that run_claude_agent_sdk_turn always populates it.
    """
    token = str(raw_name or "").strip()
    if not token:
        return False
    if known_tool_names is None:
        return True
    if not token.startswith(_MCP_TOOL_PREFIX):
        return False
    return strip_mcp_tool_prefix(token) in known_tool_names


def is_recognized_meta_tool_call(
    raw_name: str, meta_tools_enabled: Optional[frozenset] = None
) -> bool:
    """Is `raw_name` one of the CLI's own built-in meta-tools (Agent/Skill —
    see _META_TOOL_EVENT_TYPES) that THIS turn deliberately re-opened?

    Deliberately a SEPARATE check from is_registered_empyralis_tool rather
    than folded into it: Agent/Skill are not Empyralis tools (nothing here
    registered them with the SDK MCP server, and they carry no mcp__
    prefix), so widening that function's own "is this a REGISTERED EMPYRALIS
    tool" contract to also mean "or one of the CLI's own reopened built-ins"
    would blur a distinction translate_sdk_message's caller needs kept sharp
    (see _META_TOOL_EVENT_TYPES' own docstring on why the trace event type
    must differ).

    `meta_tools_enabled` mirrors is_registered_empyralis_tool's own
    known_tool_names contract on purpose, DEFENCE IN DEPTH included: `None`
    ("not configured") is permissive — the pure-translation unit-test mode,
    matching known_tool_names=None's own meaning — but production
    (run_claude_agent_sdk_turn) always passes a real frozenset, typically
    `frozenset({"Skill"})` on a turn with enabled skills configured and
    `frozenset()` (empty — recognizes NOTHING) otherwise. That empty-set
    default is what keeps this a narrow allowlist rather than a blanket
    reopening: if "Skill"/"Agent" ever reached the model on a turn that
    never asked ClaudeAgentOptions.tools to include it — a future options
    regression, exactly the failure class is_registered_empyralis_tool's own
    docstring describes — this returns False for it, and it falls through
    to the EXISTING foreign-tool guard below instead of being silently
    trusted just because its name matches a known meta-tool.
    """
    token = str(raw_name or "").strip()
    if token not in _META_TOOL_EVENT_TYPES:
        return False
    if meta_tools_enabled is None:
        return True
    return token in meta_tools_enabled


def _safe_parse_tool_name(tool_name: str) -> Tuple[str, str]:
    try:
        connector_id, action_id = direct_chat_operator_binding_service.parse_tool_name(tool_name)
        return connector_id, action_id
    except Exception:
        return "", ""


def _tool_result_block_text(content: Any) -> str:
    """Flatten a ToolResultBlock.content value (str | list[dict] | None —
    see claude_agent_sdk.types.ToolResultBlock) into plain text, the same
    shape build_direct_tool_trace_metadata's `result_text` parameter
    expects (it already only ever sees plain text from the legacy path's
    own tool_result_for_context)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _humanize_tool_progress(tool_name: str) -> str:
    # direct_chat_generation_service._humanize_tool_progress is a plain
    # module-level function (no DI needed) — imported lazily here to avoid
    # importing that (large) module's own heavy import chain at THIS
    # module's import time, since sage_agent_runtime_service.py imports
    # this module unconditionally regardless of whether the flag is ever
    # set.
    from server_modules import direct_chat_generation_service

    return direct_chat_generation_service._humanize_tool_progress(tool_name)


def _tool_timeout_seconds(connector_id: str, action_id: str) -> float:
    from server_modules import direct_chat_generation_service

    return direct_chat_generation_service._tool_timeout_seconds(connector_id, action_id)


@dataclass
class TranslationState:
    """Mutable state threaded across one turn's SDK message stream. A tool
    result (UserMessage -> ToolResultBlock) carries only a `tool_use_id` —
    the SDK never repeats the tool's name or input on that message — so the
    tool.started -> tool.result correlation this module needs (same
    connector_id/action_id/capability_id on both trace events, same
    tool_call_id key the collector buckets by) requires remembering what
    the matching ToolUseBlock (AssistantMessage, seen earlier in the
    stream) said."""

    tool_use_names: Dict[str, str] = field(default_factory=dict)
    tool_use_inputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reply_text_parts: List[str] = field(default_factory=list)
    # The STRIPPED Empyralis tool names registered with the SDK MCP server
    # for this turn — see is_registered_empyralis_tool, which this is fed
    # to. None ("not configured") disables the check; run_claude_agent_sdk_
    # turn always sets it, so production is never in that mode.
    known_tool_names: Optional[frozenset] = None
    # tool_use_ids already rejected as foreign, so the matching
    # ToolResultBlock (which carries only the id, never the name) can be
    # dropped too instead of being recorded against an empty tool name.
    foreign_tool_use_ids: set = field(default_factory=set)
    # Was this turn actually served by Anthropic's own API? Gates whether
    # ResultMessage.total_cost_usd — a CLIENT-SIDE number the CLI computes
    # from Anthropic's price table regardless of where it was pointed — may
    # be reported as this turn's cost. See turn_is_served_by_anthropic,
    # which run_claude_agent_sdk_turn always resolves this from.
    #
    # None means "nobody established where this turn went", and is treated
    # exactly like False: the cost is omitted. That default is deliberately
    # the FAIL-SAFE direction (unlike known_tool_names above, whose None
    # means "no allowlist configured, don't check") — an unattributable
    # number must not be emitted just because no one said otherwise.
    served_by_anthropic: Optional[bool] = None
    # Set when the CLI hands over an assistant message that is a FAILURE
    # NOTICE rather than the model's answer (AssistantMessage.error set,
    # and/or model="<synthetic>"). Its prose is API-error text; see the
    # guard in translate_sdk_message's AssistantMessage branch, and the use
    # in the ResultMessage branch that keeps the same prose from arriving as
    # the reply by the other door (ResultMessage.result).
    saw_provider_error: bool = False
    # The set of meta-tool names (bare "Agent"/"Skill" — they carry no
    # mcp__ prefix, see is_recognized_meta_tool_call) THIS turn deliberately
    # re-opened. None ("not configured") is the permissive
    # pure-translation-unit-test default; run_claude_agent_sdk_turn always
    # supplies a real frozenset (empty when no skills are configured this
    # turn) — see is_recognized_meta_tool_call's own docstring for why that
    # default matters (defence in depth, symmetric with known_tool_names).
    meta_tools_enabled: Optional[frozenset] = None
    # tool_use_id -> the honest trace event type it was announced under
    # (_META_TOOL_EVENT_TYPES' values), so the matching ToolResultBlock (see
    # the UserMessage branch) reports its result under the SAME event type
    # instead of falling into the orphan-result or foreign-tool branches —
    # neither of which this is: a deliberately-reopened meta-tool call is
    # neither unattributed nor foreign.
    meta_tool_use_ids: Dict[str, str] = field(default_factory=dict)


def translate_sdk_message(
    message: Any,
    *,
    state: TranslationState,
    trace_context: Optional[agent_trace_service.TraceContext],
) -> List[Dict[str, Any]]:
    """Translate ONE claude_agent_sdk message object into zero or more of
    Empyralis's event dicts — the exact {"type": "tool_progress" | "final"
    | "trace", ...} shapes sage_agent_runtime_service._collect_sage_
    operator_loop_v3_events() already parses (only "tool.started",
    "tool.result", "search.query", "trace.failed" and "plan.item.updated"
    are consumed under "trace" — see that function).

    A function of (message, state, trace_context) alone: no DB, no network,
    no tool execution (it does log a warning when it rejects a tool-shaped
    message — see the foreign-tool guard below). Tool execution happens
    inside the @tool handlers
    build_sdk_tools() registers, which the SDK subprocess calls BEFORE it
    ever emits the UserMessage/ToolResultBlock this function reads back —
    by the time that message arrives, the real tool call (governance,
    redaction, billing, the lot) has already happened.

    Matched by class NAME (not isinstance) against claude_agent_sdk.types
    so this stays importable, and unit-testable with plain SimpleNamespace
    stand-ins, without a hard import of claude_agent_sdk at module load —
    every real caller passes real SDK dataclass instances, whose type name
    is exactly what's checked here.
    """
    events: List[Dict[str, Any]] = []

    def _envelope(
        event_type: str,
        data: Dict[str, Any],
        *,
        tool_call_id: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        envelope = agent_trace_service.build_ephemeral_envelope(
            trace_context,
            event_type,
            data,
            tool_call_id=tool_call_id,
            item_id=item_id,
        )
        if envelope is None:
            return None
        return {"type": "trace", "payload": envelope}

    cls_name = type(message).__name__

    if cls_name == "AssistantMessage":
        # An AssistantMessage is not automatically the agent speaking. The
        # SDK carries a typed `error` field on it (claude_agent_sdk.types.
        # AssistantMessage.error: AssistantMessageError | None, one of
        # "authentication_failed" | "billing_error" | "rate_limit" |
        # "invalid_request" | "server_error" | "unknown"), and the CLI
        # stamps model="<synthetic>" on messages it fabricated itself. Both
        # arrive carrying a TextBlock whose text is API-ERROR PROSE.
        #
        # That field was never read here. The prose therefore went straight
        # into state.reply_text_parts alongside genuine model output and,
        # whenever ResultMessage.result was empty, became the reply the
        # customer was shown as their agent's answer — the provider's error
        # page, in the agent's voice, with no indication anything failed.
        #
        # Routed to trace.failed instead: it lands in the collector's
        # `blocked_tools` (never `tool_calls`), so the turn is recorded as
        # having failed, which is what happened.
        error_kind = str(getattr(message, "error", "") or "").strip()
        model_name = str(getattr(message, "model", "") or "").strip()
        if error_kind or model_name == _SYNTHETIC_ASSISTANT_MODEL:
            state.saw_provider_error = True
            notice = " ".join(
                str(getattr(block, "text", "") or "").strip()
                for block in list(getattr(message, "content", None) or [])
                if type(block).__name__ == "TextBlock"
            ).strip()
            LOGGER.warning(
                "claude_agent_sdk_bridge: provider error message (error=%r model=%r) — "
                "not recording its text as the agent's reply.",
                error_kind[:100], model_name[:100],
            )
            # redact_text, not the raw string: this text comes from an
            # upstream error path Empyralis does not control, and it is
            # about to be persisted on a trace event and rendered in the
            # Work tab. Truncated for the same reason the foreign-tool
            # guard truncates its name.
            detail = secret_redaction_service.redact_text(notice)[:500]
            failed_event = _envelope(
                "trace.failed",
                {
                    "code": _PROVIDER_GENERATION_FAILED_CODE,
                    "message": (
                        f"The model provider returned an error ({error_kind or 'unspecified'})"
                        + (f": {detail}" if detail else ".")
                    ),
                },
            )
            if failed_event is not None:
                events.append(failed_event)
            # Nothing else on this message is the agent's work either — a
            # fabricated failure notice carries no tool call worth
            # recording, and inventing one would be the same untruth in a
            # different column of the ledger.
            return events
        for block in list(getattr(message, "content", None) or []):
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = str(getattr(block, "text", "") or "")
                if text:
                    state.reply_text_parts.append(text)
                continue
            if block_type != "ToolUseBlock":
                # ThinkingBlock, ServerToolUseBlock, ServerToolResultBlock:
                # none map to a type _collect_sage_operator_loop_v3_events
                # consumes (reasoning.summary.delta is EPHEMERAL_TRACE_EVENT_
                # TYPES-only even on the legacy path — see agent_trace_
                # service.py) — dropped, same as that path.
                continue
            tool_use_id = str(getattr(block, "id", "") or "")
            raw_name = str(getattr(block, "name", "") or "")
            raw_input = getattr(block, "input", None)
            tool_input = dict(raw_input) if isinstance(raw_input, dict) else {}
            if is_recognized_meta_tool_call(raw_name, state.meta_tools_enabled):
                # A deliberately-reopened CLI built-in (Agent/Skill), not an
                # Empyralis tool and not a foreign one either — see
                # _META_TOOL_EVENT_TYPES. Its own honest event type, tracked
                # by tool_use_id so the matching ToolResultBlock (UserMessage
                # branch below) reports under the SAME type instead of
                # falling into the orphan-result branch.
                meta_event_type = _META_TOOL_EVENT_TYPES[raw_name]
                state.meta_tool_use_ids[tool_use_id] = meta_event_type
                meta_event = _envelope(
                    meta_event_type,
                    {
                        "phase": "started",
                        "tool_name": raw_name,
                        "args_preview": secret_redaction_service.sanitize_mapping(tool_input),
                    },
                    tool_call_id=tool_use_id,
                )
                if meta_event is not None:
                    events.append(meta_event)
                continue
            if not is_registered_empyralis_tool(raw_name, state.known_tool_names):
                # NOT Empyralis work. Emitting the usual tool.started/
                # tool.result pair here is precisely how "Task #1 created
                # successfully" from the CLI's own built-in TaskCreate
                # became a customer-visible record of work that never
                # touched the product. Surface it as an anomaly instead:
                # trace.failed lands in the collector's `blocked_tools`,
                # never its `tool_calls`, so (a) the Work tab shows a
                # blocked entry rather than a completed one and (b)
                # tool_honesty_guard sees NO corroborating tool call and
                # can catch a reply that claims the work was done.
                state.foreign_tool_use_ids.add(tool_use_id)
                LOGGER.warning(
                    "claude_agent_sdk_bridge: refusing to record non-Empyralis tool %r "
                    "(tool_use_id=%s) as work — check ClaudeAgentOptions.tools/mcp_servers.",
                    raw_name[:200], tool_use_id,
                )
                foreign_event = _envelope(
                    "trace.failed",
                    {
                        "code": _FOREIGN_TOOL_TRACE_CODE,
                        "message": (
                            f"Ignored a call to '{raw_name[:200]}': that is not a tool "
                            "Empyralis registered for this turn, so nothing it reports "
                            "is a record of work in this product."
                        ),
                    },
                    tool_call_id=tool_use_id,
                )
                if foreign_event is not None:
                    events.append(foreign_event)
                continue
            tool_name = strip_mcp_tool_prefix(raw_name)
            state.tool_use_names[tool_use_id] = tool_name
            state.tool_use_inputs[tool_use_id] = tool_input
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            trace_meta = direct_tool_execution_service.build_direct_tool_trace_metadata(
                connector_id, action_id, tool_input,
            )
            started_data = {
                "tool_name": tool_name,
                "capability_id": trace_meta.get("capability_id"),
                "connector_id": connector_id or None,
                "execution_environment": trace_meta.get("execution_environment"),
                "args_preview": secret_redaction_service.sanitize_mapping(tool_input),
            }
            started_event = _envelope("tool.started", started_data, tool_call_id=tool_use_id)
            if started_event is not None:
                events.append(started_event)
            events.append({
                "type": "tool_progress",
                "tool": tool_name,
                "message": _humanize_tool_progress(tool_name),
            })
            search_query = str(trace_meta.get("search_query") or "").strip()
            if search_query:
                query_event = _envelope(
                    "search.query",
                    {"provider": connector_id or "web", "query": search_query, "filters": {}},
                    tool_call_id=tool_use_id,
                )
                if query_event is not None:
                    events.append(query_event)
        return events

    if cls_name == "UserMessage":
        content = getattr(message, "content", None)
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if type(block).__name__ != "ToolResultBlock":
                continue
            tool_use_id = str(getattr(block, "tool_use_id", "") or "")
            if tool_use_id in state.foreign_tool_use_ids:
                # Already surfaced as a trace.failed anomaly when the
                # ToolUseBlock came through. Whatever this result says, it
                # is another system's bookkeeping — not a tool.result.
                continue
            if tool_use_id in state.meta_tool_use_ids:
                # The result half of a deliberately-reopened meta-tool call
                # (see the AssistantMessage branch above) — reported under
                # the SAME honest event type as the "started" half, never as
                # a plain tool.result (that bucket is `tool_calls`) and
                # never as an orphan (it IS attributed — to the meta-tool
                # event, not to Empyralis work).
                meta_event_type = state.meta_tool_use_ids[tool_use_id]
                is_error = bool(getattr(block, "is_error", False) or False)
                result_text = _tool_result_block_text(getattr(block, "content", None))
                # redact_text: this is the CLI's own output (which subagent
                # ran, which skill fired, what it said) — not vetted the way
                # an Empyralis connector's own result_summary already is.
                detail = secret_redaction_service.redact_text(result_text)[:500]
                meta_result_event = _envelope(
                    meta_event_type,
                    {"phase": "result", "status": "failed" if is_error else "ok", "summary": detail},
                    tool_call_id=tool_use_id,
                )
                if meta_result_event is not None:
                    events.append(meta_result_event)
                continue
            if tool_use_id not in state.tool_use_names:
                # A result for a call this stream never announced. The
                # collector's _tool_entry would invent an entry named
                # "direct_tool" and mark it completed — a green row in the
                # work ledger for a call nothing here can even name.
                LOGGER.warning(
                    "claude_agent_sdk_bridge: tool result for unknown tool_use_id=%s — "
                    "not recording it as work.", tool_use_id,
                )
                orphan_event = _envelope(
                    "trace.failed",
                    {
                        "code": _ORPHAN_TOOL_RESULT_TRACE_CODE,
                        "message": (
                            "Ignored a tool result with no matching tool call in this "
                            "turn — it cannot be attributed to work Empyralis performed."
                        ),
                    },
                    tool_call_id=tool_use_id,
                )
                if orphan_event is not None:
                    events.append(orphan_event)
                continue
            tool_name = state.tool_use_names.get(tool_use_id, "")
            tool_input = state.tool_use_inputs.get(tool_use_id, {})
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            result_text = _tool_result_block_text(getattr(block, "content", None))
            trace_meta = direct_tool_execution_service.build_direct_tool_trace_metadata(
                connector_id, action_id, tool_input, result_text=result_text,
            )
            is_error = bool(getattr(block, "is_error", False) or False)
            result_summary = str(trace_meta.get("result_summary") or result_text or "").strip()
            result_data = {
                # "failed"/"ok" — the exact two tokens tool_result_status.
                # classify_tool_result treats as an explicit status verdict
                # (its _FAILURE_STATUS_TOKENS / _SUCCESS_STATUS_TOKENS sets),
                # matching the legacy producer (direct_chat_generation_
                # service.py's own tool.result data) token-for-token.
                "status": "failed" if is_error else "ok",
                "summary": result_summary,
                "execution_environment": trace_meta.get("execution_environment"),
            }
            result_event = _envelope("tool.result", result_data, tool_call_id=tool_use_id)
            if result_event is not None:
                events.append(result_event)
            plan_item_id = f"tool:{tool_use_id}"
            plan_event = _envelope(
                "plan.item.updated",
                {
                    "item_id": plan_item_id,
                    "status": "failed" if is_error else "done",
                    "summary": (
                        f"{tool_name} failed." if is_error else f"Completed {tool_name}."
                    ),
                },
                item_id=plan_item_id,
            )
            if plan_event is not None:
                events.append(plan_event)
        return events

    if cls_name == "ResultMessage":
        is_error = bool(getattr(message, "is_error", False) or False)
        result_text = getattr(message, "result", None)
        reply = str(result_text or "").strip() or "".join(state.reply_text_parts).strip()
        if is_error and state.saw_provider_error:
            # The other door to the same untruth the AssistantMessage guard
            # above closes. When this turn already produced a fabricated
            # provider-error message, ResultMessage.result is that same
            # API-error prose — and it would land here as `reply`, i.e. as
            # the customer's answer, on a turn that demonstrably failed.
            # Dropping it lets the runtime's own honest failure wording
            # stand instead (sage_agent_runtime_service._run_sage_action_
            # loop_v3 substitutes TOOLS_LIMITED_NO_REPLY when a turn has an
            # empty reply and a blocked entry, which the trace.failed
            # emitted above guarantees). Narrow on purpose: it needs BOTH a
            # seen provider-error message AND is_error, so an ordinary
            # error_max_turns turn still returns whatever real partial work
            # the model produced.
            reply = ""
        payload: Dict[str, Any] = {"reply": reply}
        # session_id is a required (non-Optional) field on every real
        # ResultMessage the SDK yields — carried through here so the caller
        # (sage_agent_runtime_service._run_sage_action_loop_v3) can persist
        # it against Empyralis's own thread identity and resume THIS
        # conversation on a later turn instead of re-folding full history
        # (see run_claude_agent_sdk_turn's resume_session_id parameter).
        session_id = str(getattr(message, "session_id", "") or "").strip()
        if session_id:
            payload["session_id"] = session_id
        if is_error:
            # subtype is NOT a failure taxonomy — see _NON_FAILURE_RESULT_
            # SUBTYPES. It is "success" on exactly the failures that matter
            # most (API/upstream), and this used to copy that word into the
            # persisted trace.failed row's `code`, which the Work tab then
            # renders as the blocked entry's name. Only a subtype that
            # actually names a failure ("error_max_turns", "error_during_
            # execution", ...) is trusted; everything else falls back.
            raw_subtype = str(getattr(message, "subtype", "") or "").strip()
            error_code = (
                raw_subtype
                if raw_subtype.lower() not in _NON_FAILURE_RESULT_SUBTYPES
                else _PROVIDER_GENERATION_FAILED_CODE
            )
            payload["error"] = error_code
            # api_error_status is the SDK's own honest detail for exactly
            # this case ("HTTP status code of the failing API call when
            # is_error is True and subtype is 'success'"), and its docstring
            # marks it safe to log (no message content). It goes in the
            # human-readable message, NOT the code: a per-status code would
            # be a taxonomy invented here and rendered at the customer.
            api_error_status = getattr(message, "api_error_status", None)
            detail = f"HTTP {api_error_status}" if isinstance(api_error_status, int) and api_error_status else ""
            failed_message = reply or detail or error_code
            if reply and detail:
                failed_message = f"{reply} ({detail})"
            failed_event = _envelope("trace.failed", {"code": error_code, "message": failed_message})
            if failed_event is not None:
                events.append(failed_event)
        usage = getattr(message, "usage", None)
        if isinstance(usage, dict):
            payload["usage"] = usage
        # total_cost_usd is computed CLIENT-SIDE by the `claude` CLI from
        # Anthropic's price table, whatever endpoint it was actually pointed
        # at. Copied through unconditionally, a DeepSeek-served turn reported
        # an Anthropic price for tokens Anthropic never served — a canned
        # local response was billed at $0.0033 in testing. Emitted only when
        # the turn provably went to Anthropic (turn_is_served_by_anthropic,
        # resolved once per turn in run_claude_agent_sdk_turn); otherwise the
        # key is OMITTED rather than zeroed or estimated, so no downstream
        # reader can mistake a guess for a measurement.
        total_cost_usd = getattr(message, "total_cost_usd", None)
        if total_cost_usd is not None and state.served_by_anthropic is True:
            payload["total_cost_usd"] = total_cost_usd
        # model_usage: per-model token + cost breakdown (ModelUsage,
        # types.py:1203), previously dropped entirely. Token counts
        # (inputTokens/outputTokens/cacheReadInputTokens/
        # cacheCreationInputTokens/webSearchRequests/contextWindow/
        # maxOutputTokens/canonicalModel/provider) are real measurements
        # regardless of which endpoint served the turn — the CLI reports
        # what it actually counted, not a price-table guess — so those
        # pass through unconditionally, same as `usage` above.
        #
        # costUSD is the ONE key in this dict computed client-side from
        # Anthropic's price table (identical mechanism to total_cost_usd
        # above), so it gets the identical honesty gate: stripped out
        # per-entry unless this turn provably went to Anthropic. Stripping
        # only the cost key (not the whole entry) keeps the token counts —
        # which ARE trustworthy — available even on a non-Anthropic turn,
        # rather than discarding real data to protect one untrustworthy
        # field.
        model_usage = getattr(message, "model_usage", None)
        if isinstance(model_usage, dict) and model_usage:
            cleaned_model_usage: Dict[str, Any] = {}
            for model_key, entry in model_usage.items():
                if not isinstance(entry, dict):
                    continue
                cleaned_entry = dict(entry)
                if state.served_by_anthropic is not True:
                    cleaned_entry.pop("costUSD", None)
                cleaned_model_usage[str(model_key)] = cleaned_entry
            if cleaned_model_usage:
                payload["model_usage"] = cleaned_model_usage
        events.append({"type": "final", "payload": payload})
        return events

    # SystemMessage / StreamEvent / RateLimitEvent: none of these map to a
    # type _collect_sage_operator_loop_v3_events consumes — dropped.
    return events


def build_sdk_tools(
    *,
    tool_defs: Sequence[Dict[str, Any]],
    generation_services: Any,
    workspace_id: str,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    credentials: Optional[Dict[str, Any]],
    reasoning_effort: str,
    session_ctx: Optional[Dict[str, Any]],
) -> List[Any]:
    """One in-process SDK MCP tool per Empyralis direct-chat tool
    definition, each dispatching to the EXISTING executor —
    generation_services.execute_single_direct_tool_call, the exact bound
    callable direct_chat_generation_service.py's own tool loop calls (see
    that module's `services.execute_single_direct_tool_call(...)` call
    site) — so governance, secret redaction, billing attribution and
    result classification all run unchanged. Nothing here reimplements a
    tool; this only adapts the calling convention (SDK args-in/content-out
    <-> Empyralis's tool_call-dict-in/str-out executor).

    `_UNSUPPORTED_TOOL_NAMES` are silently skipped — see module docstring.
    """
    from claude_agent_sdk import tool as sdk_tool  # local: only needed on this engine's path

    index_counter = itertools.count(1)
    sdk_tools: List[Any] = []
    for tool_def in tool_defs:
        if not isinstance(tool_def, dict):
            continue
        name = str(tool_def.get("name") or "").strip()
        if not name or name in _UNSUPPORTED_TOOL_NAMES:
            continue
        description = str(tool_def.get("description") or name).strip() or name
        parameters = tool_def.get("parameters")
        input_schema: Dict[str, Any] = (
            dict(parameters) if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        )

        def _make_handler(tool_name: str) -> Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]:
            connector_id, action_id = _safe_parse_tool_name(tool_name)
            timeout_s = _tool_timeout_seconds(connector_id, action_id)

            async def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
                call_index = next(index_counter)
                tool_call = {"name": tool_name, "arguments": dict(args or {})}
                try:
                    result_text = await asyncio.wait_for(
                        asyncio.to_thread(
                            generation_services.execute_single_direct_tool_call,
                            tool_call=tool_call,
                            workspace_id=workspace_id,
                            thread_id=thread_id,
                            index=call_index,
                            provider=provider,
                            model=model,
                            credentials=credentials if isinstance(credentials, dict) else None,
                            reasoning_effort=reasoning_effort or "",
                            session_ctx=session_ctx,
                        ),
                        timeout=timeout_s,
                    )
                except asyncio.TimeoutError:
                    return {
                        "content": [{
                            "type": "text",
                            "text": (
                                f"The tool '{tool_name}' timed out after {timeout_s:.0f}s. "
                                "Try a different approach — a smaller scope, a different tool, "
                                "or ask the user for more specific guidance."
                            ),
                        }],
                        "is_error": True,
                    }
                except Exception as exc:  # governance denial, broker block, etc. — surface
                    # to the model as a tool-level error rather than crashing the
                    # whole turn. (This is one deliberate behavioral difference
                    # from the legacy path, where such an exception propagates out
                    # of the ThreadPoolExecutor future and can fail the turn — see
                    # the MAN-310 report.)
                    LOGGER.warning(
                        "claude_agent_sdk_bridge: tool %s failed: %s", tool_name, exc,
                    )
                    return {
                        "content": [{"type": "text", "text": str(exc) or "Tool call failed."}],
                        "is_error": True,
                    }
                return {"content": [{"type": "text", "text": str(result_text or "")}]}

            return _handler

        sdk_tools.append(sdk_tool(name, description, input_schema)(_make_handler(name)))
    return sdk_tools


# MAN-310 skills-delivery: minimal plugin.json shape, matching a real
# installed plugin on this machine (~/.claude/plugins/cache/openai-codex/
# codex/1.0.0/.claude-plugin/plugin.json — {"name", "description", "author"})
# rather than guessed. `name` is the plugin-qualifier prefix the CLI shows
# discovered skills under (e.g. that plugin's own skills list as "codex:
# <skill-name>"); fixed here since every turn only ever builds ONE plugin
# for ONE agent install's own skills, never a marketplace of several.
_SKILLS_PLUGIN_NAME = "empyralis-agent-skills"
_SKILL_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9]+")


def _slugify_skill_name(name: str, fallback: str) -> str:
    """A filesystem- and SKILL.md-directory-safe slug for a skill's display
    name. Never empty (falls back to `fallback`, e.g. "skill-3") — an empty
    directory name would either fail os.makedirs or, worse, collapse to the
    plugin root itself."""
    slug = _SKILL_SLUG_INVALID_CHARS_RE.sub("-", str(name or "").strip().lower()).strip("-")
    return slug or fallback


def _yaml_quoted_scalar(value: str) -> str:
    """A double-quoted YAML scalar safe for arbitrary owner-authored text
    (colons, quotes, newlines) inside a SKILL.md frontmatter block. Not a
    general YAML encoder — just enough escaping (backslash, double-quote,
    newline) for the two fields (name/description) this module ever writes
    into frontmatter, both single-line by the time they reach here."""
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def build_skills_plugin_dir(skills: Optional[Sequence[Dict[str, Any]]]) -> str:
    """Materialize this turn's ENABLED, kind="skill" entries as real
    SKILL.md files under a fresh temp directory shaped like a Claude Code
    plugin, or "" when there is nothing to deliver (no skills configured, or
    none of them are enabled skill-kind entries with a name+body) — the "no
    skills configured" case is what keeps an agent with none behaving byte-
    for-byte identically to before this function existed (see
    run_claude_agent_sdk_turn, which only sets tools/plugins/allowed_tools
    for the Skill built-in when this returns non-empty).

    Lifecycle discipline mirrors config_dir exactly, on purpose (same
    hazard, same fix): tempfile.mkdtemp() here, destroyed in run_claude_
    agent_sdk_turn's own `finally` block, never reused across turns or
    tenants. Empyralis's own code is the ONLY writer of this directory —
    it never contains a hooks/hooks.json, because nothing here ever creates
    a `hooks/` subdirectory at all. Hooks execute arbitrary shell commands
    on tool events; a tenant-authored skill's SKILL.md body is inert
    Markdown text a model reads, never something the CLI executes — that
    distinction is the whole reason this is safe to build from untrusted
    per-workspace input in the first place.

    Every skill name is validated at fleet_tools._normalize_skills_patch
    (save time) — this reads already-clean storage (fleet_tools.
    resolve_agent_skills), so it degrades a malformed record (missing
    name/body) by skipping it rather than raising, matching resolve_agent_
    skills' own fail-open-by-omission behavior.
    """
    enabled = [
        skill for skill in (skills or [])
        if isinstance(skill, dict)
        and skill.get("enabled")
        and str(skill.get("kind") or "skill").strip().lower() == "skill"
        and str(skill.get("name") or "").strip()
        and str(skill.get("body") or "").strip()
    ]
    if not enabled:
        return ""

    plugin_dir = tempfile.mkdtemp(prefix="empyralis-claude-skills-")
    plugin_meta_dir = os.path.join(plugin_dir, ".claude-plugin")
    os.makedirs(plugin_meta_dir, exist_ok=True)
    with open(os.path.join(plugin_meta_dir, "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "name": _SKILLS_PLUGIN_NAME,
                "description": "Workspace-authored skills for this Empyralis agent.",
            },
            fh,
        )

    used_slugs: set = set()
    for index, skill in enumerate(enabled, start=1):
        raw_name = str(skill.get("name") or "").strip()
        slug = _slugify_skill_name(raw_name, f"skill-{index}")
        # Two differently-named skills can slugify to the same directory
        # name (e.g. "My Skill!" and "my_skill") — de-duplicate rather than
        # let the second silently overwrite the first's SKILL.md.
        base_slug, suffix = slug, 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)

        skill_dir = os.path.join(plugin_dir, "skills", slug)
        os.makedirs(skill_dir, exist_ok=True)
        description = str(skill.get("description") or raw_name).strip().replace("\n", " ")
        body = str(skill.get("body") or "").strip()
        frontmatter = (
            "---\n"
            f"name: {_yaml_quoted_scalar(raw_name or slug)}\n"
            f"description: {_yaml_quoted_scalar(description)}\n"
            "---\n\n"
        )
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(frontmatter + body + "\n")

    return plugin_dir


def render_prompt(message: str, prior_messages: Optional[List[Dict[str, Any]]]) -> str:
    """Fold prior turns into a single prompt string for query()'s one-shot
    `prompt` mode.

    MAN-310 Phase 2: this is now the FALLBACK path, not the only path. When
    the caller has a resumable SDK session for this conversation (see
    run_claude_agent_sdk_turn's resume_session_id parameter), the session
    itself already carries the history — folding it into the prompt text
    AGAIN would be sending Empyralis's memory of the conversation and the
    SDK's own resumed memory of the same conversation at once, which is
    redundant at best (wasted tokens, a broken prompt-cache prefix on every
    turn since the folded text keeps growing) and confusing at worst (the
    model sees its own prior turns twice, once live-in-context from the
    resumed session and once again as inert transcript text). This function
    still runs unconditionally on a fresh/unresumable session (no session
    yet, a stale one, or a resume attempt that failed) — see
    run_claude_agent_sdk_turn's fallback branch — where it's exactly as
    necessary as before: query()'s one-shot `prompt` mode has no OTHER way
    to see anything before this turn.
    """
    prior = prior_messages or []
    if not prior:
        return message
    lines: List[str] = []
    for entry in prior:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip() or "user"
        content = entry.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str) if content else ""
        text = str(text or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return message
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nuser: {message}"


async def run_claude_agent_sdk_turn(
    *,
    message: str,
    system_prompt: str,
    prior_messages: Optional[List[Dict[str, Any]]],
    tool_defs: Sequence[Dict[str, Any]],
    generation_services: Any,
    workspace_id: str,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    credentials: Optional[Dict[str, Any]],
    reasoning_effort: str = "",
    session_ctx: Optional[Dict[str, Any]] = None,
    trace_context: Optional[agent_trace_service.TraceContext] = None,
    max_turns: int = 5,
    anthropic_api_key: str = "",
    anthropic_base_url: str = "",
    # MAN-310 Phase 2: a claude_agent_sdk session id previously captured for
    # THIS conversation (see sage_agent_runtime_service._sdk_engine_session_
    # lookup, which is also what verifies it's still safe to resume before
    # ever passing it here — this function trusts its caller on that). When
    # set, this turn resumes that session (ClaudeAgentOptions.resume) and
    # sends ONLY the new message as the prompt — the resumed session already
    # has everything render_prompt would otherwise fold in, and prompt
    # caching only pays off when the sent prefix doesn't change turn to
    # turn. Empty (default) — every existing caller, and any caller with no
    # resumable session — takes the exact pre-Phase-2 path: render_prompt
    # folds prior_messages in every time, byte-for-byte unchanged.
    resume_session_id: str = "",
    # MAN-310 skills-delivery: this agent install's configured skills — the
    # SAME shape fleet_tools.resolve_agent_skills(..., enabled_only=True)
    # returns ({id, name, description, body, kind, enabled}, kind="skill"
    # only for now — see fleet_tools._VALID_SKILL_KINDS). None/empty (every
    # existing caller, and any agent with none configured) means this turn
    # behaves BYTE-FOR-BYTE identically to before this parameter existed —
    # see build_skills_plugin_dir, which returns "" for that input and is
    # the one thing every skills-shaped change below is gated on.
    skills: Optional[Sequence[Dict[str, Any]]] = None,
    # Per-run spend ceiling parity: direct_chat_generation_service's legacy
    # loop enforces one on every turn (_resolve_run_cost_ceiling_usd —
    # metadata["run_cost_ceiling_usd"] override, else config_defaults_
    # service.default_run_cost_ceiling_usd()); this bridge previously set
    # NOTHING here, so an SDK-engine turn had no spend ceiling at all — a
    # runaway loop (or an adversarial prompt driving repeated expensive tool
    # calls) could spend without limit. None (every existing caller) means
    # "use the platform default", never "unbounded" — mirrors the legacy
    # function's own null-coalescing default so both engines share one
    # spend-safety floor. A caller may still pass an explicit positive
    # override (e.g. a resolved per-agent ceiling) the same way metadata
    # carries one on the legacy path.
    max_budget_usd: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Drive one turn through claude_agent_sdk, translating every yielded
    message into Empyralis's event dicts. Returns the SAME list[dict] shape
    sage_agent_runtime_service._collect_sage_operator_loop_v3_events already
    parses — this is the function server_modules/sage_agent_runtime_
    service.py's _collect_stream_events calls when a turn's engine_options
    select ENGINE_ID.

    Requires the `claude` CLI (Node.js + Claude Code) on PATH — the SDK's
    only shipped Transport spawns it as a subprocess (see this module's
    docstring and the MAN-310 spike). Requires an ANTHROPIC_AUTH_TOKEN (or
    an equivalent credential for whatever ANTHROPIC_BASE_URL is
    configured) — see resolve_sdk_process_env.

    Isolation: this spawns exactly ONE fresh `claude` subprocess for this
    one turn — never a pooled/reused warm process across turns or tenants,
    since the CLI holds process-global in-memory auth/client state that
    would otherwise bleed between tenants. It also allocates a brand-new,
    never-logged-in-to CLAUDE_CONFIG_DIR (tempfile.mkdtemp, destroyed in the
    `finally` below the moment this turn ends) rather than reusing whatever
    config dir the CLI would default to — on Linux (how this deploys) the
    entire credential store is one file under that directory, so a fresh
    directory is provably clean of any other tenant's or the operator's own
    `claude /login` session. See resolve_sdk_process_env's docstring for the
    companion fix this pairs with (blanking every credential-shaped env key
    the SDK could otherwise merge in from Empyralis's own process env).

    Engine: claude_agent_sdk.ClaudeSDKClient (connect -> query ->
    receive_response -> disconnect), not the simpler one-shot query()
    function this module used before. The two are otherwise equivalent for
    this call site — same one-subprocess-per-call lifecycle, same
    ClaudeAgentOptions, same message stream, terminating after the same
    single ResultMessage — but only ClaudeSDKClient exposes
    get_context_usage() (query()'s InternalClient has no such method), and
    that is the whole reason for the swap: see _run_via_client below, which
    calls it, best-effort, once the message loop finishes.
    """
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server

    usable_tool_defs = [
        tool_def
        for tool_def in tool_defs
        if isinstance(tool_def, dict) and str(tool_def.get("name") or "").strip() not in _UNSUPPORTED_TOOL_NAMES
    ]
    sdk_tools = build_sdk_tools(
        tool_defs=usable_tool_defs,
        generation_services=generation_services,
        workspace_id=workspace_id,
        thread_id=thread_id,
        provider=provider,
        model=model,
        credentials=credentials,
        reasoning_effort=reasoning_effort,
        session_ctx=session_ctx,
    )
    mcp_server = create_sdk_mcp_server(name=_MCP_SERVER_NAME, tools=sdk_tools)
    allowed_tools = [f"{_MCP_TOOL_PREFIX}{tool_def.get('name')}" for tool_def in usable_tool_defs]
    # The one authoritative answer to "is this tool call Empyralis work?" —
    # the same list allowed_tools is built from, minus the MCP prefix. Fed
    # to every TranslationState this turn creates; see is_registered_
    # empyralis_tool.
    known_tool_names = frozenset(
        str(tool_def.get("name") or "").strip() for tool_def in usable_tool_defs
    ) - {""}
    # MAN-310 skills-delivery: "" (no enabled skill-kind entries) is the
    # SAME as this parameter never existing — see build_skills_plugin_dir's
    # own docstring. Only when it returns a real path does anything below
    # touch tools/allowed_tools/plugins/meta_tools_enabled at all.
    skills_plugin_dir = build_skills_plugin_dir(skills)
    if skills_plugin_dir:
        # Pre-approved, the same way Empyralis's own mcp__empyralis__* tools
        # already are above — this runs headless, with no human available to
        # answer a permission prompt, so an un-approved "Skill" call would
        # simply hang rather than ever reach the model's answer.
        allowed_tools = allowed_tools + ["Skill"]
    # The one authoritative answer to "is this tool call a deliberately-
    # reopened meta-tool?" for is_recognized_meta_tool_call — empty
    # (recognizes NOTHING) unless this turn actually asked
    # ClaudeAgentOptions.tools to include "Skill". Agent is never in this
    # set yet: no agents= subagent-shaped content is wired this pass (see
    # this module's MAN-310 skills-delivery docstring note), so the
    # translate_sdk_message allowlist recognizes "Agent" in the abstract
    # (_META_TOOL_EVENT_TYPES) without any turn ever actually reopening it.
    meta_tools_enabled = frozenset({"Skill"}) if skills_plugin_dir else frozenset()
    # Resolved ONCE, from the same inputs the subprocess env is built from,
    # and fed to every TranslationState this turn creates (including the
    # resume-fallback retry below). This is the only thing that lets
    # translate_sdk_message report ResultMessage.total_cost_usd — see
    # turn_is_served_by_anthropic for why a cost from a non-Anthropic turn
    # is fiction rather than an approximation.
    served_by_anthropic = turn_is_served_by_anthropic(
        provider=provider or "",
        anthropic_base_url=anthropic_base_url,
        credentials=credentials,
    )

    # Resolved ONCE, same "explicit override else platform default" shape as
    # direct_chat_generation_service._resolve_run_cost_ceiling_usd — never
    # None, never <= 0, so every SDK-engine turn gets a real ceiling even
    # when no caller has opinions about one yet.
    effective_max_budget_usd = (
        max_budget_usd if isinstance(max_budget_usd, (int, float)) and max_budget_usd > 0
        else config_defaults_service.default_run_cost_ceiling_usd()
    )

    config_dir = tempfile.mkdtemp(prefix="empyralis-claude-sdk-")
    # Every opaque adapter-routed credential token minted for this turn
    # (_build_options may run more than once — the resume-fallback retry
    # below calls it a second time — each call mints its own token if the
    # provider is adapter-routed, and every one of them must be cleared, not
    # just the last). Cleared unconditionally in the finally block below.
    minted_adapter_tokens: List[str] = []

    try:
        def _build_options(*, resume: str) -> Any:
            turn_env = resolve_sdk_process_env(
                credentials=credentials,
                anthropic_api_key=anthropic_api_key,
                anthropic_base_url=anthropic_base_url,
                config_dir=config_dir,
                provider=provider or "",
                model=model or "",
            )
            minted_token = turn_env.get("ANTHROPIC_AUTH_TOKEN") or ""
            if minted_token and openai_compat_adapter.is_adapter_routed_provider(provider):
                minted_adapter_tokens.append(minted_token)
            return ClaudeAgentOptions(
                system_prompt=system_prompt or None,
                mcp_servers={_MCP_SERVER_NAME: mcp_server},
                allowed_tools=allowed_tools,
                # Drop the CLI's OWN built-in toolset (--tools "") so the only
                # tools reachable are Empyralis's, served over the in-process
                # MCP server above. allowed_tools alone does NOT do this:
                # `tools` defaults to the full claude_code preset, leaving
                # TaskCreate/TodoWrite/Read/Write/Bash callable.
                #
                # Caught by driving a real product turn: asked to create a
                # task, the model called the CLI's built-in TaskCreate instead
                # of Empyralis's project_task__create. It returned "Task #1
                # created successfully", Empyralis translated that into a
                # genuine tool.started/tool.result pair, and the customer was
                # told the task existed — while project_tasks stayed empty.
                # tool_honesty_guard cannot catch this: a real tool really was
                # called and really did succeed, just in the CLI's own
                # bookkeeping instead of the product's. Work that never
                # happened must never be reportable as done.
                #
                # MAN-310 skills-delivery: the ONE narrow, named exception —
                # "Skill" (never the rest of the claude_code preset, never
                # "Agent" — no agents= subagent-shaped content is wired this
                # pass) is added back ONLY when skills_plugin_dir is non-
                # empty, i.e. only when THIS agent install has at least one
                # enabled skill configured (fleet_tools.resolve_agent_
                # skills). An agent with none configured gets tools=[]
                # unchanged — byte-for-byte the pre-existing behavior.
                tools=(["Skill"] if skills_plugin_dir else []),
                # Only the MCP server built above. Without this the CLI
                # ALSO loads whatever MCP configuration it finds ambiently
                # — a project .mcp.json next to the backend process's cwd,
                # user/global settings, plugin-provided servers (see
                # ClaudeAgentOptions.strict_mcp_config's own docstring).
                # Those servers' tools arrive as ordinary `mcp__*__*`
                # tool_use blocks that `tools=[]` does NOT remove (it
                # governs BUILT-INS only), i.e. the exact same
                # foreign-work-recorded-as-Empyralis-work failure, reached
                # through a different door. A tenant turn must never
                # inherit tools from the host machine's config.
                strict_mcp_config=True,
                # SDK isolation mode: load NO filesystem settings.
                # setting_sources defaults to None, which per its own
                # docstring means "all sources are loaded (matches CLI
                # defaults)" — user ~/.claude/settings.json, project
                # .claude/settings.json, .claude/settings.local.json, and
                # (because "project" is among them) CLAUDE.md files. The
                # subprocess inherits the BACKEND process's cwd (options.
                # cwd is never set), so on this repo that meant every
                # tenant turn silently loaded Empyralis's own CLAUDE.md,
                # its permission rules, its hooks and its custom slash
                # commands into the tenant's session. That is host
                # configuration leaking into multi-tenant execution: not
                # this workspace's instructions, not this workspace's
                # permissions, and one more supply line for tools and
                # behavior Empyralis never registered. CLAUDE_CONFIG_DIR
                # (resolve_sdk_process_env) already relocates the USER
                # scope to a fresh directory; only this closes the project
                # and local scopes, which are cwd-derived and unaffected by
                # that variable.
                setting_sources=[],
                # MAN-310 skills-delivery: local plugin dirs load over
                # `--plugin-dir`, a code path INDEPENDENT of setting_sources
                # (verified in the installed SDK's _internal/transport/
                # subprocess_cli.py: the plugin-dir flags are appended
                # unconditionally, never gated on effective_setting_
                # sources) — the reason setting_sources=[] above does not
                # also have to be loosened for this to work. Empty when no
                # skills are configured this turn (build_skills_plugin_dir
                # returned ""), so an agent with none behaves identically to
                # before this parameter existed. The directory itself is
                # Empyralis's OWN, freshly-written output for this one turn
                # (build_skills_plugin_dir) — never a path supplied by a
                # tenant, and it never contains a hooks/hooks.json, because
                # nothing here ever creates a `hooks/` subdirectory.
                plugins=([{"type": "local", "path": skills_plugin_dir}] if skills_plugin_dir else []),
                model=model or None,
                max_turns=max_turns,
                # Spend-safety parity with the legacy engine (see
                # effective_max_budget_usd above) — the SDK enforces this
                # itself mid-turn and stops with an error_max_budget_usd
                # result rather than Empyralis having to poll cost after
                # the fact.
                max_budget_usd=effective_max_budget_usd,
                resume=resume or None,
                # turn_env, computed once above — NOT a second
                # resolve_sdk_process_env() call. Calling it twice would
                # mint a second, different adapter token that never gets
                # captured into minted_adapter_tokens (so it would never be
                # cleared) while this options object used a token the
                # caller never learned about at all.
                env=turn_env,
                # The per-agent "Reasoning effort" composer/detail control
                # (AgentChat.tsx / FleetAgentDetail.tsx, model_config.
                # reasoning_effort) — previously wired ONLY into build_sdk_
                # tools' execute_single_direct_tool_call calls (an internal
                # LLM call made BY a tool), never onto this, the main model
                # turn, which is what the picker actually claims to control.
                # Provider-agnostic by design: reasoning depth is an
                # SDK-level concept, not something Empyralis maintains a
                # per-provider translation table for. See resolve_sdk_
                # effort's own docstring for why forwarding it regardless of
                # provider carries no wire-contract risk.
                effort=resolve_sdk_effort(reasoning_effort),
            )

        async def _consume(sdk_message: Any, *, state: TranslationState) -> List[Dict[str, Any]]:
            new_events = translate_sdk_message(sdk_message, state=state, trace_context=trace_context)
            # MAN-310 Phase 2 (trace persistence): translate_sdk_message itself
            # stays synchronous/pure (see its own docstring — unit-tested
            # directly, no event loop) and only ever builds the EPHEMERAL
            # envelope (agent_trace_service.build_ephemeral_envelope,
            # persisted=False). This async follow-up, run from here where an
            # event loop is actually available, durably persists the subset of
            # those envelopes agent_trace_service.PERSISTED_TRACE_EVENT_TYPES
            # says should survive — matching what the legacy engine's own
            # _emit_trace_event(persisted=True) call sites already do for the
            # SAME event types (tool.started/tool.result/search.query/trace.
            # failed/plan.item.updated). Reuses the envelope's OWN seq/event_id
            # (see persist_ephemeral_envelope's docstring for why — minting a
            # second seq here would persist a differently-numbered duplicate of
            # what a live consumer already saw).
            for event in new_events:
                if isinstance(event, dict) and event.get("type") == "trace":
                    await agent_trace_service.persist_ephemeral_envelope(trace_context, event.get("payload"))
            return new_events

        async def _run_via_client(
            *, options: Any, prompt: str, state: TranslationState
        ) -> List[Dict[str, Any]]:
            """Run ONE turn to completion via ClaudeSDKClient: connect (no
            initial prompt — an empty stream, per the class's own __aenter__),
            send `prompt` via .query(), drain .receive_response() through
            _consume exactly the way the old query()-based loop drained
            query(prompt=prompt, options=options) — same message stream, same
            per-call subprocess lifecycle (connect() spawns exactly one
            `claude` subprocess; receive_response() stops after yielding the
            ResultMessage; the `async with` block's __aexit__ calls
            disconnect() whether this returns normally or raises).

            `received_any_message` is a `nonlocal` write into
            run_claude_agent_sdk_turn's own local of that name, not a return
            value — the resume/fallback decision below reads that variable
            directly (as it always did, back when the loop was written
            inline over query()), specifically so it survives an exception
            raised out of THIS function: a raised exception discards
            everything this function would otherwise have returned, but the
            nonlocal write already happened for every message received
            before the failure.

            Context usage: success path only, i.e. only once the message
            loop above returns normally (this function's own `except`-free
            body — the resume/fallback exception branch belongs to the
            caller, not here). get_context_usage() is itself a second,
            independent best-effort step — a CLI control-protocol round trip
            that can fail on its own (an older CLI build without the
            capability, a connection race right at turn end) — so it is
            wrapped in its own try/except and NEVER allowed to turn an
            otherwise-successful turn into a failed one, or to slow down
            returning the reply. On success it is attached to this attempt's
            own "final" event payload under "context_usage"; on any failure
            (or a non-dict return, which would indicate an unexpected SDK
            shape) the key is simply omitted — never fabricated, never
            zeroed.
            """
            nonlocal received_any_message
            events: List[Dict[str, Any]] = []
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for sdk_message in client.receive_response():
                    received_any_message = True
                    events.extend(await _consume(sdk_message, state=state))
                try:
                    usage = await client.get_context_usage()
                except Exception as exc:
                    LOGGER.warning(
                        "claude_agent_sdk_bridge: get_context_usage() failed — omitting "
                        "context_usage from this turn's final payload: %s", exc,
                    )
                else:
                    if isinstance(usage, dict):
                        for event in events:
                            if isinstance(event, dict) and event.get("type") == "final":
                                payload = event.get("payload")
                                if isinstance(payload, dict):
                                    payload["context_usage"] = dict(usage)
                                break
                    else:
                        LOGGER.warning(
                            "claude_agent_sdk_bridge: get_context_usage() returned a "
                            "non-dict (%s) — omitting context_usage.",
                            type(usage).__name__,
                        )
            return events

        resume_token = str(resume_session_id or "").strip()
        prompt = message if resume_token else render_prompt(message, prior_messages)
        options = _build_options(resume=resume_token)

        state = TranslationState(
            known_tool_names=known_tool_names, served_by_anthropic=served_by_anthropic,
            meta_tools_enabled=meta_tools_enabled,
        )
        events: List[Dict[str, Any]] = []
        received_any_message = False
        try:
            events = await _run_via_client(options=options, prompt=prompt, state=state)
        except Exception:
            if not resume_token or received_any_message:
                # Either there was nothing to fall back FROM (no resume was
                # attempted, so this is just a real failure), or the model turn
                # was already underway — possibly having already called a tool
                # with a real side effect (sent an email, created a task, ...)
                # through the SAME in-process executor the legacy engine uses.
                # Blindly retrying from scratch there could re-run that tool
                # call a second time. Only a resume that failed before yielding
                # ANYTHING is safe to retry fresh — that failure mode is "the
                # CLI couldn't find/load that session id" (e.g. its local
                # session store was lost to a restart, or this turn landed on a
                # different machine than the one that captured it), not
                # "something went wrong partway through the model's work".
                raise
            LOGGER.warning(
                "claude_agent_sdk_bridge: resume=%s failed before yielding any message — "
                "retrying this turn fresh (full history, new session).",
                resume_token,
            )
            state = TranslationState(
                known_tool_names=known_tool_names, served_by_anthropic=served_by_anthropic,
            )
            fallback_prompt = render_prompt(message, prior_messages)
            fallback_options = _build_options(resume="")
            events = await _run_via_client(options=fallback_options, prompt=fallback_prompt, state=state)
        return events
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
        if skills_plugin_dir:
            shutil.rmtree(skills_plugin_dir, ignore_errors=True)
        for _token in minted_adapter_tokens:
            openai_compat_adapter.clear_turn_credential(_token)


def collect_events_via_claude_agent_sdk(**kwargs: Any) -> List[Dict[str, Any]]:
    """Sync wrapper for run_claude_agent_sdk_turn — sage_agent_runtime_
    service.py's _collect_stream_events is itself a plain sync function
    (run inside asyncio.to_thread by its caller, _run_sage_action_loop_v3),
    so it needs a blocking entry point, exactly like the legacy branch's
    `list(...)` over a sync generator. asyncio.run() is safe here because
    _collect_stream_events already runs on its OWN thread with no event
    loop of its own — see the asyncio.to_thread(_collect_stream_events)
    call site."""
    return asyncio.run(run_claude_agent_sdk_turn(**kwargs))
