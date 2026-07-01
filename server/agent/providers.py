"""Provider routing — one place for base_url + model resolution.

DeepSeek's Anthropic-compatible endpoint rejects Anthropic model names, so we
override to a known DeepSeek model when routing there."""

def resolve_provider(api_key: str, requested_model: str) -> tuple[str, str]:
    """Return (base_url, model) for the given API key."""
    if api_key.startswith("sk-ant"):
        return "https://api.anthropic.com", requested_model
    return "https://api.deepseek.com/anthropic", "deepseek-chat"
