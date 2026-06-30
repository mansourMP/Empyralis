from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import request as urlrequest

from . import question_catalog as q
from .core import (
    accent,
    Choice,
    CONFIGURE_SECTIONS,
    DEFAULT_QUICK_GOAL,
    EXECUTION_TARGETS,
    FILE_ACCESS_SCOPES,
    ORION_SETUP_CONFIG_HANDLING,
    ORION_SETUP_CONFIG_SECTIONS,
    ORION_SETUP_MODEL_PROVIDER_FILTERS,
    ORION_SETUP_ONBOARDING_MODES,
    ORION_SETUP_PROVIDER_GROUPS,
    ORION_SETUP_QUICKSTART_CHANNELS,
    ORION_SETUP_RESET_SCOPES,
    GENERAL_MODE,
    LAUNCHER_ACTIONS,
    PREFLIGHT_TRUST_MODES,
    SPECIALIST_PACKS,
    TRUST_MODES,
    UI,
    err,
    muted,
    ok,
    print_launcher_header,
    short_ts,
    strong,
    tls_context,
    warn,
)
from .clients.runtime import RuntimeClient
from .wizard.contracts import PromptOption
from .runtime_flags import (
    COMPACT_MODE,
    FULL_OUTPUT,
    ORION_SECURITY_DOC_URL,
    ORION_SETUP_DOC_URL,
    RESULT_PREVIEW_CHARS,
    SPINNER_ENABLED,
    SPINNER_STYLE,
    STREAM_PREVIEW_CHARS,
)
from .services.channel_registry import CHANNEL_TO_CONNECTOR
from .services.connectors import (
    build_connector_metadata as service_build_connector_metadata,
    choose_and_create_connectors as service_choose_and_create_connectors,
    create_connector_interactive as service_create_connector_interactive,
    create_quickstart_connector as service_create_quickstart_connector,
)
from .services.provider_credentials import (
    detect_openai_oauth_env_token as service_detect_openai_oauth_env_token,
    run_codex_device_auth as service_run_codex_device_auth,
)
from .services.provider_registry import (
    AUTH_RUNTIME_PROVIDER,
    MODEL_FILTER_TO_RUNTIME_PROVIDER,
    PROVIDER_AUTH_METHODS,
    PROVIDER_TO_RUNTIME_PROVIDER,
    default_model_filter_index,
    default_provider_auth_methods,
    model_filter_choices,
    prompt_provider_auth_choice_grouped,
    resolve_model_filter_provider,
    resolve_runtime_provider,
)
from .services.runs import start_run as service_start_run, stream_run_state as service_stream_run_state, submit_run_decision
from .services.selection import collect_toggle_choices
from .services.setup_sessions import (
    complete_setup_session as service_complete_setup_session,
    create_setup_session as service_create_setup_session,
    setup_action as service_setup_action,
)
from .spinner import (
    ProgressSpinner,
    fit_spinner_line,
    message_has_signin_wait,
    spinner_final_glyph,
    spinner_frames,
    spinner_label_from_event,
)
from .text_format import compact_summary_line, flatten_text, mask_credential_label, preview_text
from .transcript import choice_log, configure_transcript, flag_log, step_banner, value_log
from .wizard.engine import WizardPrompter, WizardSession, persist_wizard_session


configure_transcript(COMPACT_MODE, FULL_OUTPUT)


ORION_SETUP_PROVIDER_TO_RUNTIME_PROVIDER = PROVIDER_TO_RUNTIME_PROVIDER
ORION_SETUP_PROVIDER_AUTH_METHODS = PROVIDER_AUTH_METHODS
ORION_SETUP_AUTH_RUNTIME_PROVIDER = AUTH_RUNTIME_PROVIDER
ORION_SETUP_MODEL_FILTER_TO_RUNTIME_PROVIDER = MODEL_FILTER_TO_RUNTIME_PROVIDER
ORION_SETUP_CHANNEL_TO_CONNECTOR = CHANNEL_TO_CONNECTOR


