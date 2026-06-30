from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

from .core import Choice

PromptKind = Literal["select", "multiselect", "text", "confirm", "note"]


@dataclass(frozen=True)
class PromptOption:
    key: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class PromptContract:
    id: str
    kind: PromptKind
    message: str
    schema_version: int = 1
    title: str = "Empyralis"
    options: List[PromptOption] = field(default_factory=list)
    default_index: int = 0
    required: bool = False
    secret: bool = False
    placeholder: Optional[str] = None


def contract_choices(contract: PromptContract) -> List[Choice]:
    return [Choice(opt.key, opt.label, opt.note) for opt in contract.options]
