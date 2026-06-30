from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient


TelegramApiRequestHandler = Callable[[Dict[str, Any], Callable[[], Any]], Any]


class _NoStartThread:
    def __init__(self, *args, target=None, daemon=None, name=None, **kwargs):
        self.target = target
        self.daemon = daemon
        self.name = name

    def start(self) -> None:
        return None

    def join(self, timeout: Optional[float] = None) -> None:
        return None


class _FakeProviderAdapter:
    provider_id = "openai"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = max(0.0, float(delay_seconds or 0.0))

    def validate(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "status": 200, "credentials": credentials}

    def list_models(self, credentials: Dict[str, Any]) -> List[str]:
        _ = credentials
        return ["gpt-4o-mini"]

    def generate(self, system_prompt: str, user_input: str, model: str, credentials: Dict[str, Any]) -> str:
        _ = model, credentials
        if self.delay_seconds > 0.0:
            time.sleep(self.delay_seconds)
        lowered_input = str(user_input or "").strip().lower()
        lowered_prompt = str(system_prompt or "").strip().lower()
        if "[healthguide_safety_policy_v1]" in lowered_prompt and (
            "chest pain" in lowered_input or "trouble breathing" in lowered_input
        ):
            return (
                "This could need urgent medical attention. Seek urgent in-person medical care now or contact "
                "local emergency services immediately."
            )
        return f"Blackbox durable reply: {str(user_input or '').strip()}"


