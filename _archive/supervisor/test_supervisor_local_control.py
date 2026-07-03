#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_modules import supervisor_client  # noqa: E402


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


def main() -> int:
    output_dir = ROOT / "tmp" / "supervisor-test"
    output_dir.mkdir(parents=True, exist_ok=True)
    before_path = output_dir / "before.png"
    after_path = output_dir / "after.png"

    before = supervisor_client.capture_screenshot()
    width, height = _save_first_image(before, before_path)
    center_x = width // 2
    center_y = height // 2

    # The supervisor currently exposes click, not a pure mouse-move capability.
    click_result = supervisor_client.click(x=center_x, y=center_y)
    type_result = supervisor_client.type_text("hello from Empyralis")
    after = supervisor_client.capture_screenshot()
    _save_first_image(after, after_path)

    print(
        json.dumps(
            {
                "ok": True,
                "before_screenshot": str(before_path),
                "after_screenshot": str(after_path),
                "screen": {"width": width, "height": height},
                "center": {"x": center_x, "y": center_y},
                "click_result": click_result,
                "type_result": type_result,
                "note": "Center step uses computer_control.click because the supervisor has no mouse_move capability yet.",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
