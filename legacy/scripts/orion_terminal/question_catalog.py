from __future__ import annotations

from typing import List, Sequence

from .core import Choice
from .wizard.contracts import PromptContract, PromptOption


TITLE_LAUNCHER = "Empyralis Launcher"
TITLE_RUN = "Empyralis Run"
TITLE_SETUP = "Empyralis Setup"
TITLE_CONFIGURE = "Empyralis Configure"
TITLE_ONBOARDING = "Empyralis Onboarding"
TITLE_PREFLIGHT = "Empyralis Preflight"
TITLE_CONNECTORS = "Empyralis Connectors"
TITLE_LIVE_TUI = "Empyralis Live TUI"
TITLE_CREDENTIAL = "Empyralis Credential"
TITLE_APPROVAL = "Empyralis Approval"
TITLE_SECURITY = "Empyralis Security"


Q_LAUNCHER_ACTION = "Choose action"
Q_RUN_GOAL = "What do you want Empyralis to do?"
Q_RUN_TRUST_MODE = "Choose trust mode"
Q_RUN_EXECUTION_TARGET = "Choose execution target"
Q_RUN_SPECIALIST_TEMPLATE = "Choose specialist template"
Q_RUN_ADD_CONTEXT = "Add optional context fields?"
Q_RUN_CONNECT_BEFORE_START = "Connect channels/tools before this run (Google Workspace / Telegram / WhatsApp)?"

Q_GUIDED_GOAL = "What do you want Empyralis to accomplish?"
Q_GUIDED_OPERATING_MODE = "Choose operating mode"
Q_GUIDED_SPECIALIST_TEMPLATE = "Choose specialist template"
Q_GUIDED_TRUST_MODE = "Choose trust mode"
Q_GUIDED_EXECUTION_TARGET = "Where should Empyralis execute this run?"
Q_GUIDED_ADD_CONTEXT = "Add optional run inputs/context now?"
Q_GUIDED_CONNECT_NOW = "Connect channels/tools now (Google Workspace / Telegram / WhatsApp)?"
Q_GUIDED_CONFIRM_START = "Start run with this configuration?"

Q_SETUP_GATEWAY_LOCATION = "Where will the Gateway run?"
Q_SETUP_REMOTE_GATEWAY_URL = "Remote gateway URL"
Q_SETUP_SELECT_SECTIONS = "Select sections to configure"
Q_SETUP_ONBOARDING_MODE = "Onboarding mode"
Q_SETUP_CONFIG_HANDLING = "Config handling"
Q_SETUP_RESET_SCOPE = "Reset scope"
Q_SETUP_WORKSPACE_ID = "Workspace id"
Q_SETUP_PROVIDER_AUTH = "Model/auth provider"
Q_SETUP_PROVIDER_AUTH_METHOD_SUFFIX = "auth method"
Q_SETUP_CREDENTIAL_HANDLING = "Credential handling"
Q_SETUP_SELECT_CREDENTIAL = "Select credential"
Q_SETUP_MODEL_FILTER = "Filter models by provider"
Q_SETUP_SET_MODEL_OVERRIDE = "Set model override now?"
Q_SETUP_DEFAULT_MODEL = "Default model"
Q_SETUP_MODEL_ID = "Model id"
Q_SETUP_ENABLE_WEB_SEARCH = "Enable web_search (Brave Search)?"
Q_SETUP_ENABLE_WEB_FETCH = "Enable web_fetch (keyless HTTP fetch)?"
Q_SETUP_GATEWAY_BIND = "Gateway bind mode"
Q_SETUP_GATEWAY_AUTH = "Gateway auth mode"
Q_SETUP_DAEMON = "Daemon setup"
Q_SETUP_QUICKSTART_CHANNEL = "Select channel (QuickStart)"
Q_SETUP_CONNECT_SELECTED_CHANNEL = "Connect selected channel now?"
Q_SETUP_MANUAL_CHANNELS = "Select channels"
Q_SETUP_SKILLS = "Skills setup"
Q_SETUP_PROFILE_NAME_WORK = "What's your name, and what are you working on right now?"
Q_SETUP_PROFILE_GREAT_DAY = "What does a great day look like for you (how should I help most)?"
Q_SETUP_PROFILE_GUARDRAILS = "Anything I should always prioritize or never touch?"
Q_SETUP_RUN_GOAL = "Run goal"

