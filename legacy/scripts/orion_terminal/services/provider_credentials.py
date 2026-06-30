from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import question_catalog as q
from ..clients.runtime import RuntimeClient
from ..core import Choice, UI, err, muted, ok, warn
from ..spinner import ProgressSpinner
from ..text_format import mask_credential_label
from ..transcript import choice_log, flag_log, value_log
from .setup_sessions import (
    complete_setup_session as service_complete_setup_session,
    create_setup_session as service_create_setup_session,
    setup_action as service_setup_action,
)


def _runtime_client(api_url: str, api_key: str) -> RuntimeClient:
    return RuntimeClient(api_url=api_url, api_key=api_key)


def detect_openai_oauth_env_token() -> Tuple[str, str]:
    auth_file = Path(os.getenv("CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json"))).expanduser()
    file_token = ""
    if auth_file.exists():
        try:
            payload = json.loads(auth_file.read_text(encoding="utf-8"))
            tokens = payload.get("tokens") if isinstance(payload, dict) else None
            if isinstance(tokens, dict):
                file_token = str(tokens.get("access_token") or "").strip()
        except Exception:
            file_token = ""
    candidates = [
        ("CODEX_OAUTH_TOKEN", os.getenv("CODEX_OAUTH_TOKEN")),
        ("OPENAI_OAUTH_TOKEN", os.getenv("OPENAI_OAUTH_TOKEN")),
        ("OPENAI_ACCESS_TOKEN", os.getenv("OPENAI_ACCESS_TOKEN")),
        ("CODEX_AUTH_FILE", file_token),
    ]
    for source, raw in candidates:
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            return token, source
    return "", ""


def run_codex_device_auth(ui: UI) -> Tuple[bool, str]:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return False, "Codex CLI is not installed or not in PATH."

    ui.note(muted("Launching Codex device sign-in. Complete browser steps, then return here."))
    try:
        completed = subprocess.run([codex_bin, "login", "--device-auth"])
    except Exception as exc:
        return False, f"Could not start Codex login: {exc}"

    if completed.returncode != 0:
        return False, "Codex device sign-in did not complete. It may be blocked by your workspace policy."

    token, source = detect_openai_oauth_env_token()
    if token:
        return True, f"Codex token detected from {source}."
    return False, "Codex sign-in completed but no token was found in env or ~/.codex/auth.json."


def setup_create_session(
    api_url: str,
    api_key: str,
    workspace_id: str,
    provider: Optional[str] = None,
    flow: Optional[str] = None,
) -> Dict[str, Any]:
    return service_create_setup_session(
        _runtime_client(api_url, api_key),
        workspace_id=workspace_id,
        provider=provider,
        flow=flow,
    )


