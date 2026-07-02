"""Gateway subsystem shared types and constants.

Extracted to break the HIGH import cycle:
  gateway_execution_service → gateway_protocol_service → personal_channels_service
  → gateway_execution_service

Module MUST NOT import from gateway_execution_service, gateway_protocol_service,
or personal_channels_service. It is a leaf — only downstream consumers import it.
"""

from __future__ import annotations

# Timeout for gateway tool requests (invoke + interrupt roundtrips).
DEFAULT_TOOL_REQUEST_TIMEOUT_SECONDS: int = 120
