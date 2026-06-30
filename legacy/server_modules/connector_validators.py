from __future__ import annotations

import base64
from typing import Any, Callable, Dict, List
from urllib.parse import quote_plus
from server_modules.connectors.github_connector import validate_github_credentials
from server_modules.connectors.linear_connector import validate_linear_credentials
from server_modules.connectors.notion_connector import validate_notion_credentials
from server_modules.connectors.dropbox_connector import validate_dropbox_credentials
from server_modules.connectors.discord_connector import validate_discord_bot_credentials
from server_modules.connectors.s3_connector import validate_s3_credentials
from server_modules.connectors.slack_connector import validate_slack_oauth_credentials
from server_modules.connectors.smtp_connector import (
    validate_imap_credentials,
    validate_smtp_credentials,
)
from server_modules.google_workspace_cli import (
    google_workspace_uses_local_cli,
    validate_google_workspace_local_connector,
)
from server_modules.microsoft_365_graph import (
    microsoft_365_get_profile,
    microsoft_365_probe_capabilities,
)


HttpJsonRequest = Callable[..., Dict[str, Any]]


def validate_github_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_github_credentials(credentials, http_json_request=http_json_request)


def validate_notion_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_notion_credentials(credentials, http_json_request=http_json_request)


def validate_linear_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_linear_credentials(credentials, http_json_request=http_json_request)


def validate_dropbox_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_dropbox_credentials(credentials)


def validate_oauth_bearer_connector(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    *,
    provider: str,
    label: str,
    profile_probe: str,
) -> Dict[str, Any]:
    access_token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
    if not access_token:
        raise RuntimeError(f"{label} access_token is required.")
    response = http_json_request(profile_probe, headers={"Authorization": f"Bearer {access_token}"})
    status = int(response.get("status") or 0)
    if status < 200 or status >= 300:
        raise RuntimeError(f"{label} token is invalid.")
    profile = response.get("json") if isinstance(response.get("json"), dict) else {}
    return {
        "ok": True,
        "status": status,
        "message": f"{label} connector is valid.",
        "profile": profile,
        "provider": provider,
        "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        "credentials": {
            **credentials,
            "access_token": access_token,
            "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        },
    }


def validate_figma_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="figma",
        label="Figma",
        profile_probe="https://api.figma.com/v1/me",
    )


def validate_todoist_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="todoist",
        label="Todoist",
        profile_probe="https://api.todoist.com/api/v1/projects?limit=1",
    )


def validate_airtable_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="airtable",
        label="Airtable",
        profile_probe="https://api.airtable.com/v0/meta/whoami",
    )


def validate_canva_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="canva",
        label="Canva",
        profile_probe="https://api.canva.com/rest/v1/users/me/profile",
    )


def validate_asana_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="asana",
        label="Asana",
        profile_probe="https://app.asana.com/api/1.0/users/me",
    )


def validate_hubspot_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="hubspot",
        label="HubSpot",
        profile_probe="https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
    )


def validate_zoom_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="zoom",
        label="Zoom",
        profile_probe="https://api.zoom.us/v2/users/me",
    )


def validate_calendly_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="calendly",
        label="Calendly",
        profile_probe="https://api.calendly.com/users/me",
    )


def validate_clickup_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="clickup",
        label="ClickUp",
        profile_probe="https://api.clickup.com/api/v2/user",
    )


def validate_jira_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="jira",
        label="Jira",
        profile_probe="https://api.atlassian.com/me",
    )


def validate_stripe_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="stripe",
        label="Stripe",
        profile_probe="https://api.stripe.com/v1/account",
    )


def validate_salesforce_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="salesforce",
        label="Salesforce",
        profile_probe="https://login.salesforce.com/services/oauth2/userinfo",
    )


def validate_webflow_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="webflow",
        label="Webflow",
        profile_probe="https://api.webflow.com/v2/token/authorized_by",
    )


