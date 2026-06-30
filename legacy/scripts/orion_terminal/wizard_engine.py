from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .core import Choice, UI
from .prompt_contracts import PromptContract, contract_choices


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class WizardStepRecord:
    ts: str
    kind: str
    prompt: str
    prompt_id: str = ""
    prompt_schema_version: int = 1
    response: Any = None
    options: List[str] = field(default_factory=list)
    title: str = ""
    sensitive: bool = False


@dataclass
class WizardSession:
    flow: str
    workspace_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=_now_iso)
    ended_at: Optional[str] = None
    status: str = "running"
    error: Optional[str] = None
    steps: List[WizardStepRecord] = field(default_factory=list)

    def record(
        self,
        kind: str,
        prompt: str,
        response: Any = None,
        options: Optional[Sequence[str]] = None,
        title: str = "",
        sensitive: bool = False,
        prompt_id: str = "",
        prompt_schema_version: int = 1,
    ) -> None:
        value = "***" if sensitive and response not in {None, ""} else response
        self.steps.append(
            WizardStepRecord(
                ts=_now_iso(),
                kind=kind,
                prompt_id=prompt_id,
                prompt_schema_version=max(1, int(prompt_schema_version or 1)),
                prompt=prompt,
                response=value,
                options=list(options or []),
                title=title,
                sensitive=sensitive,
            )
        )

    def complete(self) -> None:
        self.status = "done"
        self.ended_at = _now_iso()

    def cancel(self, reason: str = "cancelled") -> None:
        self.status = "cancelled"
        self.error = reason
        self.ended_at = _now_iso()

    def fail(self, error: str) -> None:
        self.status = "error"
        self.error = error
        self.ended_at = _now_iso()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "flow": self.flow,
            "workspace_id": self.workspace_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "error": self.error,
            "steps": [asdict(step) for step in self.steps],
        }


def persist_wizard_session(session: WizardSession) -> Optional[str]:
    enabled = os.getenv("ORION_WIZARD_SESSION_LOG", "1") != "0"
    if not enabled:
        return None
    root = os.getenv("ORION_WIZARD_SESSION_DIR", ".orion/wizard_sessions")
    target_dir = Path(root).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        file_path = target_dir / f"{session.flow}-{stamp}-{session.id[:8]}.json"
        file_path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        return str(file_path)
    except Exception:
        return None


class WizardPrompter:
    def __init__(self, ui: UI, session: WizardSession):
        self.ui = ui
        self.session = session

    def intro(self, title: str) -> None:
        self.ui.note("")
        self.ui.note(title)
        self.session.record("intro", "intro", title)

    def outro(self, message: str) -> None:
        self.ui.note(message)
        self.session.record("outro", "outro", message)

    def note(self, message: str, title: str = "") -> None:
        self.ui.note(message)
        self.session.record("note", title or "note", message, title=title)

    def select(self, prompt: str, options: Sequence[Choice], default_index: int = 0, title: str = "Empyralis") -> Choice:
        choice = self.ui.choose(prompt, options, default_index=default_index, title=title)
        self.session.record(
            "select",
            prompt=prompt,
            response=choice.key,
            options=[item.label for item in options],
            title=title,
        )
        return choice

    def select_contract(self, contract: PromptContract) -> Choice:
        options = contract_choices(contract)
        choice = self.ui.choose(
            contract.message,
            options,
            default_index=contract.default_index,
            title=contract.title,
        )
        self.session.record(
            "select",
            prompt=contract.message,
            prompt_id=contract.id,
            prompt_schema_version=contract.schema_version,
            response=choice.key,
            options=[item.label for item in options],
            title=contract.title,
        )
        return choice

    def multiselect(
        self,
        prompt: str,
        options: Sequence[Choice],
        default_indexes: Optional[Sequence[int]] = None,
        title: str = "Empyralis",
    ) -> List[Choice]:
        selected = self.ui.multi_select(prompt, options, default_indexes=default_indexes, title=title)
        self.session.record(
            "multiselect",
            prompt=prompt,
            response=[item.key for item in selected],
            options=[item.label for item in options],
            title=title,
        )
        return selected

    def multiselect_contract(
        self,
        contract: PromptContract,
        default_indexes: Optional[Sequence[int]] = None,
    ) -> List[Choice]:
        options = contract_choices(contract)
        selected = self.ui.multi_select(
            contract.message,
            options,
            default_indexes=default_indexes,
            title=contract.title,
        )
        self.session.record(
            "multiselect",
            prompt=contract.message,
            prompt_id=contract.id,
            prompt_schema_version=contract.schema_version,
            response=[item.key for item in selected],
            options=[item.label for item in options],
            title=contract.title,
        )
        return selected

    def text(
        self,
        prompt: str,
        default: Optional[str] = None,
        required: bool = False,
        secret: bool = False,
        title: str = "Empyralis",
    ) -> str:
        value = self.ui.input(prompt, default=default, required=required, secret=secret, title=title)
        self.session.record("text", prompt=prompt, response=value, title=title, sensitive=secret)
        return value

    def text_contract(self, contract: PromptContract, default: Optional[str] = None) -> str:
        value = self.ui.input(
            contract.message,
            default=default,
            required=contract.required,
            secret=contract.secret,
            title=contract.title,
        )
        self.session.record(
            "text",
            prompt=contract.message,
            prompt_id=contract.id,
            prompt_schema_version=contract.schema_version,
            response=value,
            title=contract.title,
            sensitive=contract.secret,
        )
        return value

    def confirm(self, prompt: str, default_yes: bool = True, title: str = "Empyralis") -> bool:
        result = self.ui.confirm(prompt, default_yes=default_yes, title=title)
        self.session.record("confirm", prompt=prompt, response=result, title=title)
        return result

    def confirm_contract(self, contract: PromptContract, default_yes: bool = True) -> bool:
        result = self.ui.confirm(contract.message, default_yes=default_yes, title=contract.title)
        self.session.record(
            "confirm",
            prompt=contract.message,
            prompt_id=contract.id,
            prompt_schema_version=contract.schema_version,
            response=result,
            title=contract.title,
        )
        return result
