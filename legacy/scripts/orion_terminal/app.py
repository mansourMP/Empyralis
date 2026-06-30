from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

try:
    from scripts.platform_execution import stack_start_command, stack_start_command_hint
except ImportError:
    from platform_execution import stack_start_command, stack_start_command_hint  # type: ignore[no-redef]

from .core import (
    DEFAULT_API_URL,
    EXECUTION_TARGETS,
    GENERAL_MODE,
    TRUST_MODES,
    UI,
    err,
    ok,
    print_launcher_header,
    strong,
    warn,
)
from .clients.runtime import RuntimeClient
from .errors import CompatibilityError, OrionTerminalError
from .flows import (
    choose_and_create_connectors,
    configure_flow,
    launcher_flow,
    onboard_flow,
    orion_setup_flow,
    preflight_flow,
    run_once,
    run_tui_shell,
)


def _is_local_runtime(api_url: str) -> bool:
    base = str(api_url or "").strip().lower()
    return base.startswith("http://127.0.0.1:") or base.startswith("http://localhost:")


def _normalize_runtime_key(raw: str) -> str:
    # Defend against copied/wrapped keys that include hidden spaces/newlines.
    return "".join(str(raw or "").split())


def _start_local_stack(ui: UI, root_dir: Path, runtime_key: str) -> bool:
    start_command = stack_start_command(root_dir)
    if not start_command:
        return False
    ui.note(f"{warn('[warn]')} Runtime unavailable. Starting local stack...")
    env = os.environ.copy()
    env["RUNTIME_KEY"] = _normalize_runtime_key(runtime_key)
    try:
        result = subprocess.run(
            start_command,
            cwd=str(root_dir),
            env=env,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Empyralis terminal command center")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=f"Runtime API URL (default: {DEFAULT_API_URL})")
    parser.add_argument("--runtime-key", default="", help="Runtime API key (or use RUNTIME_KEY / ORION_API_KEY env)")
    parser.add_argument("--workspace-id", default="default", help="Workspace ID (default: default)")
    parser.add_argument("--goal", default="", help="Run immediately with this goal using safe defaults (general worker mode).")
    parser.add_argument("--engine", default="codex", help="Engine (default: codex)")
    parser.add_argument(
        "--mode",
        choices=["launcher", "run", "setup", "onboard", "configure", "tui", "preflight"],
        default="launcher",
        help="Entry mode (default: launcher)",
    )
    parser.add_argument(
        "--connectors-only",
        action="store_true",
        help="Open connector onboarding only (Google Workspace / Telegram / WhatsApp), then exit.",
    )
    parser.add_argument("--text", action="store_true", help="Force text fallback (disable prompt_toolkit dialogs).")
    args = parser.parse_args()

    ui = UI(prefer_tui=not args.text)

    root_dir = Path(__file__).resolve().parents[2]
    stack_key_file = root_dir / ".orion-stack" / "runtime_key"
    stack_key = ""
    try:
        if stack_key_file.exists():
            stack_key = stack_key_file.read_text(encoding="utf-8").strip()
    except Exception:
        stack_key = ""

    api_url = args.api_url.rstrip("/")
    runtime_key = ""
    runtime_key_raw_sources = [
        args.runtime_key,
        str(os.environ.get("RUNTIME_KEY") or ""),
        str(os.environ.get("ORION_API_KEY") or ""),
        stack_key,
    ]
    for raw_value in runtime_key_raw_sources:
        normalized = _normalize_runtime_key(raw_value)
        if normalized:
            runtime_key = normalized
            if normalized != str(raw_value or ""):
                ui.note(f"{warn('[warn]')} Runtime key contained whitespace and was normalized.")
            break
    if not runtime_key:
        runtime_key = _normalize_runtime_key(
            ui.input("Runtime API key (ORION_API_KEY / RUNTIME_KEY)", required=True, secret=True)
        )

    runtime_client = RuntimeClient(api_url=api_url, api_key=runtime_key)
    try:
        health = runtime_client.get_health(enforce_compatibility=True)
    except CompatibilityError as exc:
        ui.note(f"\n{err('[error]')} Runtime compatibility check failed: {exc}")
        ui.note("Upgrade CLI/runtime pair or set ORION_CLI_ENFORCE_RUNTIME_COMPAT=0 temporarily.")
        return 2
    except OrionTerminalError as exc:
        recovered = False
        start_hint = stack_start_command_hint(root_dir)
        if _is_local_runtime(api_url):
            recovered = _start_local_stack(ui, root_dir, runtime_key)
        if recovered:
            try:
                health = runtime_client.get_health(enforce_compatibility=True)
            except Exception as retry_exc:
                ui.note(f"\n{err('[error]')} Runtime health check failed after auto-start: {retry_exc}")
                ui.note(f"Start stack first: {start_hint}")
                return 2
        else:
            ui.note(f"\n{err('[error]')} Runtime health check failed: {exc}")
            ui.note(f"Start stack first: {start_hint}")
            return 2
    except Exception as exc:
        start_hint = stack_start_command_hint(root_dir)
        ui.note(f"\n{err('[error]')} Runtime health check failed: {exc}")
        ui.note(f"Start stack first: {start_hint}")
        return 2

    if not bool(health.get("runtime_valid", True)):
        ui.note(f"{warn('[warn]')} Runtime reports invalid config. Continue carefully.")

    if args.mode == "launcher" and not bool(health.get("setup_complete", True)):
        print_launcher_header(api_url, args.workspace_id, health)
        ui.note(strong("First run detected. Starting Empyralis Setup."))
        return orion_setup_flow(ui, api_url, runtime_key, args.workspace_id, args.engine)

    if args.connectors_only:
        print_launcher_header(api_url, args.workspace_id, health)
        ui.note(strong("Connector setup mode"))
        created = choose_and_create_connectors(ui, api_url, runtime_key, args.workspace_id)
        if created:
            ui.note(f"\n{ok('[ok]')} Added {len(created)} connector(s).")
        else:
            ui.note("\nNo connectors were added.")
        return 0

    if args.mode == "run" or (args.goal.strip() and args.mode == "launcher"):
        goal = args.goal.strip() or ui.input("What should Empyralis run now?", required=True)
        return run_once(
            ui=ui,
            api_url=api_url,
            runtime_key=runtime_key,
            workspace_id=args.workspace_id,
            engine=args.engine,
            goal=goal,
            pack=GENERAL_MODE,
            trust=TRUST_MODES[0],
            target=EXECUTION_TARGETS[0],
            pack_inputs=None,
            prompt_connectors=False,
        )

    if args.mode == "setup":
        print_launcher_header(api_url, args.workspace_id, health)
        return orion_setup_flow(ui, api_url, runtime_key, args.workspace_id, args.engine)

    if args.mode == "onboard":
        print_launcher_header(api_url, args.workspace_id, health)
        return onboard_flow(ui, api_url, runtime_key, args.workspace_id, args.engine)

    if args.mode == "preflight":
        print_launcher_header(api_url, args.workspace_id, health)
        return preflight_flow(
            ui,
            api_url,
            runtime_key,
            args.workspace_id,
            args.engine,
            goal=args.goal.strip() or None,
            auto_confirm=bool(args.goal.strip()),
        )

    if args.mode == "configure":
        print_launcher_header(api_url, args.workspace_id, health)
        return configure_flow(ui, api_url, runtime_key, args.workspace_id, args.engine)

    if args.mode == "tui":
        return run_tui_shell(ui, api_url, runtime_key, args.workspace_id, args.engine)

    return launcher_flow(ui, api_url, runtime_key, args.workspace_id, args.engine, health)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
