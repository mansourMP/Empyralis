from __future__ import annotations

from typing import Any, List, Optional, Protocol

from ..core import accent, muted
from ..core import Choice


class _PrompterLike(Protocol):
    def select(self, prompt: str, options: List[Choice], default_index: int = 0, title: str = "") -> Choice:
        ...

    def multiselect(
        self,
        prompt: str,
        options: List[Choice],
        default_indexes: Optional[List[int]] = None,
        title: str = "",
    ) -> List[Choice]:
        ...


def collect_toggle_choices(
    *,
    ui: Any,
    prompt: str,
    base_options: List[Choice],
    title: str,
    prompter: Optional[_PrompterLike] = None,
    continue_skip_hint: str = "Skip for now",
    continue_done_hint: str = "Done",
    default_index: int = 0,
    show_selected_suffix: bool = False,
    log_toggles: bool = True,
    toggle_item_label: str = "Section",
    default_selected_keys: Optional[List[str]] = None,
    min_required: int = 0,
) -> List[str]:
    default_selected_keys = list(default_selected_keys or [])
    default_key_set = set(default_selected_keys)
    base_key_set = {item.key for item in base_options}
    default_indexes: List[int] = [
        idx
        for idx, item in enumerate(base_options)
        if item.key in default_key_set and item.key in base_key_set
    ]

    # Wizard-capable path: use one true multiselect prompt to avoid
    # enter-to-toggle loops and repeated add/remove transcript spam.
    if prompter is not None and hasattr(prompter, "multiselect"):
        while True:
            selected = prompter.multiselect(
                prompt,
                list(base_options),
                default_indexes=default_indexes,
                title=title,
            )
            selected_keys = [item.key for item in selected]
            if len(selected_keys) >= max(0, int(min_required)):
                return selected_keys
            can_note = hasattr(ui, "note") and callable(getattr(ui, "note"))
            if can_note:
                ui.note(
                    f"{accent('•')}  Select at least {max(1, int(min_required))} {toggle_item_label.lower()}"
                )
                ui.note(
                    f"{muted('│')}  Use Space to toggle, then Enter to confirm."
                )

    selected_keys: List[str] = []
    selected_set = set(default_selected_keys)
    selected_keys = [item.key for item in base_options if item.key in selected_set]
    key_to_label = {item.key: item.label for item in base_options}
    can_note = hasattr(ui, "note") and callable(getattr(ui, "note"))

    while True:
        rendered: List[Choice] = []
        for item in base_options:
            label = item.label
            if show_selected_suffix and item.key in selected_set:
                label = f"{label} (Selected)"
            rendered.append(Choice(item.key, label, item.note))

        continue_hint = continue_skip_hint if not selected_keys else continue_done_hint
        rendered.append(Choice("__continue", "Continue", continue_hint))
        pick_default = max(0, min(default_index, len(rendered) - 1))

        if prompter:
            picked = prompter.select(prompt, rendered, default_index=pick_default, title=title)
        else:
            picked = ui.choose(prompt, rendered, default_index=pick_default, title=title)

        if picked.key == "__continue":
            if len(selected_keys) < max(0, int(min_required)):
                if can_note:
                    ui.note(
                        f"{accent('•')}  Select at least {max(1, int(min_required))} {toggle_item_label.lower()}"
                    )
                    ui.note(f"{muted('│')}  Use Space to toggle, then Enter to confirm.")
                continue
            return selected_keys

        if picked.key in selected_set:
            selected_set.remove(picked.key)
            selected_keys = [key for key in selected_keys if key != picked.key]
            if log_toggles and can_note:
                ui.note(f"{accent('•')}  {toggle_item_label} removed")
                ui.note(f"{muted('│')}  {key_to_label.get(picked.key, picked.label)}")
        else:
            selected_set.add(picked.key)
            selected_keys.append(picked.key)
            if log_toggles and can_note:
                ui.note(f"{accent('•')}  {toggle_item_label} added")
                ui.note(f"{muted('│')}  {key_to_label.get(picked.key, picked.label)}")
