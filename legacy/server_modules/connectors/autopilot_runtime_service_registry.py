from __future__ import annotations

from typing import Any, Callable, Optional


class AutopilotRuntimeServiceRegistry:
    def __init__(
        self,
        *,
        build_connector_support_service: Callable[[], Any],
        build_transport_service: Callable[[], Any],
        build_terminal_service: Callable[[], Any],
        build_run_entry_service: Callable[[], Any],
        build_runtime_support_service: Callable[[], Any],
        build_menu_service: Callable[[], Any],
    ) -> None:
        self.build_connector_support_service = build_connector_support_service
        self.build_transport_service = build_transport_service
        self.build_terminal_service = build_terminal_service
        self.build_run_entry_service = build_run_entry_service
        self.build_runtime_support_service = build_runtime_support_service
        self.build_menu_service = build_menu_service

        self._connector_support_service: Optional[Any] = None
        self._transport_service: Optional[Any] = None
        self._terminal_service: Optional[Any] = None
        self._run_entry_service: Optional[Any] = None
        self._runtime_support_service: Optional[Any] = None
        self._menu_service: Optional[Any] = None

    def connector_support_service(self) -> Any:
        if self._connector_support_service is None:
            self._connector_support_service = self.build_connector_support_service()
        return self._connector_support_service

    def transport_service(self) -> Any:
        if self._transport_service is None:
            self._transport_service = self.build_transport_service()
        return self._transport_service

    def terminal_service(self) -> Any:
        if self._terminal_service is None:
            self._terminal_service = self.build_terminal_service()
        return self._terminal_service

    def run_entry_service(self) -> Any:
        if self._run_entry_service is None:
            self._run_entry_service = self.build_run_entry_service()
        return self._run_entry_service

    def runtime_support_service(self) -> Any:
        if self._runtime_support_service is None:
            self._runtime_support_service = self.build_runtime_support_service()
        return self._runtime_support_service

    def menu_service(self) -> Any:
        if self._menu_service is None:
            self._menu_service = self.build_menu_service()
        return self._menu_service
