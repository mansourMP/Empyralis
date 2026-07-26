from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib import parse as urlparse
from urllib import request as urlrequest
import logging

from fastapi import HTTPException, Request

from server_modules import connectors_actions
from server_modules.connectors import slack_connector
from server_modules.schemas import ConnectorCreate

_log = logging.getLogger(__name__)

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
    # RFC 7591 Dynamic Client Registration endpoint. None for every provider
    # below that has no live, confirmed self-register endpoint for its MCP
    # OAuth app (confirmed via each provider's own /.well-known/oauth-
    # authorization-server or oauth-protected-resource — see the comment
    # above each entry below for the discovery evidence trail): those
    # providers require the workspace owner to pre-register a static OAuth
    # app in the provider's developer console and supply client_id/
    # client_secret via env_vars. Every other provider below (as of the
    # 2026-07-19 connector sweep: Higgsfield, Stripe, Linear, Notion, Asana,
    # Canva, Airtable, ClickUp, Dropbox, Figma, Todoist, Calendly, Jira,
    # Confluence, Webflow, monday.com, GitLab, Miro, Intercom, Square,
    # Typeform, Vercel, Zapier, PayPal, Sentry, Attio, and Cloudflare) has no
    # such console for its MCP OAuth app, so a client_id can only be obtained
    # by self-registering here. See _resolve_oauth_client().
    registration_endpoint: str | None = None
    # Whether the dynamic-registration fallback additionally requires the
    # owner to opt in with a {PROVIDER}_OAUTH_ENABLED / _MCP_ENABLED env flag
    # before it will self-register (see _dynamic_registration_enabled()).
    # True (default) preserves Higgsfield's original behavior: DCR exists but
    # stays inert, and oauth_provider_configured() stays False, until the
    # owner explicitly turns it on. False means DCR engages the moment a
    # request needs a client and no static client_id/secret is set — no flag,
    # no console, genuinely zero-config — and oauth_provider_configured()
    # reports True out of the box. Every other DCR-capable provider listed
    # above uses False: mainstream SaaS with a live, confirmed self-register
    # endpoint and nothing an operator could "finish configuring" manually
    # even if they wanted to, so gating them behind a flag would only ever
    # produce a false "not configured" reading.
    dynamic_registration_opt_in_required: bool = True
    # RFC 7591 token_endpoint_auth_method requested when self-registering
    # (see _register_dynamic_client). Must be a value the provider's own
    # token_endpoint_auth_methods_supported actually advertises, since this
    # also determines how _exchange_standard_oauth/_exchange_notion/
    # _exchange_linear must authenticate at the token endpoint afterward
    # (token_auth / include_client_secret_in_token_body below). Defaults to
    # "client_secret_post", the shape already used by every provider that
    # doesn't override it.
    dynamic_registration_token_auth_method: str = "client_secret_post"