_flatten_text = flatten_text
_preview_text = preview_text
_compact_summary_line = compact_summary_line
_mask_credential_label = mask_credential_label
_detect_openai_oauth_env_token = service_detect_openai_oauth_env_token
_run_codex_device_auth = service_run_codex_device_auth
_spinner_frames = spinner_frames
_spinner_final_glyph = spinner_final_glyph
_message_has_signin_wait = message_has_signin_wait
_spinner_label_from_event = spinner_label_from_event
_fit_spinner_line = fit_spinner_line
_ProgressSpinner = ProgressSpinner

create_connector_interactive = service_create_connector_interactive
create_quickstart_connector = service_create_quickstart_connector
choose_and_create_connectors = service_choose_and_create_connectors
build_connector_metadata = service_build_connector_metadata


def _runtime_client(api_url: str, api_key: str) -> RuntimeClient:
    return RuntimeClient(api_url=api_url, api_key=api_key)


def _prompt_options(choices: List[Choice]) -> List[PromptOption]:
    return [PromptOption(key=item.key, label=item.label, note=item.note) for item in choices]


def _fetch_provider_models(
    api_url: str,
    api_key: str,
    provider_id: str,
    workspace_id: str,
    credential_id: Optional[str] = None,
) -> List[str]:
    return _runtime_client(api_url, api_key).list_provider_models(
        provider_id=provider_id,
        workspace_id=workspace_id,
        credential_id=credential_id,
    )


def _default_provider_auth_methods(provider_key: str) -> List[Choice]:
    return default_provider_auth_methods(provider_key)


def _resolve_runtime_provider(provider_group_key: str, auth_method_key: str) -> Optional[str]:
    return resolve_runtime_provider(provider_group_key, auth_method_key)


def _resolve_model_filter_provider(model_filter_key: str) -> Optional[str]:
    return resolve_model_filter_provider(model_filter_key)


def _model_filter_choices(api_url: str, api_key: str) -> List[Choice]:
    return model_filter_choices(api_url, api_key, list(ORION_SETUP_MODEL_PROVIDER_FILTERS))


def _default_model_filter_index(options: List[Choice], runtime_provider: Optional[str]) -> int:
    return default_model_filter_index(options, runtime_provider)


def _prompt_provider_auth_choice_grouped(
    ui: UI,
    api_url: str,
    api_key: str,
    title: str = "Empyralis Onboarding",
    provider_groups: Optional[List[Choice]] = None,
    include_runtime_extras: bool = True,
) -> Tuple[Choice, Choice]:
    runtime_provider_choices: List[Choice] = []
    try:
        if include_runtime_extras:
            runtime_provider_choices = _runtime_client(api_url, api_key).list_provider_catalog()
    except Exception:
        runtime_provider_choices = []
    groups = list(provider_groups) if provider_groups else list(ORION_SETUP_PROVIDER_GROUPS)
    return prompt_provider_auth_choice_grouped(
        ui=ui,
        provider_prompt=q.Q_SETUP_PROVIDER_AUTH,
        provider_groups=groups,
        method_prompt_suffix=q.Q_SETUP_PROVIDER_AUTH_METHOD_SUFFIX,
        title=title,
        runtime_provider_choices=runtime_provider_choices,
    )


def _collect_orion_setup_sections(ui: UI, prompter: Optional[WizardPrompter] = None) -> List[str]:
    return collect_toggle_choices(
        ui=ui,
        prompt=q.Q_SETUP_SELECT_SECTIONS,
        base_options=list(ORION_SETUP_CONFIG_SECTIONS),
        title=q.TITLE_CONFIGURE,
        prompter=prompter,
        continue_skip_hint="Skip for now",
        continue_done_hint="Done",
        default_index=0,
        show_selected_suffix=False,
        log_toggles=True,
        toggle_item_label="Section",
        default_selected_keys=["model", "channels"],
        min_required=1,
    )


def _collect_configure_sections(ui: UI, prompter: Optional[WizardPrompter] = None) -> List[str]:
    return collect_toggle_choices(
        ui=ui,
        prompt=q.Q_CONFIGURE_SECTIONS,
        base_options=list(CONFIGURE_SECTIONS),
        title=q.TITLE_CONFIGURE,
        prompter=prompter,
        continue_skip_hint="Skip for now",
        continue_done_hint="Done",
        default_index=0,
        show_selected_suffix=False,
        log_toggles=True,
        toggle_item_label="Section",
    )


