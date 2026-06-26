from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import HTTPException, Request

from server_modules import connectors_actions
from server_modules.connectors import slack_connector
from server_modules.schemas import ConnectorCreate


_STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class OAuthProviderConfig:
    label: str
    env_vars: Dict[str, tuple[str, ...]]
    scopes: tuple[str, ...]
    auth_url: str
    token_url: str
    auth_method: str
    token_parser: str
    profile_probe: str | None
    scope_separator: str = " "
    auth_params: Dict[str, str] = field(default_factory=dict)
    include_response_type: bool = True
    include_redirect_uri_in_authorization_url: bool = True
    slack_authorize_helper: bool = False
    token_auth: str = "client_secret_post"
    include_client_id_in_token_body: bool = True
    include_client_secret_in_token_body: bool = True
    include_redirect_uri_in_token_body: bool = True
    token_grant_type: str | None = "authorization_code"
    token_request_format: str = "form"


OAUTH_PROVIDER_CONFIGS: Dict[str, OAuthProviderConfig] = {
    "google_workspace": OAuthProviderConfig(
        label="Google Workspace",
        env_vars={
            "client_id": ("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_CLIENT_ID"),
            "client_secret": ("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
        },
        scopes=(
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
        ),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://www.googleapis.com/oauth2/v3/userinfo",
        auth_params={"access_type": "offline", "prompt": "consent select_account"},
    ),
    "github": OAuthProviderConfig(
        label="GitHub",
        env_vars={
            "client_id": ("GITHUB_OAUTH_CLIENT_ID", "GITHUB_CLIENT_ID"),
            "client_secret": ("GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_CLIENT_SECRET"),
        },
        scopes=("repo", "read:user", "user:email"),
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.github.com/user",
        include_response_type=False,
    ),
    "microsoft_365": OAuthProviderConfig(
        label="Microsoft 365",
        env_vars={
            "client_id": ("MICROSOFT_365_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_CLIENT_ID"),
            "client_secret": ("MICROSOFT_365_OAUTH_CLIENT_SECRET", "MICROSOFT_OAUTH_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET"),
        },
        scopes=(
            "offline_access",
            "User.Read",
            "Mail.ReadWrite",
            "Mail.Send",
            "Calendars.ReadWrite",
            "Files.ReadWrite.All",
        ),
        auth_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://graph.microsoft.com/v1.0/me",
        auth_params={"prompt": "select_account"},
    ),
    "slack": OAuthProviderConfig(
        label="Slack",
        env_vars={
            "client_id": ("SLACK_CLIENT_ID",),
            "client_secret": ("SLACK_CLIENT_SECRET",),
        },
        scopes=tuple(slack_connector.DEFAULT_SLACK_BOT_SCOPES),
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        auth_method="authorization_code",
        token_parser="custom",
        profile_probe="https://slack.com/api/auth.test",
        scope_separator=",",
        slack_authorize_helper=True,
    ),
    "notion": OAuthProviderConfig(
        label="Notion",
        env_vars={
            "client_id": ("NOTION_OAUTH_CLIENT_ID", "NOTION_CLIENT_ID"),
            "client_secret": ("NOTION_OAUTH_CLIENT_SECRET", "NOTION_CLIENT_SECRET"),
        },
        scopes=(),
        auth_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        auth_method="authorization_code",
        token_parser="custom",
        profile_probe="https://api.notion.com/v1/users/me",
        auth_params={"owner": "user"},
    ),
    "linear": OAuthProviderConfig(
        label="Linear",
        env_vars={
            "client_id": ("LINEAR_OAUTH_CLIENT_ID", "LINEAR_CLIENT_ID"),
            "client_secret": ("LINEAR_OAUTH_CLIENT_SECRET", "LINEAR_CLIENT_SECRET"),
        },
        scopes=("read", "write"),
        auth_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.linear.app/graphql",
        scope_separator=",",
    ),
    "dropbox": OAuthProviderConfig(
        label="Dropbox",
        env_vars={
            "client_id": ("DROPBOX_OAUTH_CLIENT_ID", "DROPBOX_CLIENT_ID", "DROPBOX_APP_KEY"),
            "client_secret": ("DROPBOX_OAUTH_CLIENT_SECRET", "DROPBOX_CLIENT_SECRET", "DROPBOX_APP_SECRET"),
        },
        scopes=("files.metadata.read", "files.content.read", "files.content.write", "sharing.read", "sharing.write"),
        auth_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.dropboxapi.com/2/users/get_current_account",
        auth_params={"token_access_type": "offline"},
    ),
    "discord": OAuthProviderConfig(
        label="Discord",
        env_vars={
            "client_id": ("EMPYRALIS_DISCORD_APPLICATION_ID", "DISCORD_CLIENT_ID"),
            "client_secret": ("DISCORD_CLIENT_SECRET",),
        },
        scopes=("bot", "identify"),
        auth_url="https://discord.com/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://discord.com/api/users/@me",
        auth_params={"permissions": "274877908992"},
    ),
    "figma": OAuthProviderConfig(
        label="Figma",
        env_vars={
            "client_id": ("FIGMA_CLIENT_ID",),
            "client_secret": ("FIGMA_CLIENT_SECRET",),
        },
        scopes=("current_user:read", "file_metadata:read", "file_content:read", "file_comments:read"),
        auth_url="https://www.figma.com/oauth",
        token_url="https://api.figma.com/v1/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.figma.com/v1/me",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "todoist": OAuthProviderConfig(
        label="Todoist",
        env_vars={
            "client_id": ("TODOIST_CLIENT_ID",),
            "client_secret": ("TODOIST_CLIENT_SECRET",),
        },
        scopes=("data:read_write",),
        auth_url="https://app.todoist.com/oauth/authorize",
        token_url="https://todoist.com/oauth/access_token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.todoist.com/api/v1/projects?limit=1",
    ),
    "airtable": OAuthProviderConfig(
        label="Airtable",
        env_vars={
            "client_id": ("AIRTABLE_CLIENT_ID",),
            "client_secret": ("AIRTABLE_CLIENT_SECRET",),
        },
        scopes=("data.records:read", "data.records:write", "schema.bases:read"),
        auth_url="https://airtable.com/oauth2/v1/authorize",
        token_url="https://airtable.com/oauth2/v1/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.airtable.com/v0/meta/whoami",
        token_auth="basic",
        include_client_id_in_token_body=True,
        include_client_secret_in_token_body=False,
    ),
    "canva": OAuthProviderConfig(
        label="Canva",
        env_vars={
            "client_id": ("CANVA_CLIENT_ID",),
            "client_secret": ("CANVA_CLIENT_SECRET",),
        },
        scopes=("profile:read", "design:meta:read", "design:content:read", "folder:read", "asset:read"),
        auth_url="https://www.canva.com/api/oauth/authorize",
        token_url="https://api.canva.com/rest/v1/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.canva.com/rest/v1/users/me/profile",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "asana": OAuthProviderConfig(
        label="Asana",
        env_vars={
            "client_id": ("ASANA_CLIENT_ID",),
            "client_secret": ("ASANA_CLIENT_SECRET",),
        },
        scopes=("tasks:read", "tasks:write", "projects:read", "users:read", "workspaces:read"),
        auth_url="https://app.asana.com/-/oauth_authorize",
        token_url="https://app.asana.com/-/oauth_token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://app.asana.com/api/1.0/users/me",
    ),
    "hubspot": OAuthProviderConfig(
        label="HubSpot",
        env_vars={
            "client_id": ("HUBSPOT_CLIENT_ID",),
            "client_secret": ("HUBSPOT_CLIENT_SECRET",),
        },
        scopes=(
            "oauth",
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.companies.read",
            "crm.objects.deals.read",
        ),
        auth_url="https://app.hubspot.com/oauth/authorize",
        token_url="https://api.hubapi.com/oauth/v3/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
    ),
    "zoom": OAuthProviderConfig(
        label="Zoom",
        env_vars={
            "client_id": ("ZOOM_CLIENT_ID",),
            "client_secret": ("ZOOM_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://zoom.us/oauth/authorize",
        token_url="https://zoom.us/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.zoom.us/v2/users/me",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "calendly": OAuthProviderConfig(
        label="Calendly",
        env_vars={
            "client_id": ("CALENDLY_CLIENT_ID",),
            "client_secret": ("CALENDLY_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://auth.calendly.com/oauth/authorize",
        token_url="https://auth.calendly.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.calendly.com/users/me",
    ),
    "clickup": OAuthProviderConfig(
        label="ClickUp",
        env_vars={
            "client_id": ("CLICKUP_CLIENT_ID",),
            "client_secret": ("CLICKUP_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://app.clickup.com/api",
        token_url="https://api.clickup.com/api/v2/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.clickup.com/api/v2/user",
        include_response_type=False,
        include_redirect_uri_in_token_body=False,
        token_grant_type=None,
    ),
    "jira": OAuthProviderConfig(
        label="Jira",
        env_vars={
            "client_id": ("ATLASSIAN_CLIENT_ID", "JIRA_CLIENT_ID"),
            "client_secret": ("ATLASSIAN_CLIENT_SECRET", "JIRA_CLIENT_SECRET"),
        },
        scopes=("read:me", "read:jira-user", "read:jira-work", "write:jira-work", "offline_access"),
        auth_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.atlassian.com/me",
        auth_params={"audience": "api.atlassian.com", "prompt": "consent"},
        token_request_format="json",
    ),
    "stripe": OAuthProviderConfig(
        label="Stripe",
        env_vars={
            "client_id": ("STRIPE_CLIENT_ID",),
            "client_secret": ("STRIPE_CLIENT_SECRET", "STRIPE_SECRET_KEY"),
        },
        scopes=("read_write",),
        auth_url="https://connect.stripe.com/oauth/authorize",
        token_url="https://connect.stripe.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.stripe.com/v1/account",
        include_client_id_in_token_body=False,
        include_redirect_uri_in_token_body=False,
    ),
    "salesforce": OAuthProviderConfig(
        label="Salesforce",
        env_vars={
            "client_id": ("SALESFORCE_CLIENT_ID",),
            "client_secret": ("SALESFORCE_CLIENT_SECRET",),
        },
        scopes=("openid", "api", "refresh_token"),
        auth_url="https://login.salesforce.com/services/oauth2/authorize",
        token_url="https://login.salesforce.com/services/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://login.salesforce.com/services/oauth2/userinfo",
    ),
    "webflow": OAuthProviderConfig(
        label="Webflow",
        env_vars={
            "client_id": ("WEBFLOW_CLIENT_ID",),
            "client_secret": ("WEBFLOW_CLIENT_SECRET",),
        },
        scopes=("sites:read", "pages:read", "cms:read", "assets:read", "forms:read"),
        auth_url="https://webflow.com/oauth/authorize",
        token_url="https://api.webflow.com/oauth/access_token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.webflow.com/v2/token/authorized_by",
    ),
    "monday": OAuthProviderConfig(
        label="monday.com",
        env_vars={
            "client_id": ("MONDAY_CLIENT_ID",),
            "client_secret": ("MONDAY_CLIENT_SECRET",),
        },
        scopes=("me:read", "account:read", "boards:read", "boards:write", "updates:read", "updates:write", "workspaces:read"),
        auth_url="https://auth.monday.com/oauth2/authorize",
        token_url="https://auth.monday.com/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.monday.com/v2",
        token_grant_type=None,
    ),
    "box": OAuthProviderConfig(
        label="Box",
        env_vars={
            "client_id": ("BOX_CLIENT_ID",),
            "client_secret": ("BOX_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://account.box.com/api/oauth2/authorize",
        token_url="https://api.box.com/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.box.com/2.0/users/me",
    ),
    "gitlab": OAuthProviderConfig(
        label="GitLab",
        env_vars={
            "client_id": ("GITLAB_CLIENT_ID",),
            "client_secret": ("GITLAB_CLIENT_SECRET",),
        },
        scopes=("read_user", "read_api", "api"),
        auth_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://gitlab.com/api/v4/user",
    ),
    "bitbucket": OAuthProviderConfig(
        label="Bitbucket",
        env_vars={
            "client_id": ("BITBUCKET_CLIENT_ID",),
            "client_secret": ("BITBUCKET_CLIENT_SECRET",),
        },
        scopes=("account", "repository", "pullrequest", "issue"),
        auth_url="https://bitbucket.org/site/oauth2/authorize",
        token_url="https://bitbucket.org/site/oauth2/access_token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.bitbucket.org/2.0/user",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "confluence": OAuthProviderConfig(
        label="Confluence",
        env_vars={
            "client_id": ("ATLASSIAN_CLIENT_ID", "CONFLUENCE_CLIENT_ID"),
            "client_secret": ("ATLASSIAN_CLIENT_SECRET", "CONFLUENCE_CLIENT_SECRET"),
        },
        scopes=(
            "read:me",
            "read:confluence-user",
            "read:confluence-content.summary",
            "read:confluence-space.summary",
            "write:confluence-content",
            "offline_access",
        ),
        auth_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.atlassian.com/me",
        auth_params={"audience": "api.atlassian.com", "prompt": "consent"},
        token_request_format="json",
    ),
    "miro": OAuthProviderConfig(
        label="Miro",
        env_vars={
            "client_id": ("MIRO_CLIENT_ID",),
            "client_secret": ("MIRO_CLIENT_SECRET",),
        },
        scopes=("identity:read", "boards:read", "boards:write"),
        auth_url="https://miro.com/oauth/authorize",
        token_url="https://api.miro.com/v1/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.miro.com/v2/users/me",
    ),
    "mailchimp": OAuthProviderConfig(
        label="Mailchimp",
        env_vars={
            "client_id": ("MAILCHIMP_CLIENT_ID",),
            "client_secret": ("MAILCHIMP_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://login.mailchimp.com/oauth2/authorize",
        token_url="https://login.mailchimp.com/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://login.mailchimp.com/oauth2/metadata",
    ),
    "pipedrive": OAuthProviderConfig(
        label="Pipedrive",
        env_vars={
            "client_id": ("PIPEDRIVE_CLIENT_ID",),
            "client_secret": ("PIPEDRIVE_CLIENT_SECRET",),
        },
        scopes=("deals:read", "deals:write", "contacts:read", "contacts:write", "activities:read", "activities:write"),
        auth_url="https://oauth.pipedrive.com/oauth/authorize",
        token_url="https://oauth.pipedrive.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.pipedrive.com/v1/users/me",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "intercom": OAuthProviderConfig(
        label="Intercom",
        env_vars={
            "client_id": ("INTERCOM_CLIENT_ID",),
            "client_secret": ("INTERCOM_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://app.intercom.com/oauth",
        token_url="https://api.intercom.io/auth/eagle/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.intercom.io/me",
        include_response_type=False,
        include_redirect_uri_in_authorization_url=False,
        include_redirect_uri_in_token_body=False,
        token_grant_type=None,
    ),
    "docusign": OAuthProviderConfig(
        label="Docusign",
        env_vars={
            "client_id": ("DOCUSIGN_CLIENT_ID",),
            "client_secret": ("DOCUSIGN_CLIENT_SECRET",),
        },
        scopes=("signature", "extended"),
        auth_url="https://account.docusign.com/oauth/auth",
        token_url="https://account.docusign.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://account.docusign.com/oauth/userinfo",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "square": OAuthProviderConfig(
        label="Square",
        env_vars={
            "client_id": ("SQUARE_APPLICATION_ID", "SQUARE_CLIENT_ID"),
            "client_secret": ("SQUARE_APPLICATION_SECRET", "SQUARE_CLIENT_SECRET"),
        },
        scopes=("MERCHANT_PROFILE_READ", "CUSTOMERS_READ", "ORDERS_READ", "PAYMENTS_READ", "INVOICES_READ"),
        auth_url="https://connect.squareup.com/oauth2/authorize",
        token_url="https://connect.squareup.com/oauth2/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://connect.squareup.com/oauth2/token/status",
        scope_separator=",",
        token_request_format="json",
    ),
    "typeform": OAuthProviderConfig(
        label="Typeform",
        env_vars={
            "client_id": ("TYPEFORM_CLIENT_ID",),
            "client_secret": ("TYPEFORM_CLIENT_SECRET",),
        },
        scopes=("offline", "forms:read", "forms:write", "responses:read", "accounts:read"),
        auth_url="https://api.typeform.com/oauth/authorize",
        token_url="https://api.typeform.com/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.typeform.com/me",
    ),
    "quickbooks": OAuthProviderConfig(
        label="QuickBooks",
        env_vars={
            "client_id": ("QUICKBOOKS_CLIENT_ID", "INTUIT_CLIENT_ID"),
            "client_secret": ("QUICKBOOKS_CLIENT_SECRET", "INTUIT_CLIENT_SECRET"),
        },
        scopes=("openid", "profile", "email", "com.intuit.quickbooks.accounting"),
        auth_url="https://appcenter.intuit.com/connect/oauth2",
        token_url="https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://accounts.platform.intuit.com/v1/openid_connect/userinfo",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "xero": OAuthProviderConfig(
        label="Xero",
        env_vars={
            "client_id": ("XERO_CLIENT_ID",),
            "client_secret": ("XERO_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "offline_access", "accounting.contacts.read", "accounting.invoices.read", "accounting.settings.read"),
        auth_url="https://login.xero.com/identity/connect/authorize",
        token_url="https://identity.xero.com/connect/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.xero.com/connections",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
    ),
    "freshbooks": OAuthProviderConfig(
        label="FreshBooks",
        env_vars={
            "client_id": ("FRESHBOOKS_CLIENT_ID",),
            "client_secret": ("FRESHBOOKS_CLIENT_SECRET",),
        },
        scopes=("user:profile:read", "user:clients:read", "user:invoices:read", "user:expenses:read"),
        auth_url="https://my.freshbooks.com/service/auth/oauth/authorize",
        token_url="https://api.freshbooks.com/auth/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.freshbooks.com/auth/api/v1/users/me",
    ),
    "vercel": OAuthProviderConfig(
        label="Vercel",
        env_vars={
            "client_id": ("VERCEL_CLIENT_ID",),
            "client_secret": ("VERCEL_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email"),
        auth_url="https://vercel.com/oauth/authorize",
        token_url="https://api.vercel.com/login/oauth/token",
        auth_method="authorization_code",
        token_parser="standard",
        profile_probe="https://api.vercel.com/login/oauth/userinfo",
    ),
}

_CONNECTION_PROVIDER_ALIASES = {
    "google_workspace": "google_workspace",
    "gmail": "google_workspace",
    "google_calendar": "google_workspace",
    "google_drive": "google_workspace",
    "drive": "google_workspace",
    "github": "github",
    "microsoft_365": "microsoft_365",
    "outlook": "microsoft_365",
    "outlook_calendar": "microsoft_365",
    "slack": "slack",
    "notion": "notion",
    "linear": "linear",
    "dropbox": "dropbox",
    "discord_bot": "discord",
    "discord": "discord",
    "figma": "figma",
    "todoist": "todoist",
    "airtable": "airtable",
    "canva": "canva",
    "asana": "asana",
    "hubspot": "hubspot",
    "zoom": "zoom",
    "calendly": "calendly",
    "clickup": "clickup",
    "jira": "jira",
    "atlassian": "jira",
    "stripe": "stripe",
    "salesforce": "salesforce",
    "webflow": "webflow",
    "monday": "monday",
    "monday_com": "monday",
    "monday.com": "monday",
    "box": "box",
    "gitlab": "gitlab",
    "bitbucket": "bitbucket",
    "confluence": "confluence",
    "miro": "miro",
    "mailchimp": "mailchimp",
    "pipedrive": "pipedrive",
    "intercom": "intercom",
    "docusign": "docusign",
    "docu_sign": "docusign",
    "square": "square",
    "typeform": "typeform",
    "quickbooks": "quickbooks",
    "quick_books": "quickbooks",
    "xero": "xero",
    "freshbooks": "freshbooks",
    "fresh_books": "freshbooks",
    "vercel": "vercel",
}


def _env_first(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _env_flag_enabled(*names: str) -> bool:
    value = _env_first(*names).strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


def _split_scope_tokens(value: str) -> tuple[str, ...]:
    raw = str(value or "").replace(",", " ")
    scopes: list[str] = []
    seen: set[str] = set()
    for token in raw.split():
        scope = token.strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        scopes.append(scope)
    return tuple(scopes)


def _state_secret() -> str:
    return _env_first(
        "CONNECTION_OAUTH_STATE_SECRET",
        "EMPYRALIS_CONNECTION_OAUTH_STATE_SECRET",
        "CREDENTIAL_VAULT_KEY",
        "ORION_AUTH_SECRET",
        "AUTH_SECRET",
    ) or "local-dev-connection-oauth-state"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = str(value or "").strip()
    padded += "=" * ((4 - len(padded) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign(payload_segment: str) -> str:
    return _b64url_encode(
        hmac.new(
            _state_secret().encode("utf-8"),
            payload_segment.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )


def _encode_state(payload: Dict[str, Any]) -> str:
    body = {
        **payload,
        "iat": int(time.time()),
    }
    payload_segment = _b64url_encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{payload_segment}.{_sign(payload_segment)}"


def decode_state(state: str) -> Dict[str, Any]:
    raw = str(state or "").strip()
    if "." not in raw:
        raise HTTPException(status_code=400, detail="OAuth state is invalid.")
    payload_segment, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_segment), signature):
        raise HTTPException(status_code=400, detail="OAuth state is invalid.")
    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="OAuth state is invalid.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="OAuth state is invalid.")
    issued_at = int(payload.get("iat") or 0)
    if issued_at <= 0 or int(time.time()) - issued_at > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired. Start setup again.")
    return payload


def request_origin(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if forwarded_host:
        return f"{forwarded_proto or 'http'}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def callback_url(request: Request, provider: str) -> str:
    return f"{request_origin(request)}/api/connections/oauth/{urlparse.quote(provider)}/callback"


def _provider_config(provider: str) -> OAuthProviderConfig:
    normalized = str(provider or "").strip().lower()
    config = OAUTH_PROVIDER_CONFIGS.get(normalized)
    if config is None:
        raise HTTPException(status_code=409, detail=f"{_connector_label(normalized)} OAuth is not wired yet.")
    return config


def _connector_label(provider: str) -> str:
    config = OAUTH_PROVIDER_CONFIGS.get(str(provider or "").strip().lower())
    if config is not None:
        return config.label
    return provider.replace("_", " ").title()


def _provider_env(provider: str) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    config = _provider_config(provider)
    client_names = tuple(config.env_vars.get("client_id") or ())
    secret_names = tuple(config.env_vars.get("client_secret") or ())
    return (
        _env_first(*client_names),
        _env_first(*secret_names),
        client_names,
        secret_names,
    )


def ensure_oauth_configured(provider: str) -> tuple[str, str]:
    client_id, client_secret, client_names, secret_names = _provider_env(provider)
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{_connector_label(provider)} OAuth is not configured. "
                f"Set {' or '.join(client_names)} and {' or '.join(secret_names)}."
            ),
        )
    return client_id, client_secret


def oauth_provider_configured(provider: str) -> bool:
    client_id, client_secret, _client_names, _secret_names = _provider_env(provider)
    return bool(client_id and client_secret)


def provider_from_connection_id(connection_id: str) -> str:
    normalized = str(connection_id or "").strip().lower()
    provider = _CONNECTION_PROVIDER_ALIASES.get(normalized)
    if provider is not None:
        return provider
    raise HTTPException(status_code=409, detail="This connection does not have a one-click OAuth setup yet.")


def oauth_connection_configured(connection_id: str) -> bool:
    return oauth_provider_configured(provider_from_connection_id(connection_id))


def _microsoft_tenant() -> str:
    return _env_first(
        "MICROSOFT_365_OAUTH_TENANT_ID",
        "MICROSOFT_OAUTH_TENANT_ID",
        "MICROSOFT_TENANT_ID",
    ) or "common"


def _provider_url(provider: str, url_template: str) -> str:
    if provider == "microsoft_365":
        tenant = urlparse.quote(_microsoft_tenant().strip() or "common")
        return url_template.format(tenant=tenant)
    return url_template


def _effective_scopes(provider: str, config: OAuthProviderConfig) -> tuple[str, ...]:
    normalized = str(provider or "").strip().lower()
    if normalized != "google_workspace":
        return config.scopes

    explicit_scopes = _split_scope_tokens(
        _env_first("GOOGLE_WORKSPACE_OAUTH_SCOPES", "GOOGLE_OAUTH_SCOPES")
    )
    if explicit_scopes:
        return explicit_scopes

    scopes = list(config.scopes)
    if _env_flag_enabled("GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE", "GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE"):
        drive_scope = "https://www.googleapis.com/auth/drive.file"
        if drive_scope not in scopes:
            scopes.append(drive_scope)
    return tuple(scopes)


def _joined_scopes(provider: str, config: OAuthProviderConfig) -> str:
    return config.scope_separator.join(_effective_scopes(provider, config))


def _new_pkce_nonce() -> str:
    return _b64url_encode(os.urandom(32))


def _pkce_verifier(*, provider: str, workspace_id: str, nonce: str) -> str:
    payload = f"pkce:v1:{provider}:{workspace_id}:{nonce}".encode("utf-8")
    return _b64url_encode(hmac.new(_state_secret().encode("utf-8"), payload, hashlib.sha256).digest())


def _pkce_challenge(code_verifier: str) -> str:
    return _b64url_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())


def start_oauth(
    *,
    provider: str,
    workspace_id: str,
    surface: str | None,
    request: Request,
    user_id: str = "",
) -> Dict[str, Any]:
    config = _provider_config(provider)
    client_id, _client_secret = ensure_oauth_configured(provider)
    redirect_uri = callback_url(request, provider)
    state_payload: Dict[str, Any] = {
        "provider": provider,
        "workspace_id": workspace_id,
        "surface": str(surface or "sage").strip() or "sage",
        "user_id": str(user_id or "").strip(),
    }
    code_verifier = ""
    if config.auth_method == "pkce":
        state_payload["pkce_nonce"] = _new_pkce_nonce()
        code_verifier = _pkce_verifier(
            provider=provider,
            workspace_id=workspace_id,
            nonce=str(state_payload["pkce_nonce"]),
        )
    state = _encode_state(state_payload)
    if config.slack_authorize_helper:
        authorization_url = slack_connector.oauth_authorize_url(
            redirect_uri,
            state=state,
            client_id=client_id,
        )
    else:
        query = {
            "client_id": client_id,
            "state": state,
        }
        if config.include_redirect_uri_in_authorization_url:
            query["redirect_uri"] = redirect_uri
        if config.include_response_type:
            query["response_type"] = "code"
        scopes = _effective_scopes(provider, config)
        if scopes:
            query["scope"] = _joined_scopes(provider, config)
        if config.auth_method == "pkce":
            query["code_challenge"] = _pkce_challenge(code_verifier)
            query["code_challenge_method"] = "S256"
        query.update(config.auth_params)
        authorization_url = _provider_url(provider, config.auth_url) + "?" + urlparse.urlencode(query)
    return {
        "ok": True,
        "next_action": "oauth_redirect",
        "provider": provider,
        "authorization_url": authorization_url,
        "redirect_uri": redirect_uri,
        "expires_in_seconds": _STATE_TTL_SECONDS,
    }


def _post_form_json(url: str, payload: Dict[str, Any], *, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        **(headers or {}),
    }
    req = urlrequest.Request(
        url,
        data=urlparse.urlencode(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise RuntimeError("OAuth token response was invalid.")
    return parsed


def _post_json(url: str, payload: Dict[str, Any], *, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise RuntimeError("OAuth token response was invalid.")
    return parsed


def _oauth_basic_header(client_id: str, client_secret: str) -> str:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    return f"Basic {auth}"


def _credentials_from_standard_token_response(provider: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or f"{_connector_label(provider)} token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    for key in (
        "user_id",
        "user_id_string",
        "account_id",
        "workspace_id",
        "stripe_user_id",
        "stripe_publishable_key",
        "instance_url",
        "id",
        "organization_id",
        "team_id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            credentials[key] = value
    if "livemode" in payload:
        credentials["livemode"] = bool(payload.get("livemode"))
    return credentials


def _exchange_standard_oauth(provider: str, code: str, redirect_uri: str, *, code_verifier: str = "") -> Dict[str, Any]:
    config = _provider_config(provider)
    client_id, client_secret = ensure_oauth_configured(provider)
    body: Dict[str, Any] = {
        "code": code,
    }
    if config.include_redirect_uri_in_token_body:
        body["redirect_uri"] = redirect_uri
    if config.token_grant_type:
        body["grant_type"] = config.token_grant_type
    if config.include_client_id_in_token_body:
        body["client_id"] = client_id
    if config.include_client_secret_in_token_body:
        body["client_secret"] = client_secret
    if code_verifier:
        body["code_verifier"] = code_verifier
    headers: Dict[str, str] = {}
    if config.token_auth == "basic":
        headers["Authorization"] = _oauth_basic_header(client_id, client_secret)
    token_url = _provider_url(provider, config.token_url)
    if config.token_request_format == "json":
        payload = _post_json(token_url, body, headers=headers)
    else:
        payload = _post_form_json(token_url, body, headers=headers)
    return _credentials_from_standard_token_response(provider, payload)


def _exchange_google(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("google_workspace")
    client_id, client_secret = ensure_oauth_configured("google_workspace")
    payload = _post_form_json(
        _provider_url("google_workspace", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Google token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    return credentials


def _exchange_github(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("github")
    client_id, client_secret = ensure_oauth_configured("github")
    payload = _post_form_json(
        _provider_url("github", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "GitHub token exchange failed."))
    return {
        "auth_mode": "oauth",
        "access_token": access_token,
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "bearer").strip() or "bearer",
    }


def _exchange_microsoft(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("microsoft_365")
    client_id, client_secret = ensure_oauth_configured("microsoft_365")
    payload = _post_form_json(
        _provider_url("microsoft_365", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": _joined_scopes("microsoft_365", config),
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Microsoft 365 token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    return credentials


def _exchange_slack(code: str, redirect_uri: str) -> Dict[str, Any]:
    client_id, client_secret = ensure_oauth_configured("slack")
    exchange = slack_connector.exchange_oauth_code(code, redirect_uri, client_id=client_id, client_secret=client_secret)
    credentials = exchange.get("credentials") if isinstance(exchange.get("credentials"), dict) else {}
    if not credentials.get("bot_token"):
        raise RuntimeError("Slack OAuth did not return a bot token.")
    return credentials


def _exchange_notion(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("notion")
    client_id, client_secret = ensure_oauth_configured("notion")
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    payload = _post_json(
        _provider_url("notion", config.token_url),
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Authorization": f"Basic {auth}"},
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Notion token exchange failed."))
    return {
        "auth_mode": "oauth",
        "access_token": access_token,
        "workspace_id": str(payload.get("workspace_id") or "").strip(),
        "workspace_name": str(payload.get("workspace_name") or "").strip(),
        "bot_id": str(payload.get("bot_id") or "").strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
    }


def _exchange_linear(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("linear")
    client_id, client_secret = ensure_oauth_configured("linear")
    payload = _post_form_json(
        _provider_url("linear", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Linear token exchange failed."))
    return {
        "auth_mode": "oauth",
        "access_token": access_token,
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
    }


def _exchange_dropbox(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("dropbox")
    client_id, client_secret = ensure_oauth_configured("dropbox")
    payload = _post_form_json(
        _provider_url("dropbox", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Dropbox token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "account_id": str(payload.get("account_id") or "").strip(),
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    return credentials


def _exchange_discord(code: str, redirect_uri: str) -> Dict[str, Any]:
    config = _provider_config("discord")
    client_id, client_secret = ensure_oauth_configured("discord")
    payload = _post_form_json(
        _provider_url("discord", config.token_url),
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Discord token exchange failed."))
    # When bot scope is used, Discord returns the guild the bot was installed to.
    guild = payload.get("guild") if isinstance(payload.get("guild"), dict) else {}
    guild_id = str(guild.get("id") or "").strip()
    guild_name = str(guild.get("name") or "").strip()
    if not guild_id:
        raise RuntimeError(
            "Discord bot was not installed to a server. "
            "Make sure you select a server in the Discord authorization page."
        )
    # The bot token is a static credential configured in .env — OAuth only
    # authorizes the bot to operate in a specific server (guild).
    bot_token = str(os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured in the server environment.")
    # Call /users/@me to get the bot's Discord user ID for display.
    me_req = urlrequest.Request(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bot {bot_token}"},
    )
    try:
        with urlrequest.urlopen(me_req, timeout=15) as resp:
            me_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Discord bot user lookup failed: {exc}") from exc
    bot_id = str(me_data.get("id") or "").strip()
    bot_username = str(me_data.get("username") or "").strip()
    return {
        "auth_mode": "oauth",
        "bot_token": bot_token,
        "guild_id": guild_id,
        "guild_name": guild_name,
        "bot_id": bot_id,
        "bot_username": bot_username,
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
    }


async def complete_oauth_callback(
    *,
    provider: str,
    code: str,
    state: str,
    request: Request,
) -> Dict[str, Any]:
    normalized_provider = provider_from_connection_id(provider)
    payload = decode_state(state)
    if str(payload.get("provider") or "").strip().lower() != normalized_provider:
        raise HTTPException(status_code=400, detail="OAuth state does not match this provider.")
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="OAuth workspace is missing.")
    normalized_code = str(code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="OAuth code is missing.")
    redirect_uri = callback_url(request, normalized_provider)
    provider_config = _provider_config(normalized_provider)
    code_verifier = ""
    if provider_config.auth_method == "pkce":
        pkce_nonce = str(payload.get("pkce_nonce") or "").strip()
        if not pkce_nonce:
            raise HTTPException(status_code=400, detail="OAuth PKCE state is missing.")
        code_verifier = _pkce_verifier(
            provider=normalized_provider,
            workspace_id=workspace_id,
            nonce=pkce_nonce,
        )
    try:
        if normalized_provider == "google_workspace":
            credentials = _exchange_google(normalized_code, redirect_uri)
        elif normalized_provider == "github":
            credentials = _exchange_github(normalized_code, redirect_uri)
        elif normalized_provider == "microsoft_365":
            credentials = _exchange_microsoft(normalized_code, redirect_uri)
        elif normalized_provider == "slack":
            credentials = _exchange_slack(normalized_code, redirect_uri)
        elif normalized_provider == "notion":
            credentials = _exchange_notion(normalized_code, redirect_uri)
        elif normalized_provider == "linear":
            credentials = _exchange_linear(normalized_code, redirect_uri)
        elif normalized_provider == "dropbox":
            credentials = _exchange_dropbox(normalized_code, redirect_uri)
        elif normalized_provider == "discord":
            credentials = _exchange_discord(normalized_code, redirect_uri)
        elif provider_config.token_parser == "standard":
            credentials = _exchange_standard_oauth(normalized_provider, normalized_code, redirect_uri, code_verifier=code_verifier)
        else:
            raise HTTPException(status_code=409, detail="This connection does not have a one-click OAuth setup yet.")
        # Map OAuth provider → vault connector name (Discord OAuth == discord_bot connector).
        vault_connector = "discord_bot" if normalized_provider == "discord" else normalized_provider
        result = await connectors_actions.create_connector_vault(
            ConnectorCreate(
                label=_connector_label(normalized_provider),
                connector=vault_connector,
                workspace_id=workspace_id,
                credentials=credentials,
                metadata={
                    "source": "connection_oauth",
                    "oauth_provider": normalized_provider,
                    "surface": str(payload.get("surface") or "sage").strip() or "sage",
                },
            )
        )
        return {
            "ok": True,
            "provider": normalized_provider,
            "workspace_id": workspace_id,
            "connector": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