def validate_monday_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    access_token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
    if not access_token:
        raise RuntimeError("monday.com access_token is required.")
    response = http_json_request(
        "https://api.monday.com/v2",
        headers={"Authorization": access_token, "Content-Type": "application/json"},
        payload={"query": "query { me { id name email } }"},
        method="POST",
    )
    status = int(response.get("status") or 0)
    if status < 200 or status >= 300:
        raise RuntimeError("monday.com token is invalid.")
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    profile = body.get("data", {}).get("me") if isinstance(body.get("data"), dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    return {
        "ok": True,
        "status": status,
        "message": "monday.com connector is valid.",
        "profile": profile,
        "provider": "monday",
        "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        "credentials": {
            **credentials,
            "access_token": access_token,
            "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        },
    }


def validate_box_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="box",
        label="Box",
        profile_probe="https://api.box.com/2.0/users/me",
    )


def validate_gitlab_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="gitlab",
        label="GitLab",
        profile_probe="https://gitlab.com/api/v4/user",
    )


def validate_bitbucket_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="bitbucket",
        label="Bitbucket",
        profile_probe="https://api.bitbucket.org/2.0/user",
    )


def validate_confluence_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="confluence",
        label="Confluence",
        profile_probe="https://api.atlassian.com/me",
    )


def validate_miro_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="miro",
        label="Miro",
        profile_probe="https://api.miro.com/v2/users/me",
    )


def validate_mailchimp_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    access_token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
    if not access_token:
        raise RuntimeError("Mailchimp access_token is required.")
    response = http_json_request("https://login.mailchimp.com/oauth2/metadata", headers={"Authorization": f"OAuth {access_token}"})
    status = int(response.get("status") or 0)
    if status < 200 or status >= 300:
        raise RuntimeError("Mailchimp token is invalid.")
    profile = response.get("json") if isinstance(response.get("json"), dict) else {}
    return {
        "ok": True,
        "status": status,
        "message": "Mailchimp connector is valid.",
        "profile": profile,
        "provider": "mailchimp",
        "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        "credentials": {
            **credentials,
            "access_token": access_token,
            "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        },
    }


def validate_pipedrive_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="pipedrive",
        label="Pipedrive",
        profile_probe="https://api.pipedrive.com/v1/users/me",
    )


def validate_intercom_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="intercom",
        label="Intercom",
        profile_probe="https://api.intercom.io/me",
    )


def validate_docusign_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="docusign",
        label="Docusign",
        profile_probe="https://account.docusign.com/oauth/userinfo",
    )


def validate_square_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    access_token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
    if not access_token:
        raise RuntimeError("Square access_token is required.")
    response = http_json_request(
        "https://connect.squareup.com/oauth2/token/status",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        payload={},
        method="POST",
    )
    status = int(response.get("status") or 0)
    if status < 200 or status >= 300:
        raise RuntimeError("Square token is invalid.")
    profile = response.get("json") if isinstance(response.get("json"), dict) else {}
    return {
        "ok": True,
        "status": status,
        "message": "Square connector is valid.",
        "profile": profile,
        "provider": "square",
        "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        "credentials": {
            **credentials,
            "access_token": access_token,
            "auth_mode": str(credentials.get("auth_mode") or "oauth").strip().lower() or "oauth",
        },
    }


def validate_typeform_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="typeform",
        label="Typeform",
        profile_probe="https://api.typeform.com/me",
    )


def validate_quickbooks_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="quickbooks",
        label="QuickBooks",
        profile_probe="https://accounts.platform.intuit.com/v1/openid_connect/userinfo",
    )


def validate_xero_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="xero",
        label="Xero",
        profile_probe="https://api.xero.com/connections",
    )


def validate_freshbooks_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="freshbooks",
        label="FreshBooks",
        profile_probe="https://api.freshbooks.com/auth/api/v1/users/me",
    )


def validate_vercel_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return validate_oauth_bearer_connector(
        credentials,
        http_json_request,
        provider="vercel",
        label="Vercel",
        profile_probe="https://api.vercel.com/login/oauth/userinfo",
    )


def validate_s3_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_s3_credentials(credentials)