def _collect_channels_manual(ui: UI, prompter: Optional[WizardPrompter] = None) -> List[str]:
    base_options = [item for item in ORION_SETUP_QUICKSTART_CHANNELS if item.key != "skip"]
    return collect_toggle_choices(
        ui=ui,
        prompt=q.Q_SETUP_MANUAL_CHANNELS,
        base_options=base_options,
        title=q.TITLE_ONBOARDING,
        prompter=prompter,
        continue_skip_hint="Skip for now",
        continue_done_hint="Done",
        default_index=0,
        show_selected_suffix=False,
        log_toggles=False,
    )


def _existing_config_summary(api_url: str, api_key: str, workspace_id: str) -> str:
    try:
        health = _runtime_client(api_url, api_key).get_health()
    except Exception:
        return ""

    supported_targets = health.get("supported_execution_targets")
    if isinstance(supported_targets, list):
        target_line = ", ".join([str(item) for item in supported_targets if str(item).strip()])
    else:
        target_line = ""

    lines = [
        f"workspace: {workspace_id}",
        f"runtime: {api_url}",
        f"model: {health.get('codex_model') or 'default'}",
        f"auth_required: {'yes' if bool(health.get('auth_required')) else 'no'}",
        f"local_companion: {'on' if bool(health.get('local_companion_enabled')) else 'off'}",
    ]
    if target_line:
        lines.append(f"execution_targets: {target_line}")
    return "\n".join(lines)


def collect_pack_inputs(ui: UI, pack_id: str) -> Dict[str, str]:
    ui.note("\nOptional inputs (you can leave blank):")
    if not pack_id:
        context = ui.input("Extra context (domain, constraints, style)")
        success = ui.input("Success criteria")
        return {k: v for k, v in {"context": context, "success_criteria": success}.items() if v}
    if pack_id == "weekly-content-studio":
        topics = ui.input("Topics (one line or use | separator)")
        channels = ui.input("Channels (example: Instagram|Email|LinkedIn)")
        offers = ui.input("Offers / CTAs")
        return {k: v for k, v in {"topics": topics, "channels": channels, "offers": offers}.items() if v}
    if pack_id == "competitor-brief-digest":
        competitors = ui.input("Competitors")
        positioning = ui.input("Your positioning")
        objectives = ui.input("Objectives")
        return {
            k: v
            for k, v in {"competitors": competitors, "positioning": positioning, "objectives": objectives}.items()
            if v
        }
    inbox = ui.input("Inbox messages")
    leads = ui.input("Leads")
    slots = ui.input("Booking slots")
    return {k: v for k, v in {"inbox": inbox, "leads": leads, "slots": slots}.items() if v}


def print_doctor_summary(ui: UI, api_url: str, api_key: str) -> None:
    ui.note("")
    ui.note(strong("Runtime Doctor"))
    try:
        report = _runtime_client(api_url, api_key).get_doctor()
    except Exception as exc:
        ui.note(f"{err('failed:')} doctor failed: {exc}")
        return

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    p = int(summary.get("pass", 0) or 0)
    w = int(summary.get("warn", 0) or 0)
    f = int(summary.get("fail", 0) or 0)
    head = ok(f"pass={p}") if f == 0 else warn(f"pass={p}")
    warn_text = warn(f"warn={w}") if w else muted("warn=0")
    fail_text = err(f"fail={f}") if f else muted("fail=0")
    ui.note(f"{strong('Summary')}: {head}  {warn_text}  {fail_text}")

    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for raw in checks[:10]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "unknown")
        status = str(raw.get("status") or "unknown").lower()
        detail = str(raw.get("detail") or "")
        badge = {
            "pass": ok("PASS"),
            "warn": warn("WARN"),
            "fail": err("FAIL"),
        }.get(status, muted(status.upper()))
        ui.note(f"  - {name:<28} {badge}  {detail}")
