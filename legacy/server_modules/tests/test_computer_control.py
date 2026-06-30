from __future__ import annotations

import importlib
from unittest.mock import patch

from server_modules import computer_control


def setup_function() -> None:
    global computer_control

    computer_control = importlib.import_module("server_modules.computer_control")


def test_clipboard_read_write():
    with patch("server_modules.computer_control.supervisor_client.clipboard_read", return_value={"text": "original"}):
        original = computer_control.read_clipboard()
    with patch("server_modules.computer_control.supervisor_client.clipboard_write", return_value={"written": True}):
        computer_control.write_clipboard("empyralist-test-clipboard")
    with patch("server_modules.computer_control.supervisor_client.clipboard_read", return_value={"text": "empyralist-test-clipboard"}):
        assert computer_control.read_clipboard() == "empyralist-test-clipboard"
    with patch("server_modules.computer_control.supervisor_client.clipboard_write", return_value={"written": True}):
        computer_control.write_clipboard(original)


def test_applescript_returns_output():
    with patch("server_modules.computer_control.supervisor_client.run_applescript", return_value={"output": "ok"}):
        output = computer_control.run_applescript('return "ok"')

    assert output == "ok"


def test_notify_does_not_crash():
    with patch("server_modules.computer_control.supervisor_client.notify", return_value={"sent": True}):
        result = computer_control.send_notification("Empyralist", "done")

    assert result == "Notification sent."


def test_click_routes_through_supervisor():
    with patch(
        "server_modules.computer_control.supervisor_client.click",
        return_value={"clicked": True},
    ) as click_mock:
        result = computer_control.computer_control_click(10, 20, button="right", double=True)

    assert result["success"] is True
    click_mock.assert_called_once_with(10, 20, button="right", double=True)


def test_type_routes_through_supervisor():
    with patch(
        "server_modules.computer_control.supervisor_client.type_text",
        return_value={"typed": True, "length": 5},
    ) as type_mock:
        result = computer_control.computer_control_type("hello", delay_ms=15)

    assert result["success"] is True
    type_mock.assert_called_once_with("hello", delay_ms=15)


def test_speak_text_does_not_crash():
    with patch("server_modules.computer_control.supervisor_client.speak", return_value={"spoken": True}):
        result = computer_control.speak_text("hello")

    assert result == "Spoken aloud."


def test_list_apps_returns_list():
    with patch(
        "server_modules.computer_control.supervisor_client.list_apps",
        return_value={"windows": [{"pid": 1, "name": "Safari", "exe": "/Applications/Safari.app"}]},
    ):
        apps = computer_control.list_running_apps()
    assert isinstance(apps, list)
    if apps:
        first = apps[0]
        assert isinstance(first, dict)
        assert "pid" in first
        assert "name" in first
        assert "exe" in first


def test_computer_control_list_apps_routes_through_supervisor():
    with patch(
        "server_modules.computer_control.supervisor_client.list_apps",
        return_value={"windows": []},
    ) as list_mock:
        result = computer_control.computer_control_list_apps()

    assert result["success"] is True
    list_mock.assert_called_once_with()


def test_launch_app_routes_through_supervisor():
    with patch(
        "server_modules.computer_control.supervisor_client.launch_app",
        return_value={"launched": True},
    ) as launch_mock:
        result = computer_control.computer_control_launch_app("/Applications/Safari.app")

    assert result["success"] is True
    launch_mock.assert_called_once_with("/Applications/Safari.app")
