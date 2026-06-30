from __future__ import annotations

from typing import Any, Callable, Optional


class AutopilotSupportServiceRegistry:
    def __init__(
        self,
        *,
        build_profile_service: Callable[[], Any],
        build_runtime_status_service: Callable[[], Any],
        build_workflow_setup_service: Callable[[], Any],
        build_connector_context_service: Callable[[], Any],
        build_approval_service: Callable[[], Any],
        build_common_support_service: Callable[[], Any],
        build_skill_service: Callable[[], Any],
        build_channel_support_service: Callable[[], Any],
    ) -> None:
        self.build_profile_service = build_profile_service
        self.build_runtime_status_service = build_runtime_status_service
        self.build_workflow_setup_service = build_workflow_setup_service
        self.build_connector_context_service = build_connector_context_service
        self.build_approval_service = build_approval_service
        self.build_common_support_service = build_common_support_service
        self.build_skill_service = build_skill_service
        self.build_channel_support_service = build_channel_support_service

        self._profile_service: Optional[Any] = None
        self._runtime_status_service: Optional[Any] = None
        self._workflow_setup_service: Optional[Any] = None
        self._connector_context_service: Optional[Any] = None
        self._approval_service: Optional[Any] = None
        self._common_support_service: Optional[Any] = None
        self._skill_service: Optional[Any] = None
        self._channel_support_service: Optional[Any] = None

    def profile_service(self) -> Any:
        if self._profile_service is None:
            self._profile_service = self.build_profile_service()
        return self._profile_service

    def runtime_status_service(self) -> Any:
        if self._runtime_status_service is None:
            self._runtime_status_service = self.build_runtime_status_service()
        return self._runtime_status_service

    def workflow_setup_service(self) -> Any:
        if self._workflow_setup_service is None:
            self._workflow_setup_service = self.build_workflow_setup_service()
        return self._workflow_setup_service

    def connector_context_service(self) -> Any:
        if self._connector_context_service is None:
            self._connector_context_service = self.build_connector_context_service()
        return self._connector_context_service

    def approval_service(self) -> Any:
        if self._approval_service is None:
            self._approval_service = self.build_approval_service()
        return self._approval_service

    def common_support_service(self) -> Any:
        if self._common_support_service is None:
            self._common_support_service = self.build_common_support_service()
        return self._common_support_service

    def skill_service(self) -> Any:
        if self._skill_service is None:
            self._skill_service = self.build_skill_service()
        return self._skill_service

    def channel_support_service(self) -> Any:
        if self._channel_support_service is None:
            self._channel_support_service = self.build_channel_support_service()
        return self._channel_support_service