Q_PREFLIGHT_EXECUTION_LOCATION = "Execution location"
Q_PREFLIGHT_TRUST_MODE = "Trust mode"
Q_PREFLIGHT_OPERATOR_SAFETY = "Operator safety policy"
Q_PREFLIGHT_PROVIDER = "AI provider"
Q_PREFLIGHT_AUTH = "Credential source"
Q_PREFLIGHT_MODEL_SOURCE = "Model for this run"
Q_PREFLIGHT_MODEL_ID = "Model id"
Q_PREFLIGHT_CONNECTORS = "Channels & tools"
Q_PREFLIGHT_FILE_SCOPE = "File access scope"
Q_PREFLIGHT_CONFIRM_START = "Final verification: acknowledge risks and start run?"

Q_ONBOARD_CONFIRM_SECURITY = "I understand Empyralis may take powerful actions. Continue?"

Q_CONFIGURE_SECTIONS = "Select sections to configure"
Q_CONFIGURE_WORKSPACE_ID = "Workspace id"
Q_CONFIGURE_ENABLE_WEB_SEARCH = "Enable web_search (Brave Search)?"
Q_CONFIGURE_ENABLE_WEB_FETCH = "Enable web_fetch (keyless HTTP fetch)?"
Q_CONFIGURE_GATEWAY_BIND = "Gateway bind mode"
Q_CONFIGURE_GATEWAY_AUTH = "Gateway auth mode"
Q_CONFIGURE_DAEMON = "Daemon setup"
Q_CONFIGURE_SKILLS = "Skills setup"
Q_CONFIGURE_CREATE_PROFILE = "Create a new provider profile now?"
Q_CONFIGURE_PROVIDER_PROFILE = "Provider for profile"
Q_CONFIGURE_CREDENTIAL_PROFILE = "Credential for profile"
Q_CONFIGURE_LAUNCH_TARGET = "Launch target"

Q_LIVE_TUI_ACTION = "Choose Live TUI action"
Q_LIVE_TUI_DASHBOARD_ACTION = "Dashboard action"
Q_LIVE_TUI_INBOX_FILTER = "Unified Inbox filter"
Q_LIVE_TUI_INBOX_ACTION = "Unified Inbox action"
Q_LIVE_TUI_CHAT_SESSION = "Chat session"
Q_LIVE_TUI_CHAT_ACTION = "Chat action"
Q_LIVE_TUI_CHAT_ROUTE = "Input route"
Q_LIVE_TUI_CHAT_INPUT = "›"
Q_LIVE_TUI_ENGINE = "Engine"
Q_LIVE_TUI_AGENT_MODE = "Agent"
Q_LIVE_TUI_MODEL_PROVIDER = "Model provider"
Q_LIVE_TUI_MODEL_PICK = "Model"
Q_LIVE_TUI_SETTINGS = "Settings"
Q_LIVE_TUI_INSPECT_RUN = "Inspect a run now?"
Q_LIVE_TUI_SELECT_RUN = "Select run"
Q_LIVE_TUI_STREAM_RUN = "Stream live events for this run?"
Q_LIVE_TUI_REPLAY_RUN = "Replay this run now?"
Q_LIVE_TUI_TRUST_MODE = "Choose trust mode"
Q_LIVE_TUI_EXECUTION_TARGET = "Choose execution target"
Q_LIVE_TUI_ACTIVE_MODE = "Choose active mode"
Q_LIVE_TUI_RUN_GOAL = "Run goal"
Q_LIVE_TUI_ADD_PACK_INPUTS = "Add pack inputs for this run?"
Q_LIVE_TUI_ADD_CONTEXT = "Add optional context fields?"
Q_LIVE_TUI_ADVANCED_INPUT = "Type command or goal"

Q_APPROVAL_RESOLVE = "Resolve approval"
Q_APPROVAL_CHOOSE_REQUEST = "Choose approval request"
Q_APPROVAL_DECISION = "Approval decision"
Q_APPROVAL_NOTE = "Optional note"

Q_CONNECTORS_PICK = "Choose connector to add"
Q_CONNECTORS_ADD_ANOTHER = "Add another connector?"

Q_CREDENTIAL_LABEL = "Credential label"
Q_CREDENTIAL_OPENAI_TYPE = "OpenAI credential type"
Q_CREDENTIAL_OPENAI_OAUTH_TOKEN = "Codex OAuth access token"
Q_CREDENTIAL_OPENAI_API_KEY = "OpenAI API key"
Q_CREDENTIAL_ANTHROPIC_API_KEY = "Anthropic API key"
Q_CREDENTIAL_GEMINI_API_KEY = "Google Gemini API key"
Q_CREDENTIAL_VERTEX_TOKEN = "Vertex access token"
Q_CREDENTIAL_VERTEX_PROJECT = "Vertex project_id"
Q_CREDENTIAL_VERTEX_LOCATION = "Vertex location"
Q_CREDENTIAL_JSON = "Credential JSON object (example: {\"api_key\":\"...\"})"