def validate_smtp_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    smtp_result = validate_smtp_credentials(credentials)
    imap_access = False
    imap_warning: str | None = None
    try:
        validate_imap_credentials(credentials)
        imap_access = True
    except Exception as exc:
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        imap_warning = (
            "SMTP send is ready, but inbound IMAP fetch is not reachable with the current settings."
            + (f" ({detail})" if detail else "")
        )

    warnings = [warning for warning in (imap_warning,) if warning]
    profile = smtp_result.get("profile") if isinstance(smtp_result.get("profile"), dict) else {}
    return {
        "ok": True,
        "status": 200,
        "message": (
            "SMTP connector is valid."
            if imap_access
            else "SMTP connector is valid, but IMAP fetch is not enabled yet."
        ),
        "profile": profile,
        "smtp_access": True,
        "imap_access": imap_access,
        "warning": " ".join(warnings) if warnings else None,
        "warnings": warnings,
    }


def validate_discord_bot_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest | None = None) -> Dict[str, Any]:
    return validate_discord_bot_credentials(credentials, http_json_request=http_json_request)


def validate_google_workspace_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    if google_workspace_uses_local_cli(credentials):
        return validate_google_workspace_local_connector(credentials)

    access_token = str(credentials.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Google Workspace access_token is required.")

    headers = {"Authorization": f"Bearer {access_token}"}
    profile_res = http_json_request("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers=headers)
    if profile_res.get("status") != 200:
        raise RuntimeError("Google Workspace token is invalid for Gmail.")
    profile = profile_res.get("json") if isinstance(profile_res.get("json"), dict) else {}

    calendars_json: Dict[str, Any] = {}
    calendar_access = False
    calendar_warning: str | None = None
    try:
        calendars_res = http_json_request(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers=headers,
        )
        calendars_json = (
            calendars_res.get("json") if isinstance(calendars_res.get("json"), dict) else {}
        )
        calendar_access = int(calendars_res.get("status") or 0) == 200
    except Exception as exc:
        # Calendar scope is optional for connector creation; Gmail-only mode is allowed.
        calendar_access = False
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        calendar_warning = (
            "Calendar scope is missing; Gmail actions work, Calendar actions are disabled until you "
            "grant https://www.googleapis.com/auth/calendar."
            + (f" ({detail})" if detail else "")
        )
    if not calendar_access and not calendar_warning:
        calendar_warning = (
            "Calendar scope is missing; Gmail actions work, Calendar actions are disabled until you "
            "grant https://www.googleapis.com/auth/calendar."
        )

    drive_access = False
    drive_warning: str | None = None
    drive_res: Dict[str, Any] = {}
    try:
        drive_res = http_json_request(
            "https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id,name,mimeType,webViewLink)",
            headers=headers,
        )
        drive_access = int(drive_res.get("status") or 0) == 200
    except Exception as exc:
        drive_access = False
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        drive_warning = (
            "Drive scope is missing; Gmail actions work, Drive and Google file actions are disabled until you "
            "grant a Google Drive scope."
            + (f" ({detail})" if detail else "")
        )
    if not drive_access and not drive_warning:
        drive_warning = (
            "Drive scope is missing; Gmail actions work, Drive and Google file actions are disabled until you "
            "grant a Google Drive scope."
        )

    calendars_preview: List[Dict[str, Any]] = []
    if calendar_access:
        for item in calendars_json.get("items", []) if isinstance(calendars_json.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            calendars_preview.append(
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "primary": bool(item.get("primary")),
                }
            )
            if len(calendars_preview) >= 10:
                break

    warnings = [warning for warning in (calendar_warning, drive_warning) if warning]

    return {
        "ok": True,
        "status": 200,
        "message": (
            "Google Workspace connector is valid."
            if calendar_access and drive_access
            else "Google Workspace connector is valid, but some Google Workspace scopes are still missing."
        ),
        "profile": {
            "emailAddress": profile.get("emailAddress"),
            "messagesTotal": profile.get("messagesTotal"),
            "threadsTotal": profile.get("threadsTotal"),
        },
        "calendar_access": calendar_access,
        "files_access": drive_access,
        "warning": " ".join(warnings) if warnings else None,
        "warnings": warnings,
        "calendars_preview": calendars_preview,
        "drive": {
            "driveType": "Google Drive",
            "webUrl": "https://drive.google.com/drive/my-drive",
        },
    }


def validate_microsoft_365_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    profile = microsoft_365_get_profile(credentials, http_json_request)
    capabilities = microsoft_365_probe_capabilities(credentials, http_json_request)
    mail_access = bool(capabilities.get("mail_access"))
    calendar_access = bool(capabilities.get("calendar_access"))
    files_access = bool(capabilities.get("files_access"))
    warnings = list(capabilities.get("warnings") or [])
    calendars_preview = list(capabilities.get("calendars_preview") or [])
    drive = capabilities.get("drive") if isinstance(capabilities.get("drive"), dict) else None
    message = (
        "Microsoft 365 connector is valid."
        if mail_access and calendar_access and files_access
        else "Microsoft 365 connector is valid, but some Graph scopes are still missing."
    )

    return {
        "ok": True,
        "status": 200,
        "message": message,
        "profile": {
            "id": profile.get("id"),
            "displayName": profile.get("displayName"),
            "mail": profile.get("mail"),
            "userPrincipalName": profile.get("userPrincipalName"),
        },
        "mail_access": mail_access,
        "calendar_access": calendar_access,
        "files_access": files_access,
        "warning": " ".join(warnings) if warnings else None,
        "warnings": warnings,
        "calendars_preview": calendars_preview,
        "drive": drive,
    }


def validate_telegram_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    bot_token = str(credentials.get("bot_token") or "").strip()
    chat_id = str(credentials.get("chat_id") or "").strip()
    if not bot_token:
        raise RuntimeError("Telegram bot_token is required.")
    if bot_token.startswith("<") and bot_token.endswith(">"):
        raise RuntimeError("Replace <YOUR_BOT_TOKEN> with your real Telegram bot token from @BotFather.")
    if any(ch.isspace() for ch in bot_token):
        raise RuntimeError("Telegram bot_token is invalid.")
    if ":" not in bot_token:
        raise RuntimeError("Telegram bot_token format is invalid. Expected '<digits>:<secret>'.")
    if not chat_id:
        raise RuntimeError("Telegram chat_id is required.")
    if chat_id.startswith("<") and chat_id.endswith(">"):
        raise RuntimeError("Replace <YOUR_CHAT_ID> with your real Telegram chat id.")
    if ":" in chat_id and chat_id.count(":") == 1:
        raise RuntimeError("Telegram chat_id looks like a bot token. Use numeric chat id (example: 123456789).")
    if any(ch.isspace() for ch in chat_id):
        raise RuntimeError("Telegram chat_id is invalid.")

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    res = http_json_request(url)
    body = res.get("json") if isinstance(res.get("json"), dict) else {}
    ok = bool(body.get("ok")) if isinstance(body, dict) else False
    if res.get("status") != 200 or not ok:
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("description") or "").strip()
        status = int(res.get("status") or 0)
        lowered = detail.lower()
        if status == 404 or "not found" in lowered:
            raise RuntimeError("Telegram bot token is invalid (404 from Telegram getMe). Use the token from @BotFather.")
        raise RuntimeError(detail or "Telegram bot token is invalid.")

    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    return {
        "ok": True,
        "status": 200,
        "message": "Telegram connector is valid.",
        "bot": {
            "id": result.get("id"),
            "username": result.get("username"),
            "first_name": result.get("first_name"),
            "can_join_groups": result.get("can_join_groups"),
            "supports_inline_queries": result.get("supports_inline_queries"),
        },
        "chat_id": chat_id,
    }


def validate_wechat_work_connector(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    *,
    send_test: bool = False,
) -> Dict[str, Any]:
    webhook_url = str(credentials.get("webhook_url") or "").strip()
    if not webhook_url:
        raise RuntimeError("WeChat Work webhook_url is required.")
    if not webhook_url.startswith("http://") and not webhook_url.startswith("https://"):
        raise RuntimeError("WeChat Work webhook_url must start with http:// or https://.")

    content = (
        "Empyralist test message\nTime: auto-generated\n— Empyralist"
        if send_test
        else "Empyralist connector check\n— Empyralist"
    )
    payload = {
        "msgtype": "text",
        "text": {
            "content": content,
        },
    }
    headers = {"Content-Type": "application/json"}
    res = http_json_request(
        webhook_url,
        method="POST",
        headers=headers,
        payload=payload,
    )
    body = res.get("json") if isinstance(res.get("json"), dict) else {}
    if int(res.get("status") or 0) != 200:
        detail = str(body.get("errmsg") or body.get("message") or "").strip()
        raise RuntimeError(detail or "WeChat Work webhook request failed.")
    errcode = int(body.get("errcode") or 0) if isinstance(body, dict) else 0
    if errcode != 0:
        detail = str(body.get("errmsg") or "").strip()
        raise RuntimeError(detail or "WeChat Work webhook was rejected.")

    return {
        "ok": True,
        "status": 200,
        "message": "WeChat Work test message sent." if send_test else "WeChat Work connector is valid.",
        "webhook_url": webhook_url,
    }


def validate_whatsapp_twilio_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    account_sid = str(credentials.get("account_sid") or "").strip()
    auth_token = str(credentials.get("auth_token") or "").strip()
    from_number = str(credentials.get("from_number") or "").strip()
    to_number = str(credentials.get("to_number") or "").strip()

    if not account_sid:
        raise RuntimeError("Twilio account_sid is required.")
    if not auth_token:
        raise RuntimeError("Twilio auth_token is required.")
    if not from_number:
        raise RuntimeError("Twilio from_number is required.")
    if not to_number:
        raise RuntimeError("Twilio to_number is required.")

    warnings: List[str] = []
    if not from_number.lower().startswith("whatsapp:"):
        warnings.append("from_number should start with 'whatsapp:'.")
    if not to_number.lower().startswith("whatsapp:"):
        warnings.append("to_number should start with 'whatsapp:'.")

    basic = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{quote_plus(account_sid)}.json"
    res = http_json_request(url, headers=headers)
    body = res.get("json") if isinstance(res.get("json"), dict) else {}
    if res.get("status") != 200:
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("message") or body.get("detail") or "").strip()
        raise RuntimeError(detail or "Twilio credentials are invalid.")

    return {
        "ok": True,
        "status": 200,
        "message": "WhatsApp (Twilio) connector is valid.",
        "account": {
            "sid": body.get("sid") if isinstance(body, dict) else None,
            "friendly_name": body.get("friendly_name") if isinstance(body, dict) else None,
            "status": body.get("status") if isinstance(body, dict) else None,
            "type": body.get("type") if isinstance(body, dict) else None,
        },
        "from_number": from_number,
        "to_number": to_number,
        "warnings": warnings,
    }


