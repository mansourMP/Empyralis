from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules import deployed_agent_rate_limit_service


async def evaluate_daily_quota(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: Optional[str],
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    return await deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=deployed_agent,
        channel_key=channel_key,
        external_user_id=external_user_id,
        message_id=message_id,
    )
