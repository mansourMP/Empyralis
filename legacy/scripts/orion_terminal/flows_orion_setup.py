from __future__ import annotations

from . import flows_shared as _shared
from . import question_catalog as q
from .flows_run import (
    _create_provider_credential,
    _setup_action,
    _setup_complete,
    _setup_create_session,
    _verify_provider_credential,
    run_once,
    run_tui_shell,
)

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})

def orion_setup_flow(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    engine: str,
) -> int:
    wizard_session = WizardSession(flow="orion_setup", workspace_id=workspace_id)
    wizard = WizardPrompter(ui, wizard_session)

    if FULL_OUTPUT:
        wizard.intro(strong("Empyralis Setup"))

    existing_summary = _existing_config_summary(api_url, api_key, workspace_id)
    has_existing = bool(existing_summary)
    if has_existing:
        ui.message("Existing config detected", existing_summary)
        wizard_session.record("note", "Existing config detected", "shown", title=q.TITLE_SETUP)

    local_gateway_hint = (
        "Gateway reachable (ws://127.0.0.1:18789)"
        if has_existing
        else "No gateway detected (ws://127.0.0.1:18789)"
    )
    remote_gateway_default = os.getenv("ORION_REMOTE_GATEWAY_URL", "").strip()
    remote_gateway_hint = (
        f"Configured ({remote_gateway_default})"
        if remote_gateway_default
        else "No remote URL configured yet"
    )
    gateway_location_options = [
        Choice("local", "Local (this machine)", local_gateway_hint),
        Choice("remote", "Remote (info-only)", remote_gateway_hint),
    ]

    provider_group = Choice("skip", "Skip for now", "")
    provider_auth_choice = Choice("skip", "Skip for now", "")
    runtime_provider: Optional[str] = None
    provider_override: Optional[str] = None
    credential_id: Optional[str] = None
    credential_source = "later"
    model_filter = Choice("*", "All providers", "")
    selected_model: Optional[str] = None
    onboarding_mode = ORION_SETUP_ONBOARDING_MODES[0]
    config_handling = Choice("keep", "Use default values", "No existing config to update")
    quickstart_channel = Choice("skip", "Skip for now", "")
    selected_channels: List[str] = []
    connectors_added = 0
    reset_scope = ""
    workspace_value = workspace_id
    web_search_enabled = False
    web_fetch_enabled = True
    gateway_bind = "loopback"
    gateway_auth_mode = "token"
    daemon_mode = "later"
    skills_mode = "later"
    setup_session_id = ""
    setup_session_state: Dict[str, Any] = {}

    step_no = 0

    def _ensure_setup_session() -> None:
        nonlocal setup_session_id, setup_session_state
        if setup_session_id:
            return
        try:
            session = _setup_create_session(
                api_url,
                api_key,
                workspace_value,
                flow=onboarding_mode.key,
            )
            if isinstance(session, dict):
                setup_session_state = dict(session)
            setup_session_id = str(session.get("id") or "")
            if setup_session_id:
                value_log(ui, "Setup session id", setup_session_id)
        except Exception as exc:
            ui.note(f"{warn('warning:')} Could not create setup session: {exc}")

    def _record_setup_action(action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        nonlocal setup_session_state
        if not setup_session_id:
            return
        try:
            updated = _setup_action(api_url, api_key, setup_session_id, action, payload or {})
            if isinstance(updated, dict):
                setup_session_state = dict(updated)
        except Exception:
            return

    step_no += 1
    step_banner(ui, step_no, 0, "Gateway Location")
    gateway_location = wizard.select_contract(
        q.setup_gateway_location_contract(gateway_location_options)
    )
    choice_log(ui, q.Q_SETUP_GATEWAY_LOCATION, gateway_location)
    _ensure_setup_session()
    _record_setup_action("risk_ack", {"accepted": True, "source": "orion_setup"})
    _record_setup_action("gateway_location", {"location": gateway_location.key})
    remote_gateway_url = ""
    if gateway_location.key == "remote":
        remote_gateway_url = wizard.text(
            q.Q_SETUP_REMOTE_GATEWAY_URL,
            default=os.getenv("ORION_REMOTE_GATEWAY_URL", "").strip(),
            required=False,
            title=q.TITLE_CONFIGURE,
        )
        _record_setup_action("remote_gateway", {"url": remote_gateway_url})

    # Lean Empyralis Setup keeps a fixed quick path. Advanced section selection stays in Configure.
    sections = ["model", "channels"]
    section_label_map = {item.key: item.label for item in ORION_SETUP_CONFIG_SECTIONS}
    selected_section_labels = [section_label_map[key] for key in sections if key in section_label_map]
    value_log(ui, q.Q_SETUP_SELECT_SECTIONS, ", ".join(selected_section_labels))
    selected_sections = set(sections)
    sections_needing_config = {"workspace", "model", "web", "gateway", "daemon", "channels", "skills"}
    channels_selected = "channels" in selected_sections
    needs_config_handling = bool(selected_sections & sections_needing_config)
    _record_setup_action("sections", {"selected": sections, "reason": "setup_default"})

    # Lean setup path: keep Empyralis Setup short like OpenClaw quick onboarding.
    # Advanced mode/reset remains available under Configure.
    onboarding_mode = ORION_SETUP_ONBOARDING_MODES[0]
    _record_setup_action("onboarding_mode", {"mode": onboarding_mode.key, "reason": "setup_default"})
    if has_existing and needs_config_handling:
        config_handling = Choice("keep", "Use existing values", "Use Configure for update/reset")
        _record_setup_action("config_handling", {"mode": config_handling.key, "reason": "setup_default"})
    elif not has_existing:
        config_handling = Choice("keep", "Use default values", "No existing config to update")

    ordered_section_keys = [item.key for item in ORION_SETUP_CONFIG_SECTIONS if item.key in selected_sections]
    for section_key in ordered_section_keys:
        if section_key == "workspace":
            step_no += 1
            step_banner(ui, step_no, 0, "Workspace")
            workspace_value = wizard.text(
                q.Q_SETUP_WORKSPACE_ID,
                default=workspace_id,
                required=True,
                title=q.TITLE_CONFIGURE,
            )
            value_log(ui, "Workspace", workspace_value)
            _record_setup_action("workspace", {"workspace_id": workspace_value})

        elif section_key == "model":
            step_no += 1
            step_banner(ui, step_no, 0, "Model/Auth Provider")

            # Keep setup deterministic: fixed OpenAI + Codex-session path.
            # Provider/model exploration remains in Configure.
            provider_group = Choice("openai", "OpenAI", "Fixed quick path in Empyralis Setup")
            provider_auth_choice = Choice(
                "openai_codex_vault",
                "Use existing Codex session",
                "Reuse token from Codex auth vault/env",
            )
            choice_log(ui, "Provider group", provider_group)
            choice_log(ui, "Auth method", provider_auth_choice)
            _record_setup_action(
                "provider_auth_choice",
                {"provider": provider_group.key, "auth_method": provider_auth_choice.key, "reason": "setup_fixed_quick_path"},
            )

            runtime_provider = "openai"
            provider_override = runtime_provider
            credential_id = None
            credential_source = "runtime"
            selected_model = None

            token, source = _detect_openai_oauth_env_token()
            if token:
                value_log(ui, "Codex session source", source)
                _record_setup_action(
                    "credential_handling",
                    {"mode": "runtime", "reason": "codex_auth_method", "source": source},
                )
            else:
                ui.note(
                    warn(
                        "warning: No existing Codex session found. "
                        "Use Codex device sign-in, paste token, or choose Setup later."
                    )
                )
                recovery = wizard.select(
                    "Codex session not found",
                    [
                        Choice("openai_codex_device", "Codex device sign-in", "Open login link now"),
                        Choice("openai_codex_oauth", "Paste Codex access token", "Manual token entry"),
                        Choice("openai_api_key", "OpenAI API key", "BYOK key"),
                        Choice("skip", "Setup later", "Continue without OpenAI auth"),
                    ],
                    default_index=0,
                    title=q.TITLE_ONBOARDING,
                )
                choice_log(ui, "Auth recovery", recovery)
                provider_auth_choice = recovery
                auth_method_key = str(recovery.key or "").strip().lower()

                if auth_method_key == "openai_codex_device":
                    ok_login, detail = _run_codex_device_auth(ui)
                    if ok_login:
                        ui.note(f"{ok('done:')} {detail}")
                        token, source = _detect_openai_oauth_env_token()
                        if token:
                            credential_source = "runtime"
                            value_log(ui, "Codex session source", source)
                            _record_setup_action(
                                "credential_handling",
                                {"mode": "runtime", "reason": "codex_device_signin", "source": source},
                            )
                        else:
                            credential_source = "later"
                            provider_override = None
                    else:
                        ui.note(f"{warn('warning:')} {detail}")
                        credential_source = "later"
                        provider_override = None

                elif auth_method_key in {"openai_codex_oauth", "openai_api_key"}:
                    try:
                        credential_id = _create_provider_credential(
                            ui,
                            api_url,
                            api_key,
                            workspace_value,
                            runtime_provider,
                            preferred_auth_method=auth_method_key,
                        )
                        credential_source = "new"
                        if credential_id:
                            _record_setup_action("submit_credential", {"credential_id": credential_id})
                    except Exception as exc:
                        credential_id = None
                        credential_source = "later"
                        provider_override = None
                        ui.note(f"{warn('warning:')} Could not save credential: {exc}")

                else:
                    credential_source = "later"
                    provider_override = None
                    _record_setup_action("credential_handling", {"mode": "later", "reason": "setup_later"})

            if provider_override and credential_id:
                verified, detail = _verify_provider_credential(
                    api_url,
                    api_key,
                    provider_override,
                    credential_id,
                    workspace_value,
                )
                if verified:
                    ui.note(f"{ok('done:')} Credential verified: {detail}")
                else:
                    ui.note(f"{warn('warning:')} Credential verification failed: {detail}")
                _record_setup_action("verify", {"ok": verified, "error": None if verified else detail})

            model_filter = Choice("openai", "openai", "Using runtime default model")
            value_log(ui, q.Q_SETUP_MODEL_FILTER, model_filter.label)
            _record_setup_action("model_filter", {"provider": model_filter.key, "reason": "setup_fixed_quick_path"})
            _record_setup_action(
                "model_override_skipped",
                {"reason": "setup_fixed_quick_path", "provider": provider_override or runtime_provider},
            )
            ui.note(muted("info: Setup uses runtime default model. Change model/provider in Configure > Provider/Auth."))

        elif section_key == "web":
            step_no += 1
            step_banner(ui, step_no, 0, "Web Tools")
            web_search_enabled = wizard.confirm(
                q.Q_SETUP_ENABLE_WEB_SEARCH,
                default_yes=False,
                title=q.TITLE_CONFIGURE,
            )
            web_fetch_enabled = wizard.confirm(
                q.Q_SETUP_ENABLE_WEB_FETCH,
                default_yes=True,
                title=q.TITLE_CONFIGURE,
            )
            value_log(ui, "web_search", "enabled" if web_search_enabled else "disabled")
            value_log(ui, "web_fetch", "enabled" if web_fetch_enabled else "disabled")
            _record_setup_action(
                "web_tools",
                {"web_search_enabled": web_search_enabled, "web_fetch_enabled": web_fetch_enabled},
            )

        elif section_key == "gateway":
            step_no += 1
            step_banner(ui, step_no, 0, "Gateway")
            if gateway_location.key == "local":
                bind_choice = wizard.select(
                    q.Q_SETUP_GATEWAY_BIND,
                    q.setup_gateway_bind_options(),
                    default_index=0,
                    title=q.TITLE_CONFIGURE,
                )
                gateway_bind = bind_choice.key
                choice_log(ui, "Gateway bind", bind_choice)
            auth_choice = wizard.select(
                q.Q_SETUP_GATEWAY_AUTH,
                q.setup_gateway_auth_options(),
                default_index=0,
                title=q.TITLE_CONFIGURE,
            )
            gateway_auth_mode = auth_choice.key
            choice_log(ui, "Gateway auth", auth_choice)
            _record_setup_action("gateway_config", {"bind": gateway_bind, "auth_mode": gateway_auth_mode})

        elif section_key == "daemon":
            step_no += 1
            step_banner(ui, step_no, 0, "Daemon")
            daemon_choice = wizard.select(
                q.Q_SETUP_DAEMON,
                q.setup_daemon_options(),
                default_index=1,
                title=q.TITLE_CONFIGURE,
            )
            daemon_mode = daemon_choice.key
            choice_log(ui, "Daemon", daemon_choice)
            _record_setup_action("daemon", {"mode": daemon_mode})

        elif section_key == "channels":
            step_no += 1
            step_banner(ui, step_no, 0, "Channels")
            connectors_added = 0
            selected_channels = []
            if onboarding_mode.key == "quickstart":
                quickstart_channel = wizard.select(
                    q.Q_SETUP_QUICKSTART_CHANNEL,
                    ORION_SETUP_QUICKSTART_CHANNELS,
                    default_index=0,
                    title=q.TITLE_ONBOARDING,
                )
                choice_log(ui, "QuickStart channel", quickstart_channel)
                if quickstart_channel.key != "skip":
                    selected_channels = [quickstart_channel.key]
                connector_choice = ORION_SETUP_CHANNEL_TO_CONNECTOR.get(quickstart_channel.key)
                if connector_choice:
                    created = create_quickstart_connector(ui, api_url, api_key, workspace_value, connector_choice)
                    connectors_added = 1 if created else 0
                    value_log(ui, "Connectors added", str(connectors_added))
                    if created and created.get("id"):
                        _record_setup_action(
                            "connector_added",
                            {"connector": connector_choice, "id": str(created.get("id"))},
                        )
                elif quickstart_channel.key != "skip":
                    ui.note(muted("info: Channel selection recorded."))
            else:
                selected_channels = _collect_channels_manual(ui, wizard)
                value_log(ui, "Selected channels", ", ".join(selected_channels) if selected_channels else "none")
                for channel_key in selected_channels:
                    connector_choice = ORION_SETUP_CHANNEL_TO_CONNECTOR.get(channel_key)
                    if connector_choice:
                        label = next((c.label for c in ORION_SETUP_QUICKSTART_CHANNELS if c.key == channel_key), channel_key)
                        if wizard.confirm(f"Configure {label} now?", default_yes=False, title=q.TITLE_ONBOARDING):
                            created = create_quickstart_connector(ui, api_url, api_key, workspace_value, connector_choice)
                            if created:
                                connectors_added += 1
                                if created.get("id"):
                                    _record_setup_action(
                                        "connector_added",
                                        {"connector": connector_choice, "id": str(created.get("id"))},
                                    )
                    else:
                        ui.note(muted(f"info: {channel_key} recorded."))
                value_log(ui, "Connectors added", str(connectors_added))

            _record_setup_action(
                "channel_choice",
                {
                    "channel": quickstart_channel.key,
                    "selected_channels": selected_channels,
                    "connectors_added": connectors_added,
                },
            )

        elif section_key == "skills":
            step_no += 1
            step_banner(ui, step_no, 0, "Skills")
            skills_choice = wizard.select(
                q.Q_SETUP_SKILLS,
                q.setup_skills_options(),
                default_index=1,
                title=q.TITLE_CONFIGURE,
            )
            skills_mode = skills_choice.key
            choice_log(ui, "Skills", skills_choice)
            _record_setup_action("skills", {"mode": skills_mode})

        elif section_key == "health":
            step_no += 1
            step_banner(ui, step_no, 0, "Health Check")
            print_doctor_summary(ui, api_url, api_key)
            _record_setup_action("health_check", {"ran": True})

    step_no += 1
    step_banner(ui, step_no, 0, "Confirm & Start")
    goal = DEFAULT_QUICK_GOAL
    value_log(ui, "First run goal", goal)
    if wizard.confirm("Set first run goal now?", default_yes=False, title=q.TITLE_RUN):
        goal = wizard.text(
            q.Q_SETUP_RUN_GOAL,
            default=goal,
            required=True,
            title=q.TITLE_RUN,
        )

    summary_lines = [
        f"Gateway: {gateway_location.label}",
        f"Sections: {', '.join(selected_section_labels) if selected_section_labels else 'none (Skip for now)'}",
        f"Mode: {onboarding_mode.label}",
        f"Config handling: {config_handling.label}",
        f"Workspace: {workspace_value}",
        "Onboarding: chat-first (Telegram/Web)",
    ]
    if "model" in selected_sections:
        summary_lines.append(f"Provider: {provider_group.label}")
        summary_lines.append(f"Auth method: {provider_auth_choice.label}")
        summary_lines.append(f"Model filter: {model_filter.label}")
        summary_lines.append(f"Credential source: {credential_source}")
        if selected_model:
            summary_lines.append(f"Model override: {selected_model}")
    if "channels" in selected_sections:
        if onboarding_mode.key == "quickstart":
            summary_lines.append(f"QuickStart channel: {quickstart_channel.label}")
        else:
            summary_lines.append("Channel mode: Manual")
        if selected_channels:
            summary_lines.append(f"Selected channels: {', '.join(selected_channels)}")
        summary_lines.append(f"Connectors added: {connectors_added}")
    if "web" in selected_sections:
        summary_lines.append(f"web_search: {'enabled' if web_search_enabled else 'disabled'}")
        summary_lines.append(f"web_fetch: {'enabled' if web_fetch_enabled else 'disabled'}")
    if "gateway" in selected_sections:
        summary_lines.append(f"Gateway bind: {gateway_bind}")
        summary_lines.append(f"Gateway auth: {gateway_auth_mode}")
    if "daemon" in selected_sections:
        summary_lines.append(f"Daemon: {daemon_mode}")
    if "skills" in selected_sections:
        summary_lines.append(f"Skills: {skills_mode}")
    ui.message("Empyralis Setup Summary", "\n".join(summary_lines))

    next_action = Choice("run", "Start run now", "Auto")

    if setup_session_id:
        try:
            _setup_complete(api_url, api_key, setup_session_id)
            ui.note(f"{ok('done:')} Setup session completed.")
        except Exception:
            pass

    if next_action.key == "tui":
        wizard_session.complete()
        persist_wizard_session(wizard_session)
        return run_tui_shell(ui, api_url, api_key, workspace_value, engine)

    if next_action.key == "web":
        ui.message("Web Entry", "Open: http://127.0.0.1:3000")
        wizard_session.complete()
        persist_wizard_session(wizard_session)
        return 0

    if next_action.key == "finish":
        wizard_session.complete()
        persist_wizard_session(wizard_session)
        return 0

    if setup_session_id:
        try:
            # Already completed above; keep idempotent path for older runtime behavior.
            _setup_complete(api_url, api_key, setup_session_id)
        except Exception:
            pass

    target = EXECUTION_TARGETS[0]
    if gateway_location.key == "local":
        target = next((item for item in EXECUTION_TARGETS if item.key == "local_companion"), EXECUTION_TARGETS[0])
    elif gateway_location.key == "remote":
        target = next((item for item in EXECUTION_TARGETS if item.key == "cloud"), EXECUTION_TARGETS[0])

    metadata: Dict[str, Any] = {
        "flow_style": "orion_setup_v2_configure_first",
        "gateway_location": gateway_location.key,
        "remote_gateway_url": remote_gateway_url or None,
        "configured_sections": sections,
        "onboarding_mode": onboarding_mode.key,
        "config_handling": config_handling.key,
        "reset_scope": reset_scope or None,
        "provider_group": provider_group.key,
        "provider_auth_method": provider_auth_choice.key,
        "model_filter_provider": model_filter.key,
        "quickstart_channel": quickstart_channel.key,
        "selected_channels": selected_channels,
        "connector_decision": quickstart_channel.key if onboarding_mode.key == "quickstart" else "manual",
        "connectors_added": connectors_added,
        "credential_source": credential_source,
        "provider_selection": runtime_provider or "runtime_default",
        "workspace_id": workspace_value,
        "web_search_enabled": web_search_enabled,
        "web_fetch_enabled": web_fetch_enabled,
        "gateway_bind": gateway_bind,
        "gateway_auth_mode": gateway_auth_mode,
        "daemon_mode": daemon_mode,
        "skills_mode": skills_mode,
        "onboarding_style": "chat_first",
    }
    if credential_id:
        metadata["credential_id"] = credential_id

    wizard_session.complete()
    persist_wizard_session(wizard_session)

    return run_once(
        ui=ui,
        api_url=api_url,
        runtime_key=api_key,
        workspace_id=workspace_value,
        engine=engine,
        goal=goal,
        pack=GENERAL_MODE,
        trust=TRUST_MODES[0],
        target=target,
        pack_inputs=None,
        prompt_connectors=False,
        extra_metadata=metadata,
        provider_override=provider_override,
        credential_id_override=credential_id,
        model_override=selected_model,
    )



__all__ = [
    "orion_setup_flow",
]
