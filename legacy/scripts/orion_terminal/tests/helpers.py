from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from scripts.orion_terminal.core import Choice


class FakeUI:
    def __init__(
        self,
        choose_keys: Optional[List[str]] = None,
        inputs: Optional[List[str]] = None,
        confirms: Optional[List[bool]] = None,
    ) -> None:
        self.choose_keys = list(choose_keys or [])
        self.inputs = list(inputs or [])
        self.confirms = list(confirms or [])
        self.lines: List[str] = []

    def note(self, message: str) -> None:
        self.lines.append(str(message))

    def message(self, title: str, body: str) -> None:
        self.lines.append(f"[message] {title}")
        self.lines.extend(str(body).splitlines())

    def choose(self, prompt: str, options: List[Choice], default_index: int = 0, title: str = "") -> Choice:
        if self.choose_keys:
            wanted = self.choose_keys.pop(0)
            for item in options:
                if item.key == wanted:
                    return item
            raise AssertionError(f"choice key not found: {wanted} (prompt={prompt})")
        return options[default_index]

    def multi_select(
        self,
        prompt: str,
        options: List[Choice],
        default_indexes: Optional[List[int]] = None,
        title: str = "",
    ) -> List[Choice]:
        selected = {i for i in (default_indexes or []) if 0 <= i < len(options)}
        if self.choose_keys:
            while self.choose_keys:
                wanted = self.choose_keys.pop(0)
                if wanted == "__continue":
                    break
                idx = next((i for i, item in enumerate(options) if item.key == wanted), None)
                if idx is None:
                    raise AssertionError(f"choice key not found: {wanted} (prompt={prompt})")
                if idx in selected:
                    selected.remove(idx)
                else:
                    selected.add(idx)
        return [options[i] for i in sorted(selected)]

    def input(
        self,
        prompt: str,
        default: Optional[str] = None,
        required: bool = False,
        secret: bool = False,
        title: str = "",
    ) -> str:
        if self.inputs:
            value = self.inputs.pop(0)
        else:
            value = default or ""
        if required and not str(value).strip():
            return str(default or "required-value")
        return str(value)

    def confirm(self, prompt: str, default_yes: bool = True, title: str = "") -> bool:
        if self.confirms:
            return bool(self.confirms.pop(0))
        return bool(default_yes)

    def render(self) -> str:
        return "\n".join(self.lines).strip() + "\n"


def assert_snapshot(actual: str, name: str) -> None:
    root = Path(__file__).resolve().parent
    snap_dir = root / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{name}.txt"
    if os.getenv("ORION_UPDATE_SNAPSHOTS") == "1":
        snap_path.write_text(actual, encoding="utf-8")
        return
    if not snap_path.exists():
        raise AssertionError(f"Snapshot missing: {snap_path}. Run with ORION_UPDATE_SNAPSHOTS=1 once.")
    expected = snap_path.read_text(encoding="utf-8")
    if actual != expected:
        raise AssertionError(f"Snapshot mismatch: {snap_path}")