Q_PROFILE_LABEL = "Profile label"
Q_PROFILE_MODEL = "Model override (optional)"
Q_PROFILE_PRIORITY = "Priority (0 highest, 100 default)"


def prompt_options(choices: Sequence[Choice]) -> List[PromptOption]:
    return [PromptOption(key=item.key, label=item.label, note=item.note) for item in choices]


def setup_gateway_location_contract(options: Sequence[Choice]) -> PromptContract:
    return PromptContract(
        id="orion_setup.gateway_location",
        kind="select",
        message=Q_SETUP_GATEWAY_LOCATION,
        title=TITLE_CONFIGURE,
        options=prompt_options(options),
        default_index=0,
    )


def setup_onboarding_mode_contract(options: Sequence[Choice]) -> PromptContract:
    return PromptContract(
        id="orion_setup.onboarding_mode",
        kind="select",
        message=Q_SETUP_ONBOARDING_MODE,
        title=TITLE_ONBOARDING,
        options=prompt_options(options),
        default_index=0,
    )


def setup_config_handling_contract(options: Sequence[Choice]) -> PromptContract:
    return PromptContract(
        id="orion_setup.config_handling",
        kind="select",
        message=Q_SETUP_CONFIG_HANDLING,
        title=TITLE_ONBOARDING,
        options=prompt_options(options),
        default_index=0,
    )


def setup_credential_handling_options(existing_count: int) -> List[Choice]:
    return [
        Choice("saved", "Use existing credential", f"Found {existing_count}"),
        Choice("new", "Add new credential", "Create and store now"),
        Choice("runtime", "Use runtime default", "Use env/vault fallback for this run"),
        Choice("later", "Setup later", "Record provider only"),
    ]


def setup_gateway_bind_options() -> List[Choice]:
    return [
        Choice("loopback", "Loopback", "Local-only binding"),
        Choice("lan", "LAN", "Reachable on local network"),
        Choice("tailnet", "Tailnet", "Tailscale private network"),
    ]


def setup_gateway_auth_options() -> List[Choice]:
    return [
        Choice("token", "Token", "Recommended"),
        Choice("password", "Password", "Simple password mode"),
    ]


def setup_daemon_options() -> List[Choice]:
    return [
        Choice("install", "Install/Enable daemon", "Recommended for persistent runtime"),
        Choice("later", "Later", "Skip daemon changes now"),
    ]


def setup_skills_options() -> List[Choice]:
    return [
        Choice("recommended", "Enable recommended skills", "Apply default skill set"),
        Choice("later", "Later", "Skip skills changes now"),
    ]


def preflight_auth_options(provider_id: str, saved_count: int) -> List[Choice]:
    options: List[Choice] = []
    if saved_count > 0:
        options.append(Choice("saved", "Saved Vault", f"Found {saved_count}"))
    if provider_id == "openai":
        options.append(Choice("runtime", "Runtime Environment", "Codex OAuth or API key from runtime environment"))
        options.append(Choice("new", "Enter New Key", "Securely store API key or token now"))
    else:
        options.append(Choice("runtime", "Runtime Environment", "Use runtime environment credentials"))
        options.append(Choice("new", "Enter New Key", "Securely store provider key now"))
    options.append(Choice("later", "Set up later", "Skip credential setup for now"))
    return options


def preflight_auth_options_runtime_only() -> List[Choice]:
    return [
        Choice("runtime", "Runtime Environment", "Use configured runtime auth"),
        Choice("later", "Set up later", "Skip provider credential setup"),
    ]


def preflight_model_source_options() -> List[Choice]:
    return [
        Choice("runtime", "Use runtime/provider preference", "No model override in CLI"),
        Choice("manual", "Enter model id", "Set an explicit model for this run"),
    ]


def preflight_connectors_options() -> List[Choice]:
    return [
        Choice("later", "Skip (Workspace Only)", "No external channels/tools"),
        Choice("connect_now", "Connect now", "Open connector setup now"),
    ]


def configure_launch_target_options() -> List[Choice]:
    return [
        Choice("tui", "Open Live TUI", "Interactive shell"),
        Choice("web", "Show Web URL", "Open browser workspace"),
        Choice("none", "No launch", "Finish configure only"),
    ]
