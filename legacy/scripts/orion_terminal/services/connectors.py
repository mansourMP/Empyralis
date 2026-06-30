from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import question_catalog as q
from ..clients.runtime import RuntimeClient
from ..core import Choice, UI, err, ok, warn
from ..spinner import ProgressSpinner

TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
TELEGRAM_CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z0-9_]{5,})$")
SUPPORTED_CONNECTOR_IDS = {"google_workspace", "telegram_bot", "whatsapp_twilio", "discord_bot", "irc"}
CONNECTOR_FALLBACK_CHOICES = [
    Choice("google_workspace", "Google Workspace", "access_token"),
    Choice("telegram_bot", "Telegram Bot", "bot_token, chat_id"),
    Choice("whatsapp_twilio", "WhatsApp (Twilio)", "account_sid, auth_token, from_number, to_number"),
    Choice("discord_bot", "Discord (Bot API)", "bot_token, channel_id, guild_id"),
    Choice("irc", "IRC (Server + Nick)", "server, port, nick, channel, password, use_tls"),
]


def _runtime_client(api_url: str, api_key: str) -> RuntimeClient:
    return RuntimeClient(api_url=api_url, api_key=api_key)


def _list_existing_connectors_by_type(
    runtime: RuntimeClient,
    workspace_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    try:
        items = runtime.list_connectors_vault(workspace_id)
    except Exception:
        return grouped
    for item in items:
        connector_id = str(item.get("connector") or "").strip().lower()
        if connector_id not in SUPPORTED_CONNECTOR_IDS:
            continue
        grouped.setdefault(connector_id, []).append(item)
    return grouped


def _connector_label(connector_id: str, fallback: str = "") -> str:
    if connector_id == "google_workspace":
        return "Google Workspace"
    if connector_id == "telegram_bot":
        return "Telegram Bot"
    if connector_id == "whatsapp_twilio":
        return "WhatsApp (Twilio)"
    if connector_id == "discord_bot":
        return "Discord (Bot API)"
    if connector_id == "irc":
        return "IRC (Server + Nick)"
    return fallback or connector_id


def _pick_existing_connector_entry(
    ui: UI,
    connector_id: str,
    entries: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]

    options: List[Choice] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in entries:
        entry_id = str(item.get("id") or "").strip()
        if not entry_id:
            continue
        label = str(item.get("label") or entry_id)
        created_at = str(item.get("created_at") or "").strip()
        hint = created_at[:19] if created_at else ""
        options.append(Choice(entry_id, label, hint))
        by_id[entry_id] = item
    if not options:
        return entries[0]

    picked = ui.choose(
        f"Select {_connector_label(connector_id, connector_id)} credential",
        options,
        default_index=0,
        title=q.TITLE_CONNECTORS,
    )
    return by_id.get(picked.key, entries[0])


def _prompt_existing_connector_action(
    ui: UI,
    connector_id: str,
    existing_count: int,
) -> Choice:
    return ui.choose(
        f"{_connector_label(connector_id, connector_id)} already configured",
        [
            Choice("reuse", "Use existing", f"{existing_count} saved"),
            Choice("new", "Add new credential", "Create another connector entry"),
            Choice("test", "Test existing", "Validate connector credentials"),
            Choice("skip", "Skip", "Do nothing for this connector"),
        ],
        default_index=0,
        title=q.TITLE_CONNECTORS,
    )


def _create_or_reuse_connector(
    ui: UI,
    runtime: RuntimeClient,
    api_url: str,
    api_key: str,
    workspace_id: str,
    connector_id: str,
    existing_by_type: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    existing_entries = list(existing_by_type.get(connector_id, []))
    if existing_entries:
        preview_labels = []
        for item in existing_entries[:3]:
            label = str(item.get("label") or item.get("id") or "").strip()
            if label:
                preview_labels.append(label)
        if preview_labels:
            suffix = " ..." if len(existing_entries) > 3 else ""
            ui.note(
                f"{ok('done:')} Existing {_connector_label(connector_id, connector_id)}: "
                f"{', '.join(preview_labels)}{suffix}"
            )
        action = _prompt_existing_connector_action(ui, connector_id, len(existing_entries))
        if action.key == "reuse":
            picked = _pick_existing_connector_entry(ui, connector_id, existing_entries)
            if picked:
                label = str(picked.get("label") or _connector_label(connector_id, connector_id))
                ui.note(f"{ok('done:')} Using existing connector '{label}' ({connector_id})")
                return picked
            return None
        if action.key == "test":
            picked = _pick_existing_connector_entry(ui, connector_id, existing_entries)
            if not picked:
                return None
            credential_id = str(picked.get("id") or "").strip()
            if not credential_id:
                ui.note(f"{warn('warning:')} Selected connector entry has no id.")
                return None
            spinner = ProgressSpinner("testing connector")
            spinner.tick("testing connector")
            try:
                runtime.test_connector_vault(credential_id, workspace_id=workspace_id)
                spinner.done(True, "connector valid")
                label = str(picked.get("label") or _connector_label(connector_id, connector_id))
                ui.note(f"{ok('done:')} Connector '{label}' validated.")
                return picked
            except Exception as exc:
                spinner.done(False, "connector invalid")
                ui.note(f"{err('failed:')} Connector test failed: {exc}")
                return None
        if action.key == "skip":
            ui.note(f"{warn('warning:')} Skipped {_connector_label(connector_id, connector_id)}.")
            return None

    try:
        created = create_connector_interactive(ui, api_url, api_key, workspace_id, connector_id)
        cid = str(created.get("id") or "")
        label = str(created.get("label") or _connector_label(connector_id, connector_id))
        ui.note(f"{ok('done:')} Added connector '{label}' ({connector_id}) id={cid}")
        if connector_id in SUPPORTED_CONNECTOR_IDS:
            existing_by_type.setdefault(connector_id, []).append(created)
        return created
    except Exception as exc:
        detail = str(exc)
        if connector_id == "telegram_bot":
            lowered = detail.lower()
            if "404" in lowered and "not found" in lowered:
                detail += " Hint: bot token is likely invalid."
            elif "401" in lowered or "unauthorized" in lowered:
                detail += " Hint: bot token is invalid or revoked."
            elif "400" in lowered:
                detail += " Hint: chat_id may be wrong; use numeric chat_id or @channelusername."
        ui.note(f"{err('failed:')} Failed to add connector: {detail}")
        return None


def create_connector_interactive(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    connector_id: str,
) -> Dict[str, Any]:
    def _input_telegram_bot_token() -> str:
        while True:
            token = ui.input("Telegram bot token", required=True, secret=True).strip()
            if TELEGRAM_BOT_TOKEN_RE.match(token):
                return token
            ui.note(
                f"{warn('warning:')} Invalid Telegram bot token format. "
                "Expected: <digits>:<secret> (example: 123456:ABC...)."
            )

    def _input_telegram_chat_id() -> str:
        while True:
            chat_id = ui.input("Telegram chat_id", required=True).strip()
            if TELEGRAM_BOT_TOKEN_RE.match(chat_id):
                ui.note(
                    f"{warn('warning:')} That looks like a bot token, not chat_id. "
                    "Use numeric chat_id (example: 123456789 or -100...) or @channel."
                )
                continue
            if TELEGRAM_CHAT_ID_RE.match(chat_id):
                return chat_id
            ui.note(
                f"{warn('warning:')} Invalid chat_id format. "
                "Use numeric chat_id (example: 123456789 or -100...) or @channelusername."
            )

    runtime = _runtime_client(api_url, api_key)
    if connector_id == "google_workspace":
        label = ui.input("Connector label", default="Google Workspace")
        access_token = ui.input("Google access_token", required=True, secret=True)
        calendar_id = ui.input("Calendar ID", default="primary")
        timezone = ui.input("Timezone", default="UTC")
        payload = {
            "label": label,
            "connector": connector_id,
            "workspace_id": workspace_id,
            "credentials": {
                "access_token": access_token,
                "calendar_id": calendar_id,
                "timezone": timezone,
            },
            "metadata": {"source": "orion_terminal_wizard"},
        }
        return runtime.create_connector(payload)

    if connector_id == "telegram_bot":
        label = ui.input("Connector label", default="Telegram Bot")
        bot_token = _input_telegram_bot_token()
        chat_id = _input_telegram_chat_id()
        payload = {
            "label": label,
            "connector": connector_id,
            "workspace_id": workspace_id,
            "credentials": {"bot_token": bot_token, "chat_id": chat_id},
            "metadata": {"source": "orion_terminal_wizard"},
        }
        return runtime.create_connector(payload)

    if connector_id == "whatsapp_twilio":
        label = ui.input("Connector label", default="WhatsApp Twilio")
        account_sid = ui.input("Twilio account SID", required=True)
        auth_token = ui.input("Twilio auth token", required=True, secret=True)
        from_number = ui.input("WhatsApp from number", default="whatsapp:+14155238886", required=True)
        to_number = ui.input("WhatsApp to number", required=True)
        payload = {
            "label": label,
            "connector": connector_id,
            "workspace_id": workspace_id,
            "credentials": {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": from_number,
                "to_number": to_number,
            },
            "metadata": {"source": "orion_terminal_wizard"},
        }
        return runtime.create_connector(payload)

    if connector_id == "discord_bot":
        label = ui.input("Connector label", default="Discord Bot")
        bot_token = ui.input("Discord bot token", required=True, secret=True)
        channel_id = ui.input("Discord channel_id", required=True)
        guild_id = ui.input("Discord guild_id", required=False)
        payload = {
            "label": label,
            "connector": connector_id,
            "workspace_id": workspace_id,
            "credentials": {
                "bot_token": bot_token,
                "channel_id": channel_id,
                "guild_id": guild_id,
            },
            "metadata": {"source": "orion_terminal_wizard"},
        }
        return runtime.create_connector(payload)

    if connector_id == "irc":
        label = ui.input("Connector label", default="IRC")
        server = ui.input("IRC server", required=True)
        port = ui.input("IRC port", default="6667", required=True)
        nick = ui.input("IRC nick", required=True)
        channel = ui.input("IRC channel", default="#general", required=False)
        password = ui.input("IRC password (optional)", required=False, secret=True)
        use_tls = ui.confirm("Use TLS", default_yes=True, title=q.TITLE_CONNECTORS)
        payload = {
            "label": label,
            "connector": connector_id,
            "workspace_id": workspace_id,
            "credentials": {
                "server": server,
                "port": port,
                "nick": nick,
                "channel": channel,
                "password": password,
                "use_tls": use_tls,
            },
            "metadata": {"source": "orion_terminal_wizard"},
        }
        return runtime.create_connector(payload)

    raise RuntimeError(f"Unsupported connector in wizard: {connector_id}")


def create_quickstart_connector(
    ui: UI,
    api_url: str,
    api_key: str,
    workspace_id: str,
    connector_id: str,
) -> Optional[Dict[str, Any]]:
    runtime = _runtime_client(api_url, api_key)
    existing_by_type = _list_existing_connectors_by_type(runtime, workspace_id)
    return _create_or_reuse_connector(
        ui=ui,
        runtime=runtime,
        api_url=api_url,
        api_key=api_key,
        workspace_id=workspace_id,
        connector_id=connector_id,
        existing_by_type=existing_by_type,
    )


def choose_and_create_connectors(ui: UI, api_url: str, api_key: str, workspace_id: str) -> List[Dict[str, Any]]:
    runtime = _runtime_client(api_url, api_key)
    load_spinner = ProgressSpinner("loading connectors")
    load_spinner.tick("loading connectors")
    try:
        items = runtime.list_connectors()
        load_spinner.done(True, "connectors ready")
    except Exception as exc:
        load_spinner.done(False, "connectors failed")
        ui.note(f"{warn('warning:')} Could not load connectors catalog: {exc}")
        return []
    if not items:
        choices = list(CONNECTOR_FALLBACK_CHOICES)
    else:
        choices = []
        for item in items:
            if not isinstance(item, dict):
                continue
            connector_id = str(item.get("id") or "").strip().lower()
            if connector_id not in SUPPORTED_CONNECTOR_IDS:
                continue
            auth = item.get("auth") if isinstance(item.get("auth"), list) else []
            note = ", ".join([str(x) for x in auth])
            choices.append(Choice(connector_id, str(item.get("label") or connector_id), note))
        if not choices:
            choices = list(CONNECTOR_FALLBACK_CHOICES)

    existing_by_type = _list_existing_connectors_by_type(runtime, workspace_id)
    ui.note(f"{ok('done:')} Existing connectors found: {sum(len(v) for v in existing_by_type.values())}")
    rendered_choices: List[Choice] = []
    for choice in choices:
        count = len(existing_by_type.get(choice.key, []))
        note_parts = [choice.note] if choice.note else []
        if count > 0:
            note_parts.append(f"configured:{count}")
        rendered_choices.append(Choice(choice.key, choice.label, " | ".join(note_parts)))

    created: List[Dict[str, Any]] = []
    while True:
        default_index = 0
        for idx, choice in enumerate(rendered_choices):
            if len(existing_by_type.get(choice.key, [])) == 0:
                default_index = idx
                break
        selected = ui.choose(q.Q_CONNECTORS_PICK, rendered_choices, default_index=default_index, title=q.TITLE_CONNECTORS)
        connector_item = _create_or_reuse_connector(
            ui=ui,
            runtime=runtime,
            api_url=api_url,
            api_key=api_key,
            workspace_id=workspace_id,
            connector_id=selected.key,
            existing_by_type=existing_by_type,
        )
        if connector_item:
            created.append(connector_item)

        if not ui.confirm(q.Q_CONNECTORS_ADD_ANOTHER, default_yes=False, title=q.TITLE_CONNECTORS):
            break
    return created


def build_connector_metadata(connector_entries: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, str]]]:
    google_connector_id = ""
    channel_connectors: List[Dict[str, str]] = []
    for item in connector_entries:
        cid = str(item.get("id") or "")
        connector = str(item.get("connector") or "")
        if connector == "google_workspace" and cid and not google_connector_id:
            google_connector_id = cid
        if cid and connector:
            channel_connectors.append({"connector": connector, "credential_id": cid})
    return google_connector_id, channel_connectors