OAUTH_PROVIDER_CONFIGS: Dict[str, OAuthProviderConfig] = {
    # Google Workspace: checked live 2026-07-19 for DCR as part of the wider
    # connector sweep. All 3 MCP hosts (gmailmcp/calendarmcp/drivemcp.
    # googleapis.com) declare authorization_servers=["https://accounts.google.
    # com/"] via their own oauth-protected-resource discovery -- same
    # classic issuer already configured below. GET https://accounts.google.
    # com/.well-known/oauth-authorization-server has no registration_endpoint
    # field at all -- Google has no public self-registration API; an OAuth
    # client must be created in Google Cloud Console. Stays classic-only, no
    # config changes.
    # NOTE: this base `scopes` tuple is the UNVERIFIED-app default (gmail.modify
    # + calendar only) -- it must never include the Drive scope directly. Drive
    # is opt-in only, added by _effective_scopes() when GOOGLE_WORKSPACE_ENABLE_
    # DRIVE_SCOPE/GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE is set (see that function and
    # test_google_oauth_verification_readiness.py). Baking drive.file in here
    # made that opt-in gate a no-op -- every Google Workspace OAuth start
    # (Gmail/Calendar included) silently requested Drive too, which is exactly
    # the kind of scope creep Google's app-verification review blocks an
    # unverified app on, so it could stall the *entire* Google authorize step,
    # not just Drive's.
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
    # GitHub: checked live 2026-07-19. api.githubcopilot.com/mcp/'s own
    # oauth-protected-resource doc points authorization_servers at
    # https://github.com/login/oauth, whose oauth-authorization-server
    # discovery has no registration_endpoint field -- OAuth Apps/GitHub Apps
    # still require manual registration in the GitHub UI. Stays classic-only,
    # no config changes. (Also requires a Copilot/Copilot Enterprise seat,
    # per the APP_MCP_SERVER_MAP comment below, independent of this finding.)
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
    # Microsoft 365: checked live 2026-07-19. APP_MCP_SERVER_MAP's
    # "microsoft-365" entry has endpoint=None -- Agent 365 MCP is still a
    # tenant-specific Frontier preview with no single public URL, so there is
    # nothing to run DCR discovery against on the MCP side at all. Checked
    # the identity platform directly anyway: login.microsoftonline.com has no
    # oauth-protected-resource or oauth-authorization-server document on any
    # of the common/organizations/consumers tenants (all 404); the only live
    # discovery doc is .../v2.0/.well-known/openid-configuration, which has
    # no registration_endpoint field. Microsoft app registration requires the
    # Azure Portal or an authenticated Graph API call, not RFC 7591 DCR.
    # Stays classic-only, no config changes.
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
    # Slack: checked live 2026-07-19. mcp.slack.com's own oauth-authorization-
    # server discovery has no registration_endpoint. Bonus finding, not acted
    # on: the MCP server's authorize/token endpoints
    # (slack.com/oauth/v2_user/authorize, slack.com/api/oauth.v2.user.access)
    # are a different, user-token "v2_user" pair from the classic bot-token
    # "v2" pair already configured below -- switching to it would change what
    # the OAuth actually authorizes (user token vs. bot token), which is a
    # product decision beyond a DCR sweep, so left untouched. Stays
    # classic-only, no config changes.
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
    # Notion: official remote MCP server (mcp.notion.com) — a different OAuth
    # app from the classic api.notion.com integrations OAuth this config
    # used to point at. Verified live 2026-07-19 via GET
    # https://mcp.notion.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint=".../authorize", token_endpoint=".../token",
    # registration_endpoint=".../register"; code_challenge_methods_supported
    # includes "S256" (auth_method switched to pkce to match);
    # token_endpoint_auth_methods_supported includes "client_secret_basic"
    # (matches this file's existing _exchange_notion, which already
    # authenticates with a Basic header — kept as the DCR request shape too).
    # Neither the authorization-server nor protected-resource discovery
    # document advertises scopes_supported, matching Notion's classic
    # all-or-nothing workspace grant, so scopes stays empty. auth_params
    # owner=user was a classic api.notion.com-only query param with no
    # meaning for mcp.notion.com's authorize endpoint — dropped.
    "notion": OAuthProviderConfig(
        label="Notion",
        env_vars={
            "client_id": ("NOTION_OAUTH_CLIENT_ID", "NOTION_CLIENT_ID"),
            "client_secret": ("NOTION_OAUTH_CLIENT_SECRET", "NOTION_CLIENT_SECRET"),
        },
        scopes=(),
        auth_url="https://mcp.notion.com/authorize",
        token_url="https://mcp.notion.com/token",
        auth_method="pkce",
        token_parser="custom",
        profile_probe="https://api.notion.com/v1/users/me",
        registration_endpoint="https://mcp.notion.com/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Linear: official remote MCP server (mcp.linear.app) — a different OAuth
    # app from the classic linear.app app-authorize OAuth this config used to
    # point at. Verified live 2026-07-19 via GET
    # https://mcp.linear.app/.well-known/oauth-authorization-server ->
    # authorization_endpoint=".../authorize", token_endpoint=".../token",
    # registration_endpoint=".../register", code_challenge_methods_supported
    # = ["S256"] (auth_method switched to pkce to match);
    # token_endpoint_auth_methods_supported includes "client_secret_post"
    # (matches this file's existing _exchange_linear, which already posts
    # client_id+client_secret in the form body). scopes_supported at the
    # protected-resource (mcp.linear.app/mcp) discovery is exactly
    # ["read","write"] — matches the scopes already configured below.
    # scope_separator switched from "," (a classic linear.app quirk) to the
    # RFC 6749 default space separator to match mcp.linear.app's standard
    # OAuth 2.1-shaped discovery document (same style as Notion/Asana/
    # Canva's) — unverified beyond that pattern match, since a live
    # authorize+consent round trip needs a browser and is outside what a
    # registration-only smoke test can confirm.
    "linear": OAuthProviderConfig(
        label="Linear",
        env_vars={
            "client_id": ("LINEAR_OAUTH_CLIENT_ID", "LINEAR_CLIENT_ID"),
            "client_secret": ("LINEAR_OAUTH_CLIENT_SECRET", "LINEAR_CLIENT_SECRET"),
        },
        scopes=("read", "write"),
        auth_url="https://mcp.linear.app/authorize",
        token_url="https://mcp.linear.app/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.linear.app/graphql",
        registration_endpoint="https://mcp.linear.app/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Dropbox: checked live 2026-07-19. GET https://www.dropbox.com/.well-
    # known/oauth-authorization-server (the issuer named by mcp.dropbox.com's
    # own oauth-protected-resource discovery) -> authorization_endpoint and
    # token_endpoint are IDENTICAL to the classic auth_url/token_url already
    # configured below (this is the SAME OAuth app gaining DCR, not a
    # separate MCP-specific app like Notion/Linear/Stripe were) --
    # registration_endpoint="https://www.dropbox.com/oauth2/register".
    # scopes_supported is a 38-entry superset that includes all 5 scopes
    # already configured -- no change needed. code_challenge_methods_
    # supported includes "S256" (auth_method switched to pkce to match).
    # token_endpoint_auth_methods_supported includes "client_secret_post"
    # (this config's default token_auth, unchanged). Because auth_method is
    # now pkce, _exchange_dropbox (the dedicated exchange function below,
    # kept for its account_id capture) was updated to accept and forward
    # code_verifier, and to resolve its client via _resolve_oauth_client
    # instead of ensure_oauth_configured -- the same fix Notion/Linear
    # needed (see the "Notion and Linear token exchange" comment block near
    # complete_oauth_callback).
    "dropbox": OAuthProviderConfig(
        label="Dropbox",
        env_vars={
            "client_id": ("DROPBOX_OAUTH_CLIENT_ID", "DROPBOX_CLIENT_ID", "DROPBOX_APP_KEY"),
            "client_secret": ("DROPBOX_OAUTH_CLIENT_SECRET", "DROPBOX_CLIENT_SECRET", "DROPBOX_APP_SECRET"),
        },
        scopes=("files.metadata.read", "files.content.read", "files.content.write", "sharing.read", "sharing.write"),
        auth_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.dropboxapi.com/2/users/get_current_account",
        auth_params={"token_access_type": "offline"},
        registration_endpoint="https://www.dropbox.com/oauth2/register",
        dynamic_registration_opt_in_required=False,
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
    # Figma: official MCP OAuth app, checked live 2026-07-19. GET
    # https://api.figma.com/.well-known/oauth-authorization-server (the
    # issuer named by mcp.figma.com's own oauth-protected-resource
    # discovery; byte-identical when fetched directly from mcp.figma.com
    # too) -> authorization_endpoint="https://www.figma.com/oauth/mcp" (a
    # dedicated MCP-specific path, NOT the classic
    # "https://www.figma.com/oauth" this config used to point at),
    # token_endpoint="https://api.figma.com/v1/oauth/token" (same as
    # before), registration_endpoint="https://api.figma.com/v1/oauth/mcp/
    # register". scopes_supported=["mcp:connect"] -- one coarse scope,
    # replacing the classic granular scopes (current_user:read/file_metadata:
    # read/file_content:read/file_comments:read), which don't exist in the
    # new app's model. code_challenge_methods_supported=["S256"] (auth_method
    # switched to pkce to match). token_endpoint_auth_methods_supported
    # includes "client_secret_basic", matching this config's existing
    # token_auth="basic" shape (Authorization: Basic header) -- DCR requests
    # "client_secret_basic" to match what the exchange code actually sends,
    # not merely because it's supported (client_secret_post is also
    # supported, but token_auth="basic" here predates this pass and wasn't
    # touched). profile_probe left pointing at the classic REST API
    # (api.figma.com/v1/me) on the same assumption already used for Notion/
    # Asana/Airtable: an MCP-app-issued token authorizes the same Figma
    # account and should remain a valid bearer token against the classic
    # REST API too -- unverified beyond that pattern match.
    "figma": OAuthProviderConfig(
        label="Figma",
        env_vars={
            "client_id": ("FIGMA_CLIENT_ID",),
            "client_secret": ("FIGMA_CLIENT_SECRET",),
        },
        scopes=("mcp:connect",),
        auth_url="https://www.figma.com/oauth/mcp",
        token_url="https://api.figma.com/v1/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.figma.com/v1/me",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://api.figma.com/v1/oauth/mcp/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Todoist: checked live 2026-07-19. ai.todoist.net's own oauth-protected-
    # resource discovery names issuer "https://todoist.com" (the bare apex,
    # NOT app.todoist.com -- that classic host has no discovery documents at
    # all, it's a UI-only front end). GET https://todoist.com/.well-known/
    # oauth-authorization-server -> authorization_endpoint="https://todoist.
    # com/oauth/authorize" (auth_url corrected to this from
    # app.todoist.com/oauth/authorize), token_endpoint="https://todoist.com/
    # oauth/access_token" (already matched, no change),
    # registration_endpoint="https://todoist.com/oauth/register" --
    # confirmed live via an actual RFC 7591 POST that got back a correct
    # invalid_client_metadata 400 (missing fields), proving the endpoint is
    # real. scopes_supported is a 13-entry list that includes the existing
    # "data:read_write" scope -- no change needed. code_challenge_methods_
    # supported=["S256"] (auth_method switched to pkce to match).
    # token_endpoint_auth_methods_supported includes "client_secret_post"
    # (this config's default token_auth, unchanged).
    "todoist": OAuthProviderConfig(
        label="Todoist",
        env_vars={
            "client_id": ("TODOIST_CLIENT_ID",),
            "client_secret": ("TODOIST_CLIENT_SECRET",),
        },
        scopes=("data:read_write",),
        auth_url="https://todoist.com/oauth/authorize",
        token_url="https://todoist.com/oauth/access_token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.todoist.com/api/v1/projects?limit=1",
        registration_endpoint="https://todoist.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Airtable: official remote MCP server (mcp.airtable.com), but its OAuth
    # app lives at the SAME host as before. Verified live 2026-07-19 via GET
    # https://airtable.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint / token_endpoint / registration_endpoint exactly
    # match this config's existing auth_url/token_url — no change needed
    # there. Cross-checked against
    # https://mcp.airtable.com/.well-known/oauth-protected-resource, which
    # declares authorization_servers=["https://airtable.com/oauth2/v1"] (the
    # SAME issuer) and the same data.records:*/schema.bases:*/etc scope
    # namespace already configured below — confirms an Airtable OAuth token
    # is a genuine general-purpose API token, not narrowly MCP-scoped, so the
    # profile_probe below stays valid too. token_endpoint_auth_methods_
    # supported is ["client_secret_basic","none"] (no "client_secret_post") —
    # matches this config's existing token_auth="basic"; DCR requests
    # "client_secret_basic" to match.
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
        registration_endpoint="https://airtable.com/oauth2/v1/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Canva: official remote MCP server (mcp.canva.com) — a different OAuth
    # app from the classic www.canva.com Connect API OAuth this config used
    # to point at. Verified live 2026-07-19 via GET
    # https://mcp.canva.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint=".../authorize", token_endpoint=".../token",
    # registration_endpoint=".../register";
    # token_endpoint_auth_methods_supported includes "client_secret_basic"
    # (matches this config's existing token_auth="basic" — DCR requests the
    # same). scopes_supported isn't declared at the authorization-server
    # level, but the protected-resource discovery
    # (mcp.canva.com/.well-known/oauth-protected-resource) lists a full scope
    # catalog that the 5 scopes already configured below are all real,
    # unchanged members of — no change needed there.
    "canva": OAuthProviderConfig(
        label="Canva",
        env_vars={
            "client_id": ("CANVA_CLIENT_ID",),
            "client_secret": ("CANVA_CLIENT_SECRET",),
        },
        scopes=("profile:read", "design:meta:read", "design:content:read", "folder:read", "asset:read"),
        auth_url="https://mcp.canva.com/authorize",
        token_url="https://mcp.canva.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.canva.com/rest/v1/users/me/profile",
        token_auth="basic",
        include_client_id_in_token_body=False,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://mcp.canva.com/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Asana: official remote MCP server (mcp.asana.com) — a different OAuth
    # app from the classic app.asana.com OAuth this config used to point at.
    # Verified live 2026-07-19 via GET
    # https://mcp.asana.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint=".../authorize", token_endpoint=".../token",
    # registration_endpoint=".../register";
    # token_endpoint_auth_methods_supported includes "client_secret_post"
    # (this config's default token_auth, unchanged). scopes: the classic
    # fine-grained tasks:read/tasks:write/etc scopes this config previously
    # requested do NOT exist in the new server's model — GET
    # https://mcp.asana.com/.well-known/oauth-protected-resource declares
    # scopes_supported=["default"] only, so scopes is corrected to match.
    "asana": OAuthProviderConfig(
        label="Asana",
        env_vars={
            "client_id": ("ASANA_CLIENT_ID",),
            "client_secret": ("ASANA_CLIENT_SECRET",),
        },
        scopes=("default",),
        auth_url="https://mcp.asana.com/authorize",
        token_url="https://mcp.asana.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://app.asana.com/api/1.0/users/me",
        registration_endpoint="https://mcp.asana.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # HubSpot: checked live 2026-07-19. mcp.hubspot.com publishes a complete,
    # dedicated oauth-authorization-server document (authorize/token
    # endpoints differ from the classic app below: mcp.hubspot.com/oauth/
    # authorize/user + mcp.hubspot.com/oauth/v3/token vs. classic
    # app.hubspot.com/oauth/authorize + api.hubapi.com/oauth/v1/token) but
    # has no registration_endpoint field -- neither does the classic host.
    # Stays classic-only, no config changes.
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
    # Zoom: the one provider in this DCR pass that stays static-only.
    # Verified live 2026-07-19 via GET
    # https://zoom.us/.well-known/oauth-authorization-server (also mirrored
    # at mcp.zoom.us/.well-known/oauth-authorization-server) -> no
    # registration_endpoint field at all, only authorization_endpoint,
    # token_endpoint, and token_endpoint_auth_methods_supported=
    # ["client_secret_basic"] — Zoom has no self-registration API for this
    # app, so a workspace owner must still pre-register a static OAuth app
    # in the Zoom developer console (marketplace.zoom.us) and supply
    # ZOOM_CLIENT_ID/ZOOM_CLIENT_SECRET via env vars, same as every provider
    # below this point. auth_url/token_url already match the verified
    # discovery exactly — no change.
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
    # Calendly: checked live 2026-07-19. mcp.calendly.com's own oauth-
    # protected-resource discovery names issuer "https://calendly.com/" (the
    # bare apex, NOT auth.calendly.com -- that classic host has no discovery
    # documents at all, all three well-known paths 404). GET https://
    # calendly.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://calendly.com/oauth/authorize",
    # token_endpoint="https://calendly.com/oauth/token",
    # registration_endpoint="https://calendly.com/oauth/register" --
    # confirmed live via an actual RFC 7591 POST that got back a correct
    # invalid_client_metadata 400 (missing fields). scopes_supported at the
    # protected-resource level is exactly ["mcp:scheduling:read",
    # "mcp:scheduling:write"] -- populated here since this config previously
    # requested no scopes at all, and the MCP resource specifically gates its
    # tools behind these two. token_endpoint_auth_methods_supported=["none"]
    # only -- no client secret exists, so include_client_secret_in_token_body
    # is turned off and the DCR request asks for "none" instead of the
    # default "client_secret_post" (same shape as Stripe/ClickUp).
    # code_challenge_methods_supported=["S256"] -- already auth_method=
    # "pkce" here, no change needed.
    "calendly": OAuthProviderConfig(
        label="Calendly",
        env_vars={
            "client_id": ("CALENDLY_CLIENT_ID",),
            "client_secret": ("CALENDLY_CLIENT_SECRET",),
        },
        scopes=("mcp:scheduling:read", "mcp:scheduling:write"),
        auth_url="https://calendly.com/oauth/authorize",
        token_url="https://calendly.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.calendly.com/users/me",
        include_client_secret_in_token_body=False,
        registration_endpoint="https://calendly.com/oauth/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # ClickUp: official remote MCP server (mcp.clickup.com) — a different
    # OAuth app from the classic app.clickup.com "API" authorize page this
    # config used to point at. Verified live 2026-07-19 via GET
    # https://mcp.clickup.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint=".../oauth/authorize",
    # token_endpoint=".../oauth/token",
    # registration_endpoint=".../oauth/register",
    # scopes_supported=["read","write"],
    # code_challenge_methods_supported=["S256"] (auth_method switched to
    # pkce to match), token_endpoint_auth_methods_supported=["none"] only —
    # no client secret exists for this app, so include_client_secret_in_
    # token_body is turned off and the DCR request asks for "none" instead
    # of the default "client_secret_post". The classic config's
    # include_response_type=False / include_redirect_uri_in_token_body=False
    # / token_grant_type=None overrides were app.clickup.com-specific
    # omissions; the new server's response_types_supported=["code"] and
    # grant_types_supported=["authorization_code"] expect the standard
    # parameters, so those overrides are dropped back to the field defaults.
    "clickup": OAuthProviderConfig(
        label="ClickUp",
        env_vars={
            "client_id": ("CLICKUP_CLIENT_ID",),
            "client_secret": ("CLICKUP_CLIENT_SECRET",),
        },
        scopes=("read", "write"),
        auth_url="https://mcp.clickup.com/oauth/authorize",
        token_url="https://mcp.clickup.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.clickup.com/api/v2/user",
        include_client_secret_in_token_body=False,
        registration_endpoint="https://mcp.clickup.com/oauth/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Jira: checked live 2026-07-19. GET https://mcp.atlassian.com/.well-
    # known/oauth-authorization-server resolves directly to a complete,
    # terminal AS document for a COMPLETELY SEPARATE OAuth app from the
    # classic auth.atlassian.com Auth0 flow this config used to point at
    # entirely: issuer/authorization_endpoint="https://mcp.atlassian.com/v1/
    # authorize", token_endpoint="https://cf.mcp.atlassian.com/v1/token",
    # registration_endpoint="https://cf.mcp.atlassian.com/v1/register" --
    # confirmed live via an actual RFC 7591 POST that got back a correct
    # invalid_client_metadata 400 ("At least one redirect URI is required").
    # Because this new app is not Auth0-backed, the classic auth_params
    # (audience/prompt, both Auth0-specific) and token_request_format="json"
    # (a classic-auth.atlassian.com-specific body-encoding quirk) are dropped
    # back to the field defaults, matching the ClickUp precedent of shedding
    # classic-app-specific overrides when the MCP app turns out to expect
    # standard OAuth 2.1 parameters. scopes_supported isn't declared at this
    # AS -- existing scopes kept as best-effort, unverified against the new
    # app. code_challenge_methods_supported includes "S256" (auth_method
    # switched to pkce to match); token_endpoint_auth_methods_supported
    # includes "client_secret_post" (default, unchanged).
    # IMPORTANT CAVEAT (unverified, not just under-tested): a third-party
    # Atlassian community-forum post (not Atlassian's own docs) claims
    # Atlassian's Remote MCP Beta rejects non-preapproved client_ids at the
    # authorize/consent step even though registration itself succeeds --
    # i.e. DCR may complete and still dead-end before a token is ever
    # issued. Registration was independently confirmed live and working;
    # the authorize+consent step was NOT completable by an automated check
    # (needs an interactive human login). Smoke-test a real end-to-end
    # connect before relying on this in production.
    "jira": OAuthProviderConfig(
        label="Jira",
        env_vars={
            "client_id": ("ATLASSIAN_CLIENT_ID", "JIRA_CLIENT_ID"),
            "client_secret": ("ATLASSIAN_CLIENT_SECRET", "JIRA_CLIENT_SECRET"),
        },
        scopes=("read:me", "read:jira-user", "read:jira-work", "write:jira-work", "offline_access"),
        auth_url="https://mcp.atlassian.com/v1/authorize",
        token_url="https://cf.mcp.atlassian.com/v1/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.atlassian.com/me",
        registration_endpoint="https://cf.mcp.atlassian.com/v1/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Stripe: official remote MCP server (mcp.stripe.com), authenticated via
    # a SEPARATE OAuth app at access.stripe.com/mcp — not the classic Stripe
    # Connect OAuth (connect.stripe.com) this config used to point at (Stripe
    # Connect authorizes a *merchant's* Stripe account into a platform;
    # this app authorizes our own agent to call Stripe's MCP tools — a
    # different product with a different app). Verified live 2026-07-19 via
    # GET https://access.stripe.com/.well-known/oauth-authorization-server/mcp
    # (a path-suffixed RFC 8414 discovery URL — the domain-root well-known
    # path 404s for Stripe; also mirrored verbatim at
    # https://mcp.stripe.com/.well-known/oauth-authorization-server) ->
    # authorization_endpoint="https://access.stripe.com/mcp/oauth2/authorize",
    # token_endpoint=".../mcp/oauth2/token",
    # registration_endpoint=".../mcp/oauth2/register",
    # scopes_supported=["mcp"], code_challenge_methods_supported=["S256"]
    # (auth_method switched to pkce to match),
    # token_endpoint_auth_methods_supported=["none"] only — no client secret
    # exists, so include_client_secret_in_token_body is turned off and the
    # DCR request asks for "none" instead of the default "client_secret_
    # post". The classic Connect-specific include_client_id_in_token_body=
    # False / include_redirect_uri_in_token_body=False overrides are dropped
    # back to the field defaults (RFC 6749 §3.2.1 requires client_id in the
    # body for a "none"-auth public client; redirect_uri is standard once it
    # was included in the authorize request, which it is here).
    "stripe": OAuthProviderConfig(
        label="Stripe",
        env_vars={
            "client_id": ("STRIPE_CLIENT_ID",),
            "client_secret": ("STRIPE_CLIENT_SECRET", "STRIPE_SECRET_KEY"),
        },
        scopes=("mcp",),
        auth_url="https://access.stripe.com/mcp/oauth2/authorize",
        token_url="https://access.stripe.com/mcp/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.stripe.com/v1/account",
        include_client_secret_in_token_body=False,
        registration_endpoint="https://access.stripe.com/mcp/oauth2/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Salesforce: checked live 2026-07-19. The MCP-resource discovery chain
    # (api.salesforce.com/.well-known/oauth-protected-resource/platform/mcp/
    # v1/platform/ -> its named authorization-server document) resolves to
    # issuer login.salesforce.com -- the SAME host already configured below
    # -- with no registration_endpoint field. A registration_endpoint DOES
    # exist at login.salesforce.com/services/oauth2/register (found only via
    # the separate, OIDC-only discovery doc, not the MCP-relevant chain), but
    # an unauthenticated POST to it returns 401 invalid_client -- it demands
    # pre-existing client credentials just to respond, which is not open/
    # anonymous RFC 7591 registration. Stays classic-only, no config changes.
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
    # Webflow: official MCP OAuth app, checked live 2026-07-19. GET https://
    # mcp.webflow.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://mcp.webflow.com/oauth/authorize",
    # token_endpoint="https://mcp.webflow.com/oauth/token",
    # registration_endpoint="https://mcp.webflow.com/oauth/register" --
    # confirmed live (GET returns 405 Method Not Allowed, i.e. it exists and
    # only accepts POST). A wholly separate app from the classic webflow.com/
    # api.webflow.com pair (neither has any discovery document at all).
    # scopes_supported isn't declared anywhere reachable -- existing scopes
    # kept as best-effort, unverified against the new app. code_challenge_
    # methods_supported includes "S256" (auth_method switched to pkce to
    # match). token_endpoint_auth_methods_supported includes
    # "client_secret_post" (default, unchanged). profile_probe left pointing
    # at the classic REST API on the same unverified-but-consistent
    # assumption used for Figma/Notion/Asana/Airtable.
    "webflow": OAuthProviderConfig(
        label="Webflow",
        env_vars={
            "client_id": ("WEBFLOW_CLIENT_ID",),
            "client_secret": ("WEBFLOW_CLIENT_SECRET",),
        },
        scopes=("sites:read", "pages:read", "cms:read", "assets:read", "forms:read"),
        auth_url="https://mcp.webflow.com/oauth/authorize",
        token_url="https://mcp.webflow.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.webflow.com/v2/token/authorized_by",
        registration_endpoint="https://mcp.webflow.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # monday.com: official MCP OAuth app, checked live 2026-07-19. GET
    # https://mcp.monday.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://mcp.monday.com/authorize",
    # token_endpoint="https://mcp.monday.com/token",
    # registration_endpoint="https://mcp.monday.com/register" -- confirmed
    # live (GET returns 405 Method Not Allowed). A dedicated app, distinct
    # from the classic auth.monday.com pair this config used to point at
    # (which, as a bonus finding, ALSO independently supports DCR at
    # auth.monday.com/oauth_ms/oauth/register with a larger 26-scope
    # catalog -- not used here, in favor of the dedicated MCP app to match
    # every other provider in this pass). scopes_supported isn't declared at
    # mcp.monday.com -- existing scopes kept as best-effort, unverified
    # against the new app. code_challenge_methods_supported includes "S256"
    # (auth_method switched to pkce to match). token_endpoint_auth_methods_
    # supported includes "client_secret_post" (default, unchanged). The
    # classic config's token_grant_type=None override (omitting grant_type
    # from the token body) was an auth.monday.com-specific quirk; the new
    # app's response_types_supported=["code"] and standard grant_types
    # expect the normal parameters, so it's dropped back to the field
    # default, matching the ClickUp precedent.
    "monday": OAuthProviderConfig(
        label="monday.com",
        env_vars={
            "client_id": ("MONDAY_CLIENT_ID",),
            "client_secret": ("MONDAY_CLIENT_SECRET",),
        },
        scopes=("me:read", "account:read", "boards:read", "boards:write", "updates:read", "updates:write", "workspaces:read"),
        auth_url="https://mcp.monday.com/authorize",
        token_url="https://mcp.monday.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.monday.com/v2",
        registration_endpoint="https://mcp.monday.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Box: checked live 2026-07-19. mcp.box.com auth-gates every well-known
    # path except its oauth-protected-resource document (401 on everything
    # else, confirmed via response headers to be a blanket host-level auth
    # gate, not evidence the docs don't exist), which names authorization_
    # servers=["https://api.box.com/"] as the real issuer. That issuer's own
    # discovery (also mirrored at account.box.com) has authorization_endpoint
    # and token_endpoint identical to the classic config already below, and
    # no registration_endpoint field at all. Stays classic-only, no config
    # changes.
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
    # GitLab: checked live 2026-07-19, confirming the pre-existing "OAuth
    # 2.0 + DCR" comment on this provider's APP_MCP_SERVER_MAP entry.
    # gitlab.com's own oauth-authorization-server discovery (present in 3
    # independently-fetched documents: MCP-path-suffixed, root, and openid-
    # configuration) -> registration_endpoint="https://gitlab.com/oauth/
    # register". authorization_endpoint/token_endpoint are IDENTICAL to the
    # classic config already below -- this is the SAME OAuth app gaining
    # DCR, not a separate MCP-specific app (same situation as Dropbox). The
    # MCP-path-suffixed discovery declares scopes_supported=["mcp"] only,
    # but the root (whole-host) discovery confirms "read_api"/"api"/
    # "read_user" (the 3 scopes already configured) remain valid members of
    # gitlab.com's full scope catalog, and "api" already grants the broad
    # access MCP tools need -- no scope change made. code_challenge_methods_
    # supported includes "S256" (auth_method switched to pkce to match).
    # token_endpoint_auth_methods_supported includes "client_secret_post"
    # (default, unchanged).
    "gitlab": OAuthProviderConfig(
        label="GitLab",
        env_vars={
            "client_id": ("GITLAB_CLIENT_ID",),
            "client_secret": ("GITLAB_CLIENT_SECRET",),
        },
        scopes=("read_user", "read_api", "api"),
        auth_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://gitlab.com/api/v4/user",
        registration_endpoint="https://gitlab.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Confluence: checked live 2026-07-19. mcp.atlassian.com/v1/mcp/authv2's
    # own oauth-protected-resource discovery names a TENANT-SCOPED issuer --
    # https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3 (a fixed
    # resource identifier for the Confluence MCP resource itself, returned by
    # an unauthenticated GET, not derived from any particular customer's
    # login) -- whose own discovery document has authorization_endpoint/
    # token_endpoint IDENTICAL to the classic auth.atlassian.com pair already
    # configured below (unlike jira, whose MCP app lives on a wholly
    # different, non-Auth0 host: this stays on auth.atlassian.com, so
    # auth_params and token_request_format="json" are Auth0-specific and
    # still apply -- left unchanged). New field:
    # registration_endpoint="https://auth.atlassian.com/
    # VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register" -- confirmed live via an
    # actual RFC 7591 POST that succeeded (see this task's report for the
    # disclosure: an unintended real client registration resulted and could
    # not be deleted afterward -- Atlassian's Remote MCP appears to register
    # an ephemeral client per connecting client by design). scopes_supported
    # IS declared at the protected-resource level and corrects 3 of the 6
    # scopes previously configured, which are not real members of that list:
    # read:confluence-content.summary -> read:page:confluence,
    # read:confluence-space.summary -> read:space:confluence,
    # write:confluence-content -> write:page:confluence (read:me,
    # read:confluence-user, and offline_access were already valid and are
    # unchanged). code_challenge_methods_supported includes "S256"
    # (auth_method switched to pkce to match). token_endpoint_auth_methods_
    # supported includes "client_secret_post" (default, unchanged).
    # IMPORTANT CAVEAT (unverified, not just under-tested): the same
    # third-party claim noted on the "jira" entry above -- that Atlassian's
    # Remote MCP Beta may reject non-preapproved client_ids at the
    # authorize/consent step even though registration succeeds -- applies
    # here too, and does NOT transfer 1:1 from jira's result since this flow
    # is Auth0-backed and jira's isn't. Smoke-test a real end-to-end connect
    # before relying on this in production.
    "confluence": OAuthProviderConfig(
        label="Confluence",
        env_vars={
            "client_id": ("ATLASSIAN_CLIENT_ID", "CONFLUENCE_CLIENT_ID"),
            "client_secret": ("ATLASSIAN_CLIENT_SECRET", "CONFLUENCE_CLIENT_SECRET"),
        },
        scopes=(
            "read:me",
            "read:confluence-user",
            "read:page:confluence",
            "read:space:confluence",
            "write:page:confluence",
            "offline_access",
        ),
        auth_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.atlassian.com/me",
        auth_params={"audience": "api.atlassian.com", "prompt": "consent"},
        token_request_format="json",
        registration_endpoint="https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Miro: official MCP OAuth app, checked live 2026-07-19. GET https://
    # mcp.miro.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://mcp.miro.com/authorize",
    # token_endpoint="https://mcp.miro.com/token",
    # registration_endpoint="https://mcp.miro.com/register". A dedicated app,
    # distinct from the classic miro.com/api.miro.com pair (neither has any
    # discovery document -- miro.com returns a CloudFront AccessDenied, api.
    # miro.com redirect-loops). scopes_supported=["boards:read","boards:
    # write","openid","email"] -- corrects the classic scopes, which don't
    # exist in the new app's model: drops "identity:read" (not offered) and
    # adds "openid"+"email" (needed for the standard OIDC-shaped identity
    # claims this app uses instead). code_challenge_methods_supported
    # includes "S256" (auth_method switched to pkce to match).
    # token_endpoint_auth_methods_supported=["client_secret_post",
    # "client_secret_basic"] (no "none" -- dynamically-registered clients are
    # treated as confidential, default token_auth unchanged). profile_probe
    # left pointing at the classic REST API on the same unverified-but-
    # consistent assumption used for Figma/Webflow/Notion/Asana/Airtable.
    "miro": OAuthProviderConfig(
        label="Miro",
        env_vars={
            "client_id": ("MIRO_CLIENT_ID",),
            "client_secret": ("MIRO_CLIENT_SECRET",),
        },
        scopes=("boards:read", "boards:write", "openid", "email"),
        auth_url="https://mcp.miro.com/authorize",
        token_url="https://mcp.miro.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.miro.com/v2/users/me",
        registration_endpoint="https://mcp.miro.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Intercom: official MCP OAuth app, checked live 2026-07-19. GET https://
    # mcp.intercom.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://mcp.intercom.com/authorize",
    # token_endpoint="https://mcp.intercom.com/token",
    # registration_endpoint="https://mcp.intercom.com/register". A dedicated,
    # fully standards-compliant app (response_types_supported,
    # response_modes_supported, PKCE, DCR all present) -- distinct from and
    # NOT inheriting the classic app.intercom.com/api.intercom.io pair's
    # nonstandard quirks (no redirect_uri in the authorize/token requests, no
    # response_type, custom /auth/eagle/token path, all dropped back to field
    # defaults here, matching the ClickUp precedent). scopes_supported isn't
    # declared -- existing empty scopes kept unchanged. code_challenge_
    # methods_supported includes "S256" (auth_method switched to pkce to
    # match). token_endpoint_auth_methods_supported includes
    # "client_secret_post" (default, unchanged).
    "intercom": OAuthProviderConfig(
        label="Intercom",
        env_vars={
            "client_id": ("INTERCOM_CLIENT_ID",),
            "client_secret": ("INTERCOM_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.intercom.com/authorize",
        token_url="https://mcp.intercom.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.intercom.io/me",
        registration_endpoint="https://mcp.intercom.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Docusign: checked live 2026-07-19. mcp-d.docusign.com's own oauth-
    # protected-resource discovery names issuer account-d.docusign.com (a
    # sandbox host); its discovery document has no registration_endpoint.
    # The classic production host (account.docusign.com, already configured
    # below) also has full OIDC discovery with no registration_endpoint
    # either. Neither reachable discovery document offers DCR. Stays
    # classic-only, no config changes.
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
    # Square: official MCP OAuth app, checked live 2026-07-19. GET https://
    # mcp.squareup.com/.well-known/oauth-authorization-server ->
    # authorization_endpoint="https://mcp.squareup.com/authorize",
    # token_endpoint="https://mcp.squareup.com/token",
    # registration_endpoint="https://mcp.squareup.com/register". A dedicated
    # app -- the classic connect.squareup.com host's own openid-configuration
    # has no registration_endpoint at all, confirming only the MCP app
    # supports DCR. scopes_supported isn't declared at the AS itself, but the
    # companion oauth-protected-resource document lists a 40+ scope catalog
    # that matches and extends the 5 scopes already configured -- no scope
    # change made. scope_separator switched from "," (a classic
    # connect.squareup.com quirk) to the RFC 6749 default space separator,
    # matching the same reasoning already used for Linear's classic-vs-MCP-
    # app switch. code_challenge_methods_supported includes "S256"
    # (auth_method switched to pkce to match). token_endpoint_auth_methods_
    # supported includes "client_secret_post" (default, unchanged).
    # token_request_format="json" is a documented behavior of classic
    # Square's OWN token endpoint (connect.squareup.com) -- kept as-is since
    # there's no live evidence either way for the new mcp.squareup.com host
    # (a POST test wasn't performed), so this is carried over unverified
    # rather than guessed. profile_probe (connect.squareup.com/oauth2/token/
    # status) is the classic app's OWN status-check endpoint and may not
    # accept a token minted by the new app -- left unchanged for the same
    # reason, flagged here rather than silently assumed correct.
    "square": OAuthProviderConfig(
        label="Square",
        env_vars={
            "client_id": ("SQUARE_APPLICATION_ID", "SQUARE_CLIENT_ID"),
            "client_secret": ("SQUARE_APPLICATION_SECRET", "SQUARE_CLIENT_SECRET"),
        },
        scopes=("MERCHANT_PROFILE_READ", "CUSTOMERS_READ", "ORDERS_READ", "PAYMENTS_READ", "INVOICES_READ"),
        auth_url="https://mcp.squareup.com/authorize",
        token_url="https://mcp.squareup.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://connect.squareup.com/oauth2/token/status",
        token_request_format="json",
        registration_endpoint="https://mcp.squareup.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Typeform: checked live 2026-07-19. GET https://api.typeform.com/.well-
    # known/oauth-authorization-server (the MCP endpoint api.typeform.com/mcp
    # shares this host with the classic OAuth app, so one discovery document
    # covers both) -> authorization_endpoint="https://admin.typeform.com/
    # oauth/authorize" -- corrected from api.typeform.com/oauth/authorize,
    # which the discovery document does not name as the real authorize host.
    # token_endpoint="https://api.typeform.com/oauth/token" (already
    # matched). registration_endpoint="https://api.typeform.com/oauth/
    # register". scopes_supported corrects "offline" to "offline_access" (the
    # actual supported scope name -- "offline" is not a member of the
    # declared list and would likely be rejected or silently dropped,
    # breaking refresh-token issuance); forms:read/forms:write/
    # responses:read/accounts:read were already valid and are unchanged.
    # code_challenge_methods_supported=["S256"] (auth_method switched to pkce
    # to match). token_endpoint_auth_methods_supported includes
    # "client_secret_post" (default, unchanged).
    "typeform": OAuthProviderConfig(
        label="Typeform",
        env_vars={
            "client_id": ("TYPEFORM_CLIENT_ID",),
            "client_secret": ("TYPEFORM_CLIENT_SECRET",),
        },
        scopes=("offline_access", "forms:read", "forms:write", "responses:read", "accounts:read"),
        auth_url="https://admin.typeform.com/oauth/authorize",
        token_url="https://api.typeform.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.typeform.com/me",
        registration_endpoint="https://api.typeform.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Vercel: checked live 2026-07-19. GET https://mcp.vercel.com/.well-
    # known/oauth-authorization-server -> issuer "https://vercel.com",
    # authorization_endpoint="https://vercel.com/oauth/authorize" (already
    # matched), token_endpoint="https://vercel.com/api/login/oauth/token"
    # (corrected from api.vercel.com/login/oauth/token to the exact
    # MCP-facing doc's value -- likely equivalent via Vercel's vercel.com/
    # api/* <-> api.vercel.com/* routing, but reconciled to the literal
    # discovered string rather than left as a probably-equivalent alias),
    # registration_endpoint="https://vercel.com/api/login/oauth/register".
    # Vercel exposes TWO capability views of the same issuer: this
    # MCP-facing one, which is public-client-only
    # (token_endpoint_auth_methods_supported=["none"] -- no client secret
    # exists, so include_client_secret_in_token_body is turned off and the
    # DCR request asks for "none" instead of the default "client_secret_
    # post", same shape as Stripe/ClickUp/Calendly), vs. a fuller
    # confidential-client view at the classic vercel.com root doc
    # (client_secret_basic/post, no "none") -- the MCP-facing values are used
    # here since that's what a spec-compliant MCP client resolves via the
    # RFC 9728 -> RFC 8414 chain from mcp.vercel.com itself. scopes_supported
    # (openid/email/offline_access/profile) confirms the 3 scopes already
    # configured remain valid -- no change made. code_challenge_methods_
    # supported includes "S256" (auth_method switched to pkce to match).
    "vercel": OAuthProviderConfig(
        label="Vercel",
        env_vars={
            "client_id": ("VERCEL_CLIENT_ID",),
            "client_secret": ("VERCEL_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email"),
        auth_url="https://vercel.com/oauth/authorize",
        token_url="https://vercel.com/api/login/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.vercel.com/login/oauth/userinfo",
        include_client_secret_in_token_body=False,
        registration_endpoint="https://vercel.com/api/login/oauth/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Higgsfield: official remote MCP server (mcp.higgsfield.ai/mcp) aggregating
    # ~30 image/video generation models (Kling, Sora, Veo, Seedream, Seedance,
    # FLUX, Soul, Nano Banana, and more) behind one connection. Auth: OAuth 2.1
    # + PKCE (S256) — NOT a flat API key. Confirmed live 2026-07-18 via MCP
    # OAuth discovery:
    #   GET https://mcp.higgsfield.ai/.well-known/oauth-authorization-server
    #     -> {"authorization_endpoint": ".../oauth2/authorize",
    #         "token_endpoint": ".../oauth2/token",
    #         "registration_endpoint": ".../oauth2/register",
    #         "grant_types_supported": ["authorization_code","refresh_token"],
    #         "code_challenge_methods_supported": ["S256"],
    #         "scopes_supported": ["openid","email","offline_access"]}
    #   GET https://mcp.higgsfield.ai/.well-known/oauth-protected-resource
    #     -> confirms authorization_code+PKCE is the right flow for a client
    #        that can receive a redirect (vs. the separate device_code flow
    #        at fnf-device-auth.higgsfield.ai for redirect-less CLI clients).
    # Unlike every provider above, Higgsfield has NO developer console to
    # pre-register a static client_id: higgsfield.ai/mcp's own setup docs say
    # only "sign in with your Higgsfield account" (no OAuth-app/client-ID
    # screen), and every guessed developer-console path 404s. cloud.higgsfield.ai
    # is a SEPARATE product (their REST "Cloud API") with its own flat API-key
    # auth — not the MCP server, and not used here. The only way to obtain a
    # client_id is RFC 7591 Dynamic Client Registration at
    # registration_endpoint below — see _resolve_oauth_client(), which uses
    # HIGGSFIELD_CLIENT_ID/SECRET if the owner set them, else self-registers
    # once (gated by HIGGSFIELD_OAUTH_ENABLED) and caches the result.
    "higgsfield": OAuthProviderConfig(
        label="Higgsfield",
        env_vars={
            "client_id": ("HIGGSFIELD_CLIENT_ID",),
            "client_secret": ("HIGGSFIELD_CLIENT_SECRET",),
        },
        scopes=("openid", "email", "offline_access"),
        auth_url="https://mcp.higgsfield.ai/oauth2/authorize",
        token_url="https://mcp.higgsfield.ai/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        # No userinfo/introspection endpoint is advertised in the discovery
        # document above (only authorization/token/registration endpoints) —
        # left unset rather than guessing an unverified URL.
        profile_probe=None,
        registration_endpoint="https://mcp.higgsfield.ai/oauth2/register",
    ),
    # Zapier: official hosted MCP server (mcp.zapier.com) reaching ~8-9k
    # connected apps through the CUSTOMER'S OWN Zapier account — this
    # connection only exposes app connections/Zaps the customer has already
    # wired inside Zapier; it does not create new app connections itself.
    # Zapier's own docs (docs.zapier.com/mcp/authentication) foreground a
    # manual "connection token" copy-paste flow as a fallback for MCP clients
    # that can't do OAuth discovery, but live discovery confirmed 2026-07-19
    # via GET https://mcp.zapier.com/.well-known/oauth-authorization-server
    # -> {"issuer":"https://mcp.zapier.com","authorization_endpoint":".../
    # oauth/authorize","token_endpoint":".../api/v1/oauth/token",
    # "registration_endpoint":".../api/v1/oauth/register","userinfo_
    # endpoint":".../api/v1/oauth/userinfo","scopes_supported":["openid",
    # "profile","email"],"token_endpoint_auth_methods_supported":["none",
    # "client_secret_post","client_secret_basic"],"code_challenge_methods_
    # supported":["plain","S256"]} that Zapier's docs also describe as the
    # default for "most clients" ("your AI handles authentication and tool
    # discovery automatically") — used here instead of the manual-token
    # fallback since it's what a spec-compliant MCP client resolves and
    # matches this codebase's established DCR pattern exactly.
    # oauth-protected-resource (path-suffixed) confirmed the MCP resource
    # itself: resource="https://mcp.zapier.com/api/v1/connect".
    "zapier": OAuthProviderConfig(
        label="Zapier",
        env_vars={
            "client_id": ("ZAPIER_CLIENT_ID",),
            "client_secret": ("ZAPIER_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email"),
        auth_url="https://mcp.zapier.com/oauth/authorize",
        token_url="https://mcp.zapier.com/api/v1/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://mcp.zapier.com/api/v1/oauth/userinfo",
        registration_endpoint="https://mcp.zapier.com/api/v1/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # PayPal: official remote MCP server (mcp.paypal.com, production;
    # mcp.sandbox.paypal.com, sandbox). Confirmed live 2026-07-19 via GET
    # https://mcp.paypal.com/.well-known/oauth-authorization-server ->
    # {"issuer":"https://mcp.paypal.com","authorization_endpoint":".../
    # authorize","token_endpoint":".../token","registration_endpoint":".../
    # register","token_endpoint_auth_methods_supported":["client_secret_
    # basic","client_secret_post","none"],"code_challenge_methods_
    # supported":["S256"]}. No scopes_supported field is declared in either
    # the authorization-server or protected-resource discovery document —
    # left empty rather than guessing scope names. No userinfo/introspection
    # endpoint is advertised either — profile_probe left unset rather than
    # guessing an unverified URL (same treatment as Higgsfield).
    "paypal": OAuthProviderConfig(
        label="PayPal",
        env_vars={
            "client_id": ("PAYPAL_CLIENT_ID",),
            "client_secret": ("PAYPAL_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.paypal.com/authorize",
        token_url="https://mcp.paypal.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.paypal.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Sentry: official remote MCP server (mcp.sentry.dev). Confirmed live
    # 2026-07-19 via GET https://mcp.sentry.dev/.well-known/oauth-
    # authorization-server -> {"issuer":"https://mcp.sentry.dev",
    # "authorization_endpoint":".../oauth/authorize","token_endpoint":".../
    # oauth/token","registration_endpoint":".../oauth/register","scopes_
    # supported":["org:read","project:write","team:write","event:write"],
    # "token_endpoint_auth_methods_supported":["client_secret_basic",
    # "client_secret_post","none"],"code_challenge_methods_supported":
    # ["plain","S256"]}. No userinfo/introspection endpoint advertised —
    # profile_probe left unset rather than guessing an unverified URL.
    "sentry": OAuthProviderConfig(
        label="Sentry",
        env_vars={
            "client_id": ("SENTRY_CLIENT_ID",),
            "client_secret": ("SENTRY_CLIENT_SECRET",),
        },
        scopes=("org:read", "project:write", "team:write", "event:write"),
        auth_url="https://mcp.sentry.dev/oauth/authorize",
        token_url="https://mcp.sentry.dev/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.sentry.dev/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Attio: official remote MCP server (mcp.attio.com), whose authorization
    # server lives on a DIFFERENT host — Attio's main web app. Confirmed live
    # 2026-07-19 via GET https://mcp.attio.com/.well-known/oauth-protected-
    # resource -> authorization_servers=["https://app.attio.com"]; GET
    # https://app.attio.com/.well-known/oauth-authorization-server ->
    # {"issuer":"https://app.attio.com","authorization_endpoint":".../oidc/
    # authorize","token_endpoint":".../oidc/token","registration_
    # endpoint":".../oauth/register","scopes_supported":["mcp",
    # "offline_access","openid"],"token_endpoint_auth_methods_supported":
    # ["none","client_secret_post"],"code_challenge_methods_supported":
    # ["S256"]}. No userinfo/introspection endpoint advertised — profile_probe
    # left unset rather than guessing an unverified URL.
    "attio": OAuthProviderConfig(
        label="Attio",
        env_vars={
            "client_id": ("ATTIO_CLIENT_ID",),
            "client_secret": ("ATTIO_CLIENT_SECRET",),
        },
        scopes=("mcp", "offline_access", "openid"),
        auth_url="https://app.attio.com/oidc/authorize",
        token_url="https://app.attio.com/oidc/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://app.attio.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Cloudflare: official remote MCP server (mcp.cloudflare.com) — a "Code
    # Mode" server exposing ~2,500 Cloudflare API endpoints through two tools
    # (search()/execute()) rather than one tool per endpoint. Cloudflare also
    # runs 16 domain-specific sibling MCP servers (docs/bindings/builds/
    # observability/radar/containers/browser/logs/ai-gateway/autorag/
    # auditlogs/dns-analytics/dex/casb/graphql.mcp.cloudflare.com, and
    # agents.cloudflare.com/mcp) on the same auth pattern, not individually
    # wired here — only the primary server was live-discovery-verified.
    # Confirmed live 2026-07-19 via GET https://mcp.cloudflare.com/.well-
    # known/oauth-authorization-server -> {"issuer":"https://mcp.
    # cloudflare.com","authorization_endpoint":".../authorize",
    # "token_endpoint":".../token","registration_endpoint":".../register",
    # "token_endpoint_auth_methods_supported":["client_secret_basic",
    # "client_secret_post","none"],"code_challenge_methods_supported":
    # ["plain","S256"]}. No scopes_supported or userinfo/introspection
    # endpoint advertised — scopes left empty and profile_probe left unset
    # rather than guessing.
    "cloudflare": OAuthProviderConfig(
        label="Cloudflare",
        env_vars={
            "client_id": ("CLOUDFLARE_CLIENT_ID",),
            "client_secret": ("CLOUDFLARE_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.cloudflare.com/authorize",
        token_url="https://mcp.cloudflare.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.cloudflare.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Gusto: official remote MCP server (https://mcp.api.gusto.com). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes curated to read-only HR/payroll
    # data; payrolls:run/payrolls:write (executes payroll) and
    # webhook_subscriptions:write deliberately excluded
    "gusto": OAuthProviderConfig(
        label="Gusto",
        env_vars={
            "client_id": ("GUSTO_CLIENT_ID",),
            "client_secret": ("GUSTO_CLIENT_SECRET",),
        },
        scopes=("public", "companies:read", "employees:read", "employments:read", "jobs:read", "departments:read", "compensations:read", "contractors:read", "pay_schedules:read", "payrolls:read", "time_sheet:read"),
        auth_url="https://mcp.api.gusto.com/oauth/authorize",
        token_url="https://mcp.api.gusto.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.api.gusto.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Deel: official remote MCP server (https://api.letsdeel.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none"] only -- public client. Scopes
    # curated to core people/HR read+write; global-payroll/off-cycle-
    # payments/invoice/treasury/withdrawals/equities/compensation-management write
    # scopes (money movement) deliberately excluded
    "deel": OAuthProviderConfig(
        label="Deel",
        env_vars={
            "client_id": ("DEEL_CLIENT_ID",),
            "client_secret": ("DEEL_CLIENT_SECRET",),
        },
        scopes=("people:read", "people:write", "worker:read", "worker:write", "profile:read", "time-off:read", "time-off:write", "timesheets:read", "timesheets:write", "contracts:read", "tasks:read", "tasks:write", "organizations:read"),
        auth_url="https://api.letsdeel.com/oauth/authorize",
        token_url="https://api.letsdeel.com/oauth/tokens",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://api.letsdeel.com/oauth/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Remote: official remote MCP server (https://mcp.remote.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported and
    # token_endpoint_auth_methods_supported not declared in discovery -- left at
    # field defaults rather than guessing. Auth server resolved via
    # mcp.remote.com's oauth-protected-resource -> issuer chain to
    # api.employ.remote.com
    "remote_com": OAuthProviderConfig(
        label="Remote",
        env_vars={
            "client_id": ("REMOTE_COM_CLIENT_ID",),
            "client_secret": ("REMOTE_COM_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://api.employ.remote.com/oauth/authorize",
        token_url="https://api.employ.remote.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.employ.remote.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Ashby: official remote MCP server (https://mcp.ashbyhq.com/mcp/v1). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). auth server on a DIFFERENT host (mcp-
    # auth.ashbyhq.com/oidc) from the MCP endpoint (mcp.ashbyhq.com), resolved via
    # oauth-protected-resource -> issuer chain, same pattern as Attio
    "ashby": OAuthProviderConfig(
        label="Ashby",
        env_vars={
            "client_id": ("ASHBY_CLIENT_ID",),
            "client_secret": ("ASHBY_CLIENT_SECRET",),
        },
        scopes=("openid", "mcp", "offline_access"),
        auth_url="https://mcp-auth.ashbyhq.com/oidc/auth",
        token_url="https://mcp-auth.ashbyhq.com/oidc/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://mcp-auth.ashbyhq.com/oidc/me",
        registration_endpoint="https://mcp-auth.ashbyhq.com/oidc/reg",
        dynamic_registration_opt_in_required=False,
    ),
    # Klaviyo: official remote MCP server (https://mcp.klaviyo.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported not declared
    "klaviyo": OAuthProviderConfig(
        label="Klaviyo",
        env_vars={
            "client_id": ("KLAVIYO_CLIENT_ID",),
            "client_secret": ("KLAVIYO_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.klaviyo.com/authorize",
        token_url="https://mcp.klaviyo.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.klaviyo.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Customer.io: official remote MCP server (https://mcp.customer.io/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). scopes_supported not declared
    "customer_io": OAuthProviderConfig(
        label="Customer.io",
        env_vars={
            "client_id": ("CUSTOMER_IO_CLIENT_ID",),
            "client_secret": ("CUSTOMER_IO_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.customer.io/oauth2/authorize",
        token_url="https://mcp.customer.io/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.customer.io/oauth2/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Netlify: official remote MCP server (https://netlify-mcp.netlify.app/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "netlify": OAuthProviderConfig(
        label="Netlify",
        env_vars={
            "client_id": ("NETLIFY_CLIENT_ID",),
            "client_secret": ("NETLIFY_CLIENT_SECRET",),
        },
        scopes=("offline_access", "read", "write", "claudeai"),
        auth_url="https://netlify-mcp.netlify.app/oauth-server/auth",
        token_url="https://netlify-mcp.netlify.app/oauth-server/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://netlify-mcp.netlify.app/oauth-server/reg",
        dynamic_registration_opt_in_required=False,
    ),
    # Supabase: official remote MCP server (https://mcp.supabase.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). auth server on a DIFFERENT host
    # (api.supabase.com) from the MCP endpoint, resolved via oauth-protected-
    # resource -> issuer chain
    "supabase": OAuthProviderConfig(
        label="Supabase",
        env_vars={
            "client_id": ("SUPABASE_CLIENT_ID",),
            "client_secret": ("SUPABASE_CLIENT_SECRET",),
        },
        scopes=("organizations:read", "projects:read", "projects:write", "database:write", "database:read", "analytics:read", "secrets:read", "edge_functions:read", "edge_functions:write", "environment:read", "environment:write", "storage:read"),
        auth_url="https://api.supabase.com/v1/oauth/authorize",
        token_url="https://api.supabase.com/v1/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.supabase.com/platform/oauth/apps/register",
        dynamic_registration_opt_in_required=False,
    ),
    # PlanetScale: official remote MCP server
    # (https://mcp.pscale.dev/mcp/planetscale). Confirmed live 2026-07-19 via GET
    # .well-known/oauth-authorization-server (or oauth-protected-resource -> issuer
    # chain). resolved via path-suffixed discovery (/.well-known/oauth-
    # authorization-server/mcp/planetscale). Scopes curated to read-oriented +
    # database creation; manage_passwords/manage_production_* (production
    # credential management) deliberately excluded
    "planetscale": OAuthProviderConfig(
        label="PlanetScale",
        env_vars={
            "client_id": ("PLANETSCALE_CLIENT_ID",),
            "client_secret": ("PLANETSCALE_CLIENT_SECRET",),
        },
        scopes=("email", "openid", "profile", "database:read_branches", "database:read_database", "database:read_deploy_requests", "organization:read_branches", "organization:read_databases", "organization:read_organization", "organization:create_databases", "user:read_organizations", "user:read_user"),
        auth_url="https://app.planetscale.com/oauth/authorize",
        token_url="https://auth.planetscale.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://auth.planetscale.com/oauth/userinfo",
        registration_endpoint="https://auth.planetscale.com/oauth/registration",
        dynamic_registration_opt_in_required=False,
    ),
    # Neon: official remote MCP server (https://mcp.neon.tech/mcp). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported also lists a "*"
    # wildcard-all scope -- deliberately not requested, using the narrower
    # read/write instead
    "neon": OAuthProviderConfig(
        label="Neon",
        env_vars={
            "client_id": ("NEON_CLIENT_ID",),
            "client_secret": ("NEON_CLIENT_SECRET",),
        },
        scopes=("read", "write"),
        auth_url="https://mcp.neon.tech/api/authorize",
        token_url="https://mcp.neon.tech/api/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.neon.tech/api/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Railway: official remote MCP server (https://mcp.railway.com). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). live discovery's authorization_endpoint
    # embeds ?resource=https://mcp.railway.com (RFC 8707 resource indicator) --
    # moved into auth_params rather than auth_url since _build authorization_url
    # always appends its own "?"+params (would double up)
    "railway": OAuthProviderConfig(
        label="Railway",
        env_vars={
            "client_id": ("RAILWAY_CLIENT_ID",),
            "client_secret": ("RAILWAY_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "offline_access", "workspace:member"),
        auth_url="https://backboard.railway.com/oauth/auth",
        token_url="https://backboard.railway.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        auth_params={"resource": "https://mcp.railway.com"},
        registration_endpoint="https://backboard.railway.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Heroku: official remote MCP server (https://mcp.heroku.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). token_endpoint_auth_methods_supported
    # not declared -- left at field default (client_secret_post)
    "heroku": OAuthProviderConfig(
        label="Heroku",
        env_vars={
            "client_id": ("HEROKU_CLIENT_ID",),
            "client_secret": ("HEROKU_CLIENT_SECRET",),
        },
        scopes=("openid", "offline_access"),
        auth_url="https://mcp.heroku.com/auth",
        token_url="https://mcp.heroku.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://mcp.heroku.com/me",
        registration_endpoint="https://mcp.heroku.com/reg",
        dynamic_registration_opt_in_required=False,
    ),
    # Sourcegraph: official remote MCP server (https://sourcegraph.com/.api/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). discovery also declares
    # introspection_endpoint (not a bearer-GET userinfo endpoint) -- not used as
    # profile_probe
    "sourcegraph": OAuthProviderConfig(
        label="Sourcegraph",
        env_vars={
            "client_id": ("SOURCEGRAPH_CLIENT_ID",),
            "client_secret": ("SOURCEGRAPH_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "offline_access", "user:all", "mcp", "externalapi:read", "externalapi:write"),
        auth_url="https://sourcegraph.com/.auth/idp/oauth/authorize",
        token_url="https://sourcegraph.com/.auth/idp/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://sourcegraph.com/.auth/idp/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Replit: official remote MCP server (https://replit-mcp.com/server/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "replit": OAuthProviderConfig(
        label="Replit",
        env_vars={
            "client_id": ("REPLIT_CLIENT_ID",),
            "client_secret": ("REPLIT_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "offline_access", "apps:read", "apps:write"),
        auth_url="https://replit.com/oidc/auth",
        token_url="https://replit.com/oidc/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://replit.com/oidc/reg",
        dynamic_registration_opt_in_required=False,
    ),
    # Postman: official remote MCP server (https://mcp.postman.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported not declared
    "postman": OAuthProviderConfig(
        label="Postman",
        env_vars={
            "client_id": ("POSTMAN_CLIENT_ID",),
            "client_secret": ("POSTMAN_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://mcp.postman.com/authorize",
        token_url="https://mcp.postman.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.postman.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Buildkite: official remote MCP server (https://mcp.buildkite.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "buildkite": OAuthProviderConfig(
        label="Buildkite",
        env_vars={
            "client_id": ("BUILDKITE_CLIENT_ID",),
            "client_secret": ("BUILDKITE_CLIENT_SECRET",),
        },
        scopes=("read", "write"),
        auth_url="https://mcp.buildkite.com/oauth/authorize",
        token_url="https://mcp.buildkite.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.buildkite.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Socket: official remote MCP server (https://mcp.socket.dev/). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none","client_secret_basic"] -- no
    # client_secret_post, so token_auth switches to basic (same shape as
    # Airtable/Mercury). scopes_supported has ~90 entries (full
    # admin/webhook/token-management/policy-write surface) -- curated to a read-
    # plus-core-scan subset; api-tokens:*/webhooks:*/integration:*/access-
    # policy:*/security-policy:update (admin scopes) deliberately excluded
    "socket": OAuthProviderConfig(
        label="Socket",
        env_vars={
            "client_id": ("SOCKET_CLIENT_ID",),
            "client_secret": ("SOCKET_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "alerts:list", "alerts:trend", "dependencies:list", "dependencies:trend", "full-scans:list", "full-scans:create", "packages:list", "repo:list", "report:list", "report:read", "security-policy:read", "socket-basics:read", "triage:alerts-list"),
        auth_url="https://api.socket.dev/v1/oauth2/authorize",
        token_url="https://api.socket.dev/v1/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://api.socket.dev/v1/oauth2/userinfo",
        token_auth="basic",
        registration_endpoint="https://api.socket.dev/v1/oauth2/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Whimsical: official remote MCP server (https://mcp.whimsical.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). auth/token/register endpoints live
    # on api.whimsical.com, a different host from the mcp.whimsical.com MCP
    # endpoint
    "whimsical": OAuthProviderConfig(
        label="Whimsical",
        env_vars={
            "client_id": ("WHIMSICAL_CLIENT_ID",),
            "client_secret": ("WHIMSICAL_CLIENT_SECRET",),
        },
        scopes=("mcp:read", "mcp:write", "profile"),
        auth_url="https://api.whimsical.com/v1/oauth.authorize",
        token_url="https://api.whimsical.com/v1/oauth.token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.whimsical.com/v1/oauth.register-mcp-client",
        dynamic_registration_opt_in_required=False,
    ),
    # Ramp: official remote MCP server (https://mcp.ramp.com/mcp). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none"] only -- public client.
    # authorization_endpoint embeds ?auth_level=auto, moved into auth_params for
    # the same double-"?" reason as Railway. Scopes curated to read-only
    # reporting/bookkeeping; funds:write/x402:write/banking_drawdown_requests:write
    # /bank_accounts:write/cards:write/approvals:write (money movement, card
    # issuance, spend approval) deliberately excluded
    "ramp": OAuthProviderConfig(
        label="Ramp",
        env_vars={
            "client_id": ("RAMP_CLIENT_ID",),
            "client_secret": ("RAMP_CLIENT_SECRET",),
        },
        scopes=("bills:read", "cards:read", "departments:read", "entities:read", "limits:read", "locations:read", "memos:read", "purchase_orders:read", "reimbursements:read", "spend_programs:read", "transactions:read", "users:read", "vendors:read", "accounting:read", "merchants:read", "spend_requests:read", "bank_accounts:read", "tasks:read", "trips:read"),
        auth_url="https://mcp.ramp.com/oauth/authorize",
        token_url="https://api.ramp.com/developer/v1/token/pkce",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        auth_params={"auth_level": "auto"},
        include_client_secret_in_token_body=False,
        registration_endpoint="https://mcp.ramp.com/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Brex: official remote MCP server (https://api.brex.com/mcp). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). registration_endpoint is a non-standard
    # path (/v3/clients, not /register) -- confirmed live regardless. Scopes copied
    # verbatim: already conservative (mostly *.readonly;
    # expenses.card/expenses.bill are expense-record writes, not fund transfers)
    "brex": OAuthProviderConfig(
        label="Brex",
        env_vars={
            "client_id": ("BREX_CLIENT_ID",),
            "client_secret": ("BREX_CLIENT_SECRET",),
        },
        scopes=("openid", "offline_access", "email", "users.readonly", "departments.readonly", "locations.readonly", "titles.readonly", "legal_entities.readonly", "cards.readonly", "companies.readonly", "budgets.readonly", "travel.trips.readonly", "expenses.card.readonly", "expenses.card", "expenses.bill", "accounts.cash.readonly", "vendors.readonly", "accounting.integration.read", "accounting.record.read"),
        auth_url="https://accounts-api.brex.com/oauth2/default/v1/authorize",
        token_url="https://accounts-api.brex.com/oauth2/default/v1/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.brex.com/v3/clients",
        dynamic_registration_opt_in_required=False,
    ),
    # Mercury: official remote MCP server (https://mcp.mercury.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["client_secret_basic","none"] -- no
    # client_secret_post, so token_auth switches to basic. No write scope even
    # offered -- read-only by the provider's own design
    "mercury": OAuthProviderConfig(
        label="Mercury",
        env_vars={
            "client_id": ("MERCURY_CLIENT_ID",),
            "client_secret": ("MERCURY_CLIENT_SECRET",),
        },
        scopes=("read", "offline_access"),
        auth_url="https://mcp.mercury.com/authorize",
        token_url="https://mcp.mercury.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        token_auth="basic",
        registration_endpoint="https://mcp.mercury.com/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="client_secret_basic",
    ),
    # Robinhood: official remote MCP server
    # (https://agent.robinhood.com/mcp/trading). Confirmed live 2026-07-19 via GET
    # .well-known/oauth-authorization-server (or oauth-protected-resource -> issuer
    # chain). token_endpoint_auth_methods_supported=["none"] only -- public client.
    # scopes_supported is exactly one opaque scope ("internal") -- no finer-grained
    # alternative exists
    "robinhood": OAuthProviderConfig(
        label="Robinhood",
        env_vars={
            "client_id": ("ROBINHOOD_CLIENT_ID",),
            "client_secret": ("ROBINHOOD_CLIENT_SECRET",),
        },
        scopes=("internal",),
        auth_url="https://robinhood.com/oauth",
        token_url="https://api.robinhood.com/oauth2/token/",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://agent.robinhood.com/oauth/trading/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Amplitude: official remote MCP server (https://mcp.amplitude.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "amplitude": OAuthProviderConfig(
        label="Amplitude",
        env_vars={
            "client_id": ("AMPLITUDE_CLIENT_ID",),
            "client_secret": ("AMPLITUDE_CLIENT_SECRET",),
        },
        scopes=("mcp:read", "mcp:write", "offline_access"),
        auth_url="https://mcp.amplitude.com/authorize",
        token_url="https://mcp.amplitude.com/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.amplitude.com/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Mixpanel: official remote MCP server (https://mcp.mixpanel.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). scopes copied verbatim -- all
    # analytics/reporting-read scopes, no admin/destructive scope offered
    "mixpanel": OAuthProviderConfig(
        label="Mixpanel",
        env_vars={
            "client_id": ("MIXPANEL_CLIENT_ID",),
            "client_secret": ("MIXPANEL_CLIENT_SECRET",),
        },
        scopes=("projects", "analysis", "events", "insights", "segmentation", "retention", "data:read", "funnels", "flows", "data_definitions", "bookmarks", "business_context", "cohorts", "dashboard_reports", "experiments", "feature_flags", "metrics", "user_details"),
        auth_url="https://mixpanel.com/oauth/authorize",
        token_url="https://mixpanel.com/oauth/token/",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mixpanel.com/oauth/mcp/register/",
        dynamic_registration_opt_in_required=False,
    ),
    # PostHog: official remote MCP server (https://mcp.posthog.com/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). auth server on a dedicated
    # oauth.posthog.com host. scopes_supported has ~200 entries (full product
    # surface incl. organization/billing/member-management/access-control) --
    # curated to a core analytics/feature-flag subset; organization:write,
    # organization_member:*, access_control:*, and similar admin scopes
    # deliberately excluded
    "posthog": OAuthProviderConfig(
        label="PostHog",
        env_vars={
            "client_id": ("POSTHOG_CLIENT_ID",),
            "client_secret": ("POSTHOG_CLIENT_SECRET",),
        },
        scopes=("openid", "profile", "email", "insight:read", "insight:write", "dashboard:read", "dashboard:write", "event_definition:read", "feature_flag:read", "feature_flag:write", "action:read", "action:write", "annotation:read", "annotation:write", "cohort:read", "experiment:read", "person:read", "project:read", "query:read", "session_recording:read", "survey:read", "survey:write", "web_analytics:read"),
        auth_url="https://oauth.posthog.com/oauth/authorize/",
        token_url="https://oauth.posthog.com/oauth/token/",
        auth_method="pkce",
        token_parser="standard",
        profile_probe="https://oauth.posthog.com/oauth/userinfo/",
        registration_endpoint="https://oauth.posthog.com/oauth/register/",
        dynamic_registration_opt_in_required=False,
    ),
    # Meta Ads: official remote MCP server (https://mcp.facebook.com/ads).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none"] only -- public client.
    # Authorization happens via Facebook's classic Graph API OAuth dialog
    # (www.facebook.com), resolved via mcp.facebook.com/ads's own oauth-protected-
    # resource -> issuer chain; the MCP endpoint IS the resource this token is
    # minted for, so MCP-scoped validation against it is correct
    "meta_ads": OAuthProviderConfig(
        label="Meta Ads",
        env_vars={
            "client_id": ("META_ADS_CLIENT_ID",),
            "client_secret": ("META_ADS_CLIENT_SECRET",),
        },
        scopes=("ads_management", "ads_read", "catalog_management", "business_management", "pages_show_list", "instagram_basic", "ads_mcp_management"),
        auth_url="https://www.facebook.com/v25.0/dialog/oauth",
        token_url="https://graph.facebook.com/v25.0/oauth/access_token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://mcp.facebook.com/.well-known/register/ads",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Semrush: official remote MCP server (https://mcp.semrush.com/v1/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none"] only -- public client
    "semrush": OAuthProviderConfig(
        label="Semrush",
        env_vars={
            "client_id": ("SEMRUSH_CLIENT_ID",),
            "client_secret": ("SEMRUSH_CLIENT_SECRET",),
        },
        scopes=("mcp.access",),
        auth_url="https://api.semrush.com/apis/v4/auth/v0/oauth2/auth",
        token_url="https://api.semrush.com/apis/v4-raw/auth/v1/oauth2/access_token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://api.semrush.com/apis/v4-raw/auth/v1/oauth2/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Ahrefs: official remote MCP server (https://api.ahrefs.com/mcp/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). authorize/token/register span
    # three different hosts (app.ahrefs.com / ahrefs.com / api.ahrefs.com) per live
    # discovery. token_endpoint_auth_methods_supported not declared -- left at
    # field default
    "ahrefs": OAuthProviderConfig(
        label="Ahrefs",
        env_vars={
            "client_id": ("AHREFS_CLIENT_ID",),
            "client_secret": ("AHREFS_CLIENT_SECRET",),
        },
        scopes=("apiv3-mcp",),
        auth_url="https://app.ahrefs.com/web/oauth/authorize",
        token_url="https://ahrefs.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.ahrefs.com/mcp/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Close: official remote MCP server (https://mcp.close.com/mcp). Confirmed live
    # 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported not declared
    "close_crm": OAuthProviderConfig(
        label="Close",
        env_vars={
            "client_id": ("CLOSE_CRM_CLIENT_ID",),
            "client_secret": ("CLOSE_CRM_CLIENT_SECRET",),
        },
        scopes=(),
        auth_url="https://app.close.com/oauth2/authorize/",
        token_url="https://api.close.com/oauth2/token/",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.close.com/oauth2/register/",
        dynamic_registration_opt_in_required=False,
    ),
    # Apollo.io: official remote MCP server (https://mcp.apollo.io/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain). scopes_supported has ~70 entries --
    # curated to core contact/company/opportunity/task read+write;
    # email_account_purchase_*/domain_purchase_* (literal purchases) and
    # admin/usage-stats scopes deliberately excluded
    "apollo_io": OAuthProviderConfig(
        label="Apollo.io",
        env_vars={
            "client_id": ("APOLLO_IO_CLIENT_ID",),
            "client_secret": ("APOLLO_IO_CLIENT_SECRET",),
        },
        scopes=("read_user_profile", "contacts_search", "contact_read", "contact_write", "contact_update", "account_write", "account_update", "people_match", "organizations_enrich", "organizations_bulk_enrich", "opportunities_list", "opportunity_read", "opportunity_write", "tasks_list", "tasks_create", "emailer_campaigns_search", "emailer_messages_search", "tags_list", "lists_create"),
        auth_url="https://mcp.apollo.io/mcp/oauth_metadata/redirect_to_authorize",
        token_url="https://mcp.apollo.io/api/v1/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.apollo.io/api/v1/oauth/applications/register_oauth_client",
        dynamic_registration_opt_in_required=False,
    ),
    # Outreach: official remote MCP server (https://api.outreach.io/mcp/).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "outreach": OAuthProviderConfig(
        label="Outreach",
        env_vars={
            "client_id": ("OUTREACH_CLIENT_ID",),
            "client_secret": ("OUTREACH_CLIENT_SECRET",),
        },
        scopes=("prospects.all",),
        auth_url="https://api.outreach.io/mcpOAuth/authorize",
        token_url="https://api.outreach.io/mcpOAuth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.outreach.io/mcpOAuth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Salesloft: official remote MCP server (https://mcp.salesloft.com/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain). auth/token endpoints on
    # accounts.salesloft.com, registration on mcp.salesloft.com -- different hosts
    # per live discovery
    "salesloft": OAuthProviderConfig(
        label="Salesloft",
        env_vars={
            "client_id": ("SALESLOFT_CLIENT_ID",),
            "client_secret": ("SALESLOFT_CLIENT_SECRET",),
        },
        scopes=("accounts:read", "conversations:read", "opportunities:read", "people:read", "team:read", "claudeai"),
        auth_url="https://accounts.salesloft.com/oauth/authorize",
        token_url="https://accounts.salesloft.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://mcp.salesloft.com/auth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Clay: official remote MCP server (https://api.clay.com/v3/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    "clay": OAuthProviderConfig(
        label="Clay",
        env_vars={
            "client_id": ("CLAY_CLIENT_ID",),
            "client_secret": ("CLAY_CLIENT_SECRET",),
        },
        scopes=("mcp",),
        auth_url="https://app.clay.com/oauth/authorize",
        token_url="https://api.clay.com/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.clay.com/oauth/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Fireflies.ai: official remote MCP server (https://api.fireflies.ai/mcp).
    # Confirmed live 2026-07-19 via GET .well-known/oauth-authorization-server (or
    # oauth-protected-resource -> issuer chain).
    "fireflies": OAuthProviderConfig(
        label="Fireflies.ai",
        env_vars={
            "client_id": ("FIREFLIES_CLIENT_ID",),
            "client_secret": ("FIREFLIES_CLIENT_SECRET",),
        },
        scopes=("profile", "email"),
        auth_url="https://api.fireflies.ai/authorize",
        token_url="https://api.fireflies.ai/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://api.fireflies.ai/register",
        dynamic_registration_opt_in_required=False,
    ),
    # Fathom: official remote MCP server (https://api.fathom.ai/mcp). Confirmed
    # live 2026-07-19 via GET .well-known/oauth-authorization-server (or oauth-
    # protected-resource -> issuer chain).
    # token_endpoint_auth_methods_supported=["none"] only -- public client.
    # authorize endpoint on fathom.video, token endpoint on api.fathom.ai --
    # different hosts per live discovery. Simple Icons "fathom" slug is the WRONG
    # brand (Fathom Analytics, an unrelated company) -- logo intentionally NOT
    # wired, uses a placeholder (see fleet-icons.ts)
    "fathom": OAuthProviderConfig(
        label="Fathom",
        env_vars={
            "client_id": ("FATHOM_CLIENT_ID",),
            "client_secret": ("FATHOM_CLIENT_SECRET",),
        },
        scopes=("mcp",),
        auth_url="https://fathom.video/mcp/oauth/authorize",
        token_url="https://api.fathom.ai/mcp/oauth/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        include_client_secret_in_token_body=False,
        registration_endpoint="https://api.fathom.ai/mcp/oauth/register",
        dynamic_registration_opt_in_required=False,
        dynamic_registration_token_auth_method="none",
    ),
    # Superhuman Docs (formerly Coda): official remote MCP server
    # (https://docs.superhuman.com/apis/mcp). Confirmed live 2026-07-19 via GET
    # .well-known/oauth-authorization-server (or oauth-protected-resource -> issuer
    # chain). issuer resolves to tokens.grammarly.com (Coda/Superhuman Docs is now
    # issued through Grammarly's shared identity service, post-acquisition) --
    # confirmed via live oauth-protected-resource -> issuer chain from
    # docs.superhuman.com
    "coda": OAuthProviderConfig(
        label="Superhuman Docs (formerly Coda)",
        env_vars={
            "client_id": ("CODA_CLIENT_ID",),
            "client_secret": ("CODA_CLIENT_SECRET",),
        },
        scopes=("mcp:all",),
        auth_url="https://tokens.grammarly.com/v4/api/oauth2/authorize",
        token_url="https://tokens.grammarly.com/v4/api/oauth2/token",
        auth_method="pkce",
        token_parser="standard",
        profile_probe=None,
        registration_endpoint="https://tokens.grammarly.com/v4/api/oauth2/register",
        dynamic_registration_opt_in_required=False,
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
    "confluence": "confluence",
    "miro": "miro",
    "intercom": "intercom",
    "docusign": "docusign",
    "docu_sign": "docusign",
    "square": "square",
    "typeform": "typeform",
    "vercel": "vercel",
    "higgsfield": "higgsfield",
    "zapier": "zapier",
    "paypal": "paypal",
    "sentry": "sentry",
    "attio": "attio",
    "cloudflare": "cloudflare",
    "gusto": "gusto",
    "deel": "deel",
    "remote_com": "remote_com",
    "ashby": "ashby",
    "klaviyo": "klaviyo",
    "customer_io": "customer_io",
    "netlify": "netlify",
    "supabase": "supabase",
    "planetscale": "planetscale",
    "neon": "neon",
    "railway": "railway",
    "heroku": "heroku",
    "sourcegraph": "sourcegraph",
    "replit": "replit",
    "postman": "postman",
    "buildkite": "buildkite",
    "socket": "socket",
    "whimsical": "whimsical",
    "ramp": "ramp",
    "brex": "brex",
    "mercury": "mercury",
    "robinhood": "robinhood",
    "amplitude": "amplitude",
    "mixpanel": "mixpanel",
    "posthog": "posthog",
    "meta_ads": "meta_ads",
    "semrush": "semrush",
    "ahrefs": "ahrefs",
    "close_crm": "close_crm",
    "apollo_io": "apollo_io",
    "outreach": "outreach",
    "salesloft": "salesloft",
    "clay": "clay",
    "fireflies": "fireflies",
    "fathom": "fathom",
    "coda": "coda",
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
    if client_id and client_secret:
        return True
    # Dynamic-client-registration providers: no static client_id is required
    # up front. Providers with dynamic_registration_opt_in_required=True
    # (Higgsfield) still need the owner to opt in with a feature flag before
    # this reports True — see _dynamic_registration_enabled(). Providers with
    # dynamic_registration_opt_in_required=False (Stripe, Linear, Notion,
    # Asana, Canva, Airtable, ClickUp) report True unconditionally: there is
    # no console for an operator to "finish configuring" even if they wanted
    # to, so the OAuth client self-registers on first real connect (see
    # _resolve_oauth_client) with no flag needed. Every provider with
    # config.registration_endpoint is None falls through to False exactly as
    # before this branch existed.
    config = OAUTH_PROVIDER_CONFIGS.get(str(provider or "").strip().lower())
    if config is not None and config.registration_endpoint:
        return _dynamic_registration_enabled(provider)
    return False


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591) fallback for MCP providers that have
# no developer console — see the registration_endpoint field on
# OAuthProviderConfig and the "higgsfield"/"stripe"/"linear"/"notion"/
# "asana"/"canva"/"airtable"/"clickup" entries in OAUTH_PROVIDER_CONFIGS for
# the motivating cases. This machinery is inert for every other, statically
# configured provider above, since config.registration_endpoint is None for
# all of them; _resolve_oauth_client() falls through to the exact same
# ensure_oauth_configured() call (and exception) they always used.
# ---------------------------------------------------------------------------

# Safety margin before a cached client_secret's real expiry at which it's
# already treated as unusable and re-registered — refreshing a few minutes
# early is cheap; a token exchange racing the actual expiry instant is not.
_DYNAMIC_CLIENT_EXPIRY_SAFETY_MARGIN_SECONDS = 300


def _coerce_epoch_seconds(value: Any) -> int:
    """Best-effort int coercion for RFC 7591 client_id_issued_at /
    client_secret_expires_at fields — both are supposed to be JSON numbers,
    but a provider returning a string, null, or garbage must degrade to "no
    value" rather than raise out of registration bookkeeping."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class _DynamicClientRegistration:
    """A cached RFC 7591 client registration, plus the two timing fields the
    registration response can carry (client_id_issued_at, client_secret_
    expires_at). Per RFC 7591 §3.2.1, client_secret_expires_at of 0 (or the
    field being absent, which _coerce_epoch_seconds also normalizes to 0)
    means the secret does not expire — Higgsfield returns exactly that.
    Linear, live-verified 2026-07-19, returns a real 24-hour
    client_secret_expires_at, so a cache with no expiry awareness silently
    outlives the secret and every subsequent token exchange/refresh 401s."""

    client_id: str
    client_secret: str
    client_id_issued_at: int = 0
    client_secret_expires_at: int = 0

    def is_expired(self, *, safety_margin_seconds: int = _DYNAMIC_CLIENT_EXPIRY_SAFETY_MARGIN_SECONDS) -> bool:
        if self.client_secret_expires_at <= 0:
            return False  # 0 / absent = never expires (RFC 7591 §3.2.1)
        return time.time() >= (self.client_secret_expires_at - safety_margin_seconds)


_DYNAMIC_CLIENT_CACHE: Dict[tuple[str, str], _DynamicClientRegistration] = {}
_DYNAMIC_CLIENT_CACHE_LOCK = threading.Lock()


def _dynamic_registration_enabled(provider: str) -> bool:
    normalized = str(provider or "").strip().lower()
    config = OAUTH_PROVIDER_CONFIGS.get(normalized)
    if config is not None and config.registration_endpoint and not config.dynamic_registration_opt_in_required:
        return True
    return _env_flag_enabled(f"{normalized.upper()}_OAUTH_ENABLED", f"{normalized.upper()}_MCP_ENABLED")


def _register_dynamic_client(provider: str, config: OAuthProviderConfig, redirect_uri: str) -> tuple[str, str]:
    """Self-register an OAuth client via RFC 7591 and cache the result for
    the life of the process, keyed by (provider, redirect_uri) — a client
    registration is bound to the redirect_uris declared at registration time,
    so a cached entry is only reusable for the exact redirect_uri it was
    registered with (stable in practice: one production origin per deploy).

    A cached entry whose client_secret_expires_at has passed (or is within
    _DYNAMIC_CLIENT_EXPIRY_SAFETY_MARGIN_SECONDS of passing) is treated as
    absent and transparently re-registered — see _DynamicClientRegistration.
    is_expired(). Entries with client_secret_expires_at == 0 (or missing
    from the registration response) never expire, per RFC 7591."""
    cache_key = (provider, redirect_uri)
    cached = _DYNAMIC_CLIENT_CACHE.get(cache_key)
    if cached is not None and not cached.is_expired():
        return (cached.client_id, cached.client_secret)
    with _DYNAMIC_CLIENT_CACHE_LOCK:
        cached = _DYNAMIC_CLIENT_CACHE.get(cache_key)
        if cached is not None and not cached.is_expired():
            return (cached.client_id, cached.client_secret)
        if not config.registration_endpoint:
            raise HTTPException(status_code=409, detail=f"{_connector_label(provider)} has no dynamic registration endpoint configured.")
        payload = {
            "client_name": _env_first(f"{provider.upper()}_OAUTH_CLIENT_NAME") or "Empyralis",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # Request whichever token_endpoint_auth_method this provider's
            # own discovery document actually advertises (see
            # dynamic_registration_token_auth_method on OAuthProviderConfig)
            # instead of assuming every provider supports "client_secret_
            # post" — Stripe and ClickUp only advertise "none" (public
            # client secured by PKCE, not a secret); Notion, Canva, and
            # Airtable only advertise "client_secret_basic" among the
            # secret-based options. Requesting an unsupported method risks
            # the registration being rejected outright or silently coerced
            # to a method the subsequent token exchange doesn't match.
            "token_endpoint_auth_method": config.dynamic_registration_token_auth_method,
        }
        try:
            registration = _post_json(config.registration_endpoint, payload)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{_connector_label(provider)} dynamic client registration failed: {exc}",
            ) from exc
        dynamic_client_id = str(registration.get("client_id") or "").strip()
        if not dynamic_client_id:
            raise HTTPException(
                status_code=502,
                detail=f"{_connector_label(provider)} dynamic client registration did not return a client_id.",
            )
        dynamic_client_secret = str(registration.get("client_secret") or "").strip()
        entry = _DynamicClientRegistration(
            client_id=dynamic_client_id,
            client_secret=dynamic_client_secret,
            client_id_issued_at=_coerce_epoch_seconds(registration.get("client_id_issued_at")),
            client_secret_expires_at=_coerce_epoch_seconds(registration.get("client_secret_expires_at")),
        )
        _DYNAMIC_CLIENT_CACHE[cache_key] = entry
        if cached is not None:
            _log.info(
                "Re-registered OAuth client for %s (redirect_uri=%s) — previous client_secret was expired or expiring within %ds",
                provider, redirect_uri, _DYNAMIC_CLIENT_EXPIRY_SAFETY_MARGIN_SECONDS,
            )
        else:
            _log.info("Dynamically registered OAuth client for %s (redirect_uri=%s)", provider, redirect_uri)
        return (entry.client_id, entry.client_secret)


def _resolve_oauth_client(provider: str, redirect_uri: str) -> tuple[str, str]:
    """Resolve (client_id, client_secret) for a provider's OAuth application.

    Static path (every provider except Higgsfield today): a client_id/secret
    pre-registered by the workspace owner in the provider's developer
    console, supplied via env vars — see ensure_oauth_configured().

    Dynamic path (Higgsfield): when OAUTH_PROVIDER_CONFIGS[provider] declares
    a registration_endpoint and no static env vars are set, self-register via
    RFC 7591 once per (provider, redirect_uri) instead of failing closed.
    """
    try:
        return ensure_oauth_configured(provider)
    except HTTPException:
        config = _provider_config(provider)
        if config.registration_endpoint and _dynamic_registration_enabled(provider):
            return _register_dynamic_client(provider, config, redirect_uri)
        raise


def _resolve_oauth_client_for_refresh(provider: str) -> tuple[str, str]:
    """Same resolution as _resolve_oauth_client(), for background token
    refresh where no live Request/redirect_uri is available. Reuses whichever
    dynamically-registered client is already cached for this provider (it was
    registered during the original start_oauth call that produced the
    credential now being refreshed).

    Unlike _register_dynamic_client, this path has no redirect_uri and so
    can never re-register an expired entry (RFC 7591 registration requires
    declaring redirect_uris) -- an expired cache entry is skipped rather
    than handed back, so a refresh attempt fails fast locally (falling
    through to `raise`) instead of spending a network round trip on a
    client_secret the token endpoint is guaranteed to reject."""
    try:
        return ensure_oauth_configured(provider)
    except HTTPException:
        config = OAUTH_PROVIDER_CONFIGS.get(str(provider or "").strip().lower())
        if config is not None and config.registration_endpoint:
            for (cached_provider, _redirect_uri), entry in _DYNAMIC_CLIENT_CACHE.items():
                if cached_provider == provider and not entry.is_expired():
                    return (entry.client_id, entry.client_secret)
        raise


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
    extra_state: dict | None = None,
) -> Dict[str, Any]:
    config = _provider_config(provider)
    redirect_uri = callback_url(request, provider)
    # redirect_uri must be known before client resolution: dynamic-client-
    # registration providers (Higgsfield) bind their client_id to the exact
    # redirect_uris declared at registration time. Reordered from the plain
    # ensure_oauth_configured(provider) call this replaces — callback_url()
    # only needs `request` and `provider`, so this reorder is a no-op for
    # every statically-configured provider.
    client_id, _client_secret = _resolve_oauth_client(provider, redirect_uri)
    state_payload: Dict[str, Any] = {
        "provider": provider,
        "workspace_id": workspace_id,
        "surface": str(surface or "sage").strip() or "sage",
        "user_id": str(user_id or "").strip(),
    }
    if extra_state:
        # Fleet agent-connectors: thread agent_install_id through the redirect
        # round-trip so the callback can file the credential at the agent's
        # project scope instead of bare workspace scope. Optional — every other
        # caller of this shared OAuth pipeline is unaffected.
        state_payload.update({k: str(v) for k, v in extra_state.items() if v})
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
    # _resolve_oauth_client (not ensure_oauth_configured) so dynamic-client-
    # registration providers (Higgsfield) exchange with the same client_id
    # that was used for the authorize step in start_oauth — see that
    # function's docstring. Identical to ensure_oauth_configured for every
    # other provider (config.registration_endpoint is None for all of them).
    client_id, client_secret = _resolve_oauth_client(provider, redirect_uri)
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
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "bearer").strip() or "bearer",
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    return credentials


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


def _exchange_notion(code: str, redirect_uri: str, *, code_verifier: str = "") -> Dict[str, Any]:
    config = _provider_config("notion")
    # _resolve_oauth_client (not ensure_oauth_configured) so Notion's
    # dynamic-client-registration path (registration_endpoint on the
    # "notion" config) can complete the token exchange with the same
    # client_id/secret that was used for the authorize step in start_oauth()
    # — see _exchange_standard_oauth's identical reasoning.
    client_id, client_secret = _resolve_oauth_client("notion", redirect_uri)
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body: Dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        body["code_verifier"] = code_verifier
    payload = _post_json(
        _provider_url("notion", config.token_url),
        body,
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


def _exchange_linear(code: str, redirect_uri: str, *, code_verifier: str = "") -> Dict[str, Any]:
    config = _provider_config("linear")
    # _resolve_oauth_client (not ensure_oauth_configured) so Linear's
    # dynamic-client-registration path (registration_endpoint on the
    # "linear" config) can complete the token exchange with the same
    # client_id/secret that was used for the authorize step in start_oauth()
    # — see _exchange_standard_oauth's identical reasoning.
    client_id, client_secret = _resolve_oauth_client("linear", redirect_uri)
    body: Dict[str, Any] = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        body["code_verifier"] = code_verifier
    payload = _post_form_json(_provider_url("linear", config.token_url), body)
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Linear token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    credentials: Dict[str, Any] = {
        "auth_mode": "oauth",
        "access_token": access_token,
        "scope": str(payload.get("scope") or "").strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if expires_in > 0:
        credentials["access_token_expires_at"] = int(time.time()) + expires_in
    return credentials


def _exchange_dropbox(code: str, redirect_uri: str, *, code_verifier: str = "") -> Dict[str, Any]:
    config = _provider_config("dropbox")
    # _resolve_oauth_client (not ensure_oauth_configured) so Dropbox's
    # dynamic-client-registration path (registration_endpoint on the
    # "dropbox" config) can complete the token exchange with the same
    # client_id/secret that was used for the authorize step in start_oauth()
    # — identical reasoning to _exchange_notion/_exchange_linear.
    client_id, client_secret = _resolve_oauth_client("dropbox", redirect_uri)
    body: Dict[str, Any] = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        body["code_verifier"] = code_verifier
    payload = _post_form_json(_provider_url("dropbox", config.token_url), body)
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


# Each provider can map to one or more MCP server registrations.
# Each entry has: server_id (unique per workspace), label, endpoint (URL or None).
# When endpoint is None, that server is skipped — credential stored but no tools available.
APP_MCP_SERVER_MAP: Dict[str, List[Dict[str, Optional[str]]]] = {
    "google_workspace": [
        {
            "server_id": "google-gmail",
            "label": "Google Gmail (MCP)",
            "endpoint": "https://gmailmcp.googleapis.com/mcp/v1",
        },
        {
            "server_id": "google-calendar",
            "label": "Google Calendar (MCP)",
            "endpoint": "https://calendarmcp.googleapis.com/mcp/v1",
        },
        {
            "server_id": "google-drive",
            "label": "Google Drive (MCP)",
            "endpoint": "https://drivemcp.googleapis.com/mcp/v1",
        },
    ],
    # GitHub: official remote MCP server. Requires GitHub Copilot or Copilot Enterprise seat.
    # Auth: OAuth. Source: github.blog/ai-and-ml/generative-ai/a-practical-guide-on-how-to-use-the-github-mcp-server
    "github": [
        {"server_id": "github", "label": "GitHub (MCP)", "endpoint": "https://api.githubcopilot.com/mcp/"},
    ],
    # Slack: official remote MCP server. GA Feb 2026. Auth: OAuth 2.0. Streamable HTTP only.
    # Source: github.com/slackapi/slack-mcp-plugin
    "slack": [
        {"server_id": "slack", "label": "Slack (MCP)", "endpoint": "https://mcp.slack.com/mcp"},
    ],
    # Notion: official remote MCP server. GA since 2025. Auth: OAuth 2.0 + PKCE.
    # Source: developers.notion.com/guides/mcp
    "notion": [
        {"server_id": "notion", "label": "Notion (MCP)", "endpoint": "https://mcp.notion.com/mcp"},
    ],
    # Linear: official remote MCP server. May 2025. Auth: OAuth 2.1 OR Bearer token.
    # Source: linear.app/docs/mcp
    "linear": [
        {"server_id": "linear", "label": "Linear (MCP)", "endpoint": "https://mcp.linear.app/mcp"},
    ],
    # Microsoft 365: official Agent 365 MCP servers (Frontier preview). Tenant-specific URLs.
    # No single public endpoint yet — each service has its own URL under agent365.svc.cloud.microsoft.
    # Checked 2026-06-28 — watch for GA announcement with unified endpoint.
    # Source: github.com/bap-microsoft/MCP-Platform
    "microsoft_365": [
        {"server_id": "microsoft-365", "label": "Microsoft 365 (MCP)", "endpoint": None},
    ],
    # Dropbox: two official remote MCP servers. Auth: OAuth 2.0 + DCR.
    # Source: help.dropbox.com/integrations/connect-dropbox-mcp-server
    "dropbox": [
        {"server_id": "dropbox", "label": "Dropbox (MCP)", "endpoint": "https://mcp.dropbox.com/mcp"},
    ],
    # Figma: official remote MCP server. Auth: OAuth (Figma account).
    # Source: developers.figma.com/docs/figma-mcp-server
    "figma": [
        {"server_id": "figma", "label": "Figma (MCP)", "endpoint": "https://mcp.figma.com/mcp"},
    ],
    # Atlassian: official remote MCP server for Jira + Confluence. Auth: OAuth / API tokens.
    # Source: pypi.org/project/mcp-atlassian/ (official remote endpoint)
    "jira": [
        {"server_id": "atlassian", "label": "Atlassian Jira + Confluence (MCP)", "endpoint": "https://mcp.atlassian.com/v1/mcp"},
    ],
    # HubSpot: official remote MCP server. GA April 2026. Auth: OAuth 2.1 + PKCE.
    # Source: developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server
    "hubspot": [
        {"server_id": "hubspot", "label": "HubSpot (MCP)", "endpoint": "https://mcp.hubspot.com"},
    ],
    # Todoist: official remote MCP server by Doist. GA Feb 2025. Auth: OAuth.
    # Source: todoist.com/help/articles/use-chatgpt-with-todoist
    "todoist": [
        {"server_id": "todoist", "label": "Todoist (MCP)", "endpoint": "https://ai.todoist.net/mcp"},
    ],
    # Calendly: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developer.calendly.com/mcp
    "calendly": [
        {"server_id": "calendly", "label": "Calendly (MCP)", "endpoint": "https://mcp.calendly.com"},
    ],
    # ClickUp: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: clickup.com/mcp
    "clickup": [
        {"server_id": "clickup", "label": "ClickUp (MCP)", "endpoint": "https://mcp.clickup.com/mcp"},
    ],
    # Webflow: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developers.webflow.com/mcp
    "webflow": [
        {"server_id": "webflow", "label": "Webflow (MCP)", "endpoint": "https://mcp.webflow.com/mcp"},
    ],
    # Monday.com: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developer.monday.com/mcp
    "monday": [
        {"server_id": "monday", "label": "Monday.com (MCP)", "endpoint": "https://mcp.monday.com/mcp"},
    ],
    # Box: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developer.box.com/mcp
    "box": [
        {"server_id": "box", "label": "Box (MCP)", "endpoint": "https://mcp.box.com"},
    ],
    # Miro: official remote MCP server. Auth: OAuth 2.1 (Enterprise plan). Streamable HTTP.
    # Source: developers.miro.com/docs/miro-mcp
    "miro": [
        {"server_id": "miro", "label": "Miro (MCP)", "endpoint": "https://mcp.miro.com/"},
    ],
    # Intercom: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developers.intercom.com/mcp
    "intercom": [
        {"server_id": "intercom", "label": "Intercom (MCP)", "endpoint": "https://mcp.intercom.com/mcp"},
    ],
    # Typeform: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: typeform.com/developers/mcp
    "typeform": [
        {"server_id": "typeform", "label": "Typeform (MCP)", "endpoint": "https://api.typeform.com/mcp"},
    ],
    # Vercel: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: vercel.com/docs/mcp
    "vercel": [
        {"server_id": "vercel", "label": "Vercel (MCP)", "endpoint": "https://mcp.vercel.com"},
    ],
    # Confluence: Atlassian MCP server (separate auth endpoint from Jira).
    # Endpoint uses /authv2 path vs Jira's /v1/mcp. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developer.atlassian.com/mcp
    "confluence": [
        {"server_id": "confluence", "label": "Confluence (MCP)", "endpoint": "https://mcp.atlassian.com/v1/mcp/authv2"},
    ],
    # DocuSign: official remote MCP server. Auth: OAuth 2.0. Streamable HTTP.
    # Source: developers.docusign.com
    "docusign": [
        {"server_id": "docusign", "label": "DocuSign (MCP)", "endpoint": "https://mcp-d.docusign.com/mcp"},
    ],
    # Square: official remote MCP server. Auth: OAuth 2.0.
    # NOTE: frontend has /sse endpoint but /mcp responds 401 (exists, needs auth) —
    # using streamable_http path. If Square only supports SSE transport, this may
    # need SSE support added to _call_streamable_http_tool_async.
    # Source: developer.squareup.com
    "square": [
        {"server_id": "square", "label": "Square (MCP)", "endpoint": "https://mcp.squareup.com/mcp"},
    ],
    # Stripe: official remote MCP server. GA Feb 2025. Auth: OAuth 2.0 or API key. Streamable HTTP.
    # Source: docs.stripe.com/mcp
    "stripe": [
        {"server_id": "stripe", "label": "Stripe (MCP)", "endpoint": "https://mcp.stripe.com"},
    ],
    # Salesforce: official hosted MCP servers. GA Apr 2026. Auth: OAuth 2.0 + PKCE.
    # Multiple product-specific servers under /platform/; base path for discovery.
    # Source: developer.salesforce.com/blogs/2026/04/salesforce-hosted-mcp-servers-are-now-generally-available
    "salesforce": [
        {"server_id": "salesforce", "label": "Salesforce (MCP)", "endpoint": "https://api.salesforce.com/platform/mcp/v1/platform/"},
    ],
    # Airtable: official remote MCP server. GA since early 2025. Auth: OAuth 2.0 or PAT. Streamable HTTP.
    # Source: airtable.com/mcp
    "airtable": [
        {"server_id": "airtable", "label": "Airtable (MCP)", "endpoint": "https://mcp.airtable.com/mcp"},
    ],
    # Canva: official remote MCP server. Released Jul 2025. Auth: OAuth 2.1. Streamable HTTP.
    # Source: canva.dev/docs/mcp
    "canva": [
        {"server_id": "canva", "label": "Canva (MCP)", "endpoint": "https://mcp.canva.com/mcp"},
    ],
    # Asana: official remote MCP server. V2 GA Feb 2026. Auth: OAuth 2.0 (pre-registered client).
    # Source: developers.asana.com/docs/integrating-with-asanas-mcp-server
    "asana": [
        {"server_id": "asana", "label": "Asana (MCP)", "endpoint": "https://mcp.asana.com/v2/mcp"},
    ],
    # Zoom: official remote MCP server. GA. Auth: OAuth 2.0 + PKCE. Streamable HTTP.
    # Source: developers.zoom.us/docs/mcp/servers
    "zoom": [
        {"server_id": "zoom", "label": "Zoom (MCP)", "endpoint": "https://mcp.zoom.us/mcp/zoom/streamable"},
    ],
    # GitLab: official MCP server built into GitLab. Beta (GitLab 18.6). Auth: OAuth 2.0 + DCR.
    # Premium/Ultimate tier required. Endpoint is instance-specific; gitlab.com shown.
    # Source: docs.gitlab.com/user/gitlab_duo/model_context_protocol/mcp_server
    "gitlab": [
        {"server_id": "gitlab", "label": "GitLab (MCP)", "endpoint": "https://gitlab.com/api/v4/mcp"},
    ],
    # Higgsfield: official remote MCP server aggregating ~30 image/video
    # generation models (Kling, Sora, Veo, Seedream, Seedance, FLUX, Soul,
    # Nano Banana, Cinema Studio, and more) behind one connection. Auth:
    # OAuth 2.1 + PKCE + Dynamic Client Registration (see the "higgsfield"
    # entry in OAUTH_PROVIDER_CONFIGS above for discovery evidence).
    # Streamable HTTP. Source: higgsfield.ai/mcp; confirmed live 2026-07-18
    # via GET https://mcp.higgsfield.ai/.well-known/oauth-protected-resource.
    "higgsfield": [
        {"server_id": "higgsfield", "label": "Higgsfield (MCP)", "endpoint": "https://mcp.higgsfield.ai/mcp"},
    ],
    # Zapier: official hosted MCP server reaching ~8-9k apps through the
    # customer's own Zapier account. Auth: OAuth 2.1 + PKCE + DCR (see the
    # "zapier" entry in OAUTH_PROVIDER_CONFIGS above). Streamable HTTP.
    # Source: docs.zapier.com/mcp; confirmed live 2026-07-19 via GET
    # https://mcp.zapier.com/.well-known/oauth-protected-resource/api/v1/connect.
    "zapier": [
        {"server_id": "zapier", "label": "Zapier (MCP)", "endpoint": "https://mcp.zapier.com/api/v1/connect"},
    ],
    # PayPal: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Streamable HTTP. Source: docs.paypal.ai; confirmed live 2026-07-19 via
    # GET https://mcp.paypal.com/.well-known/oauth-protected-resource.
    "paypal": [
        {"server_id": "paypal", "label": "PayPal (MCP)", "endpoint": "https://mcp.paypal.com"},
    ],
    # Sentry: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Streamable HTTP. Source: docs.sentry.io, github.com/getsentry/sentry-mcp;
    # confirmed live 2026-07-19 via GET
    # https://mcp.sentry.dev/.well-known/oauth-protected-resource/mcp.
    "sentry": [
        {"server_id": "sentry", "label": "Sentry (MCP)", "endpoint": "https://mcp.sentry.dev/mcp"},
    ],
    # Attio: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR (auth
    # server on app.attio.com, a different host from the MCP endpoint).
    # Streamable HTTP. Source: docs.attio.com; confirmed live 2026-07-19 via
    # GET https://mcp.attio.com/.well-known/oauth-protected-resource.
    "attio": [
        {"server_id": "attio", "label": "Attio (MCP)", "endpoint": "https://mcp.attio.com/mcp"},
    ],
    # Cloudflare: official remote "Code Mode" MCP server exposing ~2,500
    # Cloudflare API endpoints through two tools (search()/execute()). Auth:
    # OAuth 2.0 + PKCE + DCR. Streamable HTTP. Source:
    # developers.cloudflare.com; confirmed live 2026-07-19 via GET
    # https://mcp.cloudflare.com/.well-known/oauth-protected-resource/mcp.
    "cloudflare": [
        {"server_id": "cloudflare", "label": "Cloudflare (MCP)", "endpoint": "https://mcp.cloudflare.com/mcp"},
    ],
    # Gusto: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "gusto": [
        {"server_id": "gusto", "label": "Gusto (MCP)", "endpoint": "https://mcp.api.gusto.com"},
    ],
    # Deel: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "deel": [
        {"server_id": "deel", "label": "Deel (MCP)", "endpoint": "https://api.letsdeel.com/mcp"},
    ],
    # Remote: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "remote_com": [
        {"server_id": "remote_com", "label": "Remote (MCP)", "endpoint": "https://mcp.remote.com/mcp"},
    ],
    # Ashby: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "ashby": [
        {"server_id": "ashby", "label": "Ashby (MCP)", "endpoint": "https://mcp.ashbyhq.com/mcp/v1"},
    ],
    # Klaviyo: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "klaviyo": [
        {"server_id": "klaviyo", "label": "Klaviyo (MCP)", "endpoint": "https://mcp.klaviyo.com/mcp"},
    ],
    # Customer.io: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "customer_io": [
        {"server_id": "customer_io", "label": "Customer.io (MCP)", "endpoint": "https://mcp.customer.io/mcp"},
    ],
    # Netlify: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "netlify": [
        {"server_id": "netlify", "label": "Netlify (MCP)", "endpoint": "https://netlify-mcp.netlify.app/mcp"},
    ],
    # Supabase: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "supabase": [
        {"server_id": "supabase", "label": "Supabase (MCP)", "endpoint": "https://mcp.supabase.com/mcp"},
    ],
    # PlanetScale: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "planetscale": [
        {"server_id": "planetscale", "label": "PlanetScale (MCP)", "endpoint": "https://mcp.pscale.dev/mcp/planetscale"},
    ],
    # Neon: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "neon": [
        {"server_id": "neon", "label": "Neon (MCP)", "endpoint": "https://mcp.neon.tech/mcp"},
    ],
    # Railway: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "railway": [
        {"server_id": "railway", "label": "Railway (MCP)", "endpoint": "https://mcp.railway.com"},
    ],
    # Heroku: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "heroku": [
        {"server_id": "heroku", "label": "Heroku (MCP)", "endpoint": "https://mcp.heroku.com/mcp"},
    ],
    # Sourcegraph: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "sourcegraph": [
        {"server_id": "sourcegraph", "label": "Sourcegraph (MCP)", "endpoint": "https://sourcegraph.com/.api/mcp"},
    ],
    # Replit: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "replit": [
        {"server_id": "replit", "label": "Replit (MCP)", "endpoint": "https://replit-mcp.com/server/mcp"},
    ],
    # Postman: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "postman": [
        {"server_id": "postman", "label": "Postman (MCP)", "endpoint": "https://mcp.postman.com/mcp"},
    ],
    # Buildkite: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "buildkite": [
        {"server_id": "buildkite", "label": "Buildkite (MCP)", "endpoint": "https://mcp.buildkite.com/mcp"},
    ],
    # Socket: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "socket": [
        {"server_id": "socket", "label": "Socket (MCP)", "endpoint": "https://mcp.socket.dev/"},
    ],
    # Whimsical: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "whimsical": [
        {"server_id": "whimsical", "label": "Whimsical (MCP)", "endpoint": "https://mcp.whimsical.com/mcp"},
    ],
    # Ramp: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "ramp": [
        {"server_id": "ramp", "label": "Ramp (MCP)", "endpoint": "https://mcp.ramp.com/mcp"},
    ],
    # Brex: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "brex": [
        {"server_id": "brex", "label": "Brex (MCP)", "endpoint": "https://api.brex.com/mcp"},
    ],
    # Mercury: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "mercury": [
        {"server_id": "mercury", "label": "Mercury (MCP)", "endpoint": "https://mcp.mercury.com/mcp"},
    ],
    # Robinhood: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "robinhood": [
        {"server_id": "robinhood", "label": "Robinhood (MCP)", "endpoint": "https://agent.robinhood.com/mcp/trading"},
    ],
    # Amplitude: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "amplitude": [
        {"server_id": "amplitude", "label": "Amplitude (MCP)", "endpoint": "https://mcp.amplitude.com/mcp"},
    ],
    # Mixpanel: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "mixpanel": [
        {"server_id": "mixpanel", "label": "Mixpanel (MCP)", "endpoint": "https://mcp.mixpanel.com/mcp"},
    ],
    # PostHog: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "posthog": [
        {"server_id": "posthog", "label": "PostHog (MCP)", "endpoint": "https://mcp.posthog.com/mcp"},
    ],
    # Meta Ads: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "meta_ads": [
        {"server_id": "meta_ads", "label": "Meta Ads (MCP)", "endpoint": "https://mcp.facebook.com/ads"},
    ],
    # Semrush: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "semrush": [
        {"server_id": "semrush", "label": "Semrush (MCP)", "endpoint": "https://mcp.semrush.com/v1/mcp"},
    ],
    # Ahrefs: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "ahrefs": [
        {"server_id": "ahrefs", "label": "Ahrefs (MCP)", "endpoint": "https://api.ahrefs.com/mcp/mcp"},
    ],
    # Close: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "close_crm": [
        {"server_id": "close_crm", "label": "Close (MCP)", "endpoint": "https://mcp.close.com/mcp"},
    ],
    # Apollo.io: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "apollo_io": [
        {"server_id": "apollo_io", "label": "Apollo.io (MCP)", "endpoint": "https://mcp.apollo.io/mcp"},
    ],
    # Outreach: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "outreach": [
        {"server_id": "outreach", "label": "Outreach (MCP)", "endpoint": "https://api.outreach.io/mcp/"},
    ],
    # Salesloft: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "salesloft": [
        {"server_id": "salesloft", "label": "Salesloft (MCP)", "endpoint": "https://mcp.salesloft.com/mcp"},
    ],
    # Clay: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "clay": [
        {"server_id": "clay", "label": "Clay (MCP)", "endpoint": "https://api.clay.com/v3/mcp"},
    ],
    # Fireflies.ai: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR.
    # Confirmed live 2026-07-19.
    "fireflies": [
        {"server_id": "fireflies", "label": "Fireflies.ai (MCP)", "endpoint": "https://api.fireflies.ai/mcp"},
    ],
    # Fathom: official remote MCP server. Auth: OAuth 2.0 + PKCE + DCR. Confirmed
    # live 2026-07-19.
    "fathom": [
        {"server_id": "fathom", "label": "Fathom (MCP)", "endpoint": "https://api.fathom.ai/mcp"},
    ],
    # Superhuman Docs (formerly Coda): official remote MCP server. Auth: OAuth 2.0
    # + PKCE + DCR. Confirmed live 2026-07-19.
    "coda": [
        {"server_id": "coda", "label": "Superhuman Docs (formerly Coda) (MCP)", "endpoint": "https://docs.superhuman.com/apis/mcp"},
    ],
}


async def connect_app_via_oauth_to_mcp(
    *,
    workspace_id: str,
    provider: str,
    oauth_code: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Bridge function: OAuth code → credential vault → MCP server registration.

    1. Exchanges the OAuth code for tokens using the existing OAuth service.
    2. Stores the access_token + refresh_token in the vault.
    3. Looks up the MCP server URL for the provider.
    4. Registers the MCP server with credential injection.
    5. Returns the list of discovered tools (empty if no MCP server URL yet).
    """
    import uuid
    import time as _time
    from server_modules import connectors_actions
    from server_modules.schemas import ConnectorCreate

    normalized_provider = str(provider or "").strip().lower()
    normalized_code = str(oauth_code or "").strip()
    normalized_redirect_uri = str(redirect_uri or "").strip()

    if not normalized_provider or not normalized_code or not normalized_redirect_uri:
        raise HTTPException(status_code=400, detail="provider, oauth_code, and redirect_uri are required.")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")

    # Step 1: Exchange OAuth code for tokens (reuse existing provider-specific logic)
    # NOTE: this bridge takes a bare (oauth_code, redirect_uri) with no PKCE
    # code_verifier param and no `state` round-trip to recover one from — so
    # it cannot complete a token exchange for any auth_method="pkce"
    # provider (Airtable, Canva, Asana, Higgsfield, and — as of this pass —
    # Stripe, Linear, Notion, ClickUp all use PKCE now). That gap predates
    # this change and isn't introduced by it; the primary redirect-based
    # flow (complete_oauth_callback, driven by start_oauth()'s own `state`)
    # is the one that's fully PKCE-wired.
    provider_config = _provider_config(normalized_provider)

    try:
        if normalized_provider == "google_workspace":
            credentials = _exchange_google(normalized_code, normalized_redirect_uri)
        elif normalized_provider == "github":
            credentials = _exchange_github(normalized_code, normalized_redirect_uri)
        elif normalized_provider == "microsoft_365":
            credentials = _exchange_microsoft(normalized_code, normalized_redirect_uri)
        elif normalized_provider == "slack":
            credentials = _exchange_slack(normalized_code, normalized_redirect_uri)
        elif normalized_provider == "notion":
            credentials = _exchange_notion(normalized_code, normalized_redirect_uri)
        elif normalized_provider == "linear":
            credentials = _exchange_linear(normalized_code, normalized_redirect_uri)
        elif provider_config.token_parser == "standard":
            credentials = _exchange_standard_oauth(normalized_provider, normalized_code, normalized_redirect_uri)
        else:
            raise HTTPException(status_code=409, detail=f"{_connector_label(normalized_provider)} OAuth token exchange is not supported for MCP bridge.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OAuth token exchange failed: {exc}") from exc

    # Step 2: Store credential in vault
    vault_connector = "discord_bot" if normalized_provider == "discord" else normalized_provider
    try:
        connector_result = await connectors_actions.create_connector_vault(
            ConnectorCreate(
                label=f"{_connector_label(normalized_provider)} (MCP)",
                connector=vault_connector,
                workspace_id=workspace_id,
                credentials=credentials,
                metadata={
                    "source": "oauth_mcp_bridge",
                    "oauth_provider": normalized_provider,
                    "surface": "sage",
                },
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to store credential in vault: {exc}") from exc

    credential_id = str(connector_result.get("id") or "").strip()
    if not credential_id:
        raise HTTPException(status_code=500, detail="Credential was stored but no credential_id was returned.")

    # Step 3: Look up MCP server entries for this provider
    server_entries = APP_MCP_SERVER_MAP.get(normalized_provider)
    if not server_entries:
        _log.warning(
            "No MCP server configured for provider %s — credential stored but agent tools not available.",
            normalized_provider,
        )
        return {
            "ok": True,
            "provider": normalized_provider,
            "workspace_id": workspace_id,
            "credential_id": credential_id,
            "mcp_servers_registered": 0,
            "servers": [],
            "warning": f"No MCP server configured for provider {normalized_provider} — credential stored but agent tools not available.",
        }

    # Step 4: Register each MCP server with credential injection
    from server_modules import mcp_registry_service

    registered_servers: List[Dict[str, Any]] = []
    all_tools: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for entry in server_entries:
        server_id = str(entry.get("server_id") or "").strip()
        server_label = str(entry.get("label") or server_id).strip()
        server_endpoint = entry.get("endpoint")

        if not server_id:
            warnings.append(f"Skipping MCP server entry with empty server_id for {normalized_provider}")
            continue

        if server_endpoint is None:
            _log.warning(
                "No MCP server URL configured for %s/%s — credential stored but agent tools not available.",
                normalized_provider, server_id,
            )
            registered_servers.append({
                "server_id": server_id,
                "mcp_server_registered": False,
                "tools": [],
                "warning": f"No MCP server URL configured for {normalized_provider}/{server_id}.",
            })
            continue

        try:
            server = await mcp_registry_service.upsert_workspace_mcp_server_async(
                workspace_id=workspace_id,
                server_id=server_id,
                label=server_label,
                transport="streamable_http",
                endpoint=str(server_endpoint),
                enabled=True,
                credential_id=credential_id,
                discover_tools=True,
            )
            tools = server.get("tools") if isinstance(server.get("tools"), list) else []
            all_tools.extend(tools)
            registered_servers.append({
                "server_id": server.get("id"),
                "label": server_label,
                "endpoint": str(server_endpoint),
                "mcp_server_registered": True,
                "tool_count": len(tools),
                "tools": tools,
            })
        except Exception as exc:
            _log.warning("MCP server registration failed for %s/%s: %s", normalized_provider, server_id, exc)
            registered_servers.append({
                "server_id": server_id,
                "mcp_server_registered": False,
                "tools": [],
                "error": str(exc),
            })

    return {
        "ok": True,
        "provider": normalized_provider,
        "workspace_id": workspace_id,
        "credential_id": credential_id,
        "mcp_servers_registered": sum(1 for s in registered_servers if s.get("mcp_server_registered")),
        "servers": registered_servers,
        "tools": all_tools,
        "warning": "; ".join(warnings) if warnings else None,
    }


def _perform_oauth_refresh(credential_id: str, credential: Dict[str, Any]) -> Dict[str, Any]:
    """Shared refresh core (the ONE token-refresh implementation) used by
    both:
      - refresh_oauth_token_if_needed() below — expiry-gated, best-effort,
        silent, called opportunistically on every vault credential read
        (vault_helpers.resolve_vault_credential).
      - refresh_oauth_token_now() below — unconditional, used by
        mcp_registry_service's MCP Phase D auth-failure retry path
        (docs/design/mcp-applications-plan.md) when a live 401/invalid_token
        means the token is bad *right now*, regardless of what the expiry
        heuristic thinks.

    Never raises — every failure mode is reported via the returned dict so
    callers can distinguish "nothing to do" from "this needs the workspace
    owner to reconnect":
      {"ok": True,  "credential": <refreshed credential dict>}
      {"ok": False, "reason": "<short machine-readable code>", "detail": "<str>"}
    """
    normalized_id = str(credential_id or "").strip()
    refresh_token = str(credential.get("refresh_token") or "").strip()
    if not refresh_token:
        return {
            "ok": False,
            "reason": "no_refresh_token",
            "detail": "Credential has no refresh_token on file; it cannot be auto-refreshed.",
        }

    provider = str(credential.get("provider") or credential.get("oauth_provider") or "").strip().lower()
    if not provider:
        _log.warning("oauth refresh: credential %s has no provider info", normalized_id)
        return {"ok": False, "reason": "unknown_provider", "detail": "Credential has no provider info."}

    config = OAUTH_PROVIDER_CONFIGS.get(provider)
    if config is None:
        _log.warning("oauth refresh: no OAuth config for provider %s", provider)
        return {"ok": False, "reason": "unconfigured_provider", "detail": f"No OAuth config for provider '{provider}'."}

    # _resolve_oauth_client_for_refresh (not ensure_oauth_configured) so a
    # credential obtained through Higgsfield's dynamic-client-registration
    # path can still be refreshed — see that function's docstring.
    # Identical to ensure_oauth_configured for every other provider.
    try:
        client_id, client_secret = _resolve_oauth_client_for_refresh(provider)
    except Exception as exc:
        _log.warning("oauth refresh: provider %s OAuth not configured: %s", provider, exc)
        return {"ok": False, "reason": "oauth_not_configured", "detail": f"{provider} OAuth client is not configured: {exc}"}

    try:
        token_url = _provider_url(provider, config.token_url)
        body: Dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        headers: Dict[str, str] = {}
        if config.token_auth == "basic":
            headers["Authorization"] = _oauth_basic_header(client_id, client_secret)
            body.pop("client_id", None)
            body.pop("client_secret", None)

        if config.token_request_format == "json":
            token_response = _post_json(token_url, body, headers=headers)
        else:
            token_response = _post_form_json(token_url, body, headers=headers)

        new_access_token = str(token_response.get("access_token") or "").strip()
        if not new_access_token:
            _log.warning("oauth refresh: no access_token in refresh response for %s", provider)
            return {
                "ok": False,
                "reason": "no_access_token_in_response",
                "detail": f"Provider '{provider}' refresh response had no access_token.",
            }

        refreshed = dict(credential)
        refreshed["access_token"] = new_access_token
        now = int(time.time())
        new_expires_in = int(token_response.get("expires_in") or 0)
        if new_expires_in > 0:
            refreshed["access_token_expires_at"] = now + new_expires_in
        new_refresh_token = str(token_response.get("refresh_token") or "").strip()
        if new_refresh_token:
            refreshed["refresh_token"] = new_refresh_token

        # Persist updated credential back to vault (Phase 3C: single-row
        # UPDATE of just this credential's ciphertext — no whole-file rewrite).
        from server_modules.vault_store import _openssl_encrypt, update_credential_secret

        plain = json.dumps(refreshed, separators=(",", ":"))
        update_credential_secret(normalized_id, encrypted_secret=_openssl_encrypt(plain))
        _log.info("oauth refresh: refreshed token for credential %s (provider %s)", normalized_id, provider)
        return {"ok": True, "credential": refreshed}
    except Exception as exc:
        _log.warning("oauth refresh: refresh failed for credential %s (provider %s): %s", normalized_id, provider, exc)
        return {"ok": False, "reason": "refresh_request_failed", "detail": str(exc)}


def refresh_oauth_token_if_needed(credential_id: str) -> Dict[str, Any]:
    """Check and refresh an OAuth credential if it is expired or about to expire.

    Reads the credential from vault by id, checks if ``access_token_expires_at``
    is within 5 minutes of the current time, and if a ``refresh_token`` is
    available, calls the provider's token refresh endpoint to obtain a new
    access token.  Updates the stored credential with the new values.

    If the credential has no ``expires_at`` / ``access_token_expires_at`` or no
    ``refresh_token``, it is returned as-is.

    Returns the (possibly refreshed) credential dict. Failures are logged and
    swallowed (best-effort, silent) — see refresh_oauth_token_now() for the
    forced variant that reports success/failure explicitly.
    """
    import time as _time
    from server_modules.vault_store import _openssl_decrypt, load_vault
    from server_modules.vault_helpers import resolve_vault_credential

    normalized_id = str(credential_id or "").strip()
    if not normalized_id:
        return {}

    # Step 1: Read credential from vault
    try:
        credential = resolve_vault_credential(load_vault, _openssl_decrypt, normalized_id)
    except Exception:
        _log.warning("refresh_oauth_token_if_needed: cannot resolve credential %s", normalized_id)
        return {}

    if not isinstance(credential, dict) or not credential:
        return {}

    # Step 2: Check expiration
    expires_at = credential.get("access_token_expires_at") or credential.get("expires_at") or 0
    expires_at = int(expires_at or 0)
    if expires_at <= 0:
        return credential

    now = int(_time.time())
    five_minutes = 300
    if expires_at > now + five_minutes:
        return credential

    outcome = _perform_oauth_refresh(normalized_id, credential)
    if outcome.get("ok"):
        return outcome["credential"]
    return credential


def refresh_oauth_token_now(credential_id: str) -> Dict[str, Any]:
    """Force an immediate OAuth token refresh attempt, bypassing the
    expiry-window gate in refresh_oauth_token_if_needed() above.

    Used by mcp_registry_service's auth-failure retry path (MCP Phase D,
    docs/design/mcp-applications-plan.md): when an MCP tool call fails with
    a 401/invalid_token shape, the server just told us the credential is bad
    *right now* — there is no reason to wait for the 5-minute expiry
    heuristic to agree, and every second waited is a second the agent is
    stuck. Reuses the exact same refresh core (_perform_oauth_refresh) as
    the opportunistic path above; this is not a second implementation.

    Never raises — every failure mode (no credential, no refresh_token,
    unconfigured provider, provider rejected the refresh_token, ...) is
    reported via the returned dict so callers can build an honest message
    for both the agent and the workspace owner:
      {"ok": True,  "credential": <refreshed credential dict>}
      {"ok": False, "reason": "<short machine-readable code>", "detail": "<str>"}
    """
    from server_modules.vault_store import _openssl_decrypt, load_vault
    from server_modules.vault_helpers import resolve_vault_credential

    normalized_id = str(credential_id or "").strip()
    if not normalized_id:
        return {"ok": False, "reason": "no_credential_id", "detail": "No credential id was provided."}

    try:
        credential = resolve_vault_credential(load_vault, _openssl_decrypt, normalized_id)
    except Exception as exc:
        return {"ok": False, "reason": "credential_not_found", "detail": str(exc)}

    if not isinstance(credential, dict) or not credential:
        return {"ok": False, "reason": "credential_not_found", "detail": "Credential payload is empty."}

    return _perform_oauth_refresh(normalized_id, credential)


# §1.5 (Multiplayer Projects plan): account_label defaults to "default"
# (connectors_actions.store_agent_connector_credential) and this OAuth
# completion handler never set it to anything else, so every OAuth-connected
# account rendered as "default" — the one signal that would let a human
# notice a §1.2/§1.3 cross-account mixup before damage was blank. Fix reuses
# the SAME profile_probe endpoint "Test connection" already calls
# (OAuthProviderConfig.profile_probe / connector_validators.
# validate_oauth_bearer_connector) via the same runtime_common.
# http_json_request every other provider-probe call in this codebase uses —
# no second HTTP client, no second validation implementation.
_ACCOUNT_IDENTITY_PROFILE_FIELDS: tuple[str, ...] = (
    "email", "emailAddress", "mail", "userPrincipalName", "user_email",
    "login", "username", "name",
)


def _probe_oauth_account_identity(normalized_provider: str, credentials: Dict[str, Any]) -> str:
    """Best-effort: hit the provider's profile_probe with the freshly
    exchanged access token and pull out a human-recognizable account
    identity (an email address where the provider's userinfo shape offers
    one, else a login/username) to store as account_label.

    This is a visibility improvement, not a security gate — on ANY failure
    (no profile_probe on file, no access token, network error, non-2xx
    status, an MCP-DCR-scoped token the classic REST endpoint rejects, an
    unrecognized profile shape) this returns "" and the caller falls back to
    "default", exactly today's behavior. Never raises.
    """
    provider_config = OAUTH_PROVIDER_CONFIGS.get(str(normalized_provider or "").strip().lower())
    profile_probe = provider_config.profile_probe if provider_config else None
    if not profile_probe:
        return ""
    access_token = str(
        credentials.get("access_token")
        or credentials.get("oauth_access_token")
        or credentials.get("token")
        or ""
    ).strip()
    if not access_token:
        return ""
    try:
        from server_modules.runtime_common import http_json_request
        response = http_json_request(profile_probe, headers={"Authorization": f"Bearer {access_token}"})
    except Exception:
        return ""
    status = int((response or {}).get("status") or 0)
    if status < 200 or status >= 300:
        return ""
    profile = (response or {}).get("json")
    if not isinstance(profile, dict):
        return ""
    for field in _ACCOUNT_IDENTITY_PROFILE_FIELDS:
        value = str(profile.get(field) or "").strip()
        if value:
            return value[:160]
    return ""


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
            credentials = _exchange_notion(normalized_code, redirect_uri, code_verifier=code_verifier)
        elif normalized_provider == "linear":
            credentials = _exchange_linear(normalized_code, redirect_uri, code_verifier=code_verifier)
        elif normalized_provider == "dropbox":
            credentials = _exchange_dropbox(normalized_code, redirect_uri, code_verifier=code_verifier)
        elif normalized_provider == "discord":
            credentials = _exchange_discord(normalized_code, redirect_uri)
        elif provider_config.token_parser == "standard":
            credentials = _exchange_standard_oauth(normalized_provider, normalized_code, redirect_uri, code_verifier=code_verifier)
        else:
            raise HTTPException(status_code=409, detail="This connection does not have a one-click OAuth setup yet.")
        # Map OAuth provider → vault connector name (Discord OAuth == discord_bot connector).
        vault_connector = "discord_bot" if normalized_provider == "discord" else normalized_provider

        # Fleet agent-connectors: when the OAuth start carried an agent_install_id
        # (state_payload, threaded through the redirect round-trip), file the
        # credential at that agent's project scope and subscribe the agent —
        # same two-part truth (project credential + enabled binding) as the
        # manual-fields connect path. Otherwise unchanged: a bare workspace
        # credential, as every other caller of this shared pipeline expects.
        agent_install_id = str(payload.get("agent_install_id") or "").strip()
        if agent_install_id:
            from server_modules import control_plane_repository
            tenant_id = await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id, default="default")
            # §1.5: probe the real account identity so this connection doesn't
            # render as an anonymous "default" — see _probe_oauth_account_identity.
            account_label = _probe_oauth_account_identity(normalized_provider, credentials) or "default"
            result = await connectors_actions.store_agent_connector_credential(
                workspace_id=workspace_id,
                agent_install_id=agent_install_id,
                tenant_id=tenant_id,
                provider=vault_connector,
                label=_connector_label(normalized_provider),
                credentials=credentials,
                account_label=account_label,
                metadata={
                    "source": "connection_oauth",
                    "oauth_provider": normalized_provider,
                    "surface": str(payload.get("surface") or "sage").strip() or "sage",
                },
            )
        else:
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
        # ── Phase U: Auto-register MCP servers after OAuth credential is stored ──
        credential_id = str(result.get("id") or "").strip()
        mcp_result = await _register_mcp_servers_for_provider(
            workspace_id=workspace_id,
            normalized_provider=normalized_provider,
            credential_id=credential_id,
        )

        return {
            "ok": True,
            "provider": normalized_provider,
            "workspace_id": workspace_id,
            "connector": result,
            "mcp": mcp_result,
            "agent_install_id": agent_install_id or None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _register_mcp_servers_for_provider(
    *,
    workspace_id: str,
    normalized_provider: str,
    credential_id: str,
) -> Dict[str, Any]:
    """Register MCP servers from APP_MCP_SERVER_MAP for a provider.

    Called after OAuth credential storage.  Failures are logged, never
    raised — MCP registration is best-effort and must not block the
    OAuth flow.

    §1.3 containment (Multiplayer Projects plan): mcp_registry_service now
    refuses (McpServerCredentialCollisionError) rather than silently
    overwriting a server row's credential when a DIFFERENT agent's account
    would take over one another agent already owns. That refusal must NOT
    be swallowed into the same generic warning log as an ordinary failure —
    the whole point is that a human can see it — so it is caught separately
    and surfaced under "collisions" in the returned payload (which flows
    into complete_oauth_callback's response as `mcp.collisions`). The OAuth
    connect itself still succeeds: the credential + binding belong to the
    connecting agent and are unaffected; only the MCP tool registration for
    that specific server_id is withheld.
    """
    from server_modules import mcp_registry_service

    server_entries = APP_MCP_SERVER_MAP.get(normalized_provider)
    if not server_entries:
        return {"registered": 0, "servers": [], "collisions": []}

    registered: list[Dict[str, Any]] = []
    collisions: list[Dict[str, Any]] = []
    for entry in server_entries:
        server_id = str(entry.get("server_id") or "").strip()
        endpoint = entry.get("endpoint")
        if not server_id or endpoint is None:
            continue
        try:
            server = await mcp_registry_service.upsert_workspace_mcp_server_async(
                workspace_id=workspace_id,
                server_id=server_id,
                label=str(entry.get("label") or server_id).strip(),
                transport="streamable_http",
                endpoint=str(endpoint),
                enabled=True,
                credential_id=credential_id,
                discover_tools=True,
            )
            registered.append({
                "server_id": server.get("id"),
                "tool_count": len(server.get("tools") or []),
            })
        except mcp_registry_service.McpServerCredentialCollisionError as exc:
            _log.warning(
                "MCP auto-register REFUSED for %s/%s (cross-account collision): %s",
                normalized_provider, server_id, exc,
            )
            collisions.append({"server_id": server_id, "detail": str(exc)})
        except Exception as exc:
            _log.warning(
                "MCP auto-register failed for %s/%s: %s",
                normalized_provider, server_id, exc,
            )

    return {"registered": len(registered), "servers": registered, "collisions": collisions}
