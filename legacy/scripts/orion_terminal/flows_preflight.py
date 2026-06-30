from __future__ import annotations

from typing import List, Optional, Dict, Any

from . import flows_shared as _shared
from . import question_catalog as q
from .flows_run import (
    _create_provider_credential,
    _verify_provider_credential,
    run_once,
)
from .state.workspace import load_workspace_state, save_workspace_state
from .runtime_flags import FULL_OUTPUT
from .core import (
    Choice,
    EXECUTION_TARGETS,
    FILE_ACCESS_SCOPES,
    PREFLIGHT_OPERATOR_SAFETY_PROFILES,
    PREFLIGHT_TRUST_MODES,
    DEFAULT_QUICK_GOAL,
    GENERAL_MODE,
    strong,
    muted,
    warn,
    ok,
    err,
)

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})


def _choice_default_index(options: List[Choice], key: str, fallback: int = 0) -> int:
    for idx, option in enumerate(options):
        if str(option.key) == str(key):
            return idx
    return max(0, min(fallback, max(0, len(options) - 1)))


def _operator_approval_levels_for_profile(profile_key: str, trust_mode_key: str) -> List[str]:
    profile = str(profile_key or "").strip().lower()
    trust = str(trust_mode_key or "").strip().lower()
    if profile == "high_only":
        return ["high"]
    if profile == "medium_high":
        return ["medium", "high"]
    if profile == "all":
        return ["low", "medium", "high"]
    if profile == "none":
        return []
    if trust == "strict":
        return ["medium", "high"]
    if trust == "auto":
        return []
    return ["high"]


