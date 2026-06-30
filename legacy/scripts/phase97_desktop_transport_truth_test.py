from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_tauri_desktop_launch_is_runtime_only() -> None:
    source = _read("src-tauri/src/lib.rs")

    _assert("fn spawn_backend(" not in source, "legacy backend sidecar launcher still exists")
    _assert("backend/dist/main.js" not in source, "desktop launcher still references backend/dist/main.js")
    _assert("NEXT_PUBLIC_WS_URL" not in source, "desktop launcher still exports stale backend websocket env")
    _assert('"backend"' not in source[source.index("pub fn run()"):], "desktop run setup still references backend startup")


def test_tauri_frontend_envs_point_at_runtime() -> None:
    source = _read("src-tauri/src/lib.rs")

    _assert("fn workstation_next_envs()" in source, "runtime-only Next env helper is missing")
    _assert('("EMPYRALIS_API_URL", api_url.clone())' in source, "EMPYRALIS_API_URL is not bound to runtime url")
    _assert('("NEXT_PUBLIC_API_URL", api_url.clone())' in source, "NEXT_PUBLIC_API_URL is not bound to runtime url")
    _assert('("NEXT_PUBLIC_ORION_API_URL", api_url)' in source, "NEXT_PUBLIC_ORION_API_URL is not bound to runtime url")


def test_workspace_transport_prefers_runtime_public_url() -> None:
    source = _read("frontend/lib/workspace/workspace-services.tsx")

    orion_index = source.index("env.NEXT_PUBLIC_ORION_API_URL")
    api_index = source.index("env.NEXT_PUBLIC_API_URL")
    _assert(orion_index < api_index, "workspace transport does not prefer NEXT_PUBLIC_ORION_API_URL")


def test_server_control_plane_base_prefers_runtime_public_url() -> None:
    source = _read("frontend/lib/server/control-plane-base-url.ts")

    empyralis_index = source.index("env.EMPYRALIS_API_URL")
    orion_index = source.index("env.NEXT_PUBLIC_ORION_API_URL")
    api_index = source.index("env.NEXT_PUBLIC_API_URL")
    _assert(empyralis_index < orion_index < api_index, "control-plane base url precedence is incorrect")


def main() -> None:
    test_tauri_desktop_launch_is_runtime_only()
    test_tauri_frontend_envs_point_at_runtime()
    test_workspace_transport_prefers_runtime_public_url()
    test_server_control_plane_base_prefers_runtime_public_url()
    print("phase97_desktop_transport_truth_test: ok")


if __name__ == "__main__":
    main()
