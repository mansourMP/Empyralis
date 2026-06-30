from __future__ import annotations

from . import flows_shared as _shared
from . import question_catalog as q
from .flows_run import (
    _create_or_update_profile,
    _setup_action,
    _setup_complete,
    _setup_create_session,
    provider_and_credential_section,
    run_tui_shell,
)

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})

def configure_flow(ui: UI, api_url: str, api_key: str, workspace_id: str, engine: str) -> int:
    wizard_session = WizardSession(flow="configure", workspace_id=workspace_id)
    wizard = WizardPrompter(ui, wizard_session)

    selected_section_keys = _collect_configure_sections(ui, wizard)
    selected_keys = set(selected_section_keys)
    if not selected_keys:
        ui.note(f"{accent('•')}  {q.Q_CONFIGURE_SECTIONS}")
        ui.note(f"{muted('│')}  Continue (Skip for now)")
        wizard_session.complete()
        persist_wizard_session(wizard_session)
        return 0
    ui.note("")
    ui.note(strong("Configure Transcript"))
    selected_map = {item.key: item.label for item in CONFIGURE_SECTIONS}
    selected_labels = [selected_map[key] for key in selected_section_keys if key in selected_map]
    value_log(ui, q.Q_CONFIGURE_SECTIONS, ", ".join(selected_labels))

    session = _setup_create_session(api_url, api_key, workspace_id)
    session_id = str(session.get("id") or "")
    value_log(ui, "Setup session id", session_id or "n/a")
    total_steps = len(selected_section_keys)
    step_no = 0

    provider: Optional[str] = None
    credential_id: Optional[str] = None
    verified = False

    def _record_config_action(action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not session_id:
            return
        try:
            _setup_action(api_url, api_key, session_id, action, payload or {})
        except Exception:
            return

    if "workspace" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Workspace")
        new_workspace = wizard.text(
            q.Q_CONFIGURE_WORKSPACE_ID,
            default=workspace_id,
            required=True,
            title=q.TITLE_CONFIGURE,
        )
        value_log(ui, "Workspace", new_workspace)
        _record_config_action("workspace", {"workspace_id": new_workspace})
        if new_workspace != workspace_id:
            ui.note(muted("Note: Workspace change will apply to next run/session."))
            workspace_id = new_workspace

    if "provider" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Provider/Auth")
        provider, credential_id, verified = provider_and_credential_section(ui, api_url, api_key, workspace_id, session_id)
        if verified:
            try:
                _setup_complete(api_url, api_key, session_id)
            except Exception:
                pass

    if "profiles" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Provider Profiles")
        if provider and credential_id and verified:
            _create_or_update_profile(ui, api_url, api_key, workspace_id, provider, credential_id)
        elif "provider" in selected_keys:
            ui.note(muted("Profile creation skipped: Provider/Auth is not fully verified yet."))
            ui.note(muted("Run Configure again and complete Provider/Auth first."))
        else:
            profiles = _runtime_client(api_url, api_key).list_workspace_profiles(workspace_id)
            ui.note(f"Existing profiles: {len(profiles)}")
            if ui.confirm(q.Q_CONFIGURE_CREATE_PROFILE, default_yes=False, title=q.TITLE_CONFIGURE):
                providers = _runtime_client(api_url, api_key).list_provider_catalog()
                if not providers:
                    ui.note(f"{warn('warning:')} No providers available from runtime catalog.")
                else:
                    selected_provider = ui.choose(q.Q_CONFIGURE_PROVIDER_PROFILE, providers, default_index=0, title=q.TITLE_CONFIGURE)
                    creds = _runtime_client(api_url, api_key).list_workspace_credentials(workspace_id, provider=selected_provider.key)
                    if not creds:
                        ui.note(f"{warn('warning:')} No credentials for provider {selected_provider.key}.")
                    else:
                        cred_choices = [
                            Choice(str(c.get("id") or ""), str(c.get("label") or c.get("id") or "credential")) for c in creds
                        ]
                        selected_cred = ui.choose(q.Q_CONFIGURE_CREDENTIAL_PROFILE, cred_choices, default_index=0, title=q.TITLE_CONFIGURE)
                        _create_or_update_profile(ui, api_url, api_key, workspace_id, selected_provider.key, selected_cred.key)

    if "web" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Web Tools")
        web_search_enabled = wizard.confirm(
            q.Q_CONFIGURE_ENABLE_WEB_SEARCH,
            default_yes=False,
            title=q.TITLE_CONFIGURE,
        )
        web_fetch_enabled = wizard.confirm(
            q.Q_CONFIGURE_ENABLE_WEB_FETCH,
            default_yes=True,
            title=q.TITLE_CONFIGURE,
        )
        value_log(ui, "web_search", "enabled" if web_search_enabled else "disabled")
        value_log(ui, "web_fetch", "enabled" if web_fetch_enabled else "disabled")
        _record_config_action(
            "web_tools",
            {"web_search_enabled": web_search_enabled, "web_fetch_enabled": web_fetch_enabled},
        )

    if "gateway" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Gateway")
        bind_choice = wizard.select(
            q.Q_CONFIGURE_GATEWAY_BIND,
            q.setup_gateway_bind_options(),
            default_index=0,
            title=q.TITLE_CONFIGURE,
        )
        choice_log(ui, "Gateway bind", bind_choice)
        auth_choice = wizard.select(
            q.Q_CONFIGURE_GATEWAY_AUTH,
            q.setup_gateway_auth_options(),
            default_index=0,
            title=q.TITLE_CONFIGURE,
        )
        choice_log(ui, "Gateway auth", auth_choice)
        _record_config_action("gateway_config", {"bind": bind_choice.key, "auth_mode": auth_choice.key})

    if "daemon" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Daemon")
        daemon_choice = wizard.select(
            q.Q_CONFIGURE_DAEMON,
            q.setup_daemon_options(),
            default_index=1,
            title=q.TITLE_CONFIGURE,
        )
        choice_log(ui, "Daemon", daemon_choice)
        _record_config_action("daemon", {"mode": daemon_choice.key})

    if "channels" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Channels")
        choose_and_create_connectors(ui, api_url, api_key, workspace_id)

    if "skills" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Skills")
        skills_choice = wizard.select(
            q.Q_CONFIGURE_SKILLS,
            q.setup_skills_options(),
            default_index=1,
            title=q.TITLE_CONFIGURE,
        )
        choice_log(ui, "Skills", skills_choice)
        _record_config_action("skills", {"mode": skills_choice.key})

    if "connectors" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Connectors (Legacy)")
        choose_and_create_connectors(ui, api_url, api_key, workspace_id)

    if "doctor" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Doctor")
        print_doctor_summary(ui, api_url, api_key)

    if "launch" in selected_keys:
        step_no += 1
        step_banner(ui, step_no, total_steps, "Launch")
        launch_choice = wizard.select(
            q.Q_CONFIGURE_LAUNCH_TARGET,
            q.configure_launch_target_options(),
            default_index=0,
            title=q.TITLE_CONFIGURE,
        )
        choice_log(ui, q.Q_CONFIGURE_LAUNCH_TARGET, launch_choice)
        if launch_choice.key == "tui":
            wizard_session.complete()
            persist_wizard_session(wizard_session)
            return run_tui_shell(ui, api_url, api_key, workspace_id, engine)
        if launch_choice.key == "web":
            ui.message("Web Entry", "Open: http://127.0.0.1:3000")

    wizard_session.complete()
    persist_wizard_session(wizard_session)
    return 0


__all__ = [
    "configure_flow",
]