def validate_slack_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    bot_token = str(credentials.get("bot_token") or "").strip()
    team_id = str(credentials.get("team_id") or "").strip()
    if not bot_token:
        raise RuntimeError("Slack bot_token is required.")

    result = validate_slack_oauth_credentials(credentials, http_json_request=http_json_request)
    team = result.get("team") if isinstance(result.get("team"), dict) else {}
    resolved_team_id = str(team.get("id") or team_id or "").strip()
    if team_id and resolved_team_id and team_id != resolved_team_id:
        raise RuntimeError("Slack workspace mismatch for bot token.")

    return {
        "ok": True,
        "status": 200,
        "message": "Slack connector is valid.",
        "team": {
            "id": resolved_team_id or None,
            "name": str(team.get("name") or "").strip() or None,
            "url": str(team.get("url") or "").strip() or None,
        },
        "bot": result.get("bot") if isinstance(result.get("bot"), dict) else {},
        "authed_user": result.get("authed_user") if isinstance(result.get("authed_user"), dict) else {},
        "credentials": result.get("credentials") if isinstance(result.get("credentials"), dict) else dict(credentials),
    }


def validate_instagram_business_connector(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    access_token = str(credentials.get("access_token") or "").strip()
    instagram_account_id = str(
        credentials.get("instagram_account_id")
        or credentials.get("business_account_id")
        or credentials.get("account_id")
        or ""
    ).strip()
    page_id = str(credentials.get("page_id") or "").strip()

    if not access_token:
        raise RuntimeError("Instagram Business access_token is required.")
    if any(ch.isspace() for ch in access_token):
        raise RuntimeError("Instagram Business access_token is invalid.")
    if not instagram_account_id:
        raise RuntimeError("Instagram Business account_id is required.")
    if not instagram_account_id.isdigit():
        raise RuntimeError("Instagram Business account_id must be numeric.")
    if page_id and not page_id.isdigit():
        raise RuntimeError("Instagram Business page_id must be numeric when provided.")

    headers = {"Authorization": f"Bearer {access_token}"}
    profile_url = (
        f"https://graph.facebook.com/v23.0/{quote_plus(instagram_account_id)}"
        "?fields=id,username,account_type,name"
    )
    res = http_json_request(profile_url, headers=headers)
    body = res.get("json") if isinstance(res.get("json"), dict) else {}
    if res.get("status") != 200:
        detail = ""
        if isinstance(body, dict):
            err_obj = body.get("error")
            if isinstance(err_obj, dict):
                detail = str(err_obj.get("message") or "").strip()
            if not detail:
                detail = str(body.get("message") or "").strip()
        raise RuntimeError(detail or "Instagram Business token is invalid.")

    page_preview: Dict[str, Any] | None = None
    if page_id:
        page_url = f"https://graph.facebook.com/v23.0/{quote_plus(page_id)}?fields=id,name"
        page_res = http_json_request(page_url, headers=headers)
        page_body = page_res.get("json") if isinstance(page_res.get("json"), dict) else {}
        if page_res.get("status") != 200:
            detail = ""
            if isinstance(page_body, dict):
                err_obj = page_body.get("error")
                if isinstance(err_obj, dict):
                    detail = str(err_obj.get("message") or "").strip()
                if not detail:
                    detail = str(page_body.get("message") or "").strip()
            raise RuntimeError(detail or "Instagram Business page_id is invalid.")
        page_preview = {
            "id": page_body.get("id") if isinstance(page_body, dict) else page_id,
            "name": page_body.get("name") if isinstance(page_body, dict) else None,
        }

    return {
        "ok": True,
        "status": 200,
        "message": "Instagram Business connector is valid.",
        "account": {
            "id": body.get("id") if isinstance(body, dict) else instagram_account_id,
            "username": body.get("username") if isinstance(body, dict) else None,
            "account_type": body.get("account_type") if isinstance(body, dict) else None,
            "name": body.get("name") if isinstance(body, dict) else None,
        },
        "page": page_preview,
    }


def validate_irc_connector(credentials: Dict[str, Any]) -> Dict[str, Any]:
    server_host = str(credentials.get("server") or "").strip()
    nick = str(credentials.get("nick") or "").strip()
    channel = str(credentials.get("channel") or "").strip()
    password = str(credentials.get("password") or "").strip()

    if not server_host:
        raise RuntimeError("IRC server is required.")
    if any(ch.isspace() for ch in server_host):
        raise RuntimeError("IRC server is invalid.")
    if not nick:
        raise RuntimeError("IRC nick is required.")
    if len(nick) < 2:
        raise RuntimeError("IRC nick must be at least 2 characters.")

    raw_port = credentials.get("port", 6667)
    try:
        port = int(raw_port)
    except Exception:
        raise RuntimeError("IRC port must be a number.")
    if port < 1 or port > 65535:
        raise RuntimeError("IRC port must be in range 1..65535.")

    raw_tls = credentials.get("use_tls", True)
    use_tls = bool(raw_tls) if isinstance(raw_tls, bool) else str(raw_tls).strip().lower() in {"1", "true", "yes", "on"}

    warnings: List[str] = []
    if channel and not channel.startswith("#"):
        warnings.append("IRC channel usually starts with '#'.")
    if not channel:
        warnings.append("No channel provided; connector is saved without default room.")

    return {
        "ok": True,
        "status": 200,
        "message": "IRC connector saved with format validation.",
        "server": server_host,
        "port": port,
        "nick": nick,
        "channel": channel or None,
        "has_password": bool(password),
        "use_tls": use_tls,
        "warnings": warnings,
    }