def _usage_masked(*, reply: str) -> Dict[str, Any]:
    total_tokens = max(16, len(str(reply or "").strip()) // 4 + 12)
    prompt_tokens = max(8, total_tokens // 2)
    completion_tokens = max(8, total_tokens - prompt_tokens)
    return {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage_prompt_tokens": prompt_tokens,
        "usage_completion_tokens": completion_tokens,
        "usage_total_tokens": total_tokens,
        "usage_estimated_cost_usd": round(total_tokens * 0.000002, 6),
        "pricing_known": False,
        "estimation_mode": "heuristic_blackbox",
    }


def _build_fake_generate_chat_reply_with_provider_fallback(
    *,
    provider_delay_seconds: float,
) -> Callable[..., Any]:
    delay_seconds = max(0.0, float(provider_delay_seconds or 0.0))

    def _fake_generate_chat_reply_with_provider_fallback(
        context: Dict[str, Any],
        metadata: Dict[str, Any],
        user_goal: str,
        system_prompt: Optional[str],
        prior_messages: Any = None,
    ):
        del context, metadata, system_prompt, prior_messages
        if delay_seconds > 0.0:
            time.sleep(delay_seconds)
        reply = f"Blackbox web reply: {str(user_goal or '').strip()}"
        return reply, _usage_masked(reply=reply), "openai", ""

    return _fake_generate_chat_reply_with_provider_fallback


def _build_fake_generate_chat_reply_stream_with_provider_fallback(
    *,
    provider_delay_seconds: float,
) -> Callable[..., Any]:
    generate_once = _build_fake_generate_chat_reply_with_provider_fallback(
        provider_delay_seconds=provider_delay_seconds,
    )

    def _fake_generate_chat_reply_stream_with_provider_fallback(
        *,
        context: Dict[str, Any],
        metadata: Dict[str, Any],
        user_goal: str,
        system_prompt: Optional[str],
        prior_messages: Any = None,
    ):
        reply, usage_masked, attempted_providers, error = generate_once(
            context,
            metadata,
            user_goal,
            system_prompt,
            prior_messages=prior_messages,
        )
        yield {
            "type": "result",
            "reply": reply,
            "usage_masked": usage_masked,
            "attempted_providers": attempted_providers,
            "error": error,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "tool_calls": [],
        }

    return _fake_generate_chat_reply_stream_with_provider_fallback


def _healthy_doctor_report() -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "available": True,
        "generated_at": generated_at,
        "overview": {
            "status": "pass",
            "title": "Runtime healthy",
            "detail": "Black-box provider boundary stub is healthy.",
        },
        "summary": {"pass": 2, "warn": 0, "fail": 0},
        "checks": [
            {
                "name": "openai_connectivity",
                "status": "pass",
                "title": "OpenAI connectivity",
                "detail": "OpenAI probe healthy (source=blackbox_stub, status=200).",
                "recommendation": "No action needed.",
            },
            {
                "name": "runtime_validation",
                "status": "pass",
                "title": "Runtime validation",
                "detail": "Runtime configuration is valid.",
                "recommendation": "No action needed.",
            },
        ],
        "priority_fixes": [],
    }


def _purge_runtime_modules() -> None:
    for name in list(sys.modules):
        if name == "server" or name == "server_modules" or name.startswith("server_modules."):
            sys.modules.pop(name, None)


def _deep_merge(base: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(overrides, dict):
        return dict(base)
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def default_telegram_api_response(call: Dict[str, Any], transport_state: Dict[str, Any]) -> Any:
    method_name = str(call.get("method_name") or "").strip()
    transport_state["next_message_id"] = int(transport_state.get("next_message_id") or 1000) + 1
    if method_name == "sendMessage":
        return {"message_id": int(transport_state["next_message_id"])}
    if method_name == "editMessageText":
        payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
        return {"message_id": payload.get("message_id")}
    if method_name == "sendChatAction":
        return {"ok": True}
    raise AssertionError(f"Unexpected Telegram API method in black-box runtime: {method_name}")


class BlackboxHarness:
    def __init__(
        self,
        *,
        client: TestClient,
        app: Any,
        auth_module: Any,
        agent_registry_repository_module: Any,
        outbox_service_module: Any,
        notification_service_module: Any,
        run_state_repository_module: Any,
        control_plane_repository_module: Any,
        db_module: Any,
        telegram_requests: List[Dict[str, Any]],
        state_home: Path,
        webhook_secret: str,
        module_refs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.client = client
        self.app = app
        self.auth = auth_module
        self.agent_registry_repository = agent_registry_repository_module
        self.outbox_service = outbox_service_module
        self.notification_service = notification_service_module
        self.run_state_repository = run_state_repository_module
        self.control_plane_repository = control_plane_repository_module
        self.db = db_module
        self.telegram_requests = telegram_requests
        self.state_home = state_home
        self.webhook_secret = webhook_secret
        self.module_refs = dict(module_refs or {})
        self._telegram_update_counter = 100
        self._telegram_message_counter = 500

    @staticmethod
    def _run(coro: Any) -> Any:
        return asyncio.run(coro)

    @staticmethod
    def _sse_events(raw_text: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        event_name = "message"
        data_lines: List[str] = []
        event_id = ""
        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.rstrip("\r")
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines)
                    events.append(
                        {
                            "id": event_id,
                            "event": event_name,
                            "data": json.loads(payload) if payload else {},
                        }
                    )
                event_name = "message"
                event_id = ""
                data_lines = []
                continue
            if line.startswith("id:"):
                event_id = line[3:].strip()
            elif line.startswith("event:"):
                event_name = line[6:].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            payload = "\n".join(data_lines)
            events.append(
                {
                    "id": event_id,
                    "event": event_name,
                    "data": json.loads(payload) if payload else {},
                }
            )
        return events

    @asynccontextmanager
    async def async_client(self) -> Iterator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    def register_owner(self, *, email_prefix: str = "blackbox-owner") -> Dict[str, Any]:
        email = f"{email_prefix}-{uuid.uuid4().hex[:12]}@example.com"
        payload = self.auth.register_user(
            email,
            "password-123",
            name="Blackbox Owner",
            channel="api",
        )
        workspace_id = str(
            payload.get("current_workspace_id")
            or payload.get("default_workspace_id")
            or ""
        ).strip()
        workspace_access = payload.get("workspace_access") if isinstance(payload.get("workspace_access"), list) else []
        workspace_entry = next(
            (
                item
                for item in workspace_access
                if isinstance(item, dict) and str(item.get("workspace_id") or "").strip() == workspace_id
            ),
            {},
        )
        return {
            "payload": payload,
            "token": str(payload.get("token") or "").strip(),
            "headers": {"Authorization": f"Bearer {str(payload.get('token') or '').strip()}"},
            "user_id": str((payload.get("user") or {}).get("id") or "").strip(),
            "workspace_id": workspace_id,
            "tenant_id": str(workspace_entry.get("tenant_id") or payload.get("current_tenant_id") or "").strip(),
        }

    def create_deployed_agent(
        self,
        *,
        owner: Dict[str, Any],
        name: Optional[str] = None,
        endpoint_key: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        deployed_agent_name = str(name or f"Blackbox Agent {uuid.uuid4().hex[:6]}").strip()
        channel_endpoint = str(endpoint_key or f"@bb_{uuid.uuid4().hex[:10]}").strip()
        base_config = {
            "name": deployed_agent_name,
            "persona": "Helpful assistant",
            "system_prompt": "Answer clearly and stay within configured boundaries.",
            "runtime_target": "cloud",
            "billing_plan": "free",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "channels": {
                "telegram": {
                    "enabled": True,
                    "endpoint_key": channel_endpoint,
                    "is_inbound_owner": True,
                }
            },
            "customer_policy": {
                "upgrade_cta_label": "Upgrade here",
                "upgrade_cta_url": "https://blackbox.example/upgrade",
            },
            "memory_policy": {
                "memory_enabled": True,
                "context_budget_preset": "external_channel_customer",
                "retention_preset": "standard",
            },
            "safety_policy": {
                "health_safety_enabled": False,
            },
            "escalation_policy": {
                "preset": "default",
                "handoff_mode": "owner_notify_only",
                "owner_notification_destination": "ops@blackbox.example",
            },
            "commerce_policy": {},
            "tool_policy": {
                "enabled_tools": ["web_search"],
            },
        }
        config = _deep_merge(base_config, config_overrides)
        response = self.client.post(
            "/api/deployed-agents",
            headers=owner["headers"],
            json={
                "workspace_id": owner["workspace_id"],
                "name": deployed_agent_name,
                "persona": config.get("persona", ""),
                "system_prompt": config.get("system_prompt", ""),
                "channels": config.get("channels", {}),
                "runtime_target": config.get("runtime_target", "cloud"),
                "billing_plan": config.get("billing_plan", "free"),
                "provider": config.get("provider"),
                "model": config.get("model"),
                "config": config,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        payload["_endpoint_key"] = channel_endpoint
        return payload

    def deploy_deployed_agent(self, *, owner: Dict[str, Any], deployed_agent_id: str) -> Dict[str, Any]:
        response = self.client.post(
            f"/api/deployed-agents/{deployed_agent_id}/deploy",
            headers=owner["headers"],
            json={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def pause_deployed_agent(self, *, owner: Dict[str, Any], deployed_agent_id: str) -> Dict[str, Any]:
        response = self.client.post(
            f"/api/deployed-agents/{deployed_agent_id}/pause",
            headers=owner["headers"],
            json={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_deployed_agent_turn(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        message: str,
        channel: str = "test",
        runtime_mode: str = "text_agent",
    ) -> Dict[str, Any]:
        response = self.client.post(
            f"/api/deployed-agents/{deployed_agent_id}/test-turn",
            headers=owner["headers"],
            json={
                "workspace_id": owner["workspace_id"],
                "message": message,
                "channel": channel,
                "runtime_mode": runtime_mode,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def seed_live_deployed_agent(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        backing_install_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        deployed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = self._run(
            self.control_plane_repository.set_deployed_agent_state(
                deployed_agent_id,
                tenant_id=owner["tenant_id"],
                owner_workspace_id=owner["workspace_id"],
                deployment_state="live",
                last_deployed_at=deployed_at,
            )
        )
        assert isinstance(payload, dict), payload
        resolved_install_id = str(
            backing_install_id
            or payload.get("backing_install_id")
            or ""
        ).strip()
        if resolved_install_id:
            install_payload = self._run(
                self.agent_registry_repository.update_workspace_agent_install(
                    resolved_install_id,
                    tenant_id=owner["tenant_id"],
                    workspace_id=owner["workspace_id"],
                    status="active",
                )
            )
            assert isinstance(install_payload, dict), install_payload
        return self.get_deployed_agent(owner=owner, deployed_agent_id=deployed_agent_id)

    def create_telegram_connector(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent: Dict[str, Any],
        endpoint_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_endpoint_key = str(
            endpoint_key
            or deployed_agent.get("_endpoint_key")
            or (((deployed_agent.get("channels") or {}).get("telegram") or {}).get("endpoint_key"))
            or ""
        ).strip()
        response = self.client.post(
            "/connectors/vault",
            headers=owner["headers"],
            json={
                "label": f"Telegram {deployed_agent['id']}",
                "connector": "telegram_bot",
                "workspace_id": owner["workspace_id"],
                "credentials": {
                    "bot_token": f"telegram-bot-token-{deployed_agent['id']}",
                    "chat_id": "*",
                },
                "metadata": {
                    "source": "deployed_agent",
                    "deployed_agent_id": deployed_agent["id"],
                    "deployed_agent_name": deployed_agent["name"],
                    "deployed_agent": {
                        "id": deployed_agent["id"],
                        "name": deployed_agent["name"],
                    },
                    "channel_registry_bindings": {
                        "telegram": {
                            "endpoint_key": resolved_endpoint_key,
                            "deployed_agent_id": deployed_agent["id"],
                        }
                    },
                },
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        connector_id = str(payload.get("id") or payload.get("credential_id") or "").strip()
        if connector_id:
            self.wait_for_vault_connector(
                owner=owner,
                connector_id=connector_id,
            )
        return payload

    def wait_for_vault_connector(
        self,
        *,
        owner: Dict[str, Any],
        connector_id: str,
        timeout_seconds: float = 10.0,
    ) -> Dict[str, Any]:
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while time.time() < deadline:
            response = self.client.get(
                "/connectors/vault",
                headers=owner["headers"],
                params={"workspace_id": owner["workspace_id"]},
            )
            assert response.status_code == 200, response.text
            items = response.json().get("items") or []
            for item in items:
                if str((item or {}).get("id") or "").strip() == connector_id:
                    return item
            time.sleep(0.2)
        response = self.client.get(
            "/connectors/vault",
            headers=owner["headers"],
            params={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        items = response.json().get("items") or []
        for item in items:
            if str((item or {}).get("id") or "").strip() == connector_id:
                return item
        raise AssertionError({"connector_id": connector_id, "items": items})

    def bind_telegram_connector(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent: Dict[str, Any],
        connector: Dict[str, Any],
        endpoint_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        channels = dict(deployed_agent.get("channels") or {})
        telegram = dict(channels.get("telegram") or {})
        resolved_endpoint_key = str(
            endpoint_key
            or telegram.get("endpoint_key")
            or deployed_agent.get("_endpoint_key")
            or ""
        ).strip()
        connector_id = str(connector.get("id") or connector.get("credential_id") or "").strip()
        telegram.update(
            {
                "enabled": True,
                "connector_id": connector_id,
                "credential_id": connector_id,
                "endpoint_key": resolved_endpoint_key,
                "is_inbound_owner": True,
            }
        )
        channels["telegram"] = telegram
        response = self.client.patch(
            f"/api/deployed-agents/{deployed_agent['id']}",
            headers=owner["headers"],
            json={
                "workspace_id": owner["workspace_id"],
                "channels": channels,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        payload["_endpoint_key"] = resolved_endpoint_key
        return payload

    def create_live_telegram_deployed_agent(
        self,
        *,
        owner: Dict[str, Any],
        name: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        deployed_agent = self.create_deployed_agent(
            owner=owner,
            name=name,
            config_overrides=config_overrides,
        )
        endpoint_key = str(
            deployed_agent.get("_endpoint_key")
            or (((deployed_agent.get("channels") or {}).get("telegram") or {}).get("endpoint_key"))
            or ""
        ).strip()
        connector = self.create_telegram_connector(
            owner=owner,
            deployed_agent=deployed_agent,
            endpoint_key=endpoint_key or None,
        )
        deployed_agent = self.bind_telegram_connector(
            owner=owner,
            deployed_agent=deployed_agent,
            connector=connector,
            endpoint_key=endpoint_key or None,
        )
        try:
            deployed_agent = self.deploy_deployed_agent(owner=owner, deployed_agent_id=deployed_agent["id"])
        except AssertionError as error:
            if "customer_live mode before deployment can go live" not in str(error):
                raise
            deployed_agent = self.seed_live_deployed_agent(
                owner=owner,
                deployed_agent_id=deployed_agent["id"],
                backing_install_id=str(deployed_agent.get("backing_install_id") or "").strip() or None,
            )
        return {
            "deployed_agent": deployed_agent,
            "connector": connector,
        }

    def build_telegram_webhook_payload(
        self,
        *,
        text: str,
        chat_id: str = "chat-1",
        sender_id: str = "telegram-user-1",
        sender_username: str = "alice",
        sender_display_name: str = "Alice",
        update_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        self._telegram_update_counter += 1
        self._telegram_message_counter += 1
        return {
            "update_id": int(update_id or self._telegram_update_counter),
            "message": {
                "message_id": int(message_id or self._telegram_message_counter),
                "text": text,
                "chat": {"id": chat_id, "type": "private"},
                "from": {
                    "id": sender_id,
                    "username": sender_username,
                    "first_name": sender_display_name,
                },
            },
        }

    def telegram_webhook(
        self,
        *,
        connector_id: str,
        text: str,
        chat_id: str = "chat-1",
        sender_id: str = "telegram-user-1",
        sender_username: str = "alice",
        sender_display_name: str = "Alice",
        update_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ):
        response = self.client.post(
            f"/channels/telegram/webhook/{connector_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": self.webhook_secret},
            json=self.build_telegram_webhook_payload(
                text=text,
                chat_id=chat_id,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_display_name=sender_display_name,
                update_id=update_id,
                message_id=message_id,
            ),
        )
        return response

    async def telegram_webhook_async(
        self,
        *,
        connector_id: str,
        text: str,
        chat_id: str = "chat-1",
        sender_id: str = "telegram-user-1",
        sender_username: str = "alice",
        sender_display_name: str = "Alice",
        update_id: Optional[int] = None,
        message_id: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> httpx.Response:
        payload = self.build_telegram_webhook_payload(
            text=text,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_username=sender_username,
            sender_display_name=sender_display_name,
            update_id=update_id,
            message_id=message_id,
        )
        headers = {"X-Telegram-Bot-Api-Secret-Token": self.webhook_secret}
        if client is not None:
            return await client.post(
                f"/channels/telegram/webhook/{connector_id}",
                headers=headers,
                json=payload,
            )
        async with self.async_client() as async_client:
            return await async_client.post(
                f"/channels/telegram/webhook/{connector_id}",
                headers=headers,
                json=payload,
            )

    def pump_outbox_once(
        self,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.outbox_service.deliver_due_outbox_events_once(
            older_than_seconds=0,
            limit=50,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
            claim_due_outbox_events_fn=self.run_state_repository.sync_claim_due_outbox_events,
            mark_outbox_event_delivered_fn=self.run_state_repository.sync_mark_outbox_event_delivered,
            record_outbox_delivery_failure_fn=self.run_state_repository.sync_record_outbox_delivery_failure,
            get_outbox_delivery_status_fn=self.run_state_repository.sync_get_outbox_delivery_status,
            claimed_by=f"pytest-blackbox:{uuid.uuid4().hex[:10]}",
            claim_ttl_seconds=15,
        )

    def pump_outbox_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout_seconds: float = 15.0,
        interval_seconds: float = 0.2,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        deadline = time.time() + max(1.0, float(timeout_seconds))
        last_result: Dict[str, Any] = {}
        while time.time() < deadline:
            if predicate():
                return last_result
            last_result = self.pump_outbox_once(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=run_id,
                event_type=event_type,
            )
            if predicate():
                return last_result
            time.sleep(interval_seconds)
        assert predicate(), "Timed out waiting for outbox delivery."
        return last_result

    def pump_outbox_until_idle(
        self,
        *,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 0.2,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.pump_outbox_until(
            lambda: (
                int(self.get_outbox_status().get("undelivered_count") or 0) == 0
                and int(self.get_outbox_status().get("claimed_count") or 0) == 0
            ),
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
        )

    def wait_for_deployed_agent_state(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        expected_state: str,
        timeout_seconds: float = 15.0,
    ) -> Dict[str, Any]:
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while time.time() < deadline:
            payload = self.get_deployed_agent(owner=owner, deployed_agent_id=deployed_agent_id)
            if str(payload.get("deployment_state") or "").strip().lower() == str(expected_state).strip().lower():
                return payload
            time.sleep(0.2)
        payload = self.get_deployed_agent(owner=owner, deployed_agent_id=deployed_agent_id)
        assert (
            str(payload.get("deployment_state") or "").strip().lower()
            == str(expected_state).strip().lower()
        ), payload
        return payload

    def wait_for_run_terminal(
        self,
        run_id: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> Dict[str, Any]:
        async def _load() -> Dict[str, Any]:
            live = await self.run_state_repository.get_live_run(run_id)
            if isinstance(live, dict):
                return live
            archived = await self.run_state_repository.get_archived_run(run_id)
            return archived if isinstance(archived, dict) else {}

        terminal_statuses = {"completed", "failed", "timeout", "stopped", "cancelled"}
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while time.time() < deadline:
            payload = self._run(_load())
            if str(payload.get("status") or "").strip().lower() in terminal_statuses:
                return payload
            time.sleep(0.2)
        payload = self._run(_load())
        assert str(payload.get("status") or "").strip().lower() in terminal_statuses, payload
        return payload

    def get_deployed_agent(self, *, owner: Dict[str, Any], deployed_agent_id: str) -> Dict[str, Any]:
        response = self.client.get(
            f"/api/deployed-agents/{deployed_agent_id}",
            headers=owner["headers"],
            params={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def list_conversations(self, *, owner: Dict[str, Any], deployed_agent_id: str) -> Dict[str, Any]:
        response = self.client.get(
            f"/api/deployed-agents/{deployed_agent_id}/conversations",
            headers=owner["headers"],
            params={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_conversation_detail(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        response = self.client.get(
            f"/api/deployed-agents/{deployed_agent_id}/conversations/{session_id}",
            headers=owner["headers"],
            params={"workspace_id": owner["workspace_id"]},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def delete_external_user_data(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        external_user_id: str,
        channel: str = "telegram",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        response = self.client.post(
            f"/api/deployed-agents/{deployed_agent_id}/external-users/{external_user_id}/delete",
            headers=owner["headers"],
            json={
                "workspace_id": owner["workspace_id"],
                "channel": channel,
                "session_id": session_id,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_thread(self, *, owner: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
        response = self.client.get(
            f"/threads/{thread_id}",
            headers=owner["headers"],
        )
        assert response.status_code == 200, response.text
        return response.json()

    def list_threads(
        self,
        *,
        owner: Dict[str, Any],
        include_turns: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        response = self.client.get(
            "/threads",
            headers=owner["headers"],
            params={
                "workspace_id": owner["workspace_id"],
                "include_turns": str(bool(include_turns)).lower(),
                "limit": int(limit),
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_session(self, *, owner: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        response = self.client.get(
            f"/sessions/{session_id}",
            headers=owner["headers"],
        )
        assert response.status_code == 200, response.text
        return response.json()

    def list_activity_events(
        self,
        *,
        owner: Dict[str, Any],
        backing_install_id: str,
        session_key: Optional[str] = None,
        thread_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._run(
            self.control_plane_repository.list_activity_ledger_events(
                tenant_id=owner["tenant_id"],
                workspace_id=owner["workspace_id"],
                install_id=backing_install_id,
                session_key=session_key,
                thread_id=thread_id,
                run_id=run_id,
                limit=200,
            )
        )

    def summarize_deployed_agent_monthly_cost(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
    ) -> Dict[str, Any]:
        return self._run(
            self.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger(
                tenant_id=owner["tenant_id"],
                workspace_id=owner["workspace_id"],
                deployed_agent_id=deployed_agent_id,
            )
        )

    def list_deployed_agent_memory(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
    ) -> List[Dict[str, Any]]:
        return self._run(
            self.control_plane_repository.list_deployed_agent_conversation_memory(
                tenant_id=owner["tenant_id"],
                workspace_id=owner["workspace_id"],
                deployed_agent_id=deployed_agent_id,
                limit=50,
            )
        )

    def list_notifications(
        self,
        *,
        owner: Dict[str, Any],
        limit: int = 80,
        workspace_id: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        response = self.client.get(
            "/notifications",
            headers=owner["headers"],
            params={
                "workspace_id": workspace_id or owner["workspace_id"],
                "limit": int(limit),
                **{key: value for key, value in params.items() if value is not None},
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_outbox_status(self) -> Dict[str, Any]:
        return self.run_state_repository.sync_get_outbox_delivery_status()

    def list_poisoned_outbox_events(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        return list(self.run_state_repository.sync_list_poisoned_outbox_events(limit=limit))

    def get_privacy_request(
        self,
        *,
        owner: Dict[str, Any],
        deployed_agent_id: str,
        channel_key: str,
        external_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        async def _load() -> Optional[Dict[str, Any]]:
            pool = await self.db.get_pool()
            assert pool is not None, "Black-box tests require a reachable Postgres control-plane database."
            async with pool.acquire() as connection:
                row = await connection.fetchrow(
                    """
                    SELECT *
                    FROM external_user_privacy_requests
                    WHERE tenant_id = $1
                      AND workspace_id = $2
                      AND deployed_agent_id = $3
                      AND channel_key = $4
                      AND external_user_id = $5
                    LIMIT 1
                    """,
                    owner["tenant_id"],
                    owner["workspace_id"],
                    deployed_agent_id,
                    channel_key,
                    external_user_id,
                )
                return dict(row) if row is not None else None

        return self._run(_load())

    def captured_telegram_calls(self, *, method_name: Optional[str] = None) -> List[Dict[str, Any]]:
        normalized = str(method_name or "").strip()
        if not normalized:
            return list(self.telegram_requests)
        return [
            dict(item)
            for item in self.telegram_requests
            if str(item.get("method_name") or "").strip() == normalized
        ]

    def latest_telegram_text(self, *, method_name: Optional[str] = None) -> str:
        calls = self.captured_telegram_calls(method_name=method_name)
        if not calls:
            return ""
        payload = calls[-1].get("payload") if isinstance(calls[-1].get("payload"), dict) else {}
        return str(payload.get("text") or "").strip()

    def stream_turn(self, *, owner: Dict[str, Any], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        with self.client.stream(
            "POST",
            "/turn",
            headers=owner["headers"],
            json=payload,
        ) as response:
            assert response.status_code == 200, response.text
            body = "".join(chunk for chunk in response.iter_text())
        return self._sse_events(body)


def _apply_common_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state_home: Path,
    webhook_secret: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> None:
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.delenv("EMPYRALIS_JWT_SECRET_FILE", raising=False)
    monkeypatch.setenv("ORION_ENV", "test")
    monkeypatch.setenv("ORION_JWT_SECRET", "blackbox-jwt-secret")
    monkeypatch.setenv("EMPYRALIS_SECRETS_BROKER_SECRET", "blackbox-secrets-broker-secret")
    monkeypatch.setenv("EMPYRALIS_TOOL_BROKER_SECRET", "blackbox-tool-broker-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "blackbox-openai-key")
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://blackbox.example")
    monkeypatch.setenv("EMPYRALIS_PUBLIC_FRONTEND_ORIGIN", "https://blackbox.example")
    monkeypatch.setenv("BACKEND_PUBLIC_ORIGIN", "https://blackbox.example")
    monkeypatch.setenv("ORION_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("ORION_LOCAL_COMPANION_ENABLED", "0")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_ENABLED", "1")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT", "1")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE", "webhook")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_SEND_ACK", "1")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL", "https://blackbox.example")
    monkeypatch.setenv("ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("ORION_WHATSAPP_AUTOPILOT_ENABLED", "0")
    for key, value in dict(extra_env or {}).items():
        monkeypatch.setenv(str(key), str(value))


def _blackbox_database_url(extra_env: Optional[Dict[str, str]] = None) -> str:
    return str(dict(extra_env or {}).get("DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


@contextmanager
def blackbox_runtime_context(
    *,
    monkeypatch: Optional[pytest.MonkeyPatch] = None,
    tmp_path: Optional[Path] = None,
    state_home: Optional[Path] = None,
    db_pool_size: int = 1,
    provider_delay_seconds: float = 0.0,
    webhook_secret: str = "telegram-blackbox-secret",
    telegram_api_request_handler: Optional[TelegramApiRequestHandler] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Iterator[BlackboxHarness]:
    owned_monkeypatch = monkeypatch is None
    active_monkeypatch = monkeypatch or pytest.MonkeyPatch()
    resolved_tmp_path = Path(tmp_path or Path.cwd() / ".pytest-blackbox-runtime")
    resolved_state_home = Path(state_home or (resolved_tmp_path / "state"))
    resolved_state_home.mkdir(parents=True, exist_ok=True)

    if not _blackbox_database_url(extra_env):
        if owned_monkeypatch:
            active_monkeypatch.undo()
        pytest.skip("Black-box runtime requires DATABASE_URL for a reachable Postgres control-plane test database.")

    _apply_common_runtime_environment(
        active_monkeypatch,
        state_home=resolved_state_home,
        webhook_secret=webhook_secret,
        extra_env=extra_env,
    )

    worker_llm = importlib.import_module("scripts.orion_local_worker_llm")
    active_monkeypatch.setattr(
        worker_llm,
        "generate_chat_reply_with_provider_fallback",
        _build_fake_generate_chat_reply_with_provider_fallback(
            provider_delay_seconds=provider_delay_seconds,
        ),
    )
    active_monkeypatch.setattr(
        worker_llm,
        "generate_chat_reply_stream_with_provider_fallback",
        _build_fake_generate_chat_reply_stream_with_provider_fallback(
            provider_delay_seconds=provider_delay_seconds,
        ),
    )

    _purge_runtime_modules()

    server = importlib.import_module("server")
    auth_module = importlib.import_module("server_modules.auth")
    runs_core_module = importlib.import_module("server_modules.runs_core")
    connectors_actions_module = importlib.import_module("server_modules.connectors_actions")
    runs_engine_module = importlib.import_module("server_modules.runs_engine")
    outbox_service_module = importlib.import_module("server_modules.outbox_service")
    notification_service_module = importlib.import_module("server_modules.notification_service")
    run_state_repository_module = importlib.import_module("server_modules.run_state_repository")
    db_module = importlib.import_module("server_modules.db")
    control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
    agent_registry_repository_module = importlib.import_module("server_modules.agent_registry_repository")
    health_diagnostics_module = importlib.import_module("server_modules.health_diagnostics")
    shared_module = importlib.import_module("server_modules.shared")
    telegram_transport_module = importlib.import_module("server_modules.connectors.telegram_transport_service")
    autopilot_runtime_exports_module = importlib.import_module("server_modules.connectors.autopilot_runtime_exports")

    active_monkeypatch.setattr(runs_core_module, "initialize_runtime_services", lambda: None)
    active_monkeypatch.setattr(
        connectors_actions_module,
        "validate_telegram_connector",
        lambda credentials: {"ok": True, "status": 200, "credentials": dict(credentials or {})},
    )

    def _fake_connector_http_json_request(url: str, *, method: str = "GET", payload=None, timeout=None, **_kwargs):
        if "/setWebhook" in str(url):
            return {"status": 200, "json": {"ok": True, "result": True}}
        if "/getWebhookInfo" in str(url):
            return {
                "status": 200,
                "json": {
                    "ok": True,
                    "result": {"url": str((payload or {}).get("url") or "")},
                },
            }
        raise AssertionError(f"Unexpected connector HTTP request in black-box runtime: {method} {url}")

    active_monkeypatch.setattr(connectors_actions_module, "http_json_request", _fake_connector_http_json_request)
    active_monkeypatch.setattr(
        runs_engine_module,
        "resolve_provider_adapter",
        lambda provider, credentials: ("openai", "blackbox_openai", _FakeProviderAdapter(delay_seconds=provider_delay_seconds)),
    )

    async def _fake_doctor() -> Dict[str, Any]:
        return _healthy_doctor_report()

    active_monkeypatch.setattr(
        health_diagnostics_module,
        "doctor",
        _fake_doctor,
    )

    @asynccontextmanager
    async def _noop_empyralist_mcp_lifespan():
        yield

    active_monkeypatch.setattr(
        shared_module,
        "empyralist_mcp_lifespan",
        _noop_empyralist_mcp_lifespan,
        raising=False,
    )
    active_monkeypatch.setattr(
        db_module,
        "configured_postgres_pool_max_size",
        lambda: max(1, int(db_pool_size or 1)),
    )
    active_monkeypatch.setattr(autopilot_runtime_exports_module, "ORION_TELEGRAM_AUTOPILOT_ENABLED", True, raising=False)
    active_monkeypatch.setattr(autopilot_runtime_exports_module, "ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE", "webhook", raising=False)
    active_monkeypatch.setattr(autopilot_runtime_exports_module, "ORION_TELEGRAM_AUTOPILOT_SEND_ACK", True, raising=False)
    active_monkeypatch.setattr(connectors_actions_module, "ORION_TELEGRAM_AUTOPILOT_SEND_ACK", True, raising=False)
    active_monkeypatch.setattr(
        autopilot_runtime_exports_module,
        "ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET",
        webhook_secret,
        raising=False,
    )
    active_monkeypatch.setattr(autopilot_runtime_exports_module, "ORION_WHATSAPP_AUTOPILOT_ENABLED", False, raising=False)
    active_monkeypatch.setattr(autopilot_runtime_exports_module, "_AUTOPILOT_CONNECTOR_SHELL_SERVICE", None, raising=False)

    telegram_requests: List[Dict[str, Any]] = []
    transport_state: Dict[str, Any] = {"next_message_id": 1000}

    def _fake_api_request(self, bot_token: str, method_name: str, params=None, payload=None):
        call: Dict[str, Any] = {
            "timestamp": time.time(),
            "perf_counter": time.perf_counter(),
            "bot_token": bot_token,
            "method_name": method_name,
            "params": dict(params or {}),
            "payload": dict(payload or {}),
        }
        telegram_requests.append(call)

        def _default_response() -> Any:
            return default_telegram_api_response(call, transport_state)

        if telegram_api_request_handler is not None:
            return telegram_api_request_handler(call, _default_response)
        return _default_response()

    active_monkeypatch.setattr(
        telegram_transport_module.TelegramTransportService,
        "api_request",
        _fake_api_request,
    )

    async def _ensure_pool():
        pool = await db_module.get_pool()
        return pool is not None

    if not asyncio.run(_ensure_pool()):
        _purge_runtime_modules()
        if owned_monkeypatch:
            active_monkeypatch.undo()
        pytest.skip("Black-box runtime requires a reachable Postgres control-plane test database.")

    module_refs = {
        "server": server,
        "auth": auth_module,
        "runs_core": runs_core_module,
        "connectors_actions": connectors_actions_module,
        "runs_engine": runs_engine_module,
        "outbox_service": outbox_service_module,
        "notification_service": notification_service_module,
        "run_state_repository": run_state_repository_module,
        "db": db_module,
        "control_plane_repository": control_plane_repository_module,
        "agent_registry_repository": agent_registry_repository_module,
        "health_diagnostics": health_diagnostics_module,
        "shared": shared_module,
        "telegram_transport": telegram_transport_module,
        "autopilot_runtime_exports": autopilot_runtime_exports_module,
    }

    try:
        with TestClient(server.app) as client:
            yield BlackboxHarness(
                client=client,
                app=server.app,
                auth_module=auth_module,
                agent_registry_repository_module=agent_registry_repository_module,
                outbox_service_module=outbox_service_module,
                notification_service_module=notification_service_module,
                run_state_repository_module=run_state_repository_module,
                control_plane_repository_module=control_plane_repository_module,
                db_module=db_module,
                telegram_requests=telegram_requests,
                state_home=resolved_state_home,
                webhook_secret=webhook_secret,
                module_refs=module_refs,
            )
    finally:
        _purge_runtime_modules()
        if owned_monkeypatch:
            active_monkeypatch.undo()
