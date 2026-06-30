from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core import Choice
from ..clients.runtime import RuntimeClient


PROVIDER_TO_RUNTIME_PROVIDER: Dict[str, Optional[str]] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
    "xai": None,
    "mistral": None,
    "openrouter": None,
    "qwen": None,
    "zai": None,
    "huggingface": None,
    "custom": None,
    "skip": None,
}

PROVIDER_AUTH_METHODS: Dict[str, List[Choice]] = {
    "openai": [
        Choice("openai_codex_vault", "Use existing Codex session", "Reuse token from Codex auth vault/env"),
        Choice("openai_codex_device", "Codex device sign-in", "Open browser/device code flow now"),
        Choice("openai_codex_oauth", "Paste Codex access token", "Manual token entry"),
        Choice("openai_api_key", "OpenAI API key", "BYOK API key"),
    ],
    "anthropic": [
        Choice("anthropic_setup_token", "Setup-token", "Claude setup-token flow"),
        Choice("anthropic_api_key", "Anthropic API key", "Direct API key"),
    ],
    "google": [
        Choice("google_gemini_api_key", "Gemini API key", "Direct API key"),
        Choice("google_vertex_access_token", "Google Vertex access token", "Access token + project/location"),
    ],
    "qwen": [Choice("qwen_oauth", "Qwen OAuth", "OAuth token flow")],
    "chutes": [Choice("chutes_oauth", "Chutes OAuth", "OAuth token flow")],
}

AUTH_RUNTIME_PROVIDER: Dict[str, Optional[str]] = {
    "openai_codex_vault": "openai",
    "openai_codex_device": "openai",
    "openai_codex_oauth": "openai",
    "openai_api_key": "openai",
    "anthropic_setup_token": "anthropic",
    "anthropic_api_key": "anthropic",
    "google_gemini_api_key": "gemini",
    "google_vertex_access_token": "vertex",
}

MODEL_FILTER_TO_RUNTIME_PROVIDER: Dict[str, str] = {
    "openai": "openai",
    "openai-codex": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
    "google-gemini-cli": "gemini",
    "google-vertex": "vertex",
}


def default_provider_auth_methods(provider_key: str) -> List[Choice]:
    methods = PROVIDER_AUTH_METHODS.get(provider_key)
    if methods:
        return list(methods)
    return [Choice("api_key", "API key", "Use provider API key")]


def resolve_runtime_provider(provider_group_key: str, auth_method_key: str) -> Optional[str]:
    from_auth = AUTH_RUNTIME_PROVIDER.get((auth_method_key or "").strip().lower())
    if from_auth:
        return from_auth
    return PROVIDER_TO_RUNTIME_PROVIDER.get((provider_group_key or "").strip().lower())


def resolve_model_filter_provider(model_filter_key: str) -> Optional[str]:
    key = (model_filter_key or "").strip().lower()
    if not key or key == "*":
        return None
    if key in {"openai", "anthropic", "gemini", "vertex"}:
        return key
    return MODEL_FILTER_TO_RUNTIME_PROVIDER.get(key)


def model_filter_choices(
    api_url: str,
    api_key: str,
    base_options: List[Choice],
) -> List[Choice]:
    base: Dict[str, Choice] = {item.key: item for item in base_options}
    try:
        runtime_providers = RuntimeClient(api_url=api_url, api_key=api_key).list_provider_catalog()
        for provider in runtime_providers:
            pid = str(provider.key or "").strip().lower()
            if pid:
                existing = base.get(pid)
                if not existing:
                    base[pid] = Choice(pid, provider.label or pid, provider.note or "")
                elif (not existing.note) and provider.note:
                    base[pid] = Choice(existing.key, existing.label, provider.note)
    except Exception:
        pass
    options = list(base.values())
    options.sort(key=lambda item: (item.key != "*", item.label.lower()))
    return options


def default_model_filter_index(options: List[Choice], runtime_provider: Optional[str]) -> int:
    provider_key = (runtime_provider or "").strip().lower()
    if not provider_key:
        return 0
    aliases = {
        "gemini": ["gemini", "google", "google-gemini-cli"],
        "vertex": ["vertex", "google-vertex"],
        "openai": ["openai", "openai-codex"],
    }.get(provider_key, [provider_key])
    alias_set = set(aliases)
    for idx, item in enumerate(options):
        if item.key in alias_set:
            return idx
    return 0


def prompt_provider_auth_choice_grouped(
    *,
    ui: Any,
    provider_prompt: str,
    provider_groups: List[Choice],
    method_prompt_suffix: str,
    title: str,
    runtime_provider_choices: Optional[List[Choice]] = None,
) -> Tuple[Choice, Choice]:
    merged_groups: List[Choice] = []
    runtime_map = {
        str(item.key or "").strip().lower(): item
        for item in (runtime_provider_choices or [])
        if str(item.key or "").strip()
    }
    skip_choice: Optional[Choice] = None
    for provider in provider_groups:
        key = str(provider.key or "").strip().lower()
        if key == "skip":
            skip_choice = provider
            continue
        runtime_choice = runtime_map.pop(key, None)
        note = provider.note
        if runtime_choice and runtime_choice.note:
            note = runtime_choice.note
        merged_groups.append(Choice(provider.key, provider.label, note))
    for key in sorted(runtime_map.keys()):
        if key == "skip":
            continue
        runtime_choice = runtime_map[key]
        merged_groups.append(
            Choice(
                key,
                runtime_choice.label or key,
                runtime_choice.note or "",
            )
        )
    if skip_choice:
        merged_groups.append(skip_choice)

    while True:
        provider_group = ui.choose(provider_prompt, merged_groups, default_index=0, title=title)
        if provider_group.key == "skip":
            return provider_group, Choice("skip", "Skip for now", "")

        methods = default_provider_auth_methods(provider_group.key)
        if len(methods) == 1:
            return provider_group, methods[0]

        method_choice = ui.choose(
            f"{provider_group.label} {method_prompt_suffix}",
            methods + [Choice("__back", "Back", "Choose another provider")],
            default_index=0,
            title=title,
        )
        if method_choice.key == "__back":
            continue
        return provider_group, method_choice