def preflight_flow(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    engine: str,
    goal: Optional[str] = None,
    auto_confirm: bool = False,
) -> int:
    wizard_session = WizardSession(flow="preflight", workspace_id=workspace_id)
    wizard = WizardPrompter(ui, wizard_session)
    workspace_state = load_workspace_state(workspace_id)
    preflight_state = workspace_state.preflight

    if FULL_OUTPUT:
        wizard.intro(strong("Professional Preflight"))
        wizard.note(muted("7-step security and execution configuration."))

    # --- Step 1: Execution Location ---
    step_banner(ui, 1, 7, "Execution Location")
    target = wizard.select(
        q.Q_PREFLIGHT_EXECUTION_LOCATION,
        EXECUTION_TARGETS,
        default_index=_choice_default_index(EXECUTION_TARGETS, preflight_state.execution_target_key, 0),
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_EXECUTION_LOCATION, target)

    # --- Step 2: Trust Mode ---
    step_banner(ui, 2, 7, "Trust Mode")
    trust = wizard.select(
        q.Q_PREFLIGHT_TRUST_MODE,
        PREFLIGHT_TRUST_MODES,
        default_index=_choice_default_index(PREFLIGHT_TRUST_MODES, preflight_state.trust_mode_key, 0),
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_TRUST_MODE, trust)
    operator_safety_profile = wizard.select(
        q.Q_PREFLIGHT_OPERATOR_SAFETY,
        PREFLIGHT_OPERATOR_SAFETY_PROFILES,
        default_index=_choice_default_index(
            PREFLIGHT_OPERATOR_SAFETY_PROFILES,
            preflight_state.operator_safety_profile_key,
            0,
        ),
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_OPERATOR_SAFETY, operator_safety_profile)
    operator_approval_levels = _operator_approval_levels_for_profile(operator_safety_profile.key, trust.key)

    # --- Step 3: AI Provider ---
    step_banner(ui, 3, 7, "AI Provider")
    provider_spinner = _ProgressSpinner("loading providers")
    provider_spinner.tick("loading providers")
    try:
        providers = _runtime_client(api_url, api_key).list_provider_catalog()
        provider_spinner.done(True, "providers ready")
    except Exception as exc:
        provider_spinner.done(False, "providers failed")
        providers = []
        ui.note(f"{warn('warning:')} Could not load provider catalog: {exc}")

    provider_options = providers + [Choice("", "Use runtime default", "Use runtime/provider profile defaults")]
    provider_default_index = _choice_default_index(provider_options, preflight_state.provider_key, 0)

    provider_choice = wizard.select(
        q.Q_PREFLIGHT_PROVIDER,
        provider_options,
        default_index=provider_default_index,
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_PROVIDER, provider_choice)
    provider_id = provider_choice.key

    # --- Step 4: Credential Source ---
    step_banner(ui, 4, 7, "Credential Source")
    credential_source = "runtime"
    credential_source_label = "Runtime Environment"
    credential_id: Optional[str] = None
    existing = []
    if provider_id:
        existing = _runtime_client(api_url, api_key).list_workspace_credentials(workspace_id, provider=provider_id)
        cred_options = q.preflight_auth_options(provider_id, len(existing))
    else:
        cred_options = q.preflight_auth_options_runtime_only()

    default_cred_idx = _choice_default_index(cred_options, preflight_state.credential_source, 0)
    source_choice = wizard.select(
        q.Q_PREFLIGHT_AUTH,
        cred_options,
        default_index=default_cred_idx,
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_AUTH, source_choice)
    credential_source = source_choice.key
    credential_source_label = source_choice.label

    if provider_id:
        if source_choice.key == "saved" and existing:
            if len(existing) > 1:
                item_choices = [
                    Choice(str(c.get("id")), str(c.get("label") or c.get("id")), "")
                    for c in existing
                ]
                selected_cred = wizard.select(q.Q_SETUP_SELECT_CREDENTIAL, item_choices, default_index=0, title=q.TITLE_PREFLIGHT)
                credential_id = selected_cred.key
            else:
                credential_id = str(existing[0].get("id"))
                value_log(ui, "Credential", "Using existing saved credential")
        elif source_choice.key == "new":
            try:
                credential_id = _create_provider_credential(ui, api_url, api_key, workspace_id, provider_id)
            except Exception as exc:
                ui.note(f"{warn('warning:')} {exc}")
                credential_source = "runtime"
                credential_source_label = "Runtime Environment"
    elif source_choice.key == "new":
        ui.note(muted("Select a provider first to store a new provider-specific credential."))
        credential_source = "runtime"
        credential_source_label = "Runtime Environment"

    # --- Step 5: Channels & Tools ---
    step_banner(ui, 5, 7, "Channels & Tools")
    channel_options = q.preflight_connectors_options()
    channel_choice = wizard.select(
        q.Q_PREFLIGHT_CONNECTORS,
        channel_options,
        default_index=_choice_default_index(channel_options, preflight_state.connectors_choice, 0),
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_CONNECTORS, channel_choice)
    connectors_added = 0
    if channel_choice.key == "connect_now":
        created = choose_and_create_connectors(ui, api_url, api_key, workspace_id)
        connectors_added = len(created)

    # --- Step 6: File Access Scope ---
    step_banner(ui, 6, 7, "File Access Scope")
    file_scope = wizard.select(
        q.Q_PREFLIGHT_FILE_SCOPE,
        FILE_ACCESS_SCOPES,
        default_index=_choice_default_index(FILE_ACCESS_SCOPES, preflight_state.file_scope_key, 0),
        title=q.TITLE_PREFLIGHT,
    )
    choice_log(ui, q.Q_PREFLIGHT_FILE_SCOPE, file_scope)

    # --- Step 7: Final Risk Acknowledgement ---
    step_banner(ui, 7, 7, "Confirm & Start")
    run_goal = (goal or "").strip() or DEFAULT_QUICK_GOAL

    summary_lines = [
        f"Goal: {run_goal}",
        f"Target: {target.label}",
        f"Trust: {trust.label}",
        f"Operator Safety: {operator_safety_profile.label}",
        f"Provider: {provider_choice.label or 'Runtime Default'}",
        f"Credential: {credential_source_label}",
        f"File Scope: {file_scope.label}",
        f"Channels: {channel_choice.label}",
    ]
    ui.message("Preflight Summary", "\n".join(summary_lines))

    if auto_confirm:
        ui.note(muted("[preflight] auto-confirmed."))
    else:
        # "I acknowledge risks and start"
        ack = wizard.confirm(
            q.Q_PREFLIGHT_CONFIRM_START,
            default_yes=False,
            title=q.TITLE_PREFLIGHT
        )
        if not ack:
            ui.note(muted("Preflight cancelled."))
            wizard_session.cancel("preflight_cancelled")
            persist_wizard_session(wizard_session)
            return 0

    # --- Persistence & Execution ---
    wizard_session.complete()
    persist_wizard_session(wizard_session)

    # Save state for next time
    try:
        preflight_state.execution_target_key = target.key
        preflight_state.trust_mode_key = trust.key
        preflight_state.provider_key = provider_id
        preflight_state.credential_source = credential_source
        preflight_state.file_scope_key = file_scope.key
        preflight_state.connectors_choice = channel_choice.key
        preflight_state.operator_safety_profile_key = operator_safety_profile.key
        save_workspace_state(workspace_id, workspace_state)
    except Exception:
        pass

    metadata: Dict[str, Any] = {
        "file_access_scope": file_scope.key,
        "provider_selection": provider_id or "runtime_default",
        "credential_source": credential_source,
        "connector_decision": channel_choice.key,
        "connectors_added": connectors_added,
        "operator_safety_profile": operator_safety_profile.key,
        "operator_approval_levels": operator_approval_levels,
    }
    if credential_id:
        metadata["credential_id"] = credential_id

    return run_once(
        ui=ui,
        api_url=api_url,
        runtime_key=api_key,
        workspace_id=workspace_id,
        engine=engine,
        goal=run_goal,
        pack=GENERAL_MODE,
        trust=trust,
        target=target,
        pack_inputs=None,
        prompt_connectors=False,
        extra_metadata=metadata,
        provider_override=provider_id or None,
        credential_id_override=credential_id,
        model_override=None,
    )



__all__ = [
    "preflight_flow",
]
