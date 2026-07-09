"""Curated name pool for auto-generated agent names.

The create-agent wizard no longer has a Name step — an agent is created the
moment the owner commits the Placement step, before they've thought of a
name. This supplies a short, plain default; the owner renames it anytime
from the agent's Overview tab (display_name PATCH).

"Sage" is reserved for the workspace operator agent and deliberately excluded.
"""

from __future__ import annotations

import random
from typing import Iterable, List

NAME_POOL: List[str] = [
    "Atlas", "Beacon", "Cedar", "Delta", "Ember", "Flint", "Garnet", "Harbor",
    "Indigo", "Juniper", "Kestrel", "Lumen", "Maple", "Nova", "Onyx", "Pixel",
    "Quartz", "Reef", "Talon", "Umbra", "Vale", "Willow", "Xenon", "Yarrow",
    "Zephyr", "Alder", "Basalt", "Compass", "Drift", "Echo", "Fable", "Grove",
    "Haven", "Ibis", "Jasper", "Knoll", "Lark", "Meridian", "Nimbus", "Opal",
    "Prairie", "Quill", "Ridge", "Sail", "Thicket", "Ursa", "Verve", "Wren",
    "Yield", "Zinnia",
]


def assign_agent_name(existing_labels: Iterable[str]) -> str:
    """Pick a pool name not already used in this workspace (case-insensitive).

    Falls back to appending an incrementing numeric suffix ("Atlas 2") once
    the pool is exhausted, trying every name at each suffix level before
    moving to the next — so it stays deterministic-ish and never loops
    forever."""
    taken = {str(label or "").strip().lower() for label in existing_labels}
    available = [n for n in NAME_POOL if n.lower() not in taken]
    if available:
        return random.choice(available)

    suffix = 2
    while True:
        for n in NAME_POOL:
            candidate = f"{n} {suffix}"
            if candidate.lower() not in taken:
                return candidate
        suffix += 1
