from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agent_computer.sh"
WINDOWS_SCRIPT = ROOT / "scripts" / "install_agent_computer_windows_service.ps1"


def run_script(*args: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_agent_computer_script_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout


def test_service_install_requires_system_flag() -> None:
    result = run_script("service-install", EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Linux")

    assert result.returncode == 2
    assert "without --system" in result.stdout
    assert "desktop auto-start uses launchd-install" in result.stdout


def test_linux_service_install_dry_run_renders_systemd_unit() -> None:
    result = run_script(
        "service-install",
        "--system",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Linux",
        EMPYRALIS_AGENT_COMPUTER_SERVICE_USER="agentuser",
    )

    assert result.returncode == 0, result.stdout
    assert "[Service]" in result.stdout
    assert "User=agentuser" in result.stdout
    assert "service-run --system" in result.stdout
    assert "Restart=always" in result.stdout
    assert "EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE=1" in result.stdout


def test_service_install_rejects_unsafe_service_name() -> None:
    result = run_script(
        "service-install",
        "--system",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Linux",
        EMPYRALIS_AGENT_COMPUTER_SERVICE_NAME="../evil",
    )

    assert result.returncode == 2
    assert "Invalid service name" in result.stdout


def test_service_install_rejects_unsafe_service_user() -> None:
    result = run_script(
        "service-install",
        "--system",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Linux",
        EMPYRALIS_AGENT_COMPUTER_SERVICE_USER="agent\nUser=root",
    )

    assert result.returncode == 2
    assert "Invalid service user" in result.stdout


def test_macos_system_service_refuses_default_desktop_path() -> None:
    result = run_script(
        "service-install",
        "--system",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Darwin",
    )

    assert result.returncode == 2
    assert "Refusing macOS system LaunchDaemon" in result.stdout
    assert "user-session bridge" in result.stdout


def test_macos_server_service_dry_run_requires_explicit_server_mac_flag() -> None:
    result = run_script(
        "service-install",
        "--system",
        "--server-mac",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Darwin",
        EMPYRALIS_AGENT_COMPUTER_SERVICE_USER="agentuser",
    )

    assert result.returncode == 0, result.stdout
    assert "<key>UserName</key>" in result.stdout
    assert "<string>agentuser</string>" in result.stdout
    assert "<string>service-run</string>" in result.stdout
    assert "<string>--system</string>" in result.stdout


def test_macos_launchd_install_uses_foreground_user_session_runner() -> None:
    result = run_script(
        "launchd-install",
        "--dry-run",
        EMPYRALIS_AGENT_COMPUTER_TEST_UNAME="Darwin",
    )

    assert result.returncode == 0, result.stdout
    assert "<string>launchd-run</string>" in result.stdout
    assert "<string>start</string>" not in result.stdout
    assert "<key>KeepAlive</key>" in result.stdout


def test_windows_service_wrapper_exists_for_server_hosts() -> None:
    body = WINDOWS_SCRIPT.read_text(encoding="utf-8")

    assert 'ValidateSet("install", "uninstall", "status", "run")' in body
    assert "EmpyralisAgentComputer" in body
    assert "New-Service" in body
    assert "EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE" in body
    assert "server_vps" in body
