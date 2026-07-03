#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_modules import supervisor_client  # noqa: E402


MESSAGE = "Empyralis is alive"


def _find_supervisor_secret() -> str:
    existing = str(os.getenv("EMPYRALIS_SUPERVISOR_SECRET") or "").strip()
    if existing:
        return existing

    listener_result = subprocess.run(
        ["lsof", "-nP", "-iTCP:7788", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [line.strip() for line in listener_result.stdout.splitlines() if line.strip()]

    if not pids:
        pid_result = subprocess.run(
            ["pgrep", "-f", "empyralis-supervisor"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [line.strip() for line in pid_result.stdout.splitlines() if line.strip()]

    if not pids:
        raise RuntimeError("Could not find a running empyralis-supervisor process.")

    pid = pids[0]
    ps_result = subprocess.run(
        ["ps", "eww", "-p", pid],
        capture_output=True,
        text=True,
        check=False,
    )
    if ps_result.returncode != 0:
        raise RuntimeError(f"Failed to inspect supervisor environment for pid {pid}.")
    match = re.search(r"EMPYRALIS_SUPERVISOR_SECRET=([^ ]+)", ps_result.stdout)
    if not match:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET not found in running supervisor process.")

    secret = match.group(1).strip()
    if not secret:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET was empty in running supervisor process.")
    os.environ["EMPYRALIS_SUPERVISOR_SECRET"] = secret
    return secret


def _save_first_image(payload: dict, destination: Path) -> tuple[int, int]:
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("Supervisor screenshot returned no images.")
    image = images[0]
    if not isinstance(image, dict):
        raise RuntimeError("Supervisor screenshot returned invalid image metadata.")
    encoded = str(image.get("data_base64") or "").strip()
    if not encoded:
        raise RuntimeError("Supervisor screenshot returned empty image data.")
    destination.write_bytes(base64.b64decode(encoded))
    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("Supervisor screenshot did not report valid dimensions.")
    return width, height


def _open_google_in_new_tab() -> None:
    applescript = r'''
set targetUrl to "https://www.google.com"
tell application "System Events"
  set chromeRunning to exists process "Google Chrome"
  set safariRunning to exists process "Safari"
end tell
if chromeRunning then
  tell application "Google Chrome"
    activate
    if (count of windows) = 0 then
      make new window
    end if
    tell front window
      make new tab with properties {URL:targetUrl}
      set active tab index to (count of tabs)
    end tell
  end tell
else if safariRunning then
  tell application "Safari"
    activate
    if (count of windows) = 0 then
      make new document
    end if
    tell front window
      set current tab to (make new tab with properties {URL:targetUrl})
    end tell
  end tell
else
  open location targetUrl
end if
'''
    supervisor_client.run_applescript(applescript)


def _move_mouse_slowly(width: int, height: int) -> list[dict[str, int]]:
    y = height // 2
    left = max(40, width // 8)
    right = min(width - 40, width - (width // 8))
    steps = 24
    positions: list[dict[str, int]] = []
    for index in range(steps):
        ratio = index / max(1, steps - 1)
        x = int(left + (right - left) * ratio)
        supervisor_client.move_mouse(x, y)
        positions.append({"x": x, "y": y})
        time.sleep(0.06)
    return positions


def main() -> int:
    _find_supervisor_secret()

    output_dir = ROOT / "tmp" / "supervisor-demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    before_path = output_dir / "google-before.png"
    after_path = output_dir / "google-after.png"

    _open_google_in_new_tab()
    time.sleep(2.5)

    first = supervisor_client.capture_screenshot()
    width, height = _save_first_image(first, before_path)

    positions = _move_mouse_slowly(width, height)

    supervisor_client.press_key("cmd+l")
    time.sleep(0.2)
    supervisor_client.type_text(MESSAGE, delay_ms=20)
    time.sleep(0.5)

    second = supervisor_client.capture_screenshot()
    _save_first_image(second, after_path)

    print(
        json.dumps(
            {
                "ok": True,
                "before_screenshot": str(before_path),
                "after_screenshot": str(after_path),
                "typed": MESSAGE,
                "move_steps": len(positions),
                "screen": {"width": width, "height": height},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
