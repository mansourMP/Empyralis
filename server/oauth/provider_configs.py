"""OAuth provider configs for the 5 target providers.

Port of v1's OAUTH_PROVIDER_CONFIGS from legacy/server_modules/connection_oauth_service.py,
stripped to the 5 MVP providers with read-only scopes for Google."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthProviderConfig:
    label: str
    client_id_env: str
    client_secret_env: str
    scopes: tuple[str, ...] = ()
    auth_url: str = ""
    token_url: str = ""
    auth_params: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, OAuthProviderConfig] = {
    "google_workspace": OAuthProviderConfig(
        label="Google Workspace",
        client_id_env="GOOGLE_WORKSPACE_OAUTH_CLIENT_ID",
        client_secret_env="GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET",
        scopes=(
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        auth_params={"access_type": "offline", "prompt": "consent select_account"},
    ),
    "slack": OAuthProviderConfig(
        label="Slack",
        client_id_env="SLACK_CLIENT_ID",
        client_secret_env="SLACK_CLIENT_SECRET",
        scopes=("channels:read", "channels:history", "chat:write", "users:read"),
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
    ),
    "notion": OAuthProviderConfig(
        label="Notion",
        client_id_env="NOTION_OAUTH_CLIENT_ID",
        client_secret_env="NOTION_OAUTH_CLIENT_SECRET",
        scopes=(),
        auth_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        auth_params={"owner": "user"},
    ),
}


def get_provider(provider_key: str) -> OAuthProviderConfig:
    config = PROVIDERS.get(provider_key.strip().lower())
    if config is None:
        raise ValueError(f"Unknown OAuth provider: {provider_key}")
    return config