def setup_action(api_url: str, api_key: str, session_id: str, action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return service_setup_action(
        _runtime_client(api_url, api_key),
        session_id=session_id,
        action=action,
        payload=payload,
    )


def setup_complete(api_url: str, api_key: str, session_id: str) -> Dict[str, Any]:
    return service_complete_setup_session(_runtime_client(api_url, api_key), session_id)


def create_provider_credential(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    provider: str,
    preferred_auth_method: Optional[str] = None,
) -> str:
    provider_id = provider.lower().strip()
    label_default = f"{provider_id}-credential"
    label = ui.input(q.Q_CREDENTIAL_LABEL, default=label_default, required=True)
    preferred = (preferred_auth_method or "").strip().lower()

    if provider_id == "openai":
        env_oauth_token, env_oauth_source = detect_openai_oauth_env_token()
        if preferred in {"openai_codex_vault", "oauth_env", "codex_vault"}:
            auth_choice = Choice("oauth_env", "Use existing Codex session", "Reuse token from Codex auth vault/env")
            choice_log(ui, "OpenAI auth method", auth_choice)
            token, source = detect_openai_oauth_env_token()
            if not token:
                raise RuntimeError("No Codex token found. Run 'codex login --device-auth' or choose another auth method.")
            credentials = {"access_token": token}
            value_log(ui, "Credential source", source)
        elif preferred in {"openai_codex_device", "codex_device", "device_auth"}:
            auth_choice = Choice("codex_device", "Sign in with Codex (device code)", "Browser sign-in, then reuse token vault")
            choice_log(ui, "OpenAI auth method", auth_choice)
            ok_login, detail = run_codex_device_auth(ui)
            if not ok_login:
                raise RuntimeError(detail)
            ui.note(f"{ok('done:')} {detail}")
            token, source = detect_openai_oauth_env_token()
            if not token:
                raise RuntimeError("Codex token not found after successful sign-in.")
            credentials = {"access_token": token}
            value_log(ui, "Credential source", source)
        elif preferred in {"openai_codex_oauth", "oauth", "oauth_token", "codex_oauth"}:
            auth_choice = Choice("oauth_token", "Paste Codex access token", "Manual token entry")
            choice_log(ui, "OpenAI auth method", auth_choice)
            ui.note(muted("Interactive browser OAuth is not wired in this build yet. Paste token manually."))
            access_token = ui.input(q.Q_CREDENTIAL_OPENAI_OAUTH_TOKEN, required=True, secret=True)
            credentials = {"access_token": access_token}
        elif preferred in {"openai_api_key", "api_key"}:
            auth_choice = Choice("api_key", "OpenAI API key", "Use BYOK API key")
            choice_log(ui, "OpenAI auth method", auth_choice)
            api_secret = ui.input(q.Q_CREDENTIAL_OPENAI_API_KEY, required=True, secret=True)
            credentials = {"api_key": api_secret}
        else:
            auth_options: List[Choice] = []
            if env_oauth_token:
                auth_options.append(
                    Choice(
                        "oauth_env",
                        "Codex OAuth from environment",
                        f"Use token from {env_oauth_source}",
                    )
                )
            auth_options.extend(
                [
                    Choice("codex_device", "Sign in with Codex (device code)", "Browser sign-in, then reuse token vault"),
                    Choice("oauth_token", "Codex OAuth access token", "Paste access token from Codex sign-in"),
                    Choice("api_key", "OpenAI API key", "Use BYOK API key"),
                ]
            )
            auth_choice = ui.choose(
                q.Q_CREDENTIAL_OPENAI_TYPE,
                auth_options,
                default_index=0,
                title=q.TITLE_CREDENTIAL,
            )
            choice_log(ui, "OpenAI auth method", auth_choice)
            if auth_choice.key == "oauth_env":
                credentials = {"access_token": env_oauth_token}
            elif auth_choice.key == "codex_device":
                ok_login, detail = run_codex_device_auth(ui)
                if not ok_login:
                    raise RuntimeError(detail)
                ui.note(f"{ok('done:')} {detail}")
                token, source = detect_openai_oauth_env_token()
                if not token:
                    raise RuntimeError("Codex token not found after successful sign-in.")
                credentials = {"access_token": token}
                value_log(ui, "Credential source", source)
            elif auth_choice.key == "oauth_token":
                ui.note(muted("Interactive browser OAuth is not wired in this build yet. Paste token manually."))
                access_token = ui.input(q.Q_CREDENTIAL_OPENAI_OAUTH_TOKEN, required=True, secret=True)
                credentials = {"access_token": access_token}
            else:
                api_secret = ui.input(q.Q_CREDENTIAL_OPENAI_API_KEY, required=True, secret=True)
                credentials = {"api_key": api_secret}
    elif provider_id == "anthropic":
        key_name = "Anthropic setup-token" if preferred in {"anthropic_setup_token", "setup_token"} else q.Q_CREDENTIAL_ANTHROPIC_API_KEY
        api_secret = ui.input(key_name, required=True, secret=True)
        credentials = {"api_key": api_secret}
    elif provider_id == "gemini":
        api_secret = ui.input(q.Q_CREDENTIAL_GEMINI_API_KEY, required=True, secret=True)
        credentials = {"api_key": api_secret}
    elif provider_id == "vertex":
        access_token = ui.input(q.Q_CREDENTIAL_VERTEX_TOKEN, required=True, secret=True)
        project_id = ui.input(q.Q_CREDENTIAL_VERTEX_PROJECT, required=True)
        location = ui.input(q.Q_CREDENTIAL_VERTEX_LOCATION, default="us-central1", required=True)
        credentials = {
            "access_token": access_token,
            "project_id": project_id,
            "location": location,
        }
    else:
        raw = ui.input(q.Q_CREDENTIAL_JSON, required=True)
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError("Credential JSON must be a non-empty object.")
            credentials = parsed
        except Exception as exc:
            raise RuntimeError(f"Invalid credential JSON: {exc}") from exc

    payload = {
        "label": label,
        "provider": provider_id,
        "workspace_id": workspace_id,
        "mode": "byok",
        "credentials": credentials,
        "metadata": {"source": "orion_terminal_wizard"},
    }
    created = _runtime_client(api_url, api_key).create_credential(payload)
    credential_id = str(created.get("id") or "").strip()
    if not credential_id:
        raise RuntimeError("Credential was created without an id.")
    ui.note(f"{ok('done:')} Saved credential id={credential_id}")
    return credential_id


def verify_provider_credential(
    api_url: str,
    api_key: str,
    provider: str,
    credential_id: str,
    workspace_id: str,
) -> Tuple[bool, str]:
    provider_id = provider.lower().strip()
    try:
        models = _runtime_client(api_url, api_key).list_provider_models(
            provider_id=provider_id,
            workspace_id=workspace_id,
            credential_id=credential_id,
        )
        return True, f"Model list loaded ({len(models)} models)."
    except Exception as exc:
        detail = str(exc)
        lowered = detail.lower()
        if provider_id == "openai" and ("401" in lowered or "403" in lowered or "unauthorized" in lowered):
            detail += " If this is Codex OAuth, refresh token or use an OpenAI API key."
        return False, detail


def create_or_update_profile(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    provider: str,
    credential_id: str,
) -> Optional[str]:
    label = ui.input(q.Q_PROFILE_LABEL, default=f"{provider}-primary", required=True)
    model = ui.input(q.Q_PROFILE_MODEL)
    priority_raw = ui.input(q.Q_PROFILE_PRIORITY, default="100")
    try:
        priority = int(priority_raw)
    except Exception:
        priority = 100
    payload = {
        "provider": provider,
        "label": label,
        "credential_id": credential_id,
        "workspace_id": workspace_id,
        "priority": priority,
        "enabled": True,
        "model": model or None,
    }
    result = _runtime_client(api_url, api_key).create_provider_profile(payload)
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    profile_id = str(item.get("id") or "").strip()
    if profile_id:
        ui.note(f"{ok('done:')} Profile created id={profile_id}")
        return profile_id
    ui.note(f"{warn('warning:')} Profile upsert returned no id.")
    return None


def provider_and_credential_section(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    session_id: str,
) -> Tuple[Optional[str], Optional[str], bool]:
    load_spinner = ProgressSpinner("loading providers")
    load_spinner.tick("loading providers")
    try:
        providers = _runtime_client(api_url, api_key).list_provider_catalog()
        load_spinner.done(True, "providers ready")
    except Exception as exc:
        load_spinner.done(False, "providers failed")
        ui.note(f"{err('failed:')} Failed to load provider catalog: {exc}")
        return None, None, False
    if not providers:
        ui.note(f"{err('failed:')} Runtime returned no providers catalog.")
        return None, None, False

    provider_choice = ui.choose(
        q.Q_PREFLIGHT_PROVIDER,
        providers + [Choice("", "Skip for now", "Continue without provider setup")],
        default_index=0,
        title=q.TITLE_ONBOARDING,
    )
    if not provider_choice.key:
        flag_log(ui, "Provider setup", False)
        return None, None, False

    choice_log(ui, "Provider", provider_choice)
    provider = provider_choice.key
    setup_action(api_url, api_key, session_id, "select_provider", {"provider": provider})

    existing = _runtime_client(api_url, api_key).list_workspace_credentials(workspace_id, provider=provider)
    credential_choice = ui.choose(
        q.Q_SETUP_CREDENTIAL_HANDLING,
        [
            Choice("existing", "Use existing credential", f"Found {len(existing)}"),
            Choice("new", "Add new credential", "Create and store now"),
            Choice("skip", "Skip provider auth", "Leave setup incomplete"),
        ],
        default_index=0 if existing else 1,
        title=q.TITLE_ONBOARDING,
    )
    choice_log(ui, "Credential handling", credential_choice)

    credential_id = ""
    if credential_choice.key == "skip":
        flag_log(ui, "Provider auth", False)
        return provider, None, False

    if credential_choice.key == "existing":
        if not existing:
            ui.note(f"{warn('warning:')} No existing credentials found for provider '{provider}'.")
            credential_choice = Choice("new", "Add new credential", "")
        else:
            cred_options: List[Choice] = []
            for item in existing:
                cid = str(item.get("id") or "")
                raw_label = str(item.get("label") or cid)
                mode = str(item.get("mode") or "byok")
                cred_options.append(Choice(cid, mask_credential_label(raw_label), f"mode={mode}"))
            selected_cred = ui.choose(q.Q_SETUP_SELECT_CREDENTIAL, cred_options, default_index=0, title=q.TITLE_ONBOARDING)
            credential_id = selected_cred.key
            value_log(ui, "Credential selected", mask_credential_label(selected_cred.label))

    if credential_choice.key == "new":
        credential_id = create_provider_credential(ui, api_url, api_key, workspace_id, provider)

    if not credential_id:
        return provider, None, False

    setup_action(api_url, api_key, session_id, "submit_credential", {"credential_id": credential_id})
    verified, detail = verify_provider_credential(api_url, api_key, provider, credential_id, workspace_id)
    setup_action(api_url, api_key, session_id, "verify", {"ok": verified, "error": None if verified else detail})
    if verified:
        ui.note(f"{ok('done:')} Credential verified: {detail}")
    else:
        ui.note(f"{warn('warning:')} Credential verification failed: {detail}")
    return provider, credential_id, verified
